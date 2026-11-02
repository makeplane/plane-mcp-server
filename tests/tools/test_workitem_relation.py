"""`workitem_relation` must hit the routes self-hosted CE actually serves.

Plane CE 1.4.1 404s on `/dependencies/`, `/work-item-relations/` and
`/work-item-relation-definitions/` (makeplane/plane-mcp-server#185). The only
route that answers for built-in relation types is `/relations/`, which uses
`issues` where the newer SDK model uses `work_item_ids`, and `related_issue`
in the body (not a path segment) to remove one. Custom (definition-based)
relations are untouched here -- they stay on their own SDK surface and simply
degrade to empty results when that surface 404s, the same way list_definitions
already did before this change.
"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError
from plane.errors.errors import HttpError
from pydantic import ValidationError


def _call(registered, **kwargs):
    return registered["workitem_relation"].fn(**kwargs)


# --- list: built-in relations must come from /relations/, not /dependencies/ ---


def test_list_uses_relations_route_not_dependencies(registered, spy):
    _call(registered, action="list", project_id="p", workitem_id="w")
    assert "work_items.relations.list" in spy.recorder.methods
    assert "work_items.dependencies.list" not in spy.recorder.methods


def test_list_still_queries_custom_relations_alongside(registered, spy):
    _call(registered, action="list", project_id="p", workitem_id="w")
    assert "work_items.custom_relations.list" in spy.recorder.methods


def test_list_absorbs_a_404_on_custom_relations(registered, spy):
    """work-item-relations/ (custom, definition-based) is absent on CE.

    A 404 there must not break the whole `list` action -- it means "no custom
    relations available on this deployment", same shape as list_definitions.
    """
    spy.returns["work_items.custom_relations.list"] = HttpError("nope", status_code=404)
    result = _call(registered, action="list", project_id="p", workitem_id="w")
    assert result["custom"] == {}


def test_list_propagates_non_404_errors_on_relations(registered, spy):
    spy.returns["work_items.relations.list"] = HttpError("boom", status_code=500)
    with pytest.raises(HttpError):
        _call(registered, action="list", project_id="p", workitem_id="w")


def test_list_normalizes_ce_relation_objects(registered, spy):
    """CE answers /relations/ with objects; the SDK model wants plain ids.

    Validation must not fail the action: read the same route raw and reduce
    each entry to its issue_id, while leaving already-plain ids alone.
    """
    spy.returns["work_items.relations.list"] = ValidationError.from_exception_data(
        "WorkItemRelationResponse",
        [{"type": "list_type", "loc": ("blocked_by",), "input": {"issue_id": "u1"}}],
    )
    spy.returns["work_items.relations._get"] = {
        "blocked_by": [{"project_id": "p", "issue_id": "u1"}],
        "relates_to": ["u2"],
    }

    result = _call(registered, action="list", project_id="p", workitem_id="w")

    assert result["dependencies"]["blocked_by"] == ["u1"]
    assert result["dependencies"]["relates_to"] == ["u2"]


# --- create: a built-in relation_type must translate work_item_ids -> issues ---


def test_create_dependency_uses_relations_route_not_dependencies(registered, spy):
    _call(
        registered,
        action="create",
        project_id="p",
        workitem_id="w",
        workitem_ids=["t1", "t2"],
        relation_type="blocking",
    )
    assert "work_items.relations.create" in spy.recorder.methods
    assert "work_items.dependencies.create" not in spy.recorder.methods


def test_create_dependency_translates_work_item_ids_to_issues(registered, spy):
    _call(
        registered,
        action="create",
        project_id="p",
        workitem_id="w",
        workitem_ids=["t1", "t2"],
        relation_type="blocking",
    )
    sent = spy.recorder.only().kwargs["data"]
    assert sent.issues == ["t1", "t2"]
    assert sent.relation_type == "blocking"
    # The outgoing wire payload must use "issues", the field name CE's
    # /relations/ route actually accepts -- not "work_item_ids".
    assert sent.model_dump()["issues"] == ["t1", "t2"]
    assert "work_item_ids" not in sent.model_dump()


def test_create_dependency_accepts_a_comma_separated_string(registered, spy):
    """coerce_list still normalises a single-string id list before translation."""
    _call(
        registered,
        action="create",
        project_id="p",
        workitem_id="w",
        workitem_ids="t1,t2",
        relation_type="start_before",
    )
    sent = spy.recorder.only().kwargs["data"]
    assert sent.issues == ["t1", "t2"]


def test_create_custom_relation_is_unaffected_by_the_route_change(registered, spy):
    """Definition-based relations never used /dependencies/ and still don't."""
    _call(
        registered,
        action="create",
        project_id="p",
        workitem_id="w",
        workitem_ids=["t1"],
        relation_definition_id="def-1",
        relation_definition_label="implements",
    )
    assert spy.recorder.only().method == "work_items.custom_relations.create"
    sent = spy.recorder.only().kwargs["data"]
    assert sent.work_item_ids == ["t1"]


