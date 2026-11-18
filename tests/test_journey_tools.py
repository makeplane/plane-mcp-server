"""Unit tests for Journey tools and HTML/Markdown sanitization utilities."""

import time
from unittest.mock import MagicMock, patch

import pytest

import plane_mcp.resolver
from plane_mcp.journey.tools.create_update import CreateUpdateJourney
from plane_mcp.journey.tools.workflow import WorkflowJourney
from plane_mcp.resolver import EntityResolver
from plane_mcp.sanitize import apply_snippet_edits, markdown_or_html_to_safe_html


@pytest.fixture
def mock_client():
    """Return a mocked Plane client instance."""
    client = MagicMock()
    return client


@pytest.fixture
def resolver(mock_client):
    """Return an EntityResolver pre-populated with test workspace cache entries."""
    res = EntityResolver(mock_client, "test-workspace")
    # Pre-populate workspace-scoped cache so we don't need to mock projects.list everywhere
    plane_mcp.resolver._GLOBAL_PROJECT_CACHE["test-workspace"].update({"ENG": "proj-1"})
    plane_mcp.resolver._GLOBAL_WORK_ITEM_CACHE["test-workspace"].update({"ENG-1": "ticket-1", "ENG-2": "ticket-2"})
    plane_mcp.resolver._CACHE_LAST_UPDATED["test-workspace"]["projects"] = time.time()
    plane_mcp.resolver._CACHE_LAST_UPDATED["test-workspace"]["work_items"] = time.time()
    return res


@patch("plane_mcp.journey.tools.create_update.get_plane_client_context")
def test_update_ticket_occ_success(mock_get_context, resolver, mock_client):
    """Verify update_ticket replaces a unique target snippet and updates the title."""
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = CreateUpdateJourney(resolver)

    # Mock retrieve
    resolver.resolve_ticket = MagicMock(return_value="ticket-1")
    resolver.resolve_project = MagicMock(return_value="project-1")
    current_ticket = {"id": "ticket-1", "name": "Old Title", "description_html": "<p>Old Description</p>"}
    mock_client.work_items._get.return_value = current_ticket

    # Mock update
    mock_client.work_items.update.return_value = {
        "id": "ticket-1",
        "name": "New Title",
        "sequence_id": 1,
        "project_detail": {"identifier": "ENG"},
    }

    # Run update
    _ = journey.update_ticket(
        "ENG-1", replace_text="New Description", replace_target_snippet="<p>Old Description</p>", new_title="New Title"
    )
    assert mock_client.work_items.update.called
    update_data = mock_client.work_items.update.call_args[1]["data"]
    assert update_data.name == "New Title"
    assert update_data.description_html == "New Description"


@patch("plane_mcp.journey.tools.create_update.get_plane_client_context")
def test_update_ticket_append(mock_get_context, resolver, mock_client):
    """Verify update_ticket appends text to the end of the description when no anchor is given."""
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = CreateUpdateJourney(resolver)

    resolver.resolve_ticket = MagicMock(return_value="ticket-1")
    resolver.resolve_project = MagicMock(return_value="project-1")
    current_ticket = {"id": "ticket-1", "name": "Title", "description_html": "<p>Old Description</p>"}
    mock_client.work_items._get.return_value = current_ticket

    _ = journey.update_ticket(ticket_id="TEST-1", append_text="Appended Text")
    assert mock_client.work_items.update.called
    update_data = mock_client.work_items.update.call_args[1]["data"]
    assert update_data.description_html == "<p>Old Description</p><br><br>Appended Text"


@patch("plane_mcp.journey.tools.create_update.get_plane_client_context")
def test_update_ticket_rejects_replace_text_without_target(mock_get_context, resolver, mock_client):
    """Verify update_ticket returns an error when replace_text is provided without replace_target_snippet."""
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = CreateUpdateJourney(resolver)

    resolver.resolve_ticket = MagicMock(return_value="ticket-1")
    resolver.resolve_project = MagicMock(return_value="project-1")
    mock_client.work_items._get.return_value = {
        "id": "ticket-1",
        "name": "Title",
        "description_html": "<p>Old Description</p>",
    }

    res = journey.update_ticket(ticket_id="ENG-1", replace_text="Orphan replacement")
    assert res["status"] == "error"
    assert "replace_target_snippet is required when replace_text is provided" in res["message"]
    assert not mock_client.work_items.update.called


