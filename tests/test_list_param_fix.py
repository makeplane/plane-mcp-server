"""
Unit tests for JSON-encoded string list parameter handling.

Verifies that all tools accepting list parameters correctly handle the case
where values are passed as JSON-encoded strings (as some MCP clients serialize
list parameters this way) as well as the standard case where values are native
Python lists.
"""

import json
from unittest.mock import MagicMock, patch

from fastmcp import FastMCP

from plane_mcp.tools.cycles import register_cycle_tools
from plane_mcp.tools.epics import register_epic_tools
from plane_mcp.tools.milestones import register_milestone_tools
from plane_mcp.tools.modules import register_module_tools
from plane_mcp.tools.work_item_properties import register_work_item_property_tools
from plane_mcp.tools.work_item_relations import register_work_item_relation_tools
from plane_mcp.tools.work_item_types import register_work_item_type_tools
from plane_mcp.tools.work_items import register_work_item_tools


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
    """add_work_items_to_module should parse issue_ids when passed as a JSON-encoded string."""
    mock_client = make_mock_client()
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_module_tools(mcp)

    # Retrieve the underlying function from the FastMCP tool registry
    tool_fn = mcp._tool_manager._tools["add_work_items_to_module"].fn

    # Simulate an MCP client that serializes the list as a JSON string
    tool_fn(
        project_id="proj-1",
        module_id="mod-1",
        issue_ids=json.dumps(ISSUE_IDS),  # passed as a JSON-encoded string
    )

    mock_client.modules.add_work_items.assert_called_once_with(
        workspace_slug="test-workspace",
        project_id="proj-1",
        module_id="mod-1",
        issue_ids=ISSUE_IDS,
    )


@patch("plane_mcp.tools.modules.get_plane_client_context")
def test_add_work_items_to_module_with_list(mock_ctx):
    """add_work_items_to_module should work correctly when issue_ids is already a native list."""
    mock_client = make_mock_client()
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_module_tools(mcp)

    tool_fn = mcp._tool_manager._tools["add_work_items_to_module"].fn

    tool_fn(
        project_id="proj-1",
        module_id="mod-1",
        issue_ids=ISSUE_IDS,  # passed as a native list
    )

    mock_client.modules.add_work_items.assert_called_once_with(
        workspace_slug="test-workspace",
        project_id="proj-1",
        module_id="mod-1",
        issue_ids=ISSUE_IDS,
    )


@patch("plane_mcp.tools.cycles.get_plane_client_context")
def test_add_work_items_to_cycle_with_json_string(mock_ctx):
    """add_work_items_to_cycle should parse issue_ids when passed as a JSON-encoded string."""
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
    """add_work_items_to_milestone should parse issue_ids when passed as a JSON-encoded string."""
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
    """remove_work_items_from_milestone should parse issue_ids when passed as a JSON-encoded string.

    Some MCP clients serialize list params as JSON strings; this verifies correct handling.
    """
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


# --- work_items.py tests ---

@patch("plane_mcp.tools.work_items.get_plane_client_context")
def test_create_work_item_assignees_json_string(mock_ctx):
    """create_work_item should parse assignees when passed as a JSON-encoded string."""
    mock_client = MagicMock()
    mock_client.work_items.create = MagicMock(return_value=MagicMock())
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_work_item_tools(mcp)

    tool_fn = mcp._tool_manager._tools["create_work_item"].fn
    tool_fn(project_id="proj-1", name="Test", assignees=json.dumps(ISSUE_IDS))

    call_kwargs = mock_client.work_items.create.call_args
    assert call_kwargs.kwargs["data"].assignees == ISSUE_IDS


@patch("plane_mcp.tools.work_items.get_plane_client_context")
def test_create_work_item_labels_json_string(mock_ctx):
    """create_work_item should parse labels when passed as a JSON-encoded string."""
    mock_client = MagicMock()
    mock_client.work_items.create = MagicMock(return_value=MagicMock())
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_work_item_tools(mcp)

    tool_fn = mcp._tool_manager._tools["create_work_item"].fn
    tool_fn(project_id="proj-1", name="Test", labels=json.dumps(ISSUE_IDS))

    call_kwargs = mock_client.work_items.create.call_args
    assert call_kwargs.kwargs["data"].labels == ISSUE_IDS


