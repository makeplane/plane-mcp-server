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

from plane_mcp.surfaces import (
    SurfaceRegistry,
    SurfaceSpec,
    build_mcp_app,
    normalize_route_path,
    sanitize_prefix,
)


def test_sanitize_prefix_validations():
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
        sanitize_prefix("no-leading-slash")

    with pytest.raises(ValueError, match="Security Violation"):
        sanitize_prefix("/../traversal")

    with pytest.raises(ValueError, match="Security Violation"):
        sanitize_prefix("/double//slash")

    with pytest.raises(ValueError, match="Security Violation"):
        sanitize_prefix("/{inject:path}")


def test_normalize_route_path_edge_cases():
    assert normalize_route_path() == "/"
    assert normalize_route_path("", "   ", "") == "/"
    assert normalize_route_path("/mcp/", "/admin/", "/api-key/") == "/mcp/admin/api-key"
    assert normalize_route_path("admin") == "/admin"


def test_surface_registry_compilation_and_caching():
    reg = SurfaceRegistry(surfaces=[SurfaceSpec(name="test", surface_type="agent")])
    routes1, apps1 = reg.compile()
    assert routes1 and apps1

    # Second call uses cache
    routes2, apps2 = reg.compile()
    assert routes1 is routes2
    assert apps1 is apps2


def test_surface_registry_duplicate_signatures_and_warnings():
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
    with patch("plane_mcp.surfaces.get_oauth_mcp") as mock_get_oauth:
        mock_mcp = MagicMock()
        mock_mcp.auth.get_well_known_routes.side_effect = RuntimeError("OAuth metadata discovery crash")
        mock_mcp.http_app.return_value = MagicMock()
        mock_get_oauth.return_value = mock_mcp

        reg = SurfaceRegistry(surfaces=[SurfaceSpec(name="faulty", surface_type="admin")])
        routes, apps = reg.compile()
        assert routes and apps


def test_build_mcp_app_with_prefix_and_cors():
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
    app = build_mcp_app()
    async with app.router.lifespan_context(app):
        # Lifespan properly entered and cleanly exited without exception
        pass
