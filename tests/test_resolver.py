import pytest
from unittest.mock import MagicMock, patch
from plane.models.query_params import PaginatedQueryParams
from plane.errors.errors import HttpError

from plane_mcp.resolver import EntityResolver, EntityResolutionError


@pytest.fixture
def mock_client():
    client = MagicMock()
    return client


@pytest.fixture
def resolver(mock_client):
    return EntityResolver(mock_client, "test-workspace")


def test_resolve_project_success(resolver, mock_client):
    # Mock project list response
    project_mock1 = MagicMock()
    project_mock1.id = "proj-1"
    project_mock1.identifier = "ENG"
    project_mock1.name = "Engineering"
    project_mock1.slug = "engineering"
    
    mock_response = MagicMock()
    mock_response.results = [project_mock1]
    mock_client.projects.list.return_value = mock_response

    # Test resolving by identifier
    assert resolver.resolve_project("ENG") == "proj-1"
    
    # Test resolving by slug (should be cached now)
    assert resolver.resolve_project("engineering") == "proj-1"
    
    # Assert API was called only once due to caching
    assert mock_client.projects.list.call_count == 1


def test_resolve_project_not_found(resolver, mock_client):
    mock_response = MagicMock()
    mock_response.results = []
    mock_client.projects.list.return_value = mock_response

    with pytest.raises(EntityResolutionError) as exc_info:
        resolver.resolve_project("NOTFOUND")

    assert "Project 'NOTFOUND' not found" in str(exc_info.value)
    assert exc_info.value.available_options == []


def test_resolve_state_success(resolver, mock_client):
    # Setup project mock
    project_mock = MagicMock()
    project_mock.id = "proj-1"
    project_mock.identifier = "ENG"
    project_mock.name = "Engineering"
    mock_proj_resp = MagicMock()
    mock_proj_resp.results = [project_mock]
    mock_client.projects.list.return_value = mock_proj_resp

    # Setup state mock
    state_mock1 = MagicMock()
    state_mock1.id = "state-1"
    state_mock1.name = "In Progress"
    state_mock2 = MagicMock()
    state_mock2.id = "state-2"
    state_mock2.name = "Done"
    
    mock_state_resp = MagicMock()
    mock_state_resp.results = [state_mock1, state_mock2]
    mock_client.states.list.return_value = mock_state_resp

    # Resolve state
    assert resolver.resolve_state("ENG", "In Progress") == "state-1"
    
    # Check cache (should not trigger another list call)
    assert resolver.resolve_state("ENG", "done") == "state-2"
    assert mock_client.states.list.call_count == 1


def test_resolve_state_not_found(resolver, mock_client):
    # Setup project mock
    project_mock = MagicMock()
    project_mock.id = "proj-1"
    project_mock.identifier = "ENG"
    project_mock.name = "Engineering"
    mock_proj_resp = MagicMock()
    mock_proj_resp.results = [project_mock]
    mock_client.projects.list.return_value = mock_proj_resp

    # Setup state mock
    state_mock1 = MagicMock()
    state_mock1.id = "state-1"
    state_mock1.name = "In Progress"
    
    mock_state_resp = MagicMock()
    mock_state_resp.results = [state_mock1]
    mock_client.states.list.return_value = mock_state_resp

    with pytest.raises(EntityResolutionError) as exc_info:
        resolver.resolve_state("ENG", "QA")

    assert "State 'QA' not found" in str(exc_info.value)
    assert "In Progress" in exc_info.value.available_options


def test_resolve_ticket_success(resolver, mock_client):
    # Setup project mock
    project_mock = MagicMock()
    project_mock.id = "proj-1"
    project_mock.identifier = "ENG"
    mock_proj_resp = MagicMock()
    mock_proj_resp.results = [project_mock]
    mock_client.projects.list.return_value = mock_proj_resp

    # Setup ticket mock
    mock_client.work_items._get.return_value = {"id": "ticket-1"}

    assert resolver.resolve_ticket("ENG-123") == "ticket-1"
    mock_client.work_items._get.assert_called_once_with("test-workspace/work-items/ENG-123")
    
    # Test cache
    assert resolver.resolve_ticket("eng-123") == "ticket-1"
    assert mock_client.work_items._get.call_count == 1


def test_resolve_ticket_invalid_format(resolver):
    with pytest.raises(ValueError, match="Invalid ticket ID format"):
        resolver.resolve_ticket("ENG123")
    
    with pytest.raises(ValueError, match="Invalid ticket sequence"):
        resolver.resolve_ticket("ENG-ABC")


def test_resolve_ticket_not_found(resolver, mock_client):
    # Setup project mock
    project_mock = MagicMock()
    project_mock.id = "proj-1"
    project_mock.identifier = "ENG"
    mock_proj_resp = MagicMock()
    mock_proj_resp.results = [project_mock]
    mock_client.projects.list.return_value = mock_proj_resp

    # Mock HTTP 404
    class MockHttpError(HttpError):
        def __init__(self):
            self.status_code = 404
        def __str__(self):
            return "404 Not Found"
            
    mock_client.work_items._get.side_effect = MockHttpError()

    with pytest.raises(EntityResolutionError) as exc_info:
        resolver.resolve_ticket("ENG-999")
        
    assert "Ticket 'ENG-999' not found" in str(exc_info.value)