@patch("plane_mcp.tools.work_items.get_plane_client_context")
def test_update_work_item_assignees_json_string(mock_ctx):
    """update_work_item should parse assignees when passed as a JSON-encoded string."""
    mock_client = MagicMock()
    mock_client.work_items.update = MagicMock(return_value=MagicMock())
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_work_item_tools(mcp)

    tool_fn = mcp._tool_manager._tools["update_work_item"].fn
    tool_fn(project_id="proj-1", work_item_id="wi-1", assignees=json.dumps(ISSUE_IDS))

    call_kwargs = mock_client.work_items.update.call_args
    assert call_kwargs.kwargs["data"].assignees == ISSUE_IDS


@patch("plane_mcp.tools.work_items.get_plane_client_context")
def test_update_work_item_labels_json_string(mock_ctx):
    """update_work_item should parse labels when passed as a JSON-encoded string."""
    mock_client = MagicMock()
    mock_client.work_items.update = MagicMock(return_value=MagicMock())
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_work_item_tools(mcp)

    tool_fn = mcp._tool_manager._tools["update_work_item"].fn
    tool_fn(project_id="proj-1", work_item_id="wi-1", labels=json.dumps(ISSUE_IDS))

    call_kwargs = mock_client.work_items.update.call_args
    assert call_kwargs.kwargs["data"].labels == ISSUE_IDS


# --- modules.py members tests ---

@patch("plane_mcp.tools.modules.get_plane_client_context")
def test_create_module_members_json_string(mock_ctx):
    """create_module should parse members when passed as a JSON-encoded string."""
    mock_client = MagicMock()
    mock_client.modules.create = MagicMock(return_value=MagicMock())
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_module_tools(mcp)

    tool_fn = mcp._tool_manager._tools["create_module"].fn
    tool_fn(project_id="proj-1", name="Module 1", members=json.dumps(ISSUE_IDS))

    call_kwargs = mock_client.modules.create.call_args
    assert call_kwargs.kwargs["data"].members == ISSUE_IDS


@patch("plane_mcp.tools.modules.get_plane_client_context")
def test_update_module_members_json_string(mock_ctx):
    """update_module should parse members when passed as a JSON-encoded string."""
    mock_client = MagicMock()
    mock_client.modules.update = MagicMock(return_value=MagicMock())
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_module_tools(mcp)

    tool_fn = mcp._tool_manager._tools["update_module"].fn
    tool_fn(project_id="proj-1", module_id="mod-1", members=json.dumps(ISSUE_IDS))

    call_kwargs = mock_client.modules.update.call_args
    assert call_kwargs.kwargs["data"].members == ISSUE_IDS


# --- work_item_relations.py tests ---

@patch("plane_mcp.tools.work_item_relations.get_plane_client_context")
def test_create_work_item_relation_issues_json_string(mock_ctx):
    """create_work_item_relation should parse issues when passed as a JSON-encoded string."""
    mock_client = MagicMock()
    mock_client.work_items.relations.create = MagicMock(return_value=None)
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_work_item_relation_tools(mcp)

    tool_fn = mcp._tool_manager._tools["create_work_item_relation"].fn
    tool_fn(
        project_id="proj-1",
        work_item_id="wi-1",
        relation_type="blocking",
        issues=json.dumps(ISSUE_IDS),
    )

    call_kwargs = mock_client.work_items.relations.create.call_args
    assert call_kwargs.kwargs["data"].issues == ISSUE_IDS


# --- epics.py tests ---

@patch("plane_mcp.tools.epics.get_plane_client_context")
def test_create_epic_assignees_json_string(mock_ctx):
    """create_epic should parse assignees when passed as a JSON-encoded string."""
    mock_client = MagicMock()
    mock_epic_type = MagicMock()
    mock_epic_type.id = "epic-type-uuid"
    mock_epic_type.is_epic = True
    mock_client.work_item_types.list.return_value = [mock_epic_type]
    mock_client.work_items.create.return_value = MagicMock(id="epic-work-item-id")
    mock_client.epics.retrieve.return_value = MagicMock()
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_epic_tools(mcp)

    tool_fn = mcp._tool_manager._tools["create_epic"].fn
    tool_fn(project_id="proj-1", name="Epic 1", assignees=json.dumps(ISSUE_IDS))

    call_kwargs = mock_client.work_items.create.call_args
    assert call_kwargs.kwargs["data"].assignees == ISSUE_IDS


