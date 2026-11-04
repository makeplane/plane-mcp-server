"""One connection, many workspaces: the `workspace` argument selects per call."""

from __future__ import annotations

import pytest
from fastmcp import Client, FastMCP

from plane_mcp.client import get_plane_client_context
from plane_mcp.server import _configured
from plane_mcp.workspace import WORKSPACE_PARAM, SelectWorkspace

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("PLANE_API_KEY", "k")
    monkeypatch.setenv("PLANE_BASE_URL", "http://localhost:9999")


def _probe(with_env_slug: bool, monkeypatch) -> FastMCP:
    if with_env_slug:
        monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "fallback")
    else:
        monkeypatch.delenv("PLANE_WORKSPACE_SLUG", raising=False)
    mcp = FastMCP("probe")
    mcp.add_middleware(SelectWorkspace())

    @mcp.tool
    def which(x: int = 0) -> str:
        return get_plane_client_context().workspace_slug

    return mcp


async def test_argument_selects_workspace(env, monkeypatch):
    async with Client(_probe(True, monkeypatch)) as c:
        result = await c.call_tool("which", {WORKSPACE_PARAM: "tf"})
    assert result.data == "tf"


async def test_each_call_gets_its_own_workspace(env, monkeypatch):
    async with Client(_probe(True, monkeypatch)) as c:
        first = await c.call_tool("which", {WORKSPACE_PARAM: "tf"})
        second = await c.call_tool("which", {WORKSPACE_PARAM: "um"})
        third = await c.call_tool("which", {})
    assert (first.data, second.data, third.data) == ("tf", "um", "fallback")


async def test_missing_workspace_is_a_clear_error(env, monkeypatch):
    async with Client(_probe(False, monkeypatch)) as c:
        with pytest.raises(Exception, match="No workspace selected"):
            await c.call_tool("which", {})


async def test_every_real_tool_advertises_the_parameter(env, monkeypatch):
    monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "tf")
    async with Client(_configured(FastMCP("plane"))) as c:
        tools = await c.list_tools()
    assert tools
    assert all(WORKSPACE_PARAM in t.inputSchema["properties"] for t in tools)


async def test_workspace_does_not_trip_argument_validation(env, monkeypatch):
    """A real dispatch tool must not reject `workspace` as a stray argument."""
    monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "tf")
    async with Client(_configured(FastMCP("plane"))) as c:
        result = await c.call_tool("get_pql_reference", {WORKSPACE_PARAM: "um"})
    assert "does not take" not in str(result.content)
