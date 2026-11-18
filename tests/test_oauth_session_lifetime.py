"""A signed-in MCP connection stays signed in until the user signs out or stops using it.

Every MCP client treats a failed refresh as the end of its session and sends
the user back through sign-in, so the server's refresh path has to hold up
against the things that are not the user's fault: Plane being briefly down,
throttled or unreachable, two processes of one client refreshing at the same
moment, and the calendar. Plane is faked at the ASGI level; nothing here
touches the network.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest
from fastmcp.server.auth.oauth_proxy.models import ClientCode
from key_value.aio.stores.memory import MemoryStore
from mcp.server.auth.provider import AuthorizationCode, TokenError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl, ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from plane_mcp.auth import plane_api
from plane_mcp.auth.plane_api import TOKEN_REQUEST_TIMEOUT_SECONDS, PlaneTokenClient, PlaneUnavailable
from plane_mcp.auth.plane_oauth_provider import (
    DEFAULT_ACCESS_TOKEN_TTL_SECONDS,
    DEFAULT_REFRESH_TOKEN_TTL_SECONDS,
    UPSTREAM_REFRESH_THRESHOLD_SECONDS,
    PlaneOAuthProvider,
)
from plane_mcp.auth.refresh_grace import GRACE_WINDOW_SECONDS

PLANE = "http://plane.test"
REDIRECT = "http://localhost:3000/callback"
CLIENT_ID = "mcp-client-1"
ONE_DAY = 24 * 60 * 60


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakePlane:
    """Plane's token and verification endpoints with a scripted answer per call.

    A plan entry is an HTTP status (200 mints the next token pair), or a dict
    body sent with status ``_status`` (default 400). Nothing planned means 200.
    """

    def __init__(self) -> None:
        self.minted = 0
        self.token_calls: list[dict[str, list[str]]] = []
        self.token_plan: list[Any] = []
        self.me_calls = 0
        self.me_plan: list[int] = []
        self.installation_plan: list[int] = []
        self.app = Starlette(
            routes=[
                Route("/auth/o/token/", self.token, methods=["POST"]),
                Route("/api/v1/users/me/", self.me),
                Route("/auth/o/app-installation/", self.installations),
            ]
        )
        self.transport: httpx.AsyncBaseTransport = httpx.ASGITransport(app=self.app)

    def mint(self) -> dict[str, Any]:
        self.minted += 1
        return {
            "access_token": f"plane-at-{self.minted}",
            "refresh_token": f"plane-rt-{self.minted}",
            "expires_in": 36000,
            "token_type": "Bearer",
            "scope": "read write",
        }

    async def token(self, request: Request) -> JSONResponse:
        self.token_calls.append(parse_qs((await request.body()).decode()))
        step = self.token_plan.pop(0) if self.token_plan else 200
        if isinstance(step, dict):
            body = dict(step)
            return JSONResponse(body, status_code=body.pop("_status", 400))
        if step == 200:
            return JSONResponse(self.mint())
        return JSONResponse({"detail": "injected"}, status_code=step)

    async def me(self, request: Request) -> JSONResponse:
        self.me_calls += 1
        status = self.me_plan.pop(0) if self.me_plan else 200
        if status == "unreadable":
            return Response("<html>gateway page</html>", status_code=200)
        if status != 200:
            return JSONResponse({"detail": "injected"}, status_code=status)
        return JSONResponse(
            {"id": "user-1", "email": "ada@example.com", "display_name": "Ada", "first_name": "Ada", "last_name": "L"}
        )

    async def installations(self, request: Request) -> JSONResponse:
        status = self.installation_plan.pop(0) if self.installation_plan else 200
        if status != 200:
            return JSONResponse({"detail": "injected"}, status_code=status)
        return JSONResponse([{"id": "inst-1", "workspace_detail": {"id": "ws-1", "name": "Acme", "slug": "acme"}}])


class FlakyTransport(httpx.AsyncBaseTransport):
    """Raises the queued transport errors first, then forwards to the real transport."""

    def __init__(self, inner: httpx.AsyncBaseTransport, failures: list[Exception]) -> None:
        self.inner = inner
        self.failures = failures

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.failures:
            raise self.failures.pop(0)
        return await self.inner.handle_async_request(request)


@pytest.fixture
def plane() -> FakePlane:
    return FakePlane()


@pytest.fixture
def clock(monkeypatch):
    """`time.time` under test control; monotonic time is left alone for asyncio's sake."""
    now = [1_800_000_000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])

    class Clock:
        @property
        def now(self) -> float:
            return now[0]

        def advance(self, seconds: float) -> None:
            now[0] += seconds

    return Clock()


