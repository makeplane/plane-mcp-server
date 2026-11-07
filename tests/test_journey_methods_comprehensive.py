"""Comprehensive unit tests for ReadJourney, CreateUpdateJourney, and WorkflowJourney."""

import time
from unittest.mock import MagicMock, patch

import pytest

from plane_mcp.journey.tools.create_update import CreateUpdateJourney
from plane_mcp.journey.tools.read import ReadJourney
from plane_mcp.journey.tools.workflow import WorkflowJourney
from plane_mcp.resolver import EntityResolver


@pytest.fixture
def mock_client():
    """Return a fresh MagicMock representing the Plane SDK client."""
    return MagicMock()


@pytest.fixture
def resolver(mock_client):
    """Return an EntityResolver pre-populated with TEST project and work item cache entries."""
    res = EntityResolver(mock_client, "test-workspace")
    res._project_cache.clear()
    res._project_cache.update({"TEST": "proj-123"})
    res._work_item_cache.clear()
    res._work_item_cache.update({"TEST-1": "ticket-1", "TEST-2": "ticket-2"})
    res._state_cache.clear()
    res._ts["projects"] = time.time()
    res._ts["work_items"] = time.time()
    res._ts["states"] = 0.0
    return res


# -------------------------------------------------------------------
# READ JOURNEY TESTS
# -------------------------------------------------------------------


@patch("plane_mcp.journey.tools.read.get_plane_client_context")
def test_search_tickets_success(mock_get_context, resolver, mock_client):
    """Verify search_tickets filters by query and priority and transforms results to LOD summary."""
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
        "assignees": [],
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
        "total_results": 1,
    }

    result = journey.search_tickets(project_slug="TEST", query="filter", priority=["high"], limit=10, lod="summary")

    mock_client.work_items._get.assert_called_once()
    args, kwargs = mock_client.work_items._get.call_args
    assert args[0] == "test-workspace/projects/proj-123/work-items"
    assert kwargs["params"]["priority"] == "high"
    assert kwargs["params"]["per_page"] == 10  # per_page is now min(limit, 100)

    assert len(result["results"]) == 1
    assert result["results"][0]["issue_key"] == "TEST-1"
    assert result["results"][0]["name"] == "Test Ticket with filter keyword"


@patch("plane_mcp.journey.tools.read.get_plane_client_context")
def test_read_ticket_success(mock_get_context, resolver, mock_client):
    """Verify read_ticket retrieves ticket details and strips HTML for standard LOD."""
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
    mock_item.model_dump.return_value = {
        "id": "ticket-1",
        "name": "Test Ticket",
        "sequence_id": 1,
        "project_detail": {"identifier": "TEST"},
        "state_detail": {"name": "In Progress"},
        "priority": "high",
        "description_html": "<p>Desc</p>",
        "labels": [],
    }

    mock_client.work_items.retrieve.return_value = mock_item

    result = journey.read_ticket("TEST-1", "standard")

    mock_client.work_items.retrieve.assert_called_once()

    assert result["issue_key"] == "TEST-1"
    assert result["name"] == "Test Ticket"
    assert result["description"] == "Desc"


@patch("plane_mcp.journey.tools.read.get_plane_client_context")
def test_read_ticket_with_comments(mock_get_context, resolver, mock_client):
    """Verify read_ticket appends formatted comments when comments=True."""
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
    mock_item.model_dump.return_value = {
        "id": "ticket-1",
        "name": "Test Ticket",
        "sequence_id": 1,
        "project_detail": {"identifier": "TEST"},
        "state_detail": {"name": "In Progress"},
        "priority": "high",
        "description_html": "<p>Desc</p>",
        "labels": [],
    }

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

    assert result["issue_key"] == "TEST-1"
    assert "comments" in result
    assert "2026-03-24-@Adam Outler:\nThis is a comment." in result["comments"]


# -------------------------------------------------------------------
# CREATE / UPDATE JOURNEY TESTS
# -------------------------------------------------------------------


