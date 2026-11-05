"""End-to-end test for the OAuth redirect attack reported by security researcher.

Attack scenario:
  1. Attacker registers a malicious OAuth client with redirect_uri=https://attacker.com/steal
  2. Attacker crafts an authorization URL and tricks victim into visiting it
  3. Victim approves on the legitimate-looking consent page
  4. Server redirects auth code to attacker's domain
  5. Attacker exchanges code for access token cross-origin (enabled by CORS credentials:true)
  6. Attacker has full read/write access to victim's Plane workspace

This test replays the attack against the real server app and verifies each
security fix blocks the corresponding step. Two layers close it: dynamic client
registration refuses a redirect URI outside the allowlist (fastmcp 3.4.3+,
"Fix DCR redirect URI validation"), so step 1 fails before a client exists; and
/authorize validates the redirect_uri of a registered client again, so a client
registered with a legitimate URI cannot be steered to another one later.
"""

import pytest
from fastmcp import FastMCP
from key_value.aio.stores.memory import MemoryStore
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Mount
from starlette.testclient import TestClient

from plane_mcp.auth import PlaneOAuthProvider
from plane_mcp.server import get_allowed_client_redirect_uris

# The allowlist the server actually ships. Copying it here let it drift, and a
# stale copy silently stops testing the patterns in production.
ALLOWED_REDIRECT_URI_PATTERNS = get_allowed_client_redirect_uris()


@pytest.fixture(scope="module")
def app():
    """Build the real server app (same wiring as __main__.py) with dummy credentials."""
    oauth_mcp = FastMCP(
        "Plane MCP Server",
        auth=PlaneOAuthProvider(
            client_id="test-client-id",
            client_secret="test-client-secret",
            base_url="http://localhost:8211",
            plane_base_url="http://localhost:9999",
            plane_internal_base_url="http://localhost:9999",
            client_storage=MemoryStore(),
            required_scopes=["read", "write"],
            allowed_client_redirect_uris=ALLOWED_REDIRECT_URI_PATTERNS,
            require_authorization_consent=False,
        ),
    )

    oauth_app = oauth_mcp.http_app()

    starlette_app = Starlette(
        routes=[Mount("/", app=oauth_app)],
    )

    # Same CORS config as __main__.py
    starlette_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    return starlette_app


@pytest.fixture(scope="module")
def client(app):
    return TestClient(app, follow_redirects=False)


# One redirect URI per attack vector the allowlist must close: foreign domains,
# script-bearing schemes, and a custom scheme no supported client uses.
MALICIOUS_REDIRECT_URIS = {
    "attacker-domain": "https://attacker.com/steal",
    "evil-domain": "http://evil.com/callback",
    "javascript-injection": "javascript:alert(document.cookie)",
    "data-uri-injection": "data:text/html,<script>fetch('https://evil.com')</script>",
    "unknown-protocol": "myapp://callback",
}

AUTHORIZE_PARAMS = {
    "response_type": "code",
    "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    "code_challenge_method": "S256",
}


def register(client: TestClient, *redirect_uris: str):
    """Dynamic client registration (RFC 7591) with the given redirect URIs."""
    return client.post(
        "/register",
        json={
            "redirect_uris": list(redirect_uris),
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        },
    )


def authorize(client: TestClient, client_id: str, redirect_uri: str):
    """An authorization request naming `redirect_uri` for a registered client."""
    return client.get("/authorize", params={"client_id": client_id, "redirect_uri": redirect_uri, **AUTHORIZE_PARAMS})


