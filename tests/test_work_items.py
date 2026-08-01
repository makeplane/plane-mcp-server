"""Unit tests for work item tools (offline, monkeypatched client)."""

import asyncio

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from plane.models.projects import ProjectMember
from plane.models.work_items import WorkItem, WorkItemDetail

from plane_mcp.tools import work_items as work_item_tools
from plane_mcp.tools.work_items import _ids


class FakeWorkItems:
    """Retrieve returns bare-string assignees/labels (the plain-retrieve shape)."""

    def __init__(self):
        self.updated = None
        self.created = None

    def create(self, workspace_slug, project_id, data):
        """Record the create so a test can assert it was never issued."""
        self.created = data
        return WorkItem.model_validate({"id": "w", "name": data.name})

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


# --- assignee assignability guard -------------------------------------------
#
# Plane filters unassignable ids out of `assignees` during validation instead of
# rejecting them, then deletes the work item's existing assignees before writing
# that filtered list. One bad id clears the field and still answers 200, so the
# guard has to run before the write.

MEMBER = {"id": "member-1", "email": "member@example.com", "role": 20, "is_active": True}
AT_THRESHOLD = {"id": "member-2", "email": "member2@example.com", "role": 15, "is_active": True}
GUEST = {"id": "guest-1", "email": "guest@example.com", "role": 5, "is_active": True}
INACTIVE = {"id": "inactive-1", "email": "inactive@example.com", "role": 20, "is_active": False}
BARE = {"id": "bare-1", "email": "bare@example.com"}  # CE omits role/is_active


class FakeProjects:
    """`get_members` returns a bare list, the shape the non-lite endpoint serves."""

    def __init__(self, members, error=None):
        self.members = members
        self.error = error
        self.calls = 0

    def get_members(self, workspace_slug, project_id):
        """Return the configured members, or raise to simulate the lookup being unavailable."""
        self.calls += 1
        if self.error:
            raise self.error
        return [ProjectMember.model_validate(m) for m in self.members]


class MemberAwareClient(FakeClient):
    """A FakeClient that can also answer the project-member lookup the guard makes."""

    def __init__(self, members, error=None):
        super().__init__()
        self.projects = FakeProjects(members, error)


def _assign(monkeypatch, client, assignees, tool="update_work_item"):
    """Call a write tool with the given assignees, defaulting to the destructive update path."""
    args = {"project_id": "p", "work_item_id": "w", "assignees": assignees}
    if tool == "create_work_item":
        args = {"project_id": "p", "name": "X", "assignees": assignees}
    return _call(monkeypatch, client, tool, args)


@pytest.mark.parametrize(
    "members,bad_id,reason",
    [
        ([MEMBER], "stranger-1", "stranger-1 (not a member of this project)"),
        ([MEMBER, GUEST], "guest-1", "project role 5 is below member"),
        ([MEMBER, INACTIVE], "inactive-1", "project membership is inactive"),
    ],
    ids=["not-a-member", "below-member-role", "inactive-membership"],
)
def test_unassignable_assignee_is_rejected_before_the_write(monkeypatch, members, bad_id, reason):
    """Each way Plane filters an id out must raise, name why, and leave the write unsent.

    The error also has to name an id that would work — a caller cannot guess one.
    """
    client = MemberAwareClient(members)
    with pytest.raises(ToolError) as exc:
        _assign(monkeypatch, client, [bad_id])
    assert reason in str(exc.value)
    assert "member@example.com=member-1" in str(exc.value)
    assert client.work_items.updated is None, "the destructive update must not be issued"


@pytest.mark.parametrize("member", [MEMBER, AT_THRESHOLD], ids=["above-threshold", "exactly-at-threshold"])
def test_assignable_member_passes_through(monkeypatch, member):
    """The happy path is unchanged, including a role sitting exactly on the member floor.

    Plane's cutoff is `role >= 15`, so role 15 is the value that decides whether the
    comparison is inclusive — an off-by-one there would lock out every plain member.
    """
    client = MemberAwareClient([member])
    _assign(monkeypatch, client, [member["id"]])
    assert client.work_items.updated.assignees == [member["id"]]


def test_members_without_role_are_accepted_on_membership_alone(monkeypatch):
    """Some deployments omit role/is_active; an unreported role must not fail everyone."""
    client = MemberAwareClient([BARE])
    _assign(monkeypatch, client, ["bare-1"])
    assert client.work_items.updated.assignees == ["bare-1"]


def test_member_lookup_failure_does_not_block_the_write(monkeypatch):
    """A pre-check must not become a new failure mode when the lookup itself breaks."""
    client = MemberAwareClient([], error=RuntimeError("members endpoint unavailable"))
    _assign(monkeypatch, client, ["member-1"])
    assert client.work_items.updated.assignees == ["member-1"]


def test_empty_assignees_skips_the_member_lookup(monkeypatch):
    """Writes that carry no assignees must not pay for an extra request."""
    client = MemberAwareClient([MEMBER])
    _call(monkeypatch, client, "update_work_item", {"project_id": "p", "work_item_id": "w", "name": "Y"})
    assert client.projects.calls == 0


def test_create_work_item_rejects_unassignable_assignee(monkeypatch):
    """Create cannot wipe anything, but the requested assignment still silently fails."""
    client = MemberAwareClient([MEMBER])
    with pytest.raises(ToolError) as exc:
        _assign(monkeypatch, client, ["stranger-1"], tool="create_work_item")
    assert "not a member of this project" in str(exc.value)
    assert client.work_items.created is None


def test_manage_assignee_rejects_unassignable_add(monkeypatch):
    """The read-modify-write in manage_work_item_assignee is what makes this destructive."""
    client = MemberAwareClient([MEMBER])
    with pytest.raises(ToolError):
        _call(
            monkeypatch,
            client,
            "manage_work_item_assignee",
            {"project_id": "p", "work_item_id": "w", "add_user_id": "stranger-1"},
        )
    assert client.work_items.updated is None, "existing-user must survive a rejected add"


def test_manage_assignee_removal_is_not_blocked_by_a_stale_assignee(monkeypatch):
    """Only the incoming id is checked, so losing access does not trap the existing list."""
    client = MemberAwareClient([MEMBER])  # "existing-user" is no longer a member
    _call(
        monkeypatch,
        client,
        "manage_work_item_assignee",
        {"project_id": "p", "work_item_id": "w", "remove_user_id": "existing-user"},
    )
    assert client.work_items.updated.assignees == []
    assert client.projects.calls == 0, "a pure removal introduces no id to check"