@patch("plane_mcp.journey.tools.create_update.get_plane_client_context")
def test_update_ticket_with_comment(mock_get_context, resolver, mock_client):
    """Verify update_ticket updates fields and creates a sanitized HTML comment."""
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = CreateUpdateJourney(resolver)

    mock_current = {"id": "ticket-1", "name": "Old Title", "description_html": "<p>Old Description</p>"}
    mock_client.work_items._get.return_value = mock_current

    mock_updated = MagicMock()
    mock_updated.id = "ticket-1"
    mock_client.work_items.update.return_value = mock_updated

    result = journey.update_ticket(ticket_id="TEST-1", new_title="New Title", comment="Adding an update comment.")

    assert result.get("issue_key") == "TEST-1"

    # Verify update was called
    update_args = mock_client.work_items.update.call_args[1]
    assert update_args["data"].name == "New Title"

    # Verify comment was created
    mock_client.work_items.comments.create.assert_called_once()
    comment_args = mock_client.work_items.comments.create.call_args[1]
    assert comment_args["data"].comment_html == "<p>Adding an update comment.</p>"


@patch("plane_mcp.journey.tools.create_update.get_plane_client_context")
def test_update_ticket_empty_dict(mock_get_context, resolver, mock_client):
    """Verify update_ticket returns a warning when no modification fields are provided."""
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


@patch("plane_mcp.journey.tools.create_update.get_plane_client_context")
def test_create_ticket_with_all_params(mock_get_context, resolver, mock_client):
    """Verify create_ticket resolves state, creates missing labels/cycles, and links the new ticket."""
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = CreateUpdateJourney(resolver)

    # Mock user for assignments
    mock_me = type("User", (), {"id": "user-123"})()
    mock_client.users.get_me.return_value = mock_me

    # Mock state resolution
    mock_state = MagicMock()
    mock_state.name = "Todo"
    mock_state.id = "state-123"
    mock_state_resp = MagicMock()
    mock_state_resp.results = [mock_state]
    mock_client.states.list.return_value = mock_state_resp

    # Mock label resolution (not found, creates new)
    mock_label_resp = MagicMock()
    mock_label_resp.results = []
    mock_client.labels.list.return_value = mock_label_resp
    mock_new_label = MagicMock()
    mock_new_label.id = "label-123"
    mock_client.labels.create.return_value = mock_new_label

    # Mock cycle resolution
    mock_cycle_resp = MagicMock()
    mock_cycle_resp.results = []
    mock_client.cycles.list.return_value = mock_cycle_resp
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
        "assignees": [],
    }

    mock_client.work_items.create.return_value = mock_created
    result = journey.create_ticket(
        title="New Feature", project_slug="TEST", state_name="Todo", labels=["backend"], cycle_name="Sprint 1"
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

    assert result["issue_key"] == "TEST-99"


# -------------------------------------------------------------------
# WORKFLOW JOURNEY TESTS
# -------------------------------------------------------------------


@patch("plane_mcp.journey.tools.workflow.get_plane_client_context")
def test_transition_ticket_success(mock_get_context, resolver, mock_client):
    """Verify transition_ticket resolves state by name and updates the ticket."""
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = WorkflowJourney(resolver)

    # Mock state lookup
    mock_state = MagicMock()
    mock_state.name = "In Progress"
    mock_state.id = "state-456"
    mock_state_resp = MagicMock()
    mock_state_resp.results = [mock_state]
    mock_client.states.list.return_value = mock_state_resp

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


@patch("plane_mcp.journey.tools.workflow.get_plane_client_context")
def test_begin_work_success(mock_get_context, resolver, mock_client):
    """Verify begin_work assigns tickets to a cycle and transitions them to In Progress."""
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = WorkflowJourney(resolver)

    # Setup mocks
    mock_cycle_resp = MagicMock()
    mock_cycle_resp.results = []
    mock_client.cycles.list.return_value = mock_cycle_resp
    mock_new_cycle = MagicMock()
    mock_new_cycle.id = "cycle-123"
    mock_client.cycles.create.return_value = mock_new_cycle

    mock_state = MagicMock()
    mock_state.name = "In Progress"
    mock_state.id = "state-in-prog"
    mock_state_resp = MagicMock()
    mock_state_resp.results = [mock_state]
    mock_client.states.list.return_value = mock_state_resp

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


@patch("plane_mcp.journey.tools.workflow.get_plane_client_context")
def test_begin_work_cycles_disabled(mock_get_context, resolver, mock_client):
    """Verify begin_work skips cycle creation gracefully on HTTP 400 and still transitions state."""
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
    mock_state_resp = MagicMock()
    mock_state_resp.results = [mock_state]
    mock_client.states.list.return_value = mock_state_resp

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


@patch("plane_mcp.journey.tools.workflow.get_plane_client_context")
def test_complete_work_success(mock_get_context, resolver, mock_client):
    """Verify complete_work posts a comment and transitions the ticket to Done."""
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = WorkflowJourney(resolver)

    # Setup mock comment creation
    mock_client.work_items.comments.create.return_value = {"id": "comment-1"}

    # Setup mock state transition
    mock_state = MagicMock()
    mock_state.name = "Done"
    mock_state.id = "state-done"
    mock_state_resp = MagicMock()
    mock_state_resp.results = [mock_state]
    mock_client.states.list.return_value = mock_state_resp

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


@patch("plane_mcp.journey.tools.workflow.get_plane_client_context")
def test_complete_work_no_done_state_returns_partial(mock_get_context, resolver, mock_client):
    """Verify complete_work returns partial status when neither Done nor Completed state exists."""
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = WorkflowJourney(resolver)

    mock_client.work_items.comments.create.return_value = {"id": "comment-1"}

    # No Done or Completed state exists
    mock_state = MagicMock()
    mock_state.name = "In Progress"
    mock_state.id = "state-ip"
    mock_client.states.list.return_value = MagicMock(results=[mock_state])

    result = journey.complete_work("TEST-1", "Work is done")

    assert result["status"] == "partial"
    assert result["comment_added"] is True
    assert "transition_ticket" in result["message"]
    mock_client.work_items.comments.create.assert_called_once()
    mock_client.work_items.update.assert_not_called()


@patch("plane_mcp.journey.tools.read.get_plane_client_context")
def test_search_tickets_unknown_label_returns_warning(mock_get_context, resolver, mock_client):
    """Verify search_tickets returns a warning when some requested labels resolve and others do not."""
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = ReadJourney(resolver)

    # Only 'bug' exists; 'phantom' does not
    existing_label = MagicMock()
    existing_label.name = "bug"
    existing_label.id = "lbl-1"
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
        "total_results": 0,
    }

    result = journey.search_tickets(project_slug="TEST", labels=["bug", "phantom"])

    assert "warnings" in result
    assert any("phantom" in w for w in result["warnings"])


