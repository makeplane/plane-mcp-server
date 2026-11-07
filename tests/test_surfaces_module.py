"""Comprehensive unit tests for plane_mcp.surfaces."""

import os

os.environ["PLANE_OAUTH_PROVIDER_CLIENT_ID"] = "test-client-id"
os.environ["PLANE_OAUTH_PROVIDER_CLIENT_SECRET"] = "test-client-secret"
os.environ["PLANE_OAUTH_PROVIDER_BASE_URL"] = "http://localhost:8211"
os.environ["PLANE_BASE_URL"] = "https://api.plane.so"

from unittest.mock import MagicMock, patch

import pytest
from starlette.routing import Route
from starlette.testclient import TestClient

from plane_mcp.server import (
    _get_surface_registrar,
    get_header_mcp,
    get_oauth_mcp,
)
from plane_mcp.surfaces import (
    SurfaceRegistry,
    SurfaceSpec,
    build_mcp_app,
    normalize_route_path,
    sanitize_prefix,
)
from plane_mcp.yaml_formatter import YamlOutputMiddleware


def test_sanitize_prefix_validations():
    """Verify sanitize_prefix accepts valid prefixes and rejects double slashes, traversal, and injections."""
    # Empty / None
    assert sanitize_prefix(None) == ""
    assert sanitize_prefix("") == ""
    assert sanitize_prefix("   ") == ""
    assert sanitize_prefix("/") == ""

    # Valid prefixes
    assert sanitize_prefix("/mcp") == "/mcp"
    assert sanitize_prefix("/mcp/") == "/mcp"
    assert sanitize_prefix("/v1/mcp") == "/v1/mcp"
    assert sanitize_prefix("/api_v-2") == "/api_v-2"

    # Hostile / Invalid prefixes
    with pytest.raises(ValueError, match="Security Violation"):
        sanitize_prefix("//")

    with pytest.raises(ValueError, match="Security Violation"):
        sanitize_prefix("no-leading-slash")

    with pytest.raises(ValueError, match="Security Violation"):
        sanitize_prefix("/../traversal")

    with pytest.raises(ValueError, match="Security Violation"):
        sanitize_prefix("/double//slash")

    with pytest.raises(ValueError, match="Security Violation"):
        sanitize_prefix("/{inject:path}")


def test_normalize_route_path_edge_cases():
    """Verify normalize_route_path cleans and joins path segments into a normalized Starlette path."""
    assert normalize_route_path() == "/"
    assert normalize_route_path("", "   ", "") == "/"
    assert normalize_route_path("/mcp/", "/admin/", "/api-key/") == "/mcp/admin/api-key"
    assert normalize_route_path("admin") == "/admin"


def test_surface_registrar_and_middleware_isolation(monkeypatch):
    """Verify invalid surface rejection, OAuth base URL validation, and YAML middleware scoping."""
    with pytest.raises(ValueError, match="Unsupported MCP surface"):
        _get_surface_registrar("invalid")  # type: ignore[arg-type]

    admin_mcp = get_header_mcp("admin")
    agent_mcp = get_header_mcp("agent")
    docs_mcp = get_header_mcp("docs")

    assert not any(isinstance(m, YamlOutputMiddleware) for m in admin_mcp.middleware)
    assert any(isinstance(m, YamlOutputMiddleware) for m in agent_mcp.middleware)
    assert any(isinstance(m, YamlOutputMiddleware) for m in docs_mcp.middleware)

    # Require explicit PLANE_OAUTH_PROVIDER_BASE_URL when local dev is not enabled
    monkeypatch.delenv("PLANE_OAUTH_PROVIDER_BASE_URL", raising=False)
    monkeypatch.delenv("PLANE_MCP_LOCAL_DEV", raising=False)
    monkeypatch.delenv("PLANE_MCP_LOCAL_DEV_URL", raising=False)
    with pytest.raises(ValueError, match="PLANE_OAUTH_PROVIDER_BASE_URL is required"):
        get_oauth_mcp(base_path="/docs", surface="docs")

    # Local dev opt-in succeeds via PLANE_MCP_LOCAL_DEV and PLANE_MCP_LOCAL_DEV_URL
    monkeypatch.setenv("PLANE_MCP_LOCAL_DEV", "true")
    local_mcp = get_oauth_mcp(base_path="/docs", surface="docs")
    assert local_mcp.name == "Plane Docs MCP Server"

    monkeypatch.setenv("PLANE_MCP_LOCAL_DEV_URL", "http://127.0.0.1:8211/")
    custom_local_mcp = get_oauth_mcp(base_path="/docs", surface="docs")
    assert custom_local_mcp.name == "Plane Docs MCP Server"