class TestOAuthRedirectAttack:
    """Replay the attack chain; every step must be blocked.

    The server uses an OAuth proxy architecture: /authorize redirects to
    upstream Plane OAuth (not directly to the client's redirect_uri). After
    Plane approves, the server's /auth/callback handles the response and
    only then redirects to the MCP client's redirect_uri.

    The allowed_client_redirect_uris patterns restrict which client URIs
    can be registered at all, and which the proxy will redirect to after the
    upstream callback completes.
    """

    @pytest.mark.parametrize("malicious_uri", MALICIOUS_REDIRECT_URIS.values(), ids=MALICIOUS_REDIRECT_URIS.keys())
    def test_registration_refuses_an_unknown_redirect_uri(self, client: TestClient, malicious_uri: str) -> None:
        """Step 1: the attacker cannot register a client that redirects to them."""
        response = register(client, malicious_uri)

        assert response.status_code == 400, f"VULNERABILITY: registered a client for {malicious_uri}: {response.text}"
        body = response.json()
        assert body["error"] == "invalid_redirect_uri"
        assert "client_id" not in body

    def test_registration_is_all_or_nothing(self, client: TestClient) -> None:
        """A disallowed URI cannot ride along next to a legitimate one."""
        response = register(client, "http://localhost:3000/callback", "https://attacker.com/steal")

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_redirect_uri"

    @pytest.mark.parametrize(
        "uri",
        [
            "http://localhost:3000/callback",
            "http://127.0.0.1:54321/oauth/callback",
            "https://claude.ai/api/mcp/auth_callback",
            "cursor://anysphere.cursor-mcp/oauth/callback",
        ],
        ids=["localhost", "loopback-ip", "claude-ai", "cursor"],
    )
    def test_registration_accepts_a_supported_client_redirect_uri(self, client: TestClient, uri: str) -> None:
        """The allowlist is enforced, not closed: supported clients still register."""
        response = register(client, uri)

        assert response.status_code == 201, f"Supported redirect URI was refused: {response.text}"
        assert "client_id" in response.json()

    @pytest.mark.parametrize("malicious_uri", MALICIOUS_REDIRECT_URIS.values(), ids=MALICIOUS_REDIRECT_URIS.keys())
    def test_authorize_refuses_a_redirect_uri_the_client_did_not_register(
        self, client: TestClient, malicious_uri: str
    ) -> None:
        """Steps 2-4: a legitimately registered client is steered to the attacker's URI.

        The request must fail without any redirect — the malicious URI must
        never appear in a Location header, not even on the error path.
        """
        reg = register(client, "http://localhost:3000/callback")
        assert reg.status_code == 201, reg.text

        response = authorize(client, reg.json()["client_id"], malicious_uri)

        location = response.headers.get("location", "")
        assert malicious_uri not in location, f"VULNERABILITY: Malicious URI leaked into redirect: {location}"
        assert not response.is_redirect, f"VULNERABILITY: redirected for {malicious_uri}: {location}"
        assert response.status_code == 400

    def test_legitimate_redirect_uri_passes(self, client: TestClient) -> None:
        """Sanity check: legitimate localhost URI is accepted and the flow proceeds."""
        legitimate_uri = "http://localhost:3000/callback"

        reg = register(client, legitimate_uri)
        assert reg.status_code == 201, reg.text

        response = authorize(client, reg.json()["client_id"], legitimate_uri)

        # Consent is off in this fixture, so the browser goes straight to Plane.
        assert response.is_redirect, f"{response.status_code}: {response.text}"
        assert response.headers["location"].startswith("http://localhost:9999/auth/o/authorize-app/")

    def test_cors_blocks_cross_origin_token_theft(self, client: TestClient) -> None:
        """Step 5: Even if attacker got a code, CORS blocks cross-origin token exchange.

        With allow_credentials=False, browsers will not attach cookies or allow
        attacker JS to read the response from a cross-origin POST to /token.
        The combination of Access-Control-Allow-Origin: * with credentials:true
        was the original vulnerability — now credentials is false.
        """
        response = client.options(
            "/token",
            headers={
                "Origin": "https://attacker.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

        # Credentials must NOT be allowed
        assert response.headers.get("access-control-allow-credentials") != "true", (
            "VULNERABILITY: CORS allows credentials — attacker JS can steal tokens cross-origin"
        )

        # Wildcard origin is fine without credentials
        # (browsers enforce that * + credentials:true is invalid, so this combo is safe)
        assert response.headers.get("access-control-allow-origin") == "*"
