"""Resources Plane governs at both the workspace and the project.

The idiom is uniform and load-bearing: **supply project_id for the project's own
set, omit it for the workspace's.** Getting it wrong is quiet -- the call still
succeeds, against the wrong scope -- so each scope is pinned to the namespace and
the id keyword it must use. The two scopes do not agree on the id keyword, which
is exactly the kind of detail that rots.

Work item types are the only resource governed at both scopes today. The
resolver lives in the module rather than in a shared abstraction: one caller
does not justify one, and the two-way split here is not the three-way split
`work_item_property` needs.
"""

from __future__ import annotations

import inspect

import pytest

from plane_mcp.tools.v2.work_item_type import _scope_of

PROJECT = "project-1"
TYPE_ID = "type-1"


@pytest.fixture
def work_item_type(registered, spy):
    return registered["work_item_type"].fn, spy


@pytest.mark.parametrize(
    ("action", "extra", "project_method", "workspace_method"),
    [
        ("list", {}, "work_item_types.list", "workspace_work_item_types.list"),
        ("create", {"name": "Epic"}, "work_item_types.create", "workspace_work_item_types.create"),
        ("retrieve", {"work_item_type_id": TYPE_ID}, "work_item_types.retrieve", "workspace_work_item_types.retrieve"),
        ("update", {"work_item_type_id": TYPE_ID}, "work_item_types.update", "workspace_work_item_types.update"),
        ("delete", {"work_item_type_id": TYPE_ID}, "work_item_types.delete", "workspace_work_item_types.delete"),
    ],
)
def test_project_id_selects_the_scope(work_item_type, action, extra, project_method, workspace_method):
    tool, spy = work_item_type

    tool(action=action, project_id=PROJECT, **extra)
    scoped = spy.recorder.only()
    assert scoped.method == project_method
    assert scoped.kwargs.get("project_id") == PROJECT

    spy.recorder.calls.clear()
    tool(action=action, **extra)
    governed = spy.recorder.only()
    assert governed.method == workspace_method
    assert "project_id" not in governed.kwargs


def test_each_scope_uses_its_own_id_keyword(work_item_type):
    """The project endpoint takes work_item_type_id; the workspace one takes type_id."""
    tool, spy = work_item_type

    tool(action="retrieve", project_id=PROJECT, work_item_type_id=TYPE_ID)
    assert spy.recorder.only().kwargs["work_item_type_id"] == TYPE_ID

    spy.recorder.calls.clear()
    tool(action="retrieve", work_item_type_id=TYPE_ID)
    assert spy.recorder.only().kwargs["type_id"] == TYPE_ID


@pytest.mark.parametrize("project_id", ["", PROJECT], ids=["workspace", "project"])
def test_the_resolver_matches_the_sdk(project_id):
    """A namespace or id keyword renamed in the SDK must fail here, not in production."""
    from plane import PlaneClient

    client = PlaneClient(api_key="spy", base_url="http://spy.invalid")
    namespace, scope, id_kwarg = _scope_of(client, project_id)

    assert namespace is not None
    for verb in ("list", "retrieve", "create", "update", "delete"):
        method = getattr(namespace, verb, None)
        assert method is not None, f"the SDK namespace has no {verb}()"
        takes = inspect.signature(method).parameters
        if verb in ("retrieve", "update", "delete"):
            assert id_kwarg in takes, f"{verb}() does not take {id_kwarg!r}"
        for name in scope:
            assert name in takes, f"{verb}() does not take {name!r}"
