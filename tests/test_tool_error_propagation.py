"""Deterministic tests for MCP tool error semantics (isError: true) on argument validation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError

from plane_mcp.tools.workitem import GROUP_BY_VALUES, register


@pytest.fixture
def test_server():
    """Provide an isolated FastMCP test server with workitem tools registered."""
    server = FastMCP("test-server")
    register(server)
    return server


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invalid_group_by",
    ["stateGroup", "unknown_field", "INVALID", "priority:urgent"],
)
async def test_invalid_group_by_returns_is_error_true(test_server: FastMCP, invalid_group_by: str):
    """Invalid group_by must raise ToolError and set isError: true over MCP."""
    mock_client = MagicMock()

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            # Protocol-level call asserting isError: True
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "count",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "group_by": invalid_group_by,
                },
            )
            assert res.isError is True
            assert len(res.content) > 0
            error_msg = res.content[0].text
            assert "Error: group_by must be one of:" in error_msg
            assert "state__group" in error_msg

            # Standard call_tool raises ToolError
            with pytest.raises(ToolError) as exc_info:
                await client.call_tool(
                    "workitem",
                    {
                        "action": "count",
                        "project_id": "00000000-0000-0000-0000-000000000001",
                        "group_by": invalid_group_by,
                    },
                )
            assert "Error: group_by must be one of:" in str(exc_info.value)
            assert "state__group" in str(exc_info.value)

    assert not mock_client.work_items.count_workspace.called


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invalid_sub_group_by",
    ["stateGroup", "unknown_subfield", "INVALID", "not_a_field"],
)
async def test_invalid_sub_group_by_returns_is_error_true(test_server: FastMCP, invalid_sub_group_by: str):
    """Invalid sub_group_by must raise ToolError and set isError: true over MCP."""
    mock_client = MagicMock()

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            # Protocol-level call asserting isError: True
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "count",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "sub_group_by": invalid_sub_group_by,
                },
            )
            assert res.isError is True
            assert len(res.content) > 0
            error_msg = res.content[0].text
            assert "Error: sub_group_by must be one of:" in error_msg
            assert "state__group" in error_msg

            # Standard call_tool raises ToolError
            with pytest.raises(ToolError) as exc_info:
                await client.call_tool(
                    "workitem",
                    {
                        "action": "count",
                        "project_id": "00000000-0000-0000-0000-000000000001",
                        "sub_group_by": invalid_sub_group_by,
                    },
                )
            assert "Error: sub_group_by must be one of:" in str(exc_info.value)
            assert "state__group" in str(exc_info.value)

    assert not mock_client.work_items.count_workspace.called


@pytest.mark.anyio
@pytest.mark.parametrize(
    "valid_group_by",
    ["state__group", "priority", "state_id", "type_id"],
)
async def test_valid_group_by_dispatches_cleanly(test_server: FastMCP, valid_group_by: str):
    """Valid group_by values must dispatch to SDK and return isError: False."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.model_dump.return_value = {"total": 10, valid_group_by: {}}
    mock_client.work_items.count_workspace.return_value = mock_response

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "count",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "group_by": valid_group_by,
                },
            )
            assert res.isError is False
            assert len(res.content) > 0

    assert mock_client.work_items.count_workspace.called
    call_kwargs = mock_client.work_items.count_workspace.call_args.kwargs
    assert call_kwargs["workspace_slug"] == "test-ws"
    assert call_kwargs["params"].group_by == valid_group_by
    assert call_kwargs["params"].sub_group_by is None


@pytest.mark.anyio
async def test_valid_group_by_and_sub_group_by_dispatches_cleanly(test_server: FastMCP):
    """Valid group_by and sub_group_by values together dispatch to SDK cleanly."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.model_dump.return_value = {"total": 5, "state__group": {"started": {"priority": {"urgent": 5}}}}
    mock_client.work_items.count_workspace.return_value = mock_response

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "count",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "group_by": "state__group",
                    "sub_group_by": "priority",
                },
            )
            assert res.isError is False

    assert mock_client.work_items.count_workspace.called
    call_kwargs = mock_client.work_items.count_workspace.call_args.kwargs
    assert call_kwargs["params"].group_by == "state__group"
    assert call_kwargs["params"].sub_group_by == "priority"


@pytest.mark.anyio
async def test_unfiltered_count_preserves_behavior(test_server: FastMCP):
    """Count calls with neither group_by nor sub_group_by continue to succeed."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.model_dump.return_value = {"total": 100}
    mock_client.work_items.count_workspace.return_value = mock_response

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "count",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                },
            )
            assert res.isError is False

    assert mock_client.work_items.count_workspace.called
    call_kwargs = mock_client.work_items.count_workspace.call_args.kwargs
    assert call_kwargs["params"].group_by is None
    assert call_kwargs["params"].sub_group_by is None


@pytest.mark.anyio
async def test_workitem_tool_schema_stability(test_server: FastMCP):
    """Workitem tool parameters and schema remain stable."""
    async with Client(test_server) as client:
        tools = await client.list_tools()
        workitem_tool = next((t for t in tools if t.name == "workitem"), None)
        assert workitem_tool is not None
        schema_props = workitem_tool.inputSchema.get("properties", {})
        assert "group_by" in schema_props
        assert "sub_group_by" in schema_props
        assert schema_props["group_by"].get("type") == "string"
        assert schema_props["sub_group_by"].get("type") == "string"
        for val in ("state__group", "priority", "state_id"):
            assert val in GROUP_BY_VALUES
