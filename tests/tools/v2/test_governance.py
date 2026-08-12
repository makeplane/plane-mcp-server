"""Resources Plane governs at both the workspace and the project.

The idiom is uniform and load-bearing: **supply project_id for the project's own
set, omit it for the workspace's.** Getting it wrong is quiet — the call still
succeeds, against the wrong scope — so each scope is pinned to the namespace and
the id keyword it must use. The two scopes do not agree on the id keyword, which
is exactly the kind of detail that rots.
"""

from __future__ import annotations

import pytest

from plane_mcp.tools.v2.scope import WORK_ITEM_TYPE

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


def test_the_declaration_matches_the_sdk():
    """A namespace or id keyword renamed in the SDK must fail here, not in production."""
    import inspect

    from plane import PlaneClient

    client = PlaneClient(api_key="spy", base_url="http://spy.invalid")
    for path, id_kwarg, actions in (
        (WORK_ITEM_TYPE.project_namespace, WORK_ITEM_TYPE.project_id_kwarg, WORK_ITEM_TYPE.project_actions),
        (WORK_ITEM_TYPE.workspace_namespace, WORK_ITEM_TYPE.workspace_id_kwarg, WORK_ITEM_TYPE.workspace_actions),
    ):
        namespace = client
        for part in path.split("."):
            namespace = getattr(namespace, part, None)
            assert namespace is not None, f"the SDK has no {path}"
        for action in actions:
            method = getattr(namespace, action, None)
            assert method is not None, f"{path} has no {action}()"
            if action in ("retrieve", "update", "delete"):
                assert id_kwarg in inspect.signature(method).parameters, f"{path}.{action}() does not take {id_kwarg!r}"


def test_workspace_scope_refuses_what_it_cannot_do(registered, spy):
    """A scope that lacks an operation says so instead of calling the wrong thing."""
    unsupported = WORK_ITEM_TYPE.project_actions - WORK_ITEM_TYPE.workspace_actions
    if not unsupported:
        pytest.skip("both scopes currently support the same actions")
    tool = registered["work_item_type"].fn
    for action in sorted(unsupported):
        result = tool(action=action, work_item_type_id=TYPE_ID)
        assert isinstance(result, str) and result.startswith("Error:")
        assert not spy.recorder.calls