def test_surface_registry_compilation_and_caching():
    """Verify SurfaceRegistry caches compiled routes and omits admin SSE fallback when admin is absent."""
    reg = SurfaceRegistry(surfaces=[SurfaceSpec(name="test", surface_type="agent")])
    routes1, apps1 = reg.compile()
    assert routes1 and apps1

    # Docs/agent-only registry omits the root "/" SSE fallback mount
    mount_paths = [getattr(r, "path", "") for r in routes1]
    assert "/" not in mount_paths

    # Second call uses cache
    routes2, apps2 = reg.compile()
    assert routes1 is routes2
    assert apps1 is apps2


def test_surface_registry_duplicate_signatures_and_warnings():
    """Verify duplicate well-known route signatures are deduplicated during compilation."""
    # Test duplicate signature in well_known_routes
    dup_route = Route("/.well-known/dup", endpoint=lambda req: None, methods=["GET"])

    with patch("plane_mcp.surfaces.get_oauth_mcp") as mock_get_oauth:
        mock_mcp = MagicMock()
        mock_mcp.auth.get_well_known_routes.return_value = [dup_route, dup_route]
        mock_mcp.http_app.return_value = MagicMock()
        mock_get_oauth.return_value = mock_mcp

        reg = SurfaceRegistry(
            surfaces=[
                SurfaceSpec(name="s1", surface_type="admin"),
                SurfaceSpec(name="s2", surface_type="admin"),
            ]
        )
        routes, _ = reg.compile()
        dup_matches = [r for r in routes if isinstance(r, Route) and r.path == "/.well-known/dup"]
        assert len(dup_matches) == 1


def test_surface_registry_exception_in_well_known():
    """Verify exceptions during well-known route extraction are logged and do not abort compilation."""
    with patch("plane_mcp.surfaces.get_oauth_mcp") as mock_get_oauth:
        mock_mcp = MagicMock()
        mock_mcp.auth.get_well_known_routes.side_effect = RuntimeError("OAuth metadata discovery crash")
        mock_mcp.http_app.return_value = MagicMock()
        mock_get_oauth.return_value = mock_mcp

        reg = SurfaceRegistry(surfaces=[SurfaceSpec(name="faulty", surface_type="admin")])
        routes, apps = reg.compile()
        assert routes and apps


def test_build_mcp_app_with_prefix_and_cors():
    """Verify build_mcp_app mounts prefixed routes and configures CORS headers properly."""
    app = build_mcp_app(prefix="/custom_prefix")
    mount_paths = [getattr(r, "path", "") for r in app.routes]

    assert "/custom_prefix/admin/api-key" in mount_paths
    assert "/custom_prefix/mcp/admin/api-key" in mount_paths
    assert "/custom_prefix/admin" in mount_paths
    assert "/custom_prefix/mcp/admin" in mount_paths
    assert "/custom_prefix" in mount_paths

    with TestClient(app) as client:
        # Preflight check
        preflight = client.options(
            "/custom_prefix/admin/api-key/mcp",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert preflight.headers.get("access-control-allow-origin") == "*"

        # Actual request check for exposed headers
        res = client.post(
            "/custom_prefix/admin/api-key/mcp",
            headers={
                "Origin": "https://example.com",
            },
        )
        assert "Mcp-Session-Id" in res.headers.get("access-control-expose-headers", "")


@pytest.mark.asyncio
async def test_combined_lifespan_execution():
    """Verify combined_lifespan enters and exits all sub-application lifespans cleanly."""
    app = build_mcp_app()
    async with app.router.lifespan_context(app):
        # Lifespan properly entered and cleanly exited without exception
        pass