@pytest.fixture
def fast_retries(monkeypatch):
    monkeypatch.setattr(plane_api, "RETRY_DELAYS_SECONDS", (0, 0))


def make_provider(plane: FakePlane, store: MemoryStore | None = None, **kwargs: Any) -> PlaneOAuthProvider:
    """A provider wired to the fake Plane, with the JWT issuer ready."""
    provider = PlaneOAuthProvider(
        client_id="plane-app",
        client_secret="plane-secret-0123456789",
        base_url="http://localhost:8211/ps/http",
        plane_base_url=PLANE,
        client_storage=store or MemoryStore(),
        require_authorization_consent=False,
        **kwargs,
    )
    provider.set_mcp_path("/mcp")
    create = provider._create_upstream_oauth_client

    def upstream_client():
        client = create()
        client._transport = plane.transport
        return client

    provider._create_upstream_oauth_client = upstream_client  # type: ignore[method-assign]
    provider._token_validator._http_client = lambda: httpx.AsyncClient(transport=plane.transport, timeout=5)  # type: ignore[attr-defined]
    return provider


async def sign_in(provider: PlaneOAuthProvider, plane: FakePlane) -> OAuthToken:
    """The tokens a client holds after the authorization flow, without the browser."""
    await provider.register_client(
        OAuthClientInformationFull(
            client_id=CLIENT_ID,
            redirect_uris=[AnyUrl(REDIRECT)],
            grant_types=["authorization_code", "refresh_token"],
            token_endpoint_auth_method="none",
        )
    )
    client = await provider.get_client(CLIENT_ID)
    assert client is not None
    code = f"code-{plane.minted + 1}"
    await provider._code_store.put(
        key=code,
        value=ClientCode(
            code=code,
            client_id=CLIENT_ID,
            redirect_uri=REDIRECT,
            code_challenge="challenge",
            code_challenge_method="S256",
            scopes=["read", "write"],
            idp_tokens=plane.mint(),
            expires_at=time.time() + 300,
            created_at=time.time(),
        ),
    )
    return await provider.exchange_authorization_code(
        client,
        AuthorizationCode(
            code=code,
            scopes=["read", "write"],
            expires_at=time.time() + 300,
            client_id=CLIENT_ID,
            code_challenge="challenge",
            redirect_uri=AnyUrl(REDIRECT),
            redirect_uri_provided_explicitly=True,
        ),
    )


async def refresh(provider: PlaneOAuthProvider, tokens: OAuthToken) -> OAuthToken:
    """What the token endpoint does with a refresh_token grant."""
    client = await provider.get_client(CLIENT_ID)
    assert client is not None and tokens.refresh_token is not None
    loaded = await provider.load_refresh_token(client, tokens.refresh_token)
    assert loaded is not None, "refresh token not recognised"
    assert loaded.expires_at is None or loaded.expires_at > time.time(), "refresh token expired"
    return await provider.exchange_refresh_token(client, loaded, loaded.scopes)


def expiry(provider: PlaneOAuthProvider, token: str, use: str) -> int:
    return provider.jwt_issuer.verify_token(token, expected_token_use=use)["exp"]


def pair(tokens: OAuthToken) -> tuple[str, str | None]:
    return tokens.access_token, tokens.refresh_token


# --- the calendar -----------------------------------------------------------


@pytest.mark.anyio
async def test_a_refresh_restarts_the_session_window(plane, clock):
    """The session is not capped at N days from sign-in: each refresh buys another full window."""
    provider = make_provider(plane)
    tokens = await sign_in(provider, plane)
    assert expiry(provider, tokens.refresh_token, "refresh") == int(clock.now) + DEFAULT_REFRESH_TOKEN_TTL_SECONDS

    clock.advance(30 * ONE_DAY)
    renewed = await refresh(provider, tokens)

    assert expiry(provider, renewed.refresh_token, "refresh") == int(clock.now) + DEFAULT_REFRESH_TOKEN_TTL_SECONDS
    assert plane.token_calls[-1]["grant_type"] == ["refresh_token"]


