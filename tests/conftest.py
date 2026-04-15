"""Pytest configuration for Plane MCP Server tests."""

import pytest

import plane_mcp.resolver


@pytest.fixture(autouse=True)
def clear_resolver_caches():
    """Clear global caches before each test to prevent test state leakage."""
    plane_mcp.resolver._GLOBAL_PROJECT_CACHE.clear()
    plane_mcp.resolver._GLOBAL_STATE_CACHE.clear()
    plane_mcp.resolver._GLOBAL_WORK_ITEM_CACHE.clear()
    plane_mcp.resolver._CACHE_LAST_UPDATED.clear()
