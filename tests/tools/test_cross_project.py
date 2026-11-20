"""workitem list/count with assignee or state_group, across projects.

The Community Edition lists no workspace and rejects PQL, so the filter runs
here. These tests pin what a caller can rely on: only matching items come back,
a project the caller cannot read is reported rather than fatal, and a walk that
stopped early says so.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from plane.errors.errors import HttpError

from plane_mcp.toolkit import scan as scan_module

ME = "user-me"
OTHER = "user-other"


def _page(rows, nxt=None):
    return SimpleNamespace(results=rows, next_cursor=nxt or "", next_page_results=bool(nxt))


def _item(n, group, assignees=(ME,)):
    return SimpleNamespace(
        id=f"i{n}",
        sequence_id=n,
        name=f"Item {n}",
        state=SimpleNamespace(id="s", name=group, group=group),
        priority="medium",
        project="p",
        assignees=list(assignees),
        model_dump=lambda include=None, n=n, group=group, assignees=assignees: {
            "id": f"i{n}",
            "sequence_id": n,
            "name": f"Item {n}",
            "state": {"group": group},
            "assignees": list(assignees),
        },
    )


class World:
    """Two readable projects, one closed one, and a user endpoint."""

    def __init__(self):
        self.listed = []
        self.items = {
            "p1": [_item(1, "started"), _item(2, "completed"), _item(3, "started", (OTHER,))],
            "p2": [_item(1, "started")],
        }
        self.failure = {"p3": HttpError("denied", 403, {"error": "no"})}

        def projects(workspace_slug, params):
            rows = [SimpleNamespace(id=pid, identifier=pid.upper()) for pid in ("p1", "p2", "p3")]
            return _page(rows)

        def work_items(workspace_slug, project_id, params):
            self.listed.append((project_id, params))
            if project_id in self.failure:
                raise self.failure[project_id]
            return _page(self.items[project_id])

        self.client = SimpleNamespace(
            projects=SimpleNamespace(list_lite=projects),
            work_items=SimpleNamespace(list=work_items),
            users=SimpleNamespace(get_me=lambda: SimpleNamespace(id=ME)),
        )


@pytest.fixture
def world(registered, monkeypatch):
    from plane_mcp.tools import workitem

    w = World()
    monkeypatch.setattr(workitem, "get_plane_client_context", lambda: (w.client, "acme"))
    return w


def test_assignee_me_and_state_group_filter_across_projects(world, registered):
    result = registered["workitem"].fn(action="list", assignee="me", state_group="started")

    assert [r["identifier"] for r in result["results"]] == ["P1-1", "P2-1"]
    assert result["projects_scanned"] == 2
    assert result["truncated"] is None


def test_a_closed_project_is_reported_not_fatal(world, registered):
    result = registered["workitem"].fn(action="list", assignee="me")

    assert result["skipped_projects"] == ["P3"]
    assert len(result["results"]) == 3


def test_count_groups_by_project(world, registered):
    result = registered["workitem"].fn(action="count", assignee=ME, state_group=["started", "completed"])

    assert result["count"] == 3
    assert result["by_project"] == {"P1": 2, "P2": 1}


def test_project_id_narrows_the_walk(world, registered):
    registered["workitem"].fn(action="list", project_id="p2", assignee="me")

    assert [pid for pid, _ in world.listed] == ["p2"]


def test_an_unknown_project_is_an_error(world, registered):
    result = registered["workitem"].fn(action="list", project_id="nope", assignee="me")

    assert isinstance(result, str) and result.startswith("Error:")


def test_an_invalid_state_group_names_the_choices(world, registered):
    result = registered["workitem"].fn(action="list", state_group="open")

    assert result.startswith("Error: state_group must be one of")
    assert not world.listed


@pytest.mark.parametrize("extra", [{"pql": "x"}, {"cursor": "c"}, {"per_page": 5}, {"order_by": "name"}])
def test_arguments_the_walk_cannot_honour_are_refused_not_dropped(world, registered, extra):
    result = registered["workitem"].fn(action="list", assignee="me", **extra)

    assert result.startswith("Error:") and next(iter(extra)) in result
    assert not world.listed


def test_the_walk_is_bounded_and_says_so(world, registered, monkeypatch):
    monkeypatch.setattr(scan_module, "MAX_REQUESTS", 3)

    result = registered["workitem"].fn(action="list", assignee="me")

    assert result["truncated"] and "rate limit" in result["truncated"]
    assert result["projects_scanned"] == 2  # the project list, p1 and p2 use the 3 calls; p3 is refused


def test_a_429_returns_what_was_found(world, registered):
    world.failure["p2"] = HttpError("slow down", 429, None)

    result = registered["workitem"].fn(action="list", assignee="me")

    assert result["truncated"].startswith("Plane answered 429")
    assert [r["identifier"] for r in result["results"]] == ["P1-1", "P1-2"]


def test_other_errors_still_raise(world, registered):
    world.failure["p2"] = HttpError("boom", 500, None)

    with pytest.raises(HttpError):
        registered["workitem"].fn(action="list", assignee="me")


def test_a_page_is_followed_to_its_end(world, registered):
    pages = iter([_page([_item(1, "started")], "next"), _page([_item(2, "started")])])
    world.client.work_items.list = lambda workspace_slug, project_id, params: (
        next(pages) if project_id == "p1" else _page([])
    )

    result = registered["workitem"].fn(action="count", assignee="me")

    assert result["by_project"]["P1"] == 2


def test_no_filter_keeps_the_old_workspace_listing(registered, spy):
    registered["workitem"].fn(action="list")

    assert spy.recorder.only().method == "work_items.list_workspace"
