"""Unit tests for the Level of Detail (LOD) filtering system."""

import pytest
from plane_mcp.journey.lod import apply_lod, LODProfile, inject_sequence_id
from plane_mcp.journey.base import JourneyBase, with_lod
from unittest.mock import Mock


@pytest.fixture
def mock_issue():
    return {
        "id": "uuid-123",
        "name": "Test Issue",
        "description": "This is a test issue",
        "state": "uuid-state-1",
        "state_detail": {
            "name": "In Progress",
            "group": "started"
        },
        "priority": "high",
        "assignees": ["user-1", "user-2"],
        "sequence_id": 123,
        "project_identifier": "ENG",
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "created_by": "user-1",
        "updated_by": "user-2",
        "workspace": "uuid-workspace-1",
        "workspace_detail": {
            "id": "uuid-workspace-1",
            "name": "Test Workspace"
        },
        "project": "uuid-project-1",
        "project_detail": {
            "id": "uuid-project-1",
            "identifier": "ENG"
        },
        "extra_verbose_field": "some data"
    }


def test_lod_summary_profile(mock_issue):
    """Test that summary profile strips everything down to essential AI context."""
    result = apply_lod(mock_issue, profile=LODProfile.SUMMARY)

    # Check that sequence ID was injected to form key
    assert result.get("key") == "ENG-123"    
    # Check essential fields are present
    assert "id" not in result
    assert result.get("name") == "Test Issue"
    assert "description" not in result
    assert result.get("priority") == "high"
    assert result.get("assignees") == ["user-1", "user-2"]
    
    # Check state was lifted from state_detail
    assert result.get("state") == "In Progress"
    
    # Check verbose fields are omitted
    assert "created_at" not in result
    assert "workspace_detail" not in result
    assert "extra_verbose_field" not in result


def test_lod_standard_profile(mock_issue):
    """Test that standard profile keeps most fields but drops verbose ones."""
    result = apply_lod(mock_issue, profile=LODProfile.STANDARD)
    
    assert result.get("ticket_id") == "ENG-123"
    assert "id" not in result
    assert "extra_verbose_field" not in result
    assert result.get("name") == "Test Issue"
    assert result.get("description") == "This is a test issue"
    
    # Check verbose metadata is excluded
    assert "created_at" not in result
    assert "updated_at" not in result
    assert "created_by" not in result
    assert "updated_by" not in result
    assert "workspace" not in result
    assert "project" not in result
    assert "workspace_detail" not in result
    assert "project_detail" not in result


def test_inject_sequence_id_with_provided_identifier():
    """Test injecting sequence ID when project identifier is explicitly provided."""
    data = {
        "sequence_id": 456
    }
    inject_sequence_id(data, project_identifier="TEST")
    assert data.get("ticket_id") == "TEST-456"


def test_apply_lod_list(mock_issue):
    """Test that LOD applies correctly to a list of dicts."""
    results = apply_lod([mock_issue, mock_issue], profile=LODProfile.SUMMARY)
    
    assert len(results) == 2
    for result in results:
        assert result.get("key") == "ENG-123"
        assert "created_at" not in result


def test_journey_base_apply_lod(mock_issue):
    """Test JourneyBase class utilities."""
    resolver = Mock()
    base = JourneyBase(resolver=resolver)
    
    result = base.apply_lod(mock_issue, profile=LODProfile.SUMMARY)
    assert result.get("key") == "ENG-123"
    assert "created_at" not in result


def test_journey_base_parse_ticket_id():
    """Test parse_ticket_id utility."""
    resolver = Mock()
    base = JourneyBase(resolver=resolver)
    
    proj_id, seq_id = base.parse_ticket_id("ENG-123")
    assert proj_id == "ENG"
    assert seq_id == 123
    
    with pytest.raises(ValueError, match="Invalid ticket ID format"):
         base.parse_ticket_id("invalid")
         
    with pytest.raises(ValueError, match="Invalid ticket sequence"):
         base.parse_ticket_id("ENG-abc")


def test_with_lod_decorator(mock_issue):
    """Test the @with_lod decorator."""
    
    class MyJourney(JourneyBase):
        @with_lod(profile=LODProfile.SUMMARY)
        def get_ticket(self, ticket_id):
            return mock_issue
            
    resolver = Mock()
    journey = MyJourney(resolver=resolver)
    
    result = journey.get_ticket(ticket_id="ENG-123")
    
    assert result.get("key") == "ENG-123"
    assert "created_at" not in result


def test_lod_full_strips_description_html():
    """FULL profile should convert HTML to Markdown and remove the raw HTML to save tokens."""
    data = {
        "id": "uuid-1",
        "name": "Issue With HTML",
        "description_html": "<p>Hello <strong>world</strong></p>",
        "priority": "high",
    }
    result = apply_lod(data, profile=LODProfile.FULL)

    assert "description" in result
    assert "Hello" in result["description"]
    assert "description_html" not in result