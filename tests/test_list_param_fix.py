"""
Unit tests for JSON-encoded string list parameter fix.

Verifies that add_work_items_to_module, add_work_items_to_cycle,
add_work_items_to_milestone and remove_work_items_from_milestone
correctly handle issue_ids when passed as a JSON-encoded string
(as some MCP clients, e.g. Claude Code, serialize list params this way).
"""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import FastMCP

from plane_mcp.tools.cycles import register_cycle_tools
from plane_mcp.tools.milestones import register_milestone_tools
from plane_mcp.tools.modules import register_module_tools


def make_mock_client():
    client = MagicMock()
    client.modules.add_work_items = MagicMock(return_value=None)
    client.cycles.add_work_items = MagicMock(return_value=None)
    client.milestones.add_work_items = MagicMock(return_value=None)
    client.milestones.remove_work_items = MagicMock(return_value=None)
    return client


ISSUE_IDS = ["660bb007-c7b9-4f56-b9d7-7e468124083b", "7353ed39-a18b-4e91-a6f0-ae67c6ea4c05"]


@patch("plane_mcp.tools.modules.get_plane_client_context")
def test_add_work_items_to_module_with_json_string(mock_ctx):
    """add_work_items_to_module은 JSON 문자열로 전달된 issue_ids를 파싱해야 한다."""
    mock_client = make_mock_client()
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_module_tools(mcp)

    # FastMCP tool 함수를 직접 꺼내서 호출
    tool_fn = mcp._tool_manager._tools["add_work_items_to_module"].fn

    # MCP 클라이언트가 JSON 문자열로 직렬화해서 보내는 시나리오
    tool_fn(
        project_id="proj-1",
        module_id="mod-1",
        issue_ids=json.dumps(ISSUE_IDS),  # JSON 문자열로 전달
    )

    mock_client.modules.add_work_items.assert_called_once_with(
        workspace_slug="test-workspace",
        project_id="proj-1",
        module_id="mod-1",
        issue_ids=ISSUE_IDS,
    )


@patch("plane_mcp.tools.modules.get_plane_client_context")
def test_add_work_items_to_module_with_list(mock_ctx):
    """add_work_items_to_module은 정상적인 list도 그대로 처리해야 한다."""
    mock_client = make_mock_client()
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_module_tools(mcp)

    tool_fn = mcp._tool_manager._tools["add_work_items_to_module"].fn

    tool_fn(
        project_id="proj-1",
        module_id="mod-1",
        issue_ids=ISSUE_IDS,  # 정상 list로 전달
    )

    mock_client.modules.add_work_items.assert_called_once_with(
        workspace_slug="test-workspace",
        project_id="proj-1",
        module_id="mod-1",
        issue_ids=ISSUE_IDS,
    )


@patch("plane_mcp.tools.cycles.get_plane_client_context")
def test_add_work_items_to_cycle_with_json_string(mock_ctx):
    """add_work_items_to_cycle은 JSON 문자열로 전달된 issue_ids를 파싱해야 한다."""
    mock_client = make_mock_client()
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_cycle_tools(mcp)

    tool_fn = mcp._tool_manager._tools["add_work_items_to_cycle"].fn

    tool_fn(
        project_id="proj-1",
        cycle_id="cycle-1",
        issue_ids=json.dumps(ISSUE_IDS),
    )

    mock_client.cycles.add_work_items.assert_called_once_with(
        workspace_slug="test-workspace",
        project_id="proj-1",
        cycle_id="cycle-1",
        issue_ids=ISSUE_IDS,
    )


@patch("plane_mcp.tools.milestones.get_plane_client_context")
def test_add_work_items_to_milestone_with_json_string(mock_ctx):
    """add_work_items_to_milestone은 JSON 문자열로 전달된 issue_ids를 파싱해야 한다."""
    mock_client = make_mock_client()
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_milestone_tools(mcp)

    tool_fn = mcp._tool_manager._tools["add_work_items_to_milestone"].fn

    tool_fn(
        project_id="proj-1",
        milestone_id="ms-1",
        issue_ids=json.dumps(ISSUE_IDS),
    )

    mock_client.milestones.add_work_items.assert_called_once_with(
        workspace_slug="test-workspace",
        project_id="proj-1",
        milestone_id="ms-1",
        issue_ids=ISSUE_IDS,
    )


@patch("plane_mcp.tools.milestones.get_plane_client_context")
def test_remove_work_items_from_milestone_with_json_string(mock_ctx):
    """remove_work_items_from_milestone은 JSON 문자열로 전달된 issue_ids를 파싱해야 한다."""
    mock_client = make_mock_client()
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_milestone_tools(mcp)

    tool_fn = mcp._tool_manager._tools["remove_work_items_from_milestone"].fn

    tool_fn(
        project_id="proj-1",
        milestone_id="ms-1",
        issue_ids=json.dumps(ISSUE_IDS),
    )

    mock_client.milestones.remove_work_items.assert_called_once_with(
        workspace_slug="test-workspace",
        project_id="proj-1",
        milestone_id="ms-1",
        issue_ids=ISSUE_IDS,
    )