@patch("plane_mcp.journey.tools.create_update.get_plane_client_context")
def test_create_ticket_renders_markdown_and_deduplicates_labels(mock_get_context, resolver, mock_client):
    """Verify create_ticket converts Markdown descriptions to HTML and deduplicates labels case-insensitively."""
    mock_get_context.return_value = (mock_client, "test-workspace")
    journey = CreateUpdateJourney(resolver)

    resolver.resolve_project = MagicMock(return_value="proj-1")
    mock_client.labels.list.return_value = MagicMock(results=[])

    created_label = MagicMock()
    created_label.id = "lbl-1"
    mock_client.labels.create.return_value = created_label

    created_work_item = MagicMock()
    created_work_item.id = "wi-1"
    created_work_item.sequence_id = 42
    mock_client.work_items.create.return_value = created_work_item

    # Passing 4 case-insensitive duplicates of the same new label should not exceed MAX_NEW_LABELS_PER_REQUEST (3)
    res = journey.create_ticket(
        title="Markdown Ticket",
        project_slug="ENG",
        description="**urgent** fix for `auth`",
        labels=["Bug", "bug", "BUG", "bUg"],
    )
    assert res == {"issue_key": "ENG-42"}
    assert mock_client.labels.create.call_count == 1
    create_data = mock_client.work_items.create.call_args[1]["data"]
    assert create_data.description_html == "<p><strong>urgent</strong> fix for <code>auth</code></p>"
    assert create_data.labels == ["lbl-1"]


def test_markdown_or_html_to_safe_html_constructs():
    """Verify markdown_or_html_to_safe_html renders Markdown blocks and inline syntax before sanitizing."""
    md_input = (
        "# Heading 1\n\n"
        "**bold** and *italic* and ~~strike~~ and `code` and `__init__` and `a*b*c` and [Site](https://example.com)\n\n"
        "- item one\n- item two\n\n"
        "1. first\n2. second\n\n"
        "> quoted line\n\n"
        "```python\nprint(1 < 2)\n```"
    )
    html_out = markdown_or_html_to_safe_html(md_input)
    assert "<h1>Heading 1</h1>" in html_out
    assert "<strong>bold</strong>" in html_out
    assert "<em>italic</em>" in html_out
    assert "<del>strike</del>" in html_out
    assert "<code>code</code>" in html_out
    assert "<code>__init__</code>" in html_out
    assert "<code>a*b*c</code>" in html_out
    assert 'href="https://example.com"' in html_out
    assert "<ul><li>item one</li><li>item two</li></ul>" in html_out
    assert "<ol><li>first</li><li>second</li></ol>" in html_out
    assert "<blockquote>quoted line</blockquote>" in html_out
    assert "<pre><code>print(1 &lt; 2)</code></pre>" in html_out


def test_apply_snippet_edits_multiply_encoded_and_ambiguity():
    """Verify apply_snippet_edits matches multiply-encoded snippets and detects cross-variant ambiguity."""
    stored_html = "<p>Fish &amp;amp; Chips</p>"

    # Replace using single-escaped or raw snippet against double-escaped stored HTML
    updated, err = apply_snippet_edits(
        stored_html,
        replace_target_snippet="Fish &amp; Chips",
        replace_text="Fish &amp; Salad",
    )
    assert err is None
    assert updated == "<p>Fish &amp; Salad</p>"

    # Append after anchor using raw snippet against double-escaped stored HTML
    updated_append, err_append = apply_snippet_edits(
        stored_html,
        append_after_snippet="Fish & Chips",
        append_text=" (fresh)",
    )
    assert err_append is None
    assert "Fish &amp;amp; Chips (fresh)" in updated_append

    # Ambiguity across different encoding levels in stored HTML
    ambiguous_html = "<p>Fish &amp; Chips</p><p>Fish &amp;amp; Chips</p>"
    updated_amb, err_amb = apply_snippet_edits(
        ambiguous_html,
        replace_target_snippet="Fish & Chips",
        replace_text="Replaced",
    )
    assert updated_amb is None
    assert err_amb is not None and "Ambiguous snippet" in err_amb

    # Reject replace_text without replace_target_snippet directly in apply_snippet_edits
    updated_no_target, err_no_target = apply_snippet_edits(stored_html, replace_text="Orphan")
    assert updated_no_target is None
    assert err_no_target == "replace_target_snippet is required when replace_text is provided"


@patch("plane_mcp.journey.tools.workflow.get_plane_client_context")
def test_begin_work_batch(mock_get_context, resolver, mock_client):
    """Verify begin_work creates a missing cycle, adds tickets, and transitions them to In Progress."""
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
    plane_mcp.resolver._GLOBAL_STATE_CACHE.setdefault("test-workspace", {}).update(
        {"proj-1": {"in progress": "state-ip"}}
    )
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


@patch("plane_mcp.journey.tools.workflow.get_plane_client_context")
def test_cycle_creation_http_400_fallback(mock_get_context, resolver, mock_client):
    """Verify _resolve_or_create_cycle gracefully returns None when cycles are disabled (HTTP 400)."""
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
