"""Unit tests for work item state field Cloud/CE compatibility (#183)."""

import asyncio
from unittest.mock import MagicMock, patch

from fastmcp import FastMCP
from plane.models.work_items import WorkItem

from plane_mcp.tools.work_items import (
    _CreateWorkItemCompat,
    _UpdateWorkItemCompat,
    register_work_item_tools,
)

STATE_UUID = "ba5e07a3-b2a0-4d45-93bf-8b03b0593e13"
PROJECT_ID = "7ac2e2c6-6057-43e9-88af-59cbda15d1cd"
WORK_ITEM_ID = "7d61d6dc-8998-409d-b026-6765fc0a7df8"


def test_update_compat_emits_state_and_state_id():
    data = _UpdateWorkItemCompat(state=STATE_UUID, state_id=STATE_UUID)
    payload = data.model_dump(exclude_none=True)
    assert payload["state"] == STATE_UUID
    assert payload["state_id"] == STATE_UUID


def test_create_compat_emits_state_and_state_id():
    data = _CreateWorkItemCompat(name="Task", state=STATE_UUID, state_id=STATE_UUID)
    payload = data.model_dump(exclude_none=True)
    assert payload["name"] == "Task"
    assert payload["state"] == STATE_UUID
    assert payload["state_id"] == STATE_UUID


def test_update_compat_omits_state_fields_when_unset():
    data = _UpdateWorkItemCompat(name="Renamed")
    payload = data.model_dump(exclude_none=True)
    assert payload == {"name": "Renamed"}
    assert "state" not in payload
    assert "state_id" not in payload


def _tool_fn(mcp: FastMCP, name: str):
    return asyncio.run(mcp.get_tool(name)).fn


def test_update_work_item_tool_passes_dual_state_fields():
    mcp = FastMCP("test")
    register_work_item_tools(mcp)
    update_work_item = _tool_fn(mcp, "update_work_item")

    mock_client = MagicMock()
    mock_client.work_items.update.return_value = WorkItem(id=WORK_ITEM_ID, name="Task", state=STATE_UUID)

    with patch("plane_mcp.tools.work_items.get_plane_client_context", return_value=(mock_client, "homelab-ops")):
        update_work_item(project_id=PROJECT_ID, work_item_id=WORK_ITEM_ID, state=STATE_UUID)

    kwargs = mock_client.work_items.update.call_args.kwargs
    payload = kwargs["data"].model_dump(exclude_none=True)
    assert payload["state"] == STATE_UUID
    assert payload["state_id"] == STATE_UUID


def test_create_work_item_tool_passes_dual_state_fields():
    mcp = FastMCP("test")
    register_work_item_tools(mcp)
    create_work_item = _tool_fn(mcp, "create_work_item")

    mock_client = MagicMock()
    mock_client.work_items.create.return_value = WorkItem(id=WORK_ITEM_ID, name="Task", state=STATE_UUID)

    with patch("plane_mcp.tools.work_items.get_plane_client_context", return_value=(mock_client, "homelab-ops")):
        create_work_item(project_id=PROJECT_ID, name="Task", state=STATE_UUID)

    kwargs = mock_client.work_items.create.call_args.kwargs
    payload = kwargs["data"].model_dump(exclude_none=True)
    assert payload["state"] == STATE_UUID
    assert payload["state_id"] == STATE_UUID
