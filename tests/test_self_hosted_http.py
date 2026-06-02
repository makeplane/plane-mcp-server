"""Tests for the self-hosted HTTP deployment path.

Covers env-driven host/port binding (incl. ``$PORT`` precedence), transport parsing,
``PLANE_BASE_URL`` propagation to the API client, the preserved per-user header-auth app,
and the ``/healthz`` probe. All network is avoided: the API client is only constructed
(never called) and MCP apps are exercised with Starlette's TestClient.
"""

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from plane_mcp.__main__ import ServerMode, healthz, resolve_bind
from plane_mcp.client import get_plane_client_context
from plane_mcp.server import get_header_mcp

BIND_VARS = ("PORT", "MCP_HOST", "MCP_PORT")


class TestResolveBind:
    """resolve_bind() reads MCP_HOST/MCP_PORT, with PORT taking precedence."""

    def _clean(self, monkeypatch):
        for var in BIND_VARS:
            monkeypatch.delenv(var, raising=False)

    def test_defaults(self, monkeypatch):
        self._clean(monkeypatch)
        assert resolve_bind() == ("0.0.0.0", 8211)

    def test_env_overrides(self, monkeypatch):
        self._clean(monkeypatch)
        monkeypatch.setenv("MCP_HOST", "127.0.0.1")
        monkeypatch.setenv("MCP_PORT", "9000")
        assert resolve_bind() == ("127.0.0.1", 9000)

    def test_platform_port_takes_precedence_over_mcp_port(self, monkeypatch):
        # Cloud Run and similar inject $PORT; it must win over MCP_PORT.
        self._clean(monkeypatch)
        monkeypatch.setenv("MCP_PORT", "9000")
        monkeypatch.setenv("PORT", "8080")
        assert resolve_bind() == ("0.0.0.0", 8080)


class TestTransportParsing:
    def test_http_transport_resolves(self):
        assert ServerMode("http") == ServerMode.HTTP
        assert ServerMode("stdio") == ServerMode.STDIO


class TestPlaneBaseUrl:
    """PLANE_BASE_URL / PLANE_INTERNAL_BASE_URL flow into the constructed client."""

    def test_plane_base_url_is_used(self, monkeypatch):
        monkeypatch.delenv("PLANE_INTERNAL_BASE_URL", raising=False)
        monkeypatch.setenv("PLANE_BASE_URL", "https://plane.example.com/api")
        monkeypatch.setenv("PLANE_API_KEY", "dummy-key")
        monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "acme")

        ctx = get_plane_client_context()

        assert ctx.workspace_slug == "acme"
        assert ctx.client.config.base_path.startswith("https://plane.example.com/api")

    def test_internal_base_url_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("PLANE_BASE_URL", "https://public.example.com")
        monkeypatch.setenv("PLANE_INTERNAL_BASE_URL", "http://plane-api:8000")
        monkeypatch.setenv("PLANE_API_KEY", "dummy-key")
        monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "acme")

        ctx = get_plane_client_context()

        assert ctx.client.config.base_path.startswith("http://plane-api:8000")


class TestHeaderAuthPreserved:
    """The per-user header-auth (PAT passthrough) app stays gated."""

    def test_header_app_rejects_request_without_workspace_slug(self):
        app = get_header_mcp().http_app(stateless_http=True)
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post("/mcp", json={"jsonrpc": "2.0", "method": "initialize", "id": 1})
        assert response.status_code in (401, 403)


class TestHealthz:
    """The /healthz probe returns 200 with a JSON status body."""

    def test_healthz_returns_ok(self):
        app = Starlette(routes=[Route("/healthz", healthz)])
        client = TestClient(app)
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