@patch("plane_mcp.journey.tools.read.get_plane_client_context")
def test_search_tickets_preserves_excess_matches_across_pages(mock_get_context, resolver, mock_client):
    """Verify search_tickets encodes an intra-page offset cursor so excess matches on a page are not lost."""
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = ReadJourney(resolver)

    def make_item(seq: int) -> dict:
        """Create a mock serialized work item dictionary for pagination tests."""
        return {
            "id": f"ticket-{seq}",
            "name": f"Matching keyword item {seq}",
            "sequence_id": seq,
            "project_detail": {"identifier": "TEST"},
            "project_id": "proj-123",
            "state_detail": {"name": "Todo"},
            "priority": "medium",
            "assignees": [],
        }

    page1 = {
        "results": [make_item(1)],
        "next_cursor": "2:1:0",
        "prev_cursor": "",
        "total_count": 3,
        "next_page_results": True,
        "prev_page_results": False,
        "count": 1,
        "total_pages": 2,
        "total_results": 3,
    }
    page2 = {
        "results": [make_item(2), make_item(3)],
        "next_cursor": "",
        "prev_cursor": "2:0:1",
        "total_count": 3,
        "next_page_results": False,
        "prev_page_results": True,
        "count": 2,
        "total_pages": 2,
        "total_results": 3,
    }

    mock_client.work_items._get.side_effect = [page1, page2, page2]

    first_batch = journey.search_tickets(project_slug="TEST", query="keyword", limit=2, lod="summary")
    assert [r["issue_key"] for r in first_batch["results"]] == ["TEST-1", "TEST-2"]
    assert first_batch["next_cursor"] == "2:1:0|1"

    second_batch = journey.search_tickets(
        project_slug="TEST", query="keyword", limit=2, cursor=first_batch["next_cursor"], lod="summary"
    )
    assert [r["issue_key"] for r in second_batch["results"]] == ["TEST-3"]
    assert not second_batch["next_cursor"]


