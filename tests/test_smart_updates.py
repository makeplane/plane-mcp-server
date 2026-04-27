from unittest.mock import MagicMock, patch

import pytest

from plane_mcp.journey.tools.create_update import CreateUpdateJourney
from plane_mcp.resolver import EntityResolver


@pytest.fixture
def mock_client():
    return MagicMock()

@pytest.fixture
def resolver(mock_client):
    res = EntityResolver(mock_client, "test-workspace")
    res.resolve_ticket = MagicMock(return_value="ticket-1")
    res.resolve_project = MagicMock(return_value="project-1")
    return res

@pytest.fixture
def journey(resolver):
    return CreateUpdateJourney(resolver)

def setup_mock_ticket(mock_client, description_html, labels=None):
    current_ticket = {
        "id": "ticket-1",
        "name": "Test Ticket",
        "description_html": description_html,
        "labels": labels or []
    }
    mock_client.work_items._get.return_value = current_ticket
    mock_client.work_items.update.reset_mock()
    return current_ticket

@patch('plane_mcp.journey.tools.create_update.get_plane_client_context')
def test_plane_38_validation_error_bypass(mock_get_context, journey, mock_client):
    """
    Test for PLANE-38: Ensure that update_ticket does not crash with Pydantic 
    ValidationError when the ticket has unexpanded string labels.
    """
    mock_get_context.return_value = (mock_client, "test-workspace")
    # Simulate API returning string UUIDs for labels instead of Label objects
    setup_mock_ticket(mock_client, "description", labels=["9f8b4a2c-1d3e-4f5g"])
    
    result = journey.update_ticket("TEST-1", new_title="Updated Title")
    
    assert result["status"] == "success"
    update_data = mock_client.work_items.update.call_args[1]["data"]
    assert update_data.name == "Updated Title"


@patch('plane_mcp.journey.tools.create_update.get_plane_client_context')
def test_append_text_end(mock_get_context, journey, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    setup_mock_ticket(mock_client, "foo bar baz")
    
    result = journey.update_ticket("TEST-1", append_text="buzz")
    
    assert result["status"] == "success"
    update_data = mock_client.work_items.update.call_args[1]["data"]
    assert update_data.description_html == "foo bar baz<br><br>buzz"

@patch('plane_mcp.journey.tools.create_update.get_plane_client_context')
def test_append_after_snippet_success(mock_get_context, journey, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    setup_mock_ticket(mock_client, "foo bar baz")
    
    result = journey.update_ticket("TEST-1", append_text="buzz", append_after_snippet="bar")
    
    assert result["status"] == "success"
    update_data = mock_client.work_items.update.call_args[1]["data"]
    assert update_data.description_html == "foo bar<br><br>buzz baz"

@patch('plane_mcp.journey.tools.create_update.get_plane_client_context')
def test_append_after_snippet_not_found(mock_get_context, journey, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    setup_mock_ticket(mock_client, "foo bar baz")
    
    result = journey.update_ticket("TEST-1", append_text="buzz", append_after_snippet="missing")
    
    assert result["status"] == "error"
    assert "not found in the description" in result["message"]
    assert not mock_client.work_items.update.called

@patch('plane_mcp.journey.tools.create_update.get_plane_client_context')
def test_append_after_snippet_multiple_matches(mock_get_context, journey, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    setup_mock_ticket(mock_client, "foo bar foo bar")
    
    result = journey.update_ticket("TEST-1", append_text="buzz", append_after_snippet="bar")
    
    assert result["status"] == "error"
    assert "matched multiple times" in result["message"]
    assert not mock_client.work_items.update.called

@patch('plane_mcp.journey.tools.create_update.get_plane_client_context')
def test_replace_text_success(mock_get_context, journey, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    setup_mock_ticket(mock_client, "foo bar baz")
    
    result = journey.update_ticket("TEST-1", replace_text="qux", replace_target_snippet="bar")
    
    assert result["status"] == "success"
    update_data = mock_client.work_items.update.call_args[1]["data"]
    assert update_data.description_html == "foo qux baz"

@patch('plane_mcp.journey.tools.create_update.get_plane_client_context')
def test_replace_text_missing_snippet(mock_get_context, journey, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    setup_mock_ticket(mock_client, "foo bar baz")
    
    result = journey.update_ticket("TEST-1", replace_text="qux")
    
    assert result["status"] == "error"
    assert "did not provide 'replace_target_snippet'" in result["message"]
    assert not mock_client.work_items.update.called

@patch('plane_mcp.journey.tools.create_update.get_plane_client_context')
def test_replace_text_not_found(mock_get_context, journey, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    setup_mock_ticket(mock_client, "foo bar baz")
    
    result = journey.update_ticket("TEST-1", replace_text="qux", replace_target_snippet="missing")
    
    assert result["status"] == "error"
    assert "not found in the description" in result["message"]
    assert not mock_client.work_items.update.called

@patch('plane_mcp.journey.tools.create_update.get_plane_client_context')
def test_replace_text_multiple_matches(mock_get_context, journey, mock_client):
    mock_get_context.return_value = (mock_client, "test-workspace")
    setup_mock_ticket(mock_client, "foo bar foo bar")
    
    result = journey.update_ticket("TEST-1", replace_text="qux", replace_target_snippet="bar")
    
    assert result["status"] == "error"
    assert "matched multiple times" in result["message"]
    assert not mock_client.work_items.update.called
