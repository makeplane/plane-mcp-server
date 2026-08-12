"""Fixtures for the consolidated surface. No network, no credentials."""

from __future__ import annotations

import asyncio
import os

import pytest
from fastmcp import FastMCP

os.environ.setdefault("PLANE_API_KEY", "test")
os.environ.setdefault("PLANE_WORKSPACE_SLUG", "test")

from plane_mcp.tools.v1 import register_tools as register_v1  # noqa: E402
from plane_mcp.tools.v2 import register_tools as register_v2  # noqa: E402
from plane_mcp.tools.v2.registry import RESOURCES, alias_table, unmapped_table  # noqa: E402

from ._spyclient import SpyClient  # noqa: E402


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


class _AllFeaturesOn:
    """A workspace feature set with everything enabled."""

    def model_dump(self):
        return {"initiatives": True, "customers": True, "releases": True, "epics": True}


@pytest.fixture(scope="session")
def resource_modules():
    return RESOURCES


@pytest.fixture(scope="session")
def registered():
    """Every v2 tool as registered, keyed by name."""
    mcp = FastMCP("conformance")
    register_v2(mcp, legacy_names=False)
    return {tool.name: tool for tool in _run(mcp.list_tools())}


@pytest.fixture(scope="session")
def listing(registered):
    """The advertised listing, as a client receives it."""
    return [tool.to_mcp_tool().model_dump(exclude_none=True) for tool in registered.values()]


@pytest.fixture(scope="session")
def aliases():
    return alias_table()


@pytest.fixture(scope="session")
def unmapped():
    return unmapped_table()


@pytest.fixture(scope="session")
def legacy_tool_names():
    mcp = FastMCP("legacy")
    register_v1(mcp)
    return {tool.name for tool in _run(mcp.list_tools())}


@pytest.fixture
def spy(monkeypatch):
    """Swap the Plane client for a recording stand-in, for the whole v2 package.

    Every resource module imports `get_plane_client_context` by value, so each
    module's own reference is patched rather than the definition site.
    """
    client = SpyClient()
    # Resources gated on a workspace feature probe it first; answer yes so the
    # dispatch under test is what gets exercised.
    client.returns["workspaces.get_features"] = _AllFeaturesOn()
    for mod in RESOURCES:
        if hasattr(mod, "get_plane_client_context"):
            monkeypatch.setattr(mod, "get_plane_client_context", lambda: (client, "acme"))
    return client