@pytest.mark.anyio
async def test_the_window_is_configurable(plane, clock):
    provider = make_provider(plane, refresh_token_ttl_seconds=7 * ONE_DAY, access_token_ttl_seconds=3600)
    tokens = await sign_in(provider, plane)

    assert expiry(provider, tokens.refresh_token, "refresh") == int(clock.now) + 7 * ONE_DAY
    assert tokens.expires_in == 3600
    assert expiry(provider, tokens.access_token, "access") == int(clock.now) + 3600


def test_the_window_comes_from_the_environment(monkeypatch, plane):
    monkeypatch.setenv("PLANE_OAUTH_PROVIDER_REFRESH_TOKEN_TTL_SECONDS", str(14 * ONE_DAY))
    monkeypatch.setenv("PLANE_OAUTH_PROVIDER_ACCESS_TOKEN_TTL_SECONDS", "7200")

    provider = make_provider(plane)

    assert provider._refresh_token_ttl_seconds == 14 * ONE_DAY
    assert provider._fallback_refresh_token_expiry_seconds == 14 * ONE_DAY
    assert provider._fastmcp_access_token_expiry_seconds == 7200


def test_a_zero_window_is_refused(monkeypatch, plane):
    monkeypatch.setenv("PLANE_OAUTH_PROVIDER_REFRESH_TOKEN_TTL_SECONDS", "0")

    with pytest.raises(ValidationError):
        make_provider(plane)


@pytest.mark.parametrize(
    ("access", "refresh"),
    [(ONE_DAY, ONE_DAY), (2 * ONE_DAY, ONE_DAY), (GRACE_WINDOW_SECONDS - 1, ONE_DAY)],
    ids=["equal", "access-outlives-refresh", "access-shorter-than-grace"],
)
def test_inconsistent_lifetimes_are_refused(plane, access, refresh):
    """An access token must expire before the refresh token, and outlast a replayed pair."""
    with pytest.raises(ValidationError):
        make_provider(plane, access_token_ttl_seconds=access, refresh_token_ttl_seconds=refresh)


def test_lifetimes_are_wired_into_fastmcp(plane):
    provider = make_provider(plane)

    assert provider._fallback_refresh_token_expiry_seconds == DEFAULT_REFRESH_TOKEN_TTL_SECONDS
    assert provider._fastmcp_access_token_expiry_seconds == DEFAULT_ACCESS_TOKEN_TTL_SECONDS
    assert provider._token_expiry_threshold_seconds == UPSTREAM_REFRESH_THRESHOLD_SECONDS


@pytest.mark.anyio
async def test_the_client_token_outlives_planes(plane, clock):
    """Plane's token lasts 10 hours; the client's lasts a day, and the proxy renews Plane's behind it."""
    provider = make_provider(plane)
    tokens = await sign_in(provider, plane)
    assert tokens.expires_in == DEFAULT_ACCESS_TOKEN_TTL_SECONDS

    clock.advance(11 * 3600)
    verified = await provider.load_access_token(tokens.access_token)

    assert verified is not None
    assert verified.token == "plane-at-2", "the request should be served with a renewed Plane token"
    assert plane.token_calls[-1]["grant_type"] == ["refresh_token"]


@pytest.mark.anyio
async def test_token_responses_carry_the_refresh_lifetime(plane):
    """Plane never says how long a refresh token lives; the proxy stamps every token response with its own TTL."""
    provider = make_provider(plane, refresh_token_ttl_seconds=5 * ONE_DAY)
    client = provider._create_upstream_oauth_client()
    assert isinstance(client, PlaneTokenClient)

    token = await client.fetch_token(f"{PLANE}/auth/o/token/", grant_type="authorization_code", code="c")

    assert token["refresh_expires_in"] == 5 * ONE_DAY
    assert client.timeout.read == TOKEN_REQUEST_TIMEOUT_SECONDS
    await client.aclose()