@patch("plane_mcp.tools.epics.get_plane_client_context")
def test_update_epic_assignees_json_string(mock_ctx):
    """update_epic should parse assignees when passed as a JSON-encoded string."""
    mock_client = MagicMock()
    mock_client.work_items.update.return_value = MagicMock(id="epic-work-item-id")
    mock_client.epics.retrieve.return_value = MagicMock()
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_epic_tools(mcp)

    tool_fn = mcp._tool_manager._tools["update_epic"].fn
    tool_fn(project_id="proj-1", epic_id="epic-1", assignees=json.dumps(ISSUE_IDS))

    call_kwargs = mock_client.work_items.update.call_args
    assert call_kwargs.kwargs["data"].assignees == ISSUE_IDS


# --- work_item_properties.py tests ---

@patch("plane_mcp.tools.work_item_properties.get_plane_client_context")
def test_create_work_item_property_default_value_json_string(mock_ctx):
    """create_work_item_property should parse default_value when passed as a JSON-encoded string."""
    mock_client = MagicMock()
    mock_client.work_item_properties.create = MagicMock(return_value=MagicMock())
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_work_item_property_tools(mcp)

    tool_fn = mcp._tool_manager._tools["create_work_item_property"].fn
    tool_fn(
        project_id="proj-1",
        type_id="type-1",
        display_name="My Property",
        property_type="DECIMAL",
        default_value=json.dumps(ISSUE_IDS),
    )

    call_kwargs = mock_client.work_item_properties.create.call_args
    assert call_kwargs.kwargs["data"].default_value == ISSUE_IDS


@patch("plane_mcp.tools.work_item_properties.get_plane_client_context")
def test_update_work_item_property_default_value_json_string(mock_ctx):
    """update_work_item_property should parse default_value when passed as a JSON-encoded string."""
    mock_client = MagicMock()
    mock_client.work_item_properties.update = MagicMock(return_value=MagicMock())
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_work_item_property_tools(mcp)

    tool_fn = mcp._tool_manager._tools["update_work_item_property"].fn
    tool_fn(
        project_id="proj-1",
        type_id="type-1",
        work_item_property_id="prop-1",
        default_value=json.dumps(ISSUE_IDS),
    )

    call_kwargs = mock_client.work_item_properties.update.call_args
    assert call_kwargs.kwargs["data"].default_value == ISSUE_IDS


# --- work_item_types.py tests ---

@patch("plane_mcp.tools.work_item_types.get_plane_client_context")
def test_create_work_item_type_project_ids_json_string(mock_ctx):
    """create_work_item_type should parse project_ids when passed as a JSON-encoded string."""
    mock_client = MagicMock()
    mock_client.work_item_types.create = MagicMock(return_value=MagicMock())
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_work_item_type_tools(mcp)

    tool_fn = mcp._tool_manager._tools["create_work_item_type"].fn
    tool_fn(project_id="proj-1", name="Bug", project_ids=json.dumps(ISSUE_IDS))

    call_kwargs = mock_client.work_item_types.create.call_args
    assert call_kwargs.kwargs["data"].project_ids == ISSUE_IDS


@patch("plane_mcp.tools.work_item_types.get_plane_client_context")
def test_update_work_item_type_project_ids_json_string(mock_ctx):
    """update_work_item_type should parse project_ids when passed as a JSON-encoded string."""
    mock_client = MagicMock()
    mock_client.work_item_types.update = MagicMock(return_value=MagicMock())
    mock_ctx.return_value = (mock_client, "test-workspace")

    mcp = FastMCP("test")
    register_work_item_type_tools(mcp)

    tool_fn = mcp._tool_manager._tools["update_work_item_type"].fn
    tool_fn(
        project_id="proj-1",
        work_item_type_id="wt-1",
        project_ids=json.dumps(ISSUE_IDS),
    )

    call_kwargs = mock_client.work_item_types.update.call_args
    assert call_kwargs.kwargs["data"].project_ids == ISSUE_IDS
