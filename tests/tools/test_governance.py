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
from types import SimpleNamespace

import pytest
from plane.errors.errors import HttpError

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

class _Features:
    """A features payload; `extra: allow` on the SDK model means unknown keys survive."""

    def __init__(self, **flags):
        self._flags = flags

    def model_dump(self):
        return dict(self._flags)


def _refusal() -> HttpError:
    """The 400 Plane raises when a governed workspace refuses project-level types."""
    return HttpError(
        "Bad Request",
        status_code=400,
        response={"work_item_types": ["Cannot enable project-level work item types when workspace-level ..."]},
    )


def _typed(name: str):
    """Enough of a WorkItemType for `resolve` to match on and return."""
    return SimpleNamespace(id=f"{name.lower()}-id", name=name)


def test_resolve_returns_a_type_already_usable_in_the_project(registered, spy):
    """The first read is project-scoped, which is correct in both modes."""
    spy.returns["work_item_types.list"] = [_typed("Bug")]

    result = registered["workitem_type"].fn(action="resolve", project_id=PROJECT, name="Bug")

    assert result.name == "Bug"
    assert spy.recorder.methods == ["work_item_types.list"], "resolve looked further than it needed to"


def test_resolve_creates_in_the_project_when_the_project_owns_types(registered, spy):
    spy.returns["work_item_types.list"] = []
    spy.returns["projects.get_features"] = _Features(work_item_types=True)
    spy.returns["work_item_types.create"] = _typed("Bug")

    result = registered["workitem_type"].fn(action="resolve", project_id=PROJECT, name="Bug")

    assert result.name == "Bug"
    assert "work_item_types.create" in spy.recorder.methods
    assert "workspace_work_item_types.create" not in spy.recorder.methods


def test_resolve_adopts_from_the_workspace_when_the_project_is_refused(registered, spy):
    """The reported failure: resolve dead-ended on this 400 instead of adopting."""
    spy.returns["work_item_types.list"] = []
    spy.returns["projects.get_features"] = _Features(work_item_types=False)
    spy.returns["projects.update_features"] = _refusal()
    spy.returns["workspace_work_item_types.list"] = []
    spy.returns["workspace_work_item_types.create"] = _typed("Bug")

    result = registered["workitem_type"].fn(action="resolve", project_id=PROJECT, name="Bug")

    assert result.name == "Bug"
    assert "workspace_work_item_types.create" in spy.recorder.methods
    assert "work_item_types.import_to_project" in spy.recorder.methods, "created but never imported into the project"
    assert "work_item_types.create" not in spy.recorder.methods


def test_resolve_reuses_an_existing_workspace_type_rather_than_duplicating(registered, spy):
    spy.returns["work_item_types.list"] = []
    spy.returns["projects.get_features"] = _Features(work_item_types=False)
    spy.returns["projects.update_features"] = _refusal()
    spy.returns["workspace_work_item_types.list"] = [_typed("Bug")]

    result = registered["workitem_type"].fn(action="resolve", project_id=PROJECT, name="Bug")

    assert result.name == "Bug"
    assert "workspace_work_item_types.create" not in spy.recorder.methods
    assert "work_item_types.import_to_project" in spy.recorder.methods


def test_resolve_does_not_swallow_an_unrelated_failure(registered, spy):
    """Only the governance refusal may reroute; anything else is the caller's to see."""
    spy.returns["work_item_types.list"] = []
    spy.returns["projects.get_features"] = _Features(work_item_types=False)
    spy.returns["projects.update_features"] = HttpError("Forbidden", status_code=403, response={})

    with pytest.raises(HttpError):
        registered["workitem_type"].fn(action="resolve", project_id=PROJECT, name="Bug")


def test_the_project_feature_key_matches_the_sdk():
    """A rename once rewrote this literal, silencing the read; the SDK field is the authority."""
    from plane.models.projects import ProjectFeature

    from plane_mcp.tools.workitem_type import PROJECT_TYPES_FEATURE

    assert PROJECT_TYPES_FEATURE in ProjectFeature.model_fields, (
        f"{PROJECT_TYPES_FEATURE!r} is not a ProjectFeature field: {sorted(ProjectFeature.model_fields)}"
    )
