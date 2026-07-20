"""Unit tests for work item tools (offline, monkeypatched client)."""

import asyncio

from fastmcp import Client, FastMCP
from plane.models.work_items import WorkItem, WorkItemDetail

from plane_mcp.tools import work_items as work_item_tools
from plane_mcp.tools.work_items import _ids


class FakeWorkItems:
    """Retrieve returns bare-string assignees/labels (the plain-retrieve shape)."""

    def __init__(self):
        self.updated = None

    def retrieve(self, workspace_slug, project_id, work_item_id):
        return WorkItemDetail.model_validate(
            {
                "id": work_item_id,
                "name": "X",
                "assignees": ["existing-user"],
                "labels": ["existing-label"],
            }
        )

    def update(self, workspace_slug, project_id, work_item_id, data):
        self.updated = data
        return WorkItem.model_validate({"id": work_item_id, "name": "X"})


class FakeClient:
    def __init__(self):
        self.work_items = FakeWorkItems()


def _call(monkeypatch, client, tool, args):
    monkeypatch.setattr(work_item_tools, "get_plane_client_context", lambda: (client, "ws"))

    async def run():
        mcp = FastMCP("test")
        work_item_tools.register_work_item_tools(mcp)
        async with Client(mcp) as c:
            return await c.call_tool(tool, args)

    return asyncio.run(run())


def test_ids_handles_bare_strings_and_objects():
    """_ids extracts ids whether items are UUID strings or objects with .id."""
    assert _ids(["u-1", "u-2"]) == ["u-1", "u-2"]

    class Obj:
        def __init__(self, i):
            self.id = i

    assert _ids([Obj("u-9")]) == ["u-9"]
    assert _ids(None) == []
    assert _ids([]) == []


def test_manage_assignee_read_back_survives_bare_string_assignees(monkeypatch):
    """Regression: retrieve returns UUID-string assignees; the read-back must not
    crash with "'str' object has no attribute 'id'" (issue #98 knock-on)."""
    client = FakeClient()
    _call(
        monkeypatch,
        client,
        "manage_work_item_assignee",
        {"project_id": "p", "work_item_id": "w", "add_user_id": "new-user"},
    )
    assert client.work_items.updated.assignees == ["existing-user", "new-user"]


def test_manage_label_read_back_survives_bare_string_labels(monkeypatch):
    """Regression: retrieve returns UUID-string labels; the read-back must not crash."""
    client = FakeClient()
    _call(
        monkeypatch,
        client,
        "manage_work_item_label",
        {"project_id": "p", "work_item_id": "w", "add_label_id": "new-label"},
    )
    assert client.work_items.updated.labels == ["existing-label", "new-label"]


def test_manage_assignee_remove_bare_string(monkeypatch):
    """Removing an existing bare-string assignee drops it without touching the rest."""
    client = FakeClient()
    _call(
        monkeypatch,
        client,
        "manage_work_item_assignee",
        {"project_id": "p", "work_item_id": "w", "remove_user_id": "existing-user"},
    )
    assert client.work_items.updated.assignees == []
