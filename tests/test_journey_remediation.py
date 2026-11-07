"""Unit tests verifying remediation of journey tools, security fixes, and yaml formatting.

Verifies:
1. In-memory help path (CWE-377/379 fix): no /tmp/plane_mcp directory or files created.
2. Safe error boundary (CWE-209 fix): no traceback or error_details leaked in response.
3. Clean HTML stripping: regex strips closing tags properly without leaving '>'.
4. InlineSafeDumper alias suppression: &id and *id references are suppressed.
5. FastMCP tool wrappers: all journey tools are registered and executable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastmcp import FastMCP

from plane_mcp.journey.base import get_workspace_help_context, mcp_error_boundary
from plane_mcp.journey.tools import register_tools
from plane_mcp.journey.tools.create_update import CreateUpdateJourney
from plane_mcp.journey.tools.read import ReadJourney
from plane_mcp.resolver import EntityResolver
from plane_mcp.yaml_formatter import InlineSafeDumper, format_as_yaml


@pytest.fixture
def mock_plane_context():
    """Provide a mocked PlaneClient and workspace slug with sample projects, states, and labels."""
    mock_client = MagicMock()
    workspace_slug = "test-workspace"

    # Mock projects
    p1 = MagicMock()
    p1.id = "proj-uuid-1"
    p1.identifier = "ENG"
    p1.name = "Engineering"
    p1.description = "Core Engineering Project"

    p2 = MagicMock()
    p2.id = "proj-uuid-2"
    p2.identifier = "DESIGN"
    p2.name = "Design"
    p2.description = ""

    mock_client.projects.list.return_value = MagicMock(results=[p1, p2])

    # Mock states
    s1 = MagicMock(name="Todo")
    s1.name = "Todo"
    s2 = MagicMock(name="In Progress")
    s2.name = "In Progress"
    s3 = MagicMock(name="Done")
    s3.name = "Done"
    mock_client.states.list.return_value = MagicMock(results=[s1, s2, s3])

    # Mock labels
    l1 = MagicMock()
    l1.name = "bug"
    l2 = MagicMock()
    l2.name = "feature"
    mock_client.labels.list.return_value = MagicMock(results=[l1, l2])

    return mock_client, workspace_slug


class TestInMemoryHelpPath:
    """Remediation Task 1: Verify no /tmp/plane_mcp cache file is written."""

    def test_get_workspace_help_context_in_memory_only(self, mock_plane_context):
        """Verify workspace help context is fetched in memory without writing to /tmp/plane_mcp."""
        mock_client, workspace_slug = mock_plane_context
        cache_dir = os.path.join(tempfile.gettempdir(), "plane_mcp")

        # Ensure directory does not exist prior to call
        if os.path.exists(cache_dir):
            for f in os.listdir(cache_dir):
                os.remove(os.path.join(cache_dir, f))
            os.rmdir(cache_dir)

        ctx = get_workspace_help_context(client=mock_client, workspace_slug=workspace_slug)

        assert "projects" in ctx
        assert len(ctx["projects"]) == 2
        assert ctx["projects"][0]["project_slug"] == "ENG"
        assert ctx["projects"][0]["states"] == ["Todo", "In Progress", "Done"]
        assert ctx["projects"][0]["labels"] == ["bug", "feature"]
        assert ctx["priorities"] == ["urgent", "high", "medium", "low", "none"]

        # Assert no /tmp/plane_mcp was created
        assert not os.path.exists(cache_dir), f"Directory {cache_dir} must not be created"

    @patch("plane_mcp.journey.tools.read.get_plane_client_context")
    def test_search_tickets_help_path_no_tmp_files(self, mock_get_ctx, mock_plane_context):
        """Verify search_tickets help discovery returns in-memory metadata without creating temp files."""
        mock_client, workspace_slug = mock_plane_context
        mock_get_ctx.return_value = (mock_client, workspace_slug)
        resolver = EntityResolver(mock_client, workspace_slug)

        journey = ReadJourney(resolver)
        res = journey.search_tickets(project_slug="help")

        assert "projects" in res
        assert "priorities" in res
        assert any(p["project_slug"] == "ENG" for p in res["projects"])

        cache_dir = os.path.join(tempfile.gettempdir(), "plane_mcp")
        assert not os.path.exists(cache_dir)

    @patch("plane_mcp.journey.tools.create_update.get_plane_client_context")
    def test_create_ticket_help_path_no_tmp_files(self, mock_get_ctx, mock_plane_context):
        """Verify create_ticket help discovery returns in-memory metadata without creating temp files."""
        mock_client, workspace_slug = mock_plane_context
        mock_get_ctx.return_value = (mock_client, workspace_slug)
        resolver = EntityResolver(mock_client, workspace_slug)

        journey = CreateUpdateJourney(resolver)
        res = journey.create_ticket(title="Help Query", project_slug="help")

        assert "projects" in res
        assert "priorities" in res
        assert any(p["project_slug"] == "ENG" for p in res["projects"])

        cache_dir = os.path.join(tempfile.gettempdir(), "plane_mcp")
        assert not os.path.exists(cache_dir)

    def test_help_path_api_failure_graceful_fallback(self):
        """Verify get_workspace_help_context returns a structured fallback dictionary when the API fails."""
        failing_client = MagicMock()
        failing_client.projects.list.side_effect = ConnectionError("Plane API unreachable")

        ctx = get_workspace_help_context(client=failing_client, workspace_slug="test-ws")

        assert ctx["projects"] == []
        assert "error" in ctx
        assert "Plane API unreachable" in ctx["error"]


class TestHtmlRegexStripping:
    """Remediation Task 4: Verify regex properly strips HTML tags without trailing '>'."""

    @patch("plane_mcp.journey.tools.read.get_plane_client_context")
    def test_search_tickets_strips_html_cleanly(self, mock_get_ctx):
        """Verify search_tickets strips nested HTML tags cleanly without leaving stray angle brackets."""
        mock_client = MagicMock()
        workspace_slug = "test-ws"
        mock_get_ctx.return_value = (mock_client, workspace_slug)

        # Mock project for resolver
        p = MagicMock()
        p.id = "proj-uuid-1"
        p.identifier = "ENG"
        p.name = "Engineering"
        mock_client.projects.list.return_value = MagicMock(results=[p])

        # Mock work items response with various HTML descriptions
        mock_client.work_items._get.return_value = {
            "results": [
                {
                    "id": "ticket-1",
                    "sequence_id": 1,
                    "name": "Issue 1",
                    "description": "<p>This is paragraph one.</p> <p>This is paragraph two.</p>",
                    "project_detail": {"identifier": "ENG"},
                    "state_detail": {"name": "Backlog"},
                    "priority": "high",
                    "assignees": [],
                },
                {
                    "id": "ticket-2",
                    "sequence_id": 2,
                    "name": "Issue 2",
                    "description": "<div><span>Nested <b>bold</b> text</span> and <a href='https://plane.so'>link</a>.</div>",
                    "project_detail": {"identifier": "ENG"},
                    "state_detail": {"name": "Todo"},
                    "priority": "low",
                    "assignees": [],
                },
            ],
            "next_cursor": "",
            "prev_cursor": "",
            "total_count": 2,
            "next_page_results": False,
            "prev_page_results": False,
            "count": 2,
            "total_pages": 1,
            "total_results": 2,
        }

        mcp = FastMCP("test-read")
        from plane_mcp.journey.tools.read import register_read_tools

        register_read_tools(mcp)

        tool_fn = asyncio.run(mcp.get_tool("search_tickets")).fn
        yaml_output = tool_fn(project_slug="ENG")
        parsed = yaml.safe_load(yaml_output)

        results = parsed.get("results", [])
        assert len(results) == 2

        desc1 = results[0]["description"]
        assert desc1 == "This is paragraph one. This is paragraph two."
        assert ">" not in desc1
        assert "<" not in desc1

        desc2 = results[1]["description"]
        assert desc2 == "Nested bold text and link."
        assert ">" not in desc2
        assert "<" not in desc2


class TestErrorBoundaryInformationDisclosure:
    """Remediation Task 3: Verify stack traces and details are not disclosed to client (CWE-209)."""

    def test_error_boundary_suppresses_traceback_on_unhandled_exception(self, caplog):
        """Verify unhandled exceptions return a generic error omitting internal paths and parameter values."""

        @mcp_error_boundary
        def sensitive_failing_tool(param: str) -> dict:
            """Simulate a tool raising an unexpected exception containing sensitive details."""
            raise RuntimeError(f"Database query failed at /internal/secrets/db.py:42 with param={param}")

        with caplog.at_level(logging.ERROR):
            result = sensitive_failing_tool(param="secret-val")

        assert isinstance(result, dict)
        assert result["tool"] == "sensitive_failing_tool"
        assert "unexpected error" in result["error"].lower()
        assert "secret-val" not in result["error"]
        assert "/internal/secrets/db.py" not in result["error"]
        assert "details" not in result
        assert "error_details" not in result
        assert "Traceback" not in result.get("error", "")

        # Verify stack trace and full message are recorded on server logger securely
        assert any("Traceback" in record.message or record.exc_info for record in caplog.records)
        assert any("secret-val" in record.getMessage() for record in caplog.records)

    def test_error_boundary_list_return_type_suppresses_traceback(self):
        """Verify list-annotated tools return a generic error item without exposing internal keys."""

        @mcp_error_boundary
        def failing_list_tool() -> list:
            """Simulate a list-returning tool raising an unexpected KeyError."""
            raise KeyError("missing_internal_key")

        result = failing_list_tool()
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["tool"] == "failing_list_tool"
        assert "unexpected error" in result[0]["error"].lower()
        assert "missing_internal_key" not in result[0]["error"]
        assert "details" not in result[0]
        assert "error_details" not in result[0]

    def test_error_boundary_preserves_safe_validation_errors(self):
        """Verify safe validation errors keep their actionable message while scrubbing paths and key-values."""

        @mcp_error_boundary
        def validation_failing_tool(param: str) -> dict:
            """Simulate a tool raising a ValueError with both safe text and sensitive path/param tokens."""
            raise ValueError(f"Invalid ticket format at /internal/secrets/db.py:42 with param={param}")

        result = validation_failing_tool(param="secret-val")
        assert isinstance(result, dict)
        assert result["tool"] == "validation_failing_tool"
        assert "Invalid ticket format" in result["error"]
        assert "secret-val" not in result["error"]
        assert "/internal/secrets/db.py" not in result["error"]


class TestInlineSafeDumperAliasSuppression:
    """Remediation Task 5: Verify InlineSafeDumper suppresses anchors and aliases."""

    def test_repeated_dict_references_suppressed(self):
        """Verify shared dictionary references are serialized inline without YAML anchors or aliases."""
        shared_dict = {"status": "active", "code": 200}
        data = {
            "primary": shared_dict,
            "secondary": shared_dict,
            "items": [shared_dict, shared_dict],
        }

        # SafeDumper without InlineSafeDumper creates aliases
        default_yaml = yaml.safe_dump(data)
        assert "&id" in default_yaml or "*id" in default_yaml

        # InlineSafeDumper suppresses aliases
        safe_yaml = yaml.dump(data, Dumper=InlineSafeDumper)
        assert "&id" not in safe_yaml
        assert "*id" not in safe_yaml

        # format_as_yaml uses InlineSafeDumper
        formatted = format_as_yaml(data)
        assert "&id" not in formatted
        assert "*id" not in formatted

        # Data structure loads back correctly
        loaded = yaml.safe_load(formatted)
        assert loaded["primary"] == {"status": "active", "code": 200}
        assert loaded["secondary"] == {"status": "active", "code": 200}
        assert loaded["items"][0] == {"status": "active", "code": 200}


class TestFastMCPToolWrappers:
    """Remediation Task 6: Verify registration and invocation of all FastMCP journey tool wrappers."""

    def test_all_journey_tools_registered(self):
        """Verify all seven journey tools are registered on the FastMCP server instance."""
        mcp = FastMCP("test-all-journey")
        register_tools(mcp)

        tools = asyncio.run(mcp.list_tools())
        tool_names = {t.name for t in tools}

        expected = {
            "search_tickets",
            "read_ticket",
            "create_ticket",
            "update_ticket",
            "transition_ticket",
            "begin_work",
            "complete_work",
        }
        assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"

    @patch("plane_mcp.journey.tools.create_update.get_plane_client_context")
    @patch("plane_mcp.journey.tools.read.get_plane_client_context")
    @patch("plane_mcp.journey.tools.workflow.get_plane_client_context")
    def test_journey_tool_wrappers_execute_with_yaml_output(
        self, mock_wf_ctx, mock_read_ctx, mock_cu_ctx, mock_plane_context
    ):
        """Verify every registered journey tool executes end-to-end and returns valid YAML output."""
        mock_client, workspace_slug = mock_plane_context
        mock_wf_ctx.return_value = (mock_client, workspace_slug)
        mock_read_ctx.return_value = (mock_client, workspace_slug)
        mock_cu_ctx.return_value = (mock_client, workspace_slug)

        mcp = FastMCP("test-journey-exec")
        register_tools(mcp)

        # 1. search_tickets help
        search_fn = asyncio.run(mcp.get_tool("search_tickets")).fn
        out_search = search_fn(project_slug="help")
        assert isinstance(out_search, str)
        parsed_search = yaml.safe_load(out_search)
        assert "projects" in parsed_search

        # 2. create_ticket help
        create_fn = asyncio.run(mcp.get_tool("create_ticket")).fn
        out_create = create_fn(title="Help Ticket", project_slug="help")
        assert isinstance(out_create, str)
        parsed_create = yaml.safe_load(out_create)
        assert "projects" in parsed_create

        # Mock work items responses for entity resolution and reads
        mock_ticket = {
            "id": "ticket-uuid-1",
            "name": "Remediation Ticket",
            "sequence_id": 1,
            "project_detail": {"identifier": "ENG"},
            "project_id": "proj-uuid-1",
            "state_detail": {"name": "In Progress"},
            "priority": "high",
            "assignees": [],
            "description_html": "<p>Existing description</p>",
        }
        mock_client.work_items._get.return_value = mock_ticket
        mock_client.work_items.retrieve.return_value = mock_ticket
        mock_client.work_items.update.return_value = {
            "id": "ticket-uuid-1",
            "sequence_id": 1,
            "project_detail": {"identifier": "ENG"},
        }
        c1 = MagicMock()
        c1.id = "cycle-1"
        c1.name = "Sprint 1"
        mock_client.cycles.list.return_value = MagicMock(results=[c1])
        mock_client.users.me.retrieve.return_value = MagicMock(id="user-123")

        # 3. read_ticket
        read_fn = asyncio.run(mcp.get_tool("read_ticket")).fn
        out_read = read_fn(ticket_id="ENG-1", comments=False)
        assert isinstance(out_read, str)
        parsed_read = yaml.safe_load(out_read)
        assert parsed_read.get("name") == "Remediation Ticket"

        # 4. update_ticket
        update_fn = asyncio.run(mcp.get_tool("update_ticket")).fn
        out_update = update_fn(ticket_id="ENG-1", new_title="Updated Title")
        assert isinstance(out_update, str)
        parsed_update = yaml.safe_load(out_update)
        assert parsed_update.get("issue_key") == "ENG-1"

        # 5. transition_ticket
        trans_fn = asyncio.run(mcp.get_tool("transition_ticket")).fn
        out_trans = trans_fn(ticket_id="ENG-1", state_name="Done")
        assert isinstance(out_trans, str)
        parsed_trans = yaml.safe_load(out_trans)
        assert parsed_trans.get("issue_key") == "ENG-1"

        # 6. begin_work
        begin_fn = asyncio.run(mcp.get_tool("begin_work")).fn
        out_begin = begin_fn(ticket_ids=["ENG-1"], cycle_name="Sprint 1")
        assert isinstance(out_begin, str)
        parsed_begin = yaml.safe_load(out_begin)
        assert parsed_begin.get("status") == "success"

        # 7. complete_work
        complete_fn = asyncio.run(mcp.get_tool("complete_work")).fn
        out_complete = complete_fn(ticket_id="ENG-1", comment="Work complete")
        assert isinstance(out_complete, str)
        parsed_complete = yaml.safe_load(out_complete)
        assert parsed_complete.get("issue_key") == "ENG-1"