@patch("plane_mcp.journey.tools.read.get_plane_client_context")
def test_search_tickets_all_unknown_labels_returns_error(mock_get_context, resolver, mock_client):
    """Verify search_tickets returns an error without querying work items when no requested labels resolve."""
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = ReadJourney(resolver)

    existing_label = MagicMock()
    existing_label.name = "bug"
    existing_label.id = "lbl-1"
    mock_client.labels.list.return_value = MagicMock(results=[existing_label])

    result = journey.search_tickets(project_slug="TEST", labels=["nonexistent"])

    assert result["status"] == "error"
    assert "nonexistent" in result["error"]
    assert result["results"] == []
    mock_client.work_items._get.assert_not_called()


@patch("plane_mcp.journey.tools.read.get_plane_client_context")
def test_search_tickets_assignee_username_resolution_and_unknown_error(mock_get_context, resolver, mock_client):
    """Verify search_tickets resolves member usernames to UUIDs and errors on unresolvable assignees."""
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = ReadJourney(resolver)

    member = MagicMock()
    member.id = "user-uuid-42"
    member.username = "alice"
    member.display_name = "Alice Smith"
    mock_client.projects.get_members.return_value = MagicMock(results=[member])

    mock_client.work_items._get.return_value = {
        "results": [],
        "next_cursor": "",
        "prev_cursor": "",
        "total_count": 0,
        "next_page_results": False,
        "prev_page_results": False,
        "count": 0,
        "total_pages": 1,
        "total_results": 0,
    }

    ok_result = journey.search_tickets(project_slug="TEST", assignees=["alice"])
    assert ok_result["results"] == []
    _, kwargs = mock_client.work_items._get.call_args
    assert kwargs["params"]["assignees"] == "user-uuid-42"

    mock_client.work_items._get.reset_mock()
    err_result = journey.search_tickets(project_slug="TEST", assignees=["unknown_user"])
    assert err_result["status"] == "error"
    assert "unknown_user" in err_result["error"]
    mock_client.work_items._get.assert_not_called()


@patch("plane_mcp.journey.tools.workflow.get_plane_client_context")
def test_begin_work_partial_transition_failure_continues_and_reports(mock_get_context, resolver, mock_client):
    """Verify begin_work continues updating remaining tickets when one transition fails and reports failures."""
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = WorkflowJourney(resolver)

    mock_cycle = MagicMock()
    mock_cycle.name = "Sprint 3"
    mock_cycle.id = "cycle-3"
    mock_client.cycles.list.return_value = MagicMock(results=[mock_cycle])

    mock_state = MagicMock()
    mock_state.name = "In Progress"
    mock_state.id = "state-in-prog"
    mock_client.states.list.return_value = MagicMock(results=[mock_state])

    mock_updated = MagicMock()
    mock_client.work_items.update.side_effect = [RuntimeError("API timeout"), mock_updated]

    result = journey.begin_work(["TEST-1", "TEST-2"], "Sprint 3")

    assert result["status"] == "success"
    assert mock_client.work_items.update.call_count == 2
    assert "failed_transitions" in result
    assert result["failed_transitions"] == ["TEST-1"]
    assert "TEST-1" in result["details"]["TEST"]


@patch("plane_mcp.journey.tools.workflow.get_plane_client_context")
def test_complete_work_update_failure_after_comment_returns_partial(mock_get_context, resolver, mock_client):
    """Verify complete_work returns partial status with comment_added=True when state transition fails."""
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = WorkflowJourney(resolver)

    mock_client.work_items.comments.create.return_value = {"id": "comment-1"}

    mock_state = MagicMock()
    mock_state.name = "Done"
    mock_state.id = "state-done"
    mock_client.states.list.return_value = MagicMock(results=[mock_state])

    mock_client.work_items.update.side_effect = RuntimeError("502 Bad Gateway")

    result = journey.complete_work("TEST-1", "Completed work")

    assert result["status"] == "partial"
    assert result["issue_key"] == "TEST-1"
    assert result["comment_added"] is True
    assert "transition_ticket" in result["message"]
    mock_client.work_items.comments.create.assert_called_once()
    mock_client.work_items.update.assert_called_once()