# --- delete: a dependency removal must use relations.delete() + related_issue ---


def test_delete_dependency_uses_relations_delete_not_dependencies(registered, spy):
    _call(
        registered,
        action="delete",
        project_id="p",
        workitem_id="w",
        related_workitem_id="t1",
        is_dependency=True,
    )
    assert "work_items.relations.delete" in spy.recorder.methods
    assert "work_items.dependencies.remove" not in spy.recorder.methods


def test_delete_dependency_sends_related_issue_in_the_body(registered, spy):
    _call(
        registered,
        action="delete",
        project_id="p",
        workitem_id="w",
        related_workitem_id="t1",
        is_dependency=True,
    )
    sent = spy.recorder.only().kwargs["data"]
    assert sent.related_issue == "t1"
    assert sent.model_dump(exclude_none=True) == {"related_issue": "t1"}


def test_delete_dependency_404_raises_unsupported_operation_error(registered, spy):
    """No released Plane version registers the relations/remove route yet

    (makeplane/plane#10091 adds it). A 404 there must surface as a clear,
    actionable error -- not the raw SDK HttpError -- so the caller knows this
    is a server limitation, not something wrong with the request.
    """
    spy.returns["work_items.relations.delete"] = HttpError("nope", status_code=404)
    with pytest.raises(ToolError, match="not supported on this Plane server"):
        _call(
            registered,
            action="delete",
            project_id="p",
            workitem_id="w",
            related_workitem_id="t1",
            is_dependency=True,
        )


def test_delete_dependency_propagates_non_404_errors(registered, spy):
    spy.returns["work_items.relations.delete"] = HttpError("boom", status_code=500)
    with pytest.raises(HttpError):
        _call(
            registered,
            action="delete",
            project_id="p",
            workitem_id="w",
            related_workitem_id="t1",
            is_dependency=True,
        )


def test_delete_custom_relation_is_unaffected_by_the_route_change(registered, spy):
    """The default (is_dependency=False) path never used /dependencies/."""
    _call(
        registered,
        action="delete",
        project_id="p",
        workitem_id="w",
        related_workitem_id="t1",
    )
    call = spy.recorder.only()
    assert call.method == "work_items.custom_relations.remove"
    assert call.kwargs["related_work_item_id"] == "t1"


# --- list_definitions: the built-in set must survive a 404 on definitions ---


def test_list_definitions_absorbs_a_404_on_the_definitions_route(registered, spy):
    spy.returns["work_item_relation_definitions.list"] = HttpError("nope", status_code=404)
    result = _call(registered, action="list_definitions")
    assert result["custom_definitions"] == []
    assert result["built_in_dependencies"]  # the six built-in types are not routed, always present


def test_list_definitions_propagates_non_404_errors(registered, spy):
    spy.returns["work_item_relation_definitions.list"] = HttpError("boom", status_code=500)
    with pytest.raises(HttpError):
        _call(registered, action="list_definitions")
