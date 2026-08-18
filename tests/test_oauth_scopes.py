"""Regression tests for Plane OAuth scope forwarding."""

import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
from fastmcp import FastMCP
from key_value.aio.stores.memory import MemoryStore
from starlette.testclient import TestClient

from plane_mcp.auth import PlaneOAuthProvider
from plane_mcp.auth.plane_oauth_provider import PlaneOAuthTokenVerifier


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


def test_token_verifier_returns_configured_scopes(monkeypatch):
    required_scopes = ["projects:read"]
    verifier = PlaneOAuthTokenVerifier(
        required_scopes=required_scopes,
        plane_base_url="http://plane.example",
    )
    user = {
        "id": "00000000-0000-0000-0000-000000000001",
        "email": "user@example.com",
        "first_name": "Test",
        "last_name": "User",
        "display_name": "Test User",
        "avatar": "",
        "avatar_url": None,
    }
    installation = {
        "id": "installation-1",
        "workspace_detail": {"name": "Test", "slug": "test", "id": "workspace-1"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/users/me/":
            return httpx.Response(200, json=user)
        if request.url.path == "/auth/o/app-installation/":
            return httpx.Response(200, json=[installation])
        return httpx.Response(404)

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    access_token = asyncio.run(verifier.verify_token("upstream-token"))

    assert access_token is not None
    assert access_token.scopes == required_scopes
