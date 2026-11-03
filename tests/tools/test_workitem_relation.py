"""`workitem_relation` against self-hosted Community Edition (#185).

Community Edition serves one relations route, GET and POST on
`/work-items/{id}/relations/`. It has no `/dependencies/`, no
`/work-item-relations/` and no relation definitions, so every other action is
refused by name rather than reaching the network for a bare 404.
"""

from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError
from plane.errors.errors import HttpError

CE_RESPONSE = {
    "blocking": [],
    "blocked_by": [{"project_id": "proj-1", "issue_id": "wi-2"}],
    "duplicate": [],
    "relates_to": [],
    "start_after": [],
    "start_before": [],
    "finish_after": [],
    "finish_before": [],
}


@pytest.fixture
def community(monkeypatch):
    monkeypatch.setenv("PLANE_COMMUNITY_EDITION", "true")


def _relation(registered, **kwargs):
    return registered["workitem_relation"].fn(**kwargs)


# --- Community Edition: list and create use /relations/ ---


def test_list_reads_the_relations_route(community, registered, spy):
    spy.returns["work_items.relations._get"] = CE_RESPONSE
    result = _relation(registered, action="list", project_id="proj-1", workitem_id="wi-1")
    call = spy.recorder.only()
    assert call.method == "work_items.relations._get"
    assert call.kwargs["endpoint"] == "acme/projects/proj-1/work-items/wi-1/relations"
    assert result == {"relations": CE_RESPONSE}


def test_list_does_not_swallow_an_error(community, registered, spy):
    spy.returns["work_items.relations._get"] = HttpError("Not Found", status_code=404)
    with pytest.raises(HttpError):
        _relation(registered, action="list", project_id="proj-1", workitem_id="wi-1")


@pytest.mark.parametrize("relation_type", ["blocked_by", "blocking", "relates_to", "duplicate", "finish_before"])
def test_create_posts_to_the_relations_route_with_issues(relation_type, community, registered, spy):
    _relation(
        registered,
        action="create",
        project_id="proj-1",
        workitem_id="wi-1",
        workitem_ids=["wi-2", "wi-3"],
        relation_type=relation_type,
    )
    call = spy.recorder.only()
    assert call.method == "work_items.relations.create"
    assert call.kwargs["work_item_id"] == "wi-1"
    assert call.kwargs["data"].model_dump() == {"relation_type": relation_type, "issues": ["wi-2", "wi-3"]}


def test_create_refuses_an_unknown_type_before_plane_sees_it(community, registered, spy):
    result = _relation(
        registered, action="create", project_id="p", workitem_id="w", workitem_ids=["x"], relation_type="parent_of"
    )
    assert result.startswith("Error: relation_type must be one of:") and "relates_to" in result
    assert spy.recorder.calls == []


def test_create_requires_a_relation_type(community, registered, spy):
    result = _relation(registered, action="create", project_id="p", workitem_id="w", workitem_ids=["x"])
    assert result.startswith("Error:") and "relation_type" in result
    assert spy.recorder.calls == []


def test_create_refuses_a_custom_relation(community, registered, spy):
    with pytest.raises(ToolError, match="Community Edition has no endpoint for custom relations"):
        _relation(
            registered,
            action="create",
            project_id="p",
            workitem_id="w",
            workitem_ids=["x"],
            relation_definition_id="def-1",
            relation_definition_label="causes",
        )
    assert spy.recorder.calls == []


@pytest.mark.parametrize(
    ("action", "kwargs"),
    [
        ("delete", {"project_id": "p", "workitem_id": "w", "related_workitem_id": "x", "is_dependency": True}),
        ("list_definitions", {}),
        ("create_definition", {"name": "causes"}),
        ("update_definition", {"definition_id": "def-1", "name": "causes"}),
        ("delete_definition", {"definition_id": "def-1"}),
    ],
)
def test_actions_without_an_endpoint_are_refused_by_name(action, kwargs, community, registered, spy):
    with pytest.raises(ToolError, match=f"no endpoint for the {action} action") as raised:
        _relation(registered, action=action, **kwargs)
    assert "blocked_by" in str(raised.value)
    assert spy.recorder.calls == []


# --- without the setting, nothing changes ---


def test_list_uses_dependencies_and_custom_relations_by_default(registered, spy, monkeypatch):
    monkeypatch.delenv("PLANE_COMMUNITY_EDITION", raising=False)
    _relation(registered, action="list", project_id="p", workitem_id="w")
    assert spy.recorder.methods == ["work_items.dependencies.list", "work_items.custom_relations.list"]


def test_create_uses_dependencies_by_default(registered, spy, monkeypatch):
    monkeypatch.delenv("PLANE_COMMUNITY_EDITION", raising=False)
    _relation(
        registered, action="create", project_id="p", workitem_id="w", workitem_ids=["x"], relation_type="blocking"
    )
    assert spy.recorder.only().method == "work_items.dependencies.create"
