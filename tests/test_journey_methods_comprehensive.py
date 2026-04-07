import pytest
from unittest.mock import MagicMock, patch

from plane_mcp.resolver import EntityResolver
from plane_mcp.journey.tools.read import ReadJourney
from plane_mcp.journey.tools.create_update import CreateUpdateJourney
from plane_mcp.journey.tools.workflow import WorkflowJourney

@pytest.fixture
def mock_client():
    return MagicMock()

@pytest.fixture
def resolver(mock_client):
    res = EntityResolver(mock_client, "test-workspace")
    res._project_cache = {"TEST": "proj-123"}
    res._work_item_cache = {"TEST-1": "ticket-1", "TEST-2": "ticket-2"}
    return res

# -------------------------------------------------------------------
# READ JOURNEY TESTS
# -------------------------------------------------------------------

@patch('plane_mcp.journey.tools.read.get_plane_client_context')
def test_search_tickets_success(mock_get_context, resolver, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = ReadJourney(resolver)
    
    # Mock _get response
    mock_item = {
        "id": "ticket-1",
        "name": "Test Ticket with filter keyword",
        "sequence_id": 1,
        "project_detail": {"identifier": "TEST"},
        "project_id": "proj-123",
        "state_detail": {"name": "In Progress"},
        "priority": "high",
        "assignees": []
    }
    
    mock_client.work_items._get.return_value = {
        "results": [mock_item],
        "next_cursor": "",
        "prev_cursor": "",
        "total_count": 1,
        "next_page_results": False,
        "prev_page_results": False,
        "count": 1,
        "total_pages": 1,
        "total_results": 1
    }
    
    result = journey.search_tickets(
        project_slug="TEST", 
        query="filter", 
        priority=["high"],
        limit=10,
        lod="summary"
    )
    
    mock_client.work_items._get.assert_called_once()
    args, kwargs = mock_client.work_items._get.call_args
    assert args[0] == "test-workspace/projects/proj-123/work-items"
    assert kwargs["params"]["priority"] == "high"
    assert kwargs["params"]["per_page"] == 100
    
    assert len(result["results"]) == 1
    assert result["results"][0]["key"] == "TEST-1"
    assert result["results"][0]["name"] == "Test Ticket with filter keyword"

@patch('plane_mcp.journey.tools.read.get_plane_client_context')
def test_read_ticket_success(mock_get_context, resolver, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = ReadJourney(resolver)
    
    mock_item = MagicMock()
    mock_item.id = "ticket-1"
    mock_item.name = "Test Ticket"
    mock_item.sequence_id = 1
    mock_item.project_detail = {"identifier": "TEST"}
    mock_item.project_id = "proj-123"
    mock_item.state_detail = {"name": "In Progress"}
    mock_item.priority = "high"
    mock_item.description_html = "<p>Desc</p>"
    mock_item.labels = []
    mock_item.model_dump.return_value = {"id": "ticket-1", "name": "Test Ticket", "sequence_id": 1, "project_detail": {"identifier": "TEST"}, "state_detail": {"name": "In Progress"}, "priority": "high", "description_html": "<p>Desc</p>", "labels": []}
    
    mock_client.work_items.retrieve.return_value = mock_item
    
    result = journey.read_ticket("TEST-1", "standard")
    
    mock_client.work_items.retrieve.assert_called_once()
    
    assert result["ticket_id"] == "TEST-1"
    assert result["name"] == "Test Ticket"
    assert result["description"] == "Desc"


@patch('plane_mcp.journey.tools.read.get_plane_client_context')
def test_read_ticket_with_comments(mock_get_context, resolver, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = ReadJourney(resolver)
    
    mock_item = MagicMock()
    mock_item.id = "ticket-1"
    mock_item.name = "Test Ticket"
    mock_item.sequence_id = 1
    mock_item.project_detail = {"identifier": "TEST"}
    mock_item.project_id = "proj-123"
    mock_item.state_detail = {"name": "In Progress"}
    mock_item.priority = "high"
    mock_item.description_html = "<p>Desc</p>"
    mock_item.labels = []
    mock_item.model_dump.return_value = {"id": "ticket-1", "name": "Test Ticket", "sequence_id": 1, "project_detail": {"identifier": "TEST"}, "state_detail": {"name": "In Progress"}, "priority": "high", "description_html": "<p>Desc</p>", "labels": []}
    
    mock_client.work_items.retrieve.return_value = mock_item
    
    # Mock comments
    mock_comment = MagicMock()
    mock_comment.created_at = "2026-03-24T12:00:00Z"
    mock_comment.actor_detail.display_name = "Adam Outler"
    mock_comment.comment_stripped = "This is a comment."
    mock_comment_resp = MagicMock()
    mock_comment_resp.results = [mock_comment]
    mock_client.work_items.comments.list.return_value = mock_comment_resp
    
    result = journey.read_ticket("TEST-1", "standard", comments=True)
    
    mock_client.work_items.retrieve.assert_called_once()
    mock_client.work_items.comments.list.assert_called_once()
    
    assert result["ticket_id"] == "TEST-1"
    assert "comments" in result
    assert "2026-03-24-@Adam Outler:\nThis is a comment." in result["comments"]


# -------------------------------------------------------------------
# CREATE / UPDATE JOURNEY TESTS
# -------------------------------------------------------------------

@patch('plane_mcp.journey.tools.create_update.get_plane_client_context')
def test_update_ticket_with_comment(mock_get_context, resolver, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = CreateUpdateJourney(resolver)
    
    mock_current = {"id": "ticket-1", "name": "Old Title", "description_html": "<p>Old Description</p>"}
    mock_client.work_items._get.return_value = mock_current
    
    mock_updated = MagicMock()
    mock_updated.id = "ticket-1"
    mock_client.work_items.update.return_value = mock_updated
    
    result = journey.update_ticket(ticket_id="TEST-1", new_title="New Title", comment="Adding an update comment.")
    
    assert result.get("key") == "TEST-1"
    
    # Verify update was called
    update_args = mock_client.work_items.update.call_args[1]
    assert update_args["data"].name == "New Title"
    
    # Verify comment was created
    mock_client.work_items.comments.create.assert_called_once()
    comment_args = mock_client.work_items.comments.create.call_args[1]
    assert comment_args["data"].comment_html == "<p>Adding an update comment.</p>"

@patch('plane_mcp.journey.tools.create_update.get_plane_client_context')
def test_update_ticket_empty_dict(mock_get_context, resolver, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = CreateUpdateJourney(resolver)
    
    mock_current = {"id": "ticket-1", "name": "Old Title", "description_html": "<p>Old Description</p>"}
    mock_client.work_items._get.return_value = mock_current
    
    mock_updated = MagicMock()
    mock_updated.id = "ticket-1"
    mock_client.work_items.update.return_value = mock_updated
    
    # Should handle empty dictionaries gracefully without throwing a 500
    result = journey.update_ticket(ticket_id="TEST-1")
    
    assert result.get("status") == "warning"
    assert "No changes were provided" in result.get("message")
    
    # Verify update was NOT called
    assert not mock_client.work_items.update.called

@patch('plane_mcp.journey.tools.create_update.get_plane_client_context')
def test_create_ticket_with_all_params(mock_get_context, resolver, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = CreateUpdateJourney(resolver)
    
    # Mock user for assignments
    mock_me = type('User', (), {'id': 'user-123'})()
    mock_client.users.get_me.return_value = mock_me
    
    # Mock state resolution
    mock_state = MagicMock()
    mock_state.name = "Todo"
    mock_state.id = "state-123"
    mock_state_resp = MagicMock(); mock_state_resp.results = [mock_state]; mock_client.states.list.return_value = mock_state_resp
    
    # Mock label resolution (not found, creates new)
    mock_label_resp = MagicMock(); mock_label_resp.results = []; mock_client.labels.list.return_value = mock_label_resp
    mock_new_label = MagicMock()
    mock_new_label.id = "label-123"
    mock_client.labels.create.return_value = mock_new_label
    
    # Mock cycle resolution
    mock_cycle_resp = MagicMock(); mock_cycle_resp.results = []; mock_client.cycles.list.return_value = mock_cycle_resp
    mock_new_cycle = MagicMock()
    mock_new_cycle.id = "cycle-123"
    mock_client.cycles.create.return_value = mock_new_cycle
    
    # Mock create
    mock_created = MagicMock()
    mock_created.id = "ticket-new"
    mock_created.name = "New Feature"
    mock_created.sequence_id = 99
    mock_created.project_detail = {"identifier": "TEST"}
    mock_created.state_detail = {"name": "Todo"}
    mock_created.priority = "none"
    mock_created.assignees = []
    mock_created.model_dump.return_value = {
        "id": "ticket-new",
        "name": "New Feature",
        "sequence_id": 99,
        "project_detail": {"identifier": "TEST"},
        "state_detail": {"name": "Todo"},
        "priority": "none",
        "assignees": []
    }

    mock_client.work_items.create.return_value = mock_created    
    result = journey.create_ticket(
        title="New Feature",
        project_slug="TEST",
        state_name="Todo",
        labels=["backend"],
        cycle_name="Sprint 1"
    )
    
    # Verify label creation
    assert mock_client.labels.create.called
    
    # Verify cycle creation
    assert mock_client.cycles.create.called
    
    # Verify ticket creation params
    create_args = mock_client.work_items.create.call_args[1]
    assert create_args["workspace_slug"] == "test-workspace"
    assert create_args["project_id"] == "proj-123"
    assert create_args["data"].name == "New Feature"
    assert create_args["data"].state == "state-123"
    assert create_args["data"].labels == ["label-123"]    
    # Verify link to cycle
    assert mock_client.cycles.add_work_items.called
    
    assert result["key"] == "TEST-99"


# -------------------------------------------------------------------
# WORKFLOW JOURNEY TESTS
# -------------------------------------------------------------------

@patch('plane_mcp.journey.tools.workflow.get_plane_client_context')
def test_transition_ticket_success(mock_get_context, resolver, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = WorkflowJourney(resolver)
    
    # Mock state lookup
    mock_state = MagicMock()
    mock_state.name = "In Progress"
    mock_state.id = "state-456"
    mock_state_resp = MagicMock(); mock_state_resp.results = [mock_state]; mock_client.states.list.return_value = mock_state_resp
    
    # Mock update
    mock_updated = MagicMock()
    mock_updated.id = "ticket-1"
    mock_updated.name = "Ticket"
    mock_updated.sequence_id = 1
    mock_updated.project_detail = {"identifier": "TEST"}
    mock_updated.state_detail = {"name": "In Progress"}
    mock_updated.priority = "none"
    mock_updated.assignees = []
    mock_updated.model_dump.return_value = {"state_detail": {"name": "In Progress"}}
    mock_client.work_items.update.return_value = mock_updated
    
    result = journey.transition_ticket("TEST-1", "In Progress")
    
    # Verify state lookup
    mock_client.states.list.assert_called_once_with(workspace_slug="test-workspace", project_id="proj-123")
    
    # Verify update payload
    update_args = mock_client.work_items.update.call_args[1]
    assert update_args["data"].state == "state-456"
    
    assert result["state"] == "In Progress"


@patch('plane_mcp.journey.tools.workflow.get_plane_client_context')
def test_begin_work_success(mock_get_context, resolver, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = WorkflowJourney(resolver)
    
    # Setup mocks
    mock_cycle_resp = MagicMock(); mock_cycle_resp.results = []; mock_client.cycles.list.return_value = mock_cycle_resp
    mock_new_cycle = MagicMock()
    mock_new_cycle.id = "cycle-123"
    mock_client.cycles.create.return_value = mock_new_cycle
    
    mock_state = MagicMock()
    mock_state.name = "In Progress"
    mock_state.id = "state-in-prog"
    mock_state_resp = MagicMock(); mock_state_resp.results = [mock_state]; mock_client.states.list.return_value = mock_state_resp
    
    mock_updated = MagicMock()
    mock_updated.sequence_id = 1
    mock_updated.project_detail = {"identifier": "TEST"}
    mock_updated.state_detail = {"name": "In Progress"}
    mock_updated.priority = "none"
    mock_updated.assignees = []
    mock_updated.model_dump.return_value = {"state_detail": {"name": "In Progress"}}
    mock_client.work_items.update.return_value = mock_updated
    
    mock_me = type("User", (), {"id": "user-123"})()
    mock_client.users.get_me.return_value = mock_me
    result = journey.begin_work(["TEST-1"], "Sprint 2")
    
    assert result["status"] == "success"
    assert "TEST" in result["details"]
    
    # Verify links
    mock_client.cycles.add_work_items.assert_called_once()
    
    # Verify state transition
    update_args = mock_client.work_items.update.call_args[1]
    assert update_args["data"].state == "state-in-prog"

@patch('plane_mcp.journey.tools.workflow.get_plane_client_context')
def test_begin_work_cycles_disabled(mock_get_context, resolver, mock_client):
    from plane.errors.errors import HttpError
    
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = WorkflowJourney(resolver)
    
    # Setup mock to simulate disabled cycles (HTTP 400)
    mock_error = HttpError(message="Bad Request", status_code=400)
    mock_client.cycles.list.side_effect = mock_error
    
    # Setup state transition mocks
    mock_state = MagicMock()
    mock_state.name = "In Progress"
    mock_state.id = "state-in-prog"
    mock_state_resp = MagicMock(); mock_state_resp.results = [mock_state]; mock_client.states.list.return_value = mock_state_resp
    
    mock_updated = MagicMock()
    mock_updated.sequence_id = 1
    mock_updated.project_detail = {"identifier": "TEST"}
    mock_updated.state_detail = {"name": "In Progress"}
    mock_updated.priority = "none"
    mock_updated.assignees = []
    mock_updated.model_dump.return_value = {"state_detail": {"name": "In Progress"}}
    mock_client.work_items.update.return_value = mock_updated
    
    result = journey.begin_work(["TEST-1"], "Sprint 2")
    
    # Verify success despite disabled cycles
    assert result["status"] == "success"
    assert "Cycles are disabled" in result["details"]["TEST"]
    
    # Verify cycle addition was skipped
    assert not mock_client.cycles.add_work_items.called
    
    # Verify state transition still happened
    update_args = mock_client.work_items.update.call_args[1]
    assert update_args["data"].state == "state-in-prog"

@patch('plane_mcp.journey.tools.workflow.get_plane_client_context')
def test_complete_work_success(mock_get_context, resolver, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = WorkflowJourney(resolver)
    
    # Setup mock comment creation
    mock_client.work_items.comments.create.return_value = {"id": "comment-1"}
    
    # Setup mock state transition
    mock_state = MagicMock()
    mock_state.name = "Done"
    mock_state.id = "state-done"
    mock_state_resp = MagicMock(); mock_state_resp.results = [mock_state]; mock_client.states.list.return_value = mock_state_resp
    
    mock_updated = MagicMock()
    mock_updated.sequence_id = 1
    mock_updated.model_dump.return_value = {"state_detail": {"name": "Done"}}
    mock_client.work_items.update.return_value = mock_updated
    
    result = journey.complete_work("TEST-1", "Finished implementing feature")
    
    # Verify comment was created
    comment_args = mock_client.work_items.comments.create.call_args[1]
    assert comment_args["data"].comment_html == "<p>Finished implementing feature</p>"
    
    # Verify state was changed
    assert result["state"] == "Done"


@patch('plane_mcp.journey.tools.workflow.get_plane_client_context')
def test_complete_work_no_done_state_returns_partial(mock_get_context, resolver, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = WorkflowJourney(resolver)

    mock_client.work_items.comments.create.return_value = {"id": "comment-1"}

    # No Done or Completed state exists
    mock_state = MagicMock(); mock_state.name = "In Progress"; mock_state.id = "state-ip"
    mock_client.states.list.return_value = MagicMock(results=[mock_state])

    result = journey.complete_work("TEST-1", "Work is done")

    assert result["status"] == "partial"
    assert "transition_ticket" in result["message"]
    mock_client.work_items.comments.create.assert_called_once()
    mock_client.work_items.update.assert_not_called()


@patch('plane_mcp.journey.tools.read.get_plane_client_context')
def test_search_tickets_unknown_label_returns_warning(mock_get_context, resolver, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = ReadJourney(resolver)

    # Only 'bug' exists; 'phantom' does not
    existing_label = MagicMock(); existing_label.name = "bug"; existing_label.id = "lbl-1"
    mock_client.labels.list.return_value = MagicMock(results=[existing_label])

    mock_client.work_items._get.return_value = {
        "results": [],
        "next_cursor": "",
        "prev_cursor": "",
        "total_count": 0,
        "next_page_results": False,
        "prev_page_results": False,
        "count": 0,
        "total_pages": 1,
        "total_results": 0
    }

    result = journey.search_tickets(project_slug="TEST", labels=["bug", "phantom"])

    assert "warnings" in result
    assert any("phantom" in w for w in result["warnings"])
