"""Offline eval tests for tool names."""

from __future__ import annotations

from evals.core.tool_names import (
    is_plane_mcp_tool,
    strip_mcp_prefix,
)


def test_strip_mcp_prefix():
    assert strip_mcp_prefix("mcp__plane__list_work_items") == "list_work_items"
    assert strip_mcp_prefix("mcp__plane-mcp-server__find_work_items") == "find_work_items"
    assert strip_mcp_prefix("list_work_items") == "list_work_items"
    assert strip_mcp_prefix("Bash") == "Bash"


def test_is_plane_mcp_tool():
    assert is_plane_mcp_tool("mcp__plane__find_work_items")
    assert is_plane_mcp_tool("mcp__plane-foo__x")
    assert not is_plane_mcp_tool("ToolSearch")
    assert not is_plane_mcp_tool("Bash")
    assert not is_plane_mcp_tool("mcp__other__tool")
    assert not is_plane_mcp_tool("find_work_items")