# --- Plane being briefly unavailable ------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
async def test_a_refresh_survives_plane_answering_an_error_once(plane, fast_retries, status):
    provider = make_provider(plane)
    tokens = await sign_in(provider, plane)
    plane.token_plan = [status]

    renewed = await refresh(provider, tokens)

    assert len(plane.token_calls) == 2
    assert renewed.refresh_token != tokens.refresh_token


@pytest.mark.anyio
async def test_a_refresh_survives_network_failures(plane, fast_retries):
    provider = make_provider(plane)
    tokens = await sign_in(provider, plane)
    plane.transport = FlakyTransport(plane.transport, [httpx.ReadTimeout("slow"), httpx.ConnectError("refused")])

    renewed = await refresh(provider, tokens)

    assert len(plane.token_calls) == 1
    assert renewed.refresh_token != tokens.refresh_token


@pytest.mark.anyio
async def test_a_refresh_gives_up_once_plane_stays_down(plane, fast_retries):
    """An outage is reported as an outage, not as a verdict on the grant."""
    provider = make_provider(plane)
    tokens = await sign_in(provider, plane)
    plane.token_plan = [503] * 10

    with pytest.raises(PlaneUnavailable):
        await refresh(provider, tokens)

    assert len(plane.token_calls) == len(plane_api.RETRY_DELAYS_SECONDS) + 1
    plane.token_plan = []
    assert (await refresh(provider, tokens)).refresh_token != tokens.refresh_token, "the token is still good"


@pytest.mark.anyio
async def test_a_refused_grant_is_not_retried(plane, fast_retries):
    """invalid_grant is Plane's verdict on the token; asking again would not change it."""
    provider = make_provider(plane)
    tokens = await sign_in(provider, plane)
    plane.token_plan = [{"error": "invalid_grant", "error_description": "revoked", "_status": 400}]

    with pytest.raises(TokenError) as excinfo:
        await refresh(provider, tokens)

    assert excinfo.value.error == "invalid_grant"
    assert len(plane.token_calls) == 1


# --- two refreshes with one token ---------------------------------------------


@pytest.mark.anyio
async def test_a_refresh_token_replayed_within_the_grace_window_gets_the_same_pair(plane):
    """A second process of the same client refreshing with the token that was just rotated is not signed out."""
    provider = make_provider(plane)
    tokens = await sign_in(provider, plane)

    renewed = await refresh(provider, tokens)
    again = await refresh(provider, tokens)

    assert pair(again) == pair(renewed)
    assert len(plane.token_calls) == 1, "Plane is asked once"
    after = await refresh(provider, again)
    assert pair(after) != pair(renewed), "the pair handed out twice is live"


@pytest.mark.anyio
async def test_the_grace_window_closes(plane, clock):
    provider = make_provider(plane)
    tokens = await sign_in(provider, plane)
    await refresh(provider, tokens)
    client = await provider.get_client(CLIENT_ID)

    clock.advance(GRACE_WINDOW_SECONDS + 1)

    assert await provider.load_refresh_token(client, tokens.refresh_token) is None


@pytest.mark.anyio
async def test_the_grace_window_is_bound_to_the_client(plane):
    provider = make_provider(plane)
    tokens = await sign_in(provider, plane)
    await refresh(provider, tokens)
    other = OAuthClientInformationFull(client_id="someone-else", redirect_uris=[AnyUrl(REDIRECT)])

    assert await provider.load_refresh_token(other, tokens.refresh_token) is None


@pytest.mark.anyio
async def test_concurrent_refreshes_with_one_token_all_succeed(plane):
    provider = make_provider(plane)
    tokens = await sign_in(provider, plane)

    results = await asyncio.gather(*(refresh(provider, tokens) for _ in range(5)))

    assert len({pair(r) for r in results}) == 1
    assert len(plane.token_calls) == 1


@pytest.mark.anyio
async def test_the_grace_window_spans_server_replicas(plane):
    """Replicas share the token store, so a replay landing on another replica is answered the same way."""
    store = MemoryStore()
    first = make_provider(plane, store)
    second = make_provider(plane, store)
    tokens = await sign_in(first, plane)

    renewed = await refresh(first, tokens)
    again = await refresh(second, tokens)

    assert pair(again) == pair(renewed)
    assert len(plane.token_calls) == 1


