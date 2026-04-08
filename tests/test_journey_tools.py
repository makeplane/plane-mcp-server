import pytest
from unittest.mock import MagicMock, patch

from plane_mcp.resolver import EntityResolver
from plane_mcp.journey.tools.create_update import CreateUpdateJourney
from plane_mcp.journey.tools.workflow import WorkflowJourney

import time
import plane_mcp.resolver

@pytest.fixture
def mock_client():
    client = MagicMock()
    return client

@pytest.fixture
def resolver(mock_client):
    res = EntityResolver(mock_client, "test-workspace")
    # Pre-populate workspace-scoped cache so we don't need to mock projects.list everywhere
    plane_mcp.resolver._GLOBAL_PROJECT_CACHE["test-workspace"].update({"ENG": "proj-1"})
    plane_mcp.resolver._GLOBAL_WORK_ITEM_CACHE["test-workspace"].update({"ENG-1": "ticket-1", "ENG-2": "ticket-2"})
    plane_mcp.resolver._CACHE_LAST_UPDATED["test-workspace"]["projects"] = time.time()
    plane_mcp.resolver._CACHE_LAST_UPDATED["test-workspace"]["work_items"] = time.time()
    return res

@patch('plane_mcp.journey.tools.create_update.get_plane_client_context')
def test_update_ticket_occ_success(mock_get_context, resolver, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = CreateUpdateJourney(resolver)
    
    # Mock retrieve
    resolver.resolve_ticket = MagicMock(return_value="ticket-1")
    resolver.resolve_project = MagicMock(return_value="project-1")
    current_ticket = {"id": "ticket-1", "name": "Old Title", "description_html": "<p>Old Description</p>"}
    mock_client.work_items._get.return_value = current_ticket
    
    # Mock update
    mock_client.work_items.update.return_value = {"id": "ticket-1", "name": "New Title", "sequence_id": 1, "project_detail": {"identifier": "ENG"}}
    
    # Run update
    result = journey.update_ticket(
        "ENG-1",
        replace_text="New Description",
        replace_target_snippet="<p>Old Description</p>",
        new_title="New Title"
    )    
    assert mock_client.work_items.update.called
    update_data = mock_client.work_items.update.call_args[1]["data"]
    assert update_data.name == "New Title"
    assert update_data.description_html == "New Description"



@patch('plane_mcp.journey.tools.create_update.get_plane_client_context')
def test_update_ticket_append(mock_get_context, resolver, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = CreateUpdateJourney(resolver)
    
    resolver.resolve_ticket = MagicMock(return_value="ticket-1")
    resolver.resolve_project = MagicMock(return_value="project-1")
    current_ticket = {"id": "ticket-1", "name": "Title", "description_html": "<p>Old Description</p>"}
    mock_client.work_items._get.return_value = current_ticket

    result = journey.update_ticket(
        ticket_id="TEST-1",
        append_text="Appended Text"
    )
    assert mock_client.work_items.update.called
    update_data = mock_client.work_items.update.call_args[1]["data"]
    assert update_data.description_html == "<p>Old Description</p><br><br>Appended Text"



@patch('plane_mcp.journey.tools.workflow.get_plane_client_context')
def test_begin_work_batch(mock_get_context, resolver, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    
    # Mock cycle list to return empty (so it creates one)
    mock_cycle_resp = MagicMock()
    mock_cycle_resp.results = []
    mock_client.cycles.list.return_value = mock_cycle_resp
    
    # Mock me
    me = MagicMock()
    me.id = "user-1"
    mock_client.users.get_me.return_value = me
    
    # Mock cycle create
    new_cycle = MagicMock()
    new_cycle.id = "cycle-1"
    mock_client.cycles.create.return_value = new_cycle
    
    # Pre-populate state cache for "In Progress"
    plane_mcp.resolver._GLOBAL_STATE_CACHE.setdefault("test-workspace", {}).update({"proj-1": {"in progress": "state-ip"}})
    plane_mcp.resolver._CACHE_LAST_UPDATED.setdefault("test-workspace", {})["states"] = time.time()
    
    journey = WorkflowJourney(resolver)
    
    res = journey.begin_work(["ENG-1", "ENG-2"], "Sprint 1")
    
    assert res["status"] == "success"
    assert "ENG" in res["details"]
    
    # Verify cycle created
    assert mock_client.cycles.create.called
    create_args = mock_client.cycles.create.call_args[1]["data"]
    assert create_args.name == "Sprint 1"
    
    # Verify add to cycle
    assert mock_client.cycles.add_work_items.called
    add_args = mock_client.cycles.add_work_items.call_args[1]
    assert add_args["issue_ids"] == ["ticket-1", "ticket-2"]
    assert add_args["cycle_id"] == "cycle-1"
    
    # Verify state transition to In Progress
    assert mock_client.work_items.update.call_count == 2
    update_calls = mock_client.work_items.update.call_args_list
    assert update_calls[0][1]["data"].state == "state-ip"
    assert update_calls[1][1]["data"].state == "state-ip"

@patch('plane_mcp.journey.tools.workflow.get_plane_client_context')
def test_cycle_creation_http_400_fallback(mock_get_context, resolver, mock_client):
    from plane.errors.errors import HttpError
    mock_get_context.return_value = (mock_client, "test-workspace")
    
    # Mock cycle list to return empty (so it creates one)
    mock_cycle_resp = MagicMock()
    mock_cycle_resp.results = []
    mock_client.cycles.list.return_value = mock_cycle_resp
    
    # Mock cycle create throwing 400
    err = HttpError(message="Bad Request", status_code=400)
    mock_client.cycles.create.side_effect = err
    
    # Mock users.get_me() so me.id is a string (prevents PyDantic ValidationError)
    mock_me = MagicMock()
    mock_me.id = "user-123"
    mock_client.users.get_me.return_value = mock_me
    
    journey = WorkflowJourney(resolver)
    
    # Must NOT throw exception!
    res = journey._resolve_or_create_cycle("proj-1", "Sprint 1")
    assert res is None
