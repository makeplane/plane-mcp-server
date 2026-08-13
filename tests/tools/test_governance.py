"""Resources Plane governs at both the workspace and the project.

The idiom is uniform and load-bearing: **supply project_id for the project's own
set, omit it for the workspace's.** Getting it wrong is quiet -- the call still
succeeds, against the wrong scope -- so each scope is pinned to the namespace and
the id keyword it must use. The two scopes do not agree on the id keyword, which
is exactly the kind of detail that rots.

Work item types are the only resource governed at both scopes today. The
resolver lives in the module rather than in a shared abstraction: one caller
does not justify one, and the two-way split here is not the three-way split
`workitem_property` needs.
"""

from __future__ import annotations

import inspect

import pytest

from plane_mcp.tools.workitem_type import _scope_of

PROJECT = "project-1"
TYPE_ID = "type-1"


@pytest.fixture
def workitem_type(registered, spy):
    return registered["workitem_type"].fn, spy


@pytest.mark.parametrize(
    ("action", "extra", "project_method", "workspace_method"),
    [
        ("list", {}, "work_item_types.list", "workspace_work_item_types.list"),
        ("create", {"name": "Epic"}, "work_item_types.create", "workspace_work_item_types.create"),
        ("retrieve", {"workitem_type_id": TYPE_ID}, "work_item_types.retrieve", "workspace_work_item_types.retrieve"),
        ("update", {"workitem_type_id": TYPE_ID}, "work_item_types.update", "workspace_work_item_types.update"),
        ("delete", {"workitem_type_id": TYPE_ID}, "work_item_types.delete", "workspace_work_item_types.delete"),
    ],
)
def test_project_id_selects_the_scope(workitem_type, action, extra, project_method, workspace_method):
    tool, spy = workitem_type

    tool(action=action, project_id=PROJECT, **extra)
    scoped = spy.recorder.only()
    assert scoped.method == project_method
    assert scoped.kwargs.get("project_id") == PROJECT

    spy.recorder.calls.clear()
    tool(action=action, **extra)
    governed = spy.recorder.only()
    assert governed.method == workspace_method
    assert "project_id" not in governed.kwargs


def test_each_scope_uses_its_own_id_keyword(workitem_type):
    """One tool parameter, two SDK spellings.

    The tool takes `workitem_type_id` at both scopes. Underneath, the project
    endpoint calls it `work_item_type_id` and the workspace one calls it
    `type_id`, so the assertions below are on the SDK's names, not the tool's.
    """
    tool, spy = workitem_type

    tool(action="retrieve", project_id=PROJECT, workitem_type_id=TYPE_ID)
    assert spy.recorder.only().kwargs["work_item_type_id"] == TYPE_ID

    spy.recorder.calls.clear()
    tool(action="retrieve", workitem_type_id=TYPE_ID)
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