@pytest.mark.anyio
async def test_a_replay_arriving_mid_refresh_waits_for_it(plane):
    """FastMCP forgets the old token before the new pair is recorded; a replay in that gap must not fail."""
    provider = make_provider(plane)
    tokens = await sign_in(provider, plane)
    grace = provider._refresh_grace
    record = grace.record
    in_gap = asyncio.Event()

    async def slow_record(*args, **kwargs):
        in_gap.set()
        await asyncio.sleep(0.05)
        await record(*args, **kwargs)

    grace.record = slow_record

    async def replay_in_gap():
        await in_gap.wait()
        return await refresh(provider, tokens)

    renewed, replayed = await asyncio.gather(refresh(provider, tokens), replay_in_gap())

    assert pair(replayed) == pair(renewed)


@pytest.mark.anyio
async def test_simultaneous_refreshes_on_two_replicas_both_succeed(plane):
    store = MemoryStore()
    first, second = make_provider(plane, store), make_provider(plane, store)
    tokens = await sign_in(first, plane)

    results = await asyncio.gather(refresh(first, tokens), refresh(second, tokens))

    assert all(r.refresh_token for r in results)


@pytest.mark.anyio
async def test_a_failed_grace_record_still_returns_the_new_pair(plane):
    """The old token is already gone by then; the client must not lose the new one too."""
    provider = make_provider(plane)
    tokens = await sign_in(provider, plane)

    async def broken_record(*args, **kwargs):
        raise ConnectionError("redis went away")

    provider._refresh_grace.record = broken_record

    renewed = await refresh(provider, tokens)

    assert renewed.refresh_token and renewed.refresh_token != tokens.refresh_token


def test_a_held_refresh_lock_is_never_evicted(monkeypatch):
    from key_value.aio.stores.memory import MemoryStore as Store

    from plane_mcp.auth import refresh_grace

    monkeypatch.setattr(refresh_grace, "LOCK_CACHE_SIZE", 2)
    grace = refresh_grace.RefreshGrace(Store())
    held = grace.lock("token-a")

    async def hold_and_crowd():
        async with held:
            grace.lock("token-b")
            grace.lock("token-c")
            assert grace.lock("token-a") is held

    import anyio

    anyio.run(hold_and_crowd)


# --- verifying tokens while Plane wobbles --------------------------------------


def age(provider: PlaneOAuthProvider, token: str, seconds: float) -> None:
    """Make Plane's last answer about `token` `seconds` older."""
    verifier = provider._token_validator
    verified, at = verifier._verified[token]
    verifier._verified[token] = (verified, at - seconds)


@pytest.mark.anyio
async def test_a_verified_token_is_not_verified_again_for_a_minute(plane):
    provider = make_provider(plane)
    verifier = provider._token_validator

    first = await verifier.verify_token("plane-at-1")
    second = await verifier.verify_token("plane-at-1")

    assert plane.me_calls == 1
    assert first is not None and first.claims["workspace_slug"] == "acme"
    first.claims["workspace_slug"] = "tampered"
    assert second is not None and second.claims["workspace_slug"] == "acme", "callers get their own copy"


@pytest.mark.anyio
async def test_a_minute_later_plane_is_asked_again(plane):
    provider = make_provider(plane)
    verifier = provider._token_validator
    await verifier.verify_token("plane-at-1")
    age(provider, "plane-at-1", 61)

    await verifier.verify_token("plane-at-1")

    assert plane.me_calls == 2


@pytest.mark.anyio
@pytest.mark.parametrize("status", [408, 429, 500, 503, "unreadable"])
async def test_a_recently_verified_token_stays_valid_while_plane_is_down(plane, status):
    provider = make_provider(plane)
    verifier = provider._token_validator
    await verifier.verify_token("plane-at-1")
    age(provider, "plane-at-1", 5 * 60)
    plane.me_plan = [status]

    assert await verifier.verify_token("plane-at-1") is not None


