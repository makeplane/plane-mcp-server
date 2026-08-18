"""Regression tests for Plane OAuth scope forwarding."""

from urllib.parse import parse_qs, urlparse

from fastmcp import FastMCP
from key_value.aio.stores.memory import MemoryStore
from starlette.testclient import TestClient

from plane_mcp.auth import PlaneOAuthProvider


def test_empty_required_scopes_are_advertised_and_omitted_upstream():
    provider = PlaneOAuthProvider(
        client_id="test-client-id",
        client_secret="test-client-secret",
        base_url="http://localhost:8211",
        plane_base_url="http://plane.example",
        plane_internal_base_url="http://plane.example",
        client_storage=MemoryStore(),
        required_scopes=[],
        allowed_client_redirect_uris=["http://localhost:*/*"],
        require_authorization_consent=False,
    )
    app = FastMCP("Plane MCP Server", auth=provider).http_app()

    with TestClient(app, follow_redirects=False) as client:
        metadata = client.get("/.well-known/oauth-authorization-server").json()
        assert metadata["scopes_supported"] == []

        registration = client.post(
            "/register",
            json={
                "redirect_uris": ["http://localhost:3000/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "client_secret_post",
            },
        )
        assert registration.status_code == 201

        response = client.get(
            "/authorize",
            params={
                "client_id": registration.json()["client_id"],
                "redirect_uri": "http://localhost:3000/callback",
                "response_type": "code",
                "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
                "code_challenge_method": "S256",
            },
        )

    assert response.status_code == 302
    upstream_query = parse_qs(urlparse(response.headers["location"]).query, keep_blank_values=True)
    assert "scope" not in upstream_query