@pytest.mark.anyio
async def test_installations_failing_counts_as_plane_being_down(plane):
    provider = make_provider(plane)
    verifier = provider._token_validator
    await verifier.verify_token("plane-at-1")
    age(provider, "plane-at-1", 5 * 60)
    plane.installation_plan = [502]

    assert await verifier.verify_token("plane-at-1") is not None


@pytest.mark.anyio
async def test_an_unknown_token_fails_closed_while_plane_is_down(plane):
    provider = make_provider(plane)
    plane.me_plan = [503]

    assert await provider._token_validator.verify_token("never-seen") is None


@pytest.mark.anyio
async def test_a_refusal_from_plane_is_final(plane):
    provider = make_provider(plane)
    verifier = provider._token_validator
    await verifier.verify_token("plane-at-1")
    age(provider, "plane-at-1", 61)
    plane.me_plan = [401]

    assert await verifier.verify_token("plane-at-1") is None
    plane.me_plan = [503]
    assert await verifier.verify_token("plane-at-1") is None, "a refused token is forgotten, not kept as a fallback"


@pytest.mark.anyio
async def test_an_old_verification_is_not_trusted_forever(plane):
    provider = make_provider(plane)
    verifier = provider._token_validator
    await verifier.verify_token("plane-at-1")
    age(provider, "plane-at-1", 11 * 60)
    plane.me_plan = [503]

    assert await verifier.verify_token("plane-at-1") is None


# --- through the real token endpoint ------------------------------------------


def test_the_token_endpoint_answers_a_replayed_refresh_with_the_same_pair(plane, monkeypatch):
    """The whole stack: the MCP SDK's token handler, FastMCP's error mapping and this provider."""
    from fastmcp import FastMCP
    from starlette.testclient import TestClient

    provider = make_provider(plane)
    app = FastMCP("Plane MCP Server", auth=provider).http_app()
    verifier = "v" * 43
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")

    with TestClient(app) as http:
        registered = http.post(
            "/register",
            json={"redirect_uris": [REDIRECT], "grant_types": ["authorization_code", "refresh_token"]},
        )
        assert registered.status_code == 201, registered.text
        client_id = registered.json()["client_id"]

        async def seed() -> None:
            await provider._code_store.put(
                key="code-1",
                value=ClientCode(
                    code="code-1",
                    client_id=client_id,
                    redirect_uri=REDIRECT,
                    code_challenge=challenge,
                    code_challenge_method="S256",
                    scopes=["read", "write"],
                    idp_tokens=plane.mint(),
                    expires_at=time.time() + 300,
                    created_at=time.time(),
                ),
            )

        http.portal.call(seed)
        signed_in = http.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": "code-1",
                "redirect_uri": REDIRECT,
                "client_id": client_id,
                "code_verifier": verifier,
            },
        )
        assert signed_in.status_code == 200, signed_in.text
        first_refresh = signed_in.json()["refresh_token"]

        def refresh_with(token: str):
            return http.post(
                "/token", data={"grant_type": "refresh_token", "refresh_token": token, "client_id": client_id}
            )

        renewed = refresh_with(first_refresh)
        replayed = refresh_with(first_refresh)

        assert renewed.status_code == 200, renewed.text
        assert replayed.status_code == 200, replayed.text
        assert replayed.json()["refresh_token"] == renewed.json()["refresh_token"]
        assert replayed.json()["access_token"] == renewed.json()["access_token"]
        assert len(plane.token_calls) == 1

        # Plane down: 503 and the client keeps its token.
        monkeypatch.setattr(plane_api, "RETRY_DELAYS_SECONDS", (0, 0))
        plane.token_plan = [502] * 10
        outage = refresh_with(renewed.json()["refresh_token"])
        assert outage.status_code == 503, outage.text
        assert outage.headers["retry-after"] == "5"
        assert outage.json()["error"] == "temporarily_unavailable"
        plane.token_plan = []
        recovered = refresh_with(renewed.json()["refresh_token"])
        assert recovered.status_code == 200, recovered.text

        # Plane's verdict on the grant is passed on as such.
        plane.token_plan = [{"error": "invalid_grant", "_status": 400}]
        rejected = refresh_with(recovered.json()["refresh_token"])
        assert rejected.status_code == 401, rejected.text
        assert rejected.json()["error"] == "invalid_grant"
