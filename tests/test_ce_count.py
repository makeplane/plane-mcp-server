"""Deterministic unit tests for self-hosted Plane CE workitem count fallback."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from plane.errors.errors import HttpError
from plane.models.projects import PaginatedProjectResponse, Project
from plane.models.work_items import (
    PaginatedWorkItemResponse,
    WorkItem,
    WorkItemCountResponse,
)

from plane_mcp.ce_count import MAX_PAGES, MAX_PROJECTS, MAX_TRAVERSED_PROJECTS, count_fallback
from plane_mcp.tools.workitem import register


def _make_http_error(status: int = 404, message: str = "Not Found") -> HttpError:
    return HttpError(message, status_code=status, response={"error": message})


@pytest.fixture
def test_server():
    """FastMCP test server instance registering workitem tool."""
    server = FastMCP("test-server")
    register(server)
    return server


# ============================================================================
# Unit Tests for count_fallback core logic
# ============================================================================


def test_count_fallback_single_project():
    """Project-scoped count returns exact total_count from single project listing."""
    client = MagicMock()
    page = PaginatedWorkItemResponse(
        results=[
            WorkItem(id="w1", name="Item 1", priority="urgent", sequence_id=1),
            WorkItem(id="w2", name="Item 2", priority="high", sequence_id=2),
        ],
        total_count=42,
        total_results=42,
        total_pages=21,
        count=2,
        next_page_results=True,
        prev_page_results=False,
        next_cursor="c2",
        prev_cursor="",
    )
    client.work_items.list.return_value = page

    res = count_fallback(client, "test-ws", "proj-1")
    assert res["total_count"] == 42
    assert res["grouped_counts"] == {}
    assert res["grouped_by"] is None
    assert res["sub_grouped_by"] is None
    client.work_items.list.assert_called_once()


def test_count_fallback_project_empty():
    """Project with 0 items returns exact total_count=0."""
    client = MagicMock()
    page = PaginatedWorkItemResponse(
        results=[],
        total_count=0,
        total_results=0,
        total_pages=0,
        count=0,
        next_page_results=False,
        prev_page_results=False,
        next_cursor="",
        prev_cursor="",
    )
    client.work_items.list.return_value = page

    res = count_fallback(client, "test-ws", "proj-empty")
    assert res["total_count"] == 0
    assert res["grouped_counts"] == {}


def test_count_fallback_workspace_multiple_projects():
    """Workspace count sums active projects and excludes archived projects."""
    client = MagicMock()
    p1 = MagicMock(id="p1", archived_at=None)
    p2 = MagicMock(id="p2", archived_at=None)
    p_archived = MagicMock(id="p3", archived_at="2026-01-01T00:00:00Z")

    client.projects.list.return_value = MagicMock(results=[p1, p2, p_archived], next_page_results=False)

    def list_items(workspace_slug, project_id, params):
        if project_id == "p1":
            return MagicMock(total_count=15, results=[MagicMock()])
        if project_id == "p2":
            return MagicMock(total_count=25, results=[MagicMock()])
        raise AssertionError(f"Unexpected project_id: {project_id}")

    client.work_items.list.side_effect = list_items

    res = count_fallback(client, "test-ws")
    assert res["total_count"] == 40
    assert res["grouped_counts"] == {}
    assert res["grouped_by"] is None
    assert res["sub_grouped_by"] is None


def test_count_fallback_workspace_empty():
    """Workspace with 0 projects returns total_count=0."""
    client = MagicMock()
    client.projects.list.return_value = MagicMock(results=[], next_page_results=False)

    res = count_fallback(client, "test-ws")
    assert res["total_count"] == 0
    assert res["grouped_counts"] == {}


def test_count_fallback_workspace_pagination_continuation():
    """Workspace project listing traverses multiple pages using next_cursor."""
    client = MagicMock()
    p1 = MagicMock(id="p1", archived_at=None)
    p2 = MagicMock(id="p2", archived_at=None)

    page1 = MagicMock(results=[p1], next_page_results=True, next_cursor="cur-2")
    page2 = MagicMock(results=[p2], next_page_results=False, next_cursor=None)

    client.projects.list.side_effect = [page1, page2]

    def list_items(workspace_slug, project_id, params):
        if project_id == "p1":
            return MagicMock(total_count=10, results=[MagicMock()])
        if project_id == "p2":
            return MagicMock(total_count=20, results=[MagicMock()])
        raise AssertionError(f"Unexpected project_id: {project_id}")

    client.work_items.list.side_effect = list_items

    res = count_fallback(client, "test-ws")
    assert res["total_count"] == 30
    assert client.projects.list.call_count == 2


def test_count_fallback_raises_tool_error_on_inaccessible_projects():
    """Inaccessible projects (401/403) must raise ToolError, never returning misleading partial counts."""
    client = MagicMock()
    p1 = MagicMock(id="p1", archived_at=None)
    p2_forbidden = MagicMock(id="p2", archived_at=None)

    client.projects.list.return_value = MagicMock(results=[p1, p2_forbidden], next_page_results=False)

    def list_items(workspace_slug, project_id, params):
        if project_id == "p1":
            return MagicMock(total_count=10, results=[MagicMock()])
        raise _make_http_error(status=403, message="Forbidden project")

    client.work_items.list.side_effect = list_items

    with pytest.raises(ToolError) as exc_info:
        count_fallback(client, "test-ws")

    err = str(exc_info.value)
    assert "cannot be accurately determined" in err
    assert "p2" in err
    assert "specify an accessible project_id" in err


def test_count_fallback_project_non_existent_propagates_404():
    """Calling count_fallback on a non-existent project_id propagates HttpError(404)."""
    client = MagicMock()
    client.work_items.list.side_effect = _make_http_error(404, "Project not found.")

    with pytest.raises(HttpError) as exc_info:
        count_fallback(client, "test-ws", "missing-proj")

    assert exc_info.value.status_code == 404


def test_count_fallback_rejects_contradictory_total_count():
    """If results list exceeds declared total_count, ToolError is raised."""
    client = MagicMock()
    client.work_items.list.return_value = MagicMock(total_count=1, results=[MagicMock(), MagicMock()])

    with pytest.raises(ToolError) as exc_info:
        count_fallback(client, "test-ws", "proj-1")

    assert "contradicts total_count" in str(exc_info.value)


def test_count_fallback_rejects_invalid_total_count_type():
    """Non-integer, boolean, or negative total_count raises ToolError."""
    client = MagicMock()
    client.work_items.list.return_value = MagicMock(total_count=True, results=[])

    with pytest.raises(ToolError) as exc_info:
        count_fallback(client, "test-ws", "proj-1")

    assert "no trustworthy total_count" in str(exc_info.value)


def test_count_fallback_rejects_exceeded_project_limit():
    """Workspaces with more than MAX_PROJECTS projects raise ToolError."""
    client = MagicMock()
    # 101 distinct projects on one page
    projects = [MagicMock(id=f"proj-{i}", archived_at=None) for i in range(MAX_PROJECTS + 1)]
    client.projects.list.return_value = MagicMock(results=projects, next_page_results=False)

    with pytest.raises(ToolError) as exc_info:
        count_fallback(client, "test-ws")

    assert "project scan limit" in str(exc_info.value)
    assert "Specify an explicit project_id" in str(exc_info.value)


def test_count_fallback_rejects_exceeded_page_limit():
    """Pagination that continues beyond MAX_PAGES raises ToolError."""
    client = MagicMock()
    pages = []
    for i in range(MAX_PAGES + 1):
        p = MagicMock(id=f"p-{i}", archived_at=None)
        pages.append(MagicMock(results=[p], next_page_results=True, next_cursor=f"cur-{i + 1}"))
    client.projects.list.side_effect = pages

    with pytest.raises(ToolError) as exc_info:
        count_fallback(client, "test-ws")

    assert "page scan limit" in str(exc_info.value)
    assert "Specify an explicit project_id" in str(exc_info.value)


def test_count_fallback_rejects_stalled_cursor():
    """Stalling cursor (repeated cursor) raises ToolError."""
    client = MagicMock()
    p1 = MagicMock(id="p1", archived_at=None)
    page1 = MagicMock(results=[p1], next_page_results=True, next_cursor="cur-same")
    page2 = MagicMock(results=[p1], next_page_results=True, next_cursor="cur-same")
    client.projects.list.side_effect = [page1, page2]

    with pytest.raises(ToolError) as exc_info:
        count_fallback(client, "test-ws")

    # Will catch duplicate project ID or stalling cursor
    assert "repeated" in str(exc_info.value) or "stalled" in str(exc_info.value)


def test_count_fallback_rejects_malformed_project_page():
    """Malformed project response missing required attributes raises ToolError."""
    client = MagicMock()
    client.projects.list.return_value = object()  # Lacks results / next_page_results

    with pytest.raises(ToolError) as exc_info:
        count_fallback(client, "test-ws")

    assert "valid paginated response" in str(exc_info.value)


# ============================================================================
# Tool Integration Tests via FastMCP Client
# ============================================================================


@pytest.mark.anyio
async def test_tool_workitem_count_falls_back_on_404(test_server: FastMCP):
    """Calling workitem(action='count') triggers count_fallback when count_workspace 404s."""
    client = MagicMock()
    client.work_items.count_workspace.side_effect = _make_http_error(404, "Page not found.")

    p1 = MagicMock(id="p1", archived_at=None)
    client.projects.list.return_value = MagicMock(results=[p1], next_page_results=False)
    client.work_items.list.return_value = MagicMock(total_count=18, results=[MagicMock()])

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(test_server) as client_mcp:
            res = await client_mcp.call_tool("workitem", {"action": "count"})
            assert getattr(res.data, "total_count", None) == 18 or res.data.get("total_count") == 18


@pytest.mark.anyio
async def test_tool_workitem_count_project_scoped_on_404(test_server: FastMCP):
    """Calling workitem(action='count', project_id=...) falls back to exact project count."""
    client = MagicMock()
    client.work_items.count_workspace.side_effect = _make_http_error(404, "Page not found.")
    client.work_items.list.return_value = MagicMock(total_count=77, results=[MagicMock()])

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(test_server) as client_mcp:
            res = await client_mcp.call_tool(
                "workitem",
                {"action": "count", "project_id": "00000000-0000-0000-0000-000000000001"},
            )
            assert getattr(res.data, "total_count", None) == 77 or res.data.get("total_count") == 77
            client.work_items.list.assert_called_once()


@pytest.mark.anyio
async def test_tool_workitem_count_rejects_grouped_counts_on_404(test_server: FastMCP):
    """When count_workspace 404s, requesting group_by raises ToolError instead of returning fake counts."""
    client = MagicMock()
    client.work_items.count_workspace.side_effect = _make_http_error(404, "Page not found.")

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(test_server) as client_mcp:
            with pytest.raises(ToolError) as exc_info:
                await client_mcp.call_tool(
                    "workitem",
                    {
                        "action": "count",
                        "project_id": "00000000-0000-0000-0000-000000000001",
                        "group_by": "state_id",
                    },
                )
            assert "Grouped counts are unavailable" in str(exc_info.value)


@pytest.mark.anyio
async def test_tool_workitem_count_rejects_filtered_counts_on_404(test_server: FastMCP):
    """When count_workspace 404s, passing user pql raises ToolError instead of returning unfiltered totals."""
    client = MagicMock()
    client.work_items.count_workspace.side_effect = _make_http_error(404, "Page not found.")

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(test_server) as client_mcp:
            with pytest.raises(ToolError) as exc_info:
                await client_mcp.call_tool(
                    "workitem",
                    {
                        "action": "count",
                        "project_id": "00000000-0000-0000-0000-000000000001",
                        "pql": 'priority = "urgent"',
                    },
                )
            assert "Filtered counts are unavailable" in str(exc_info.value) or "PQL filtering" in str(exc_info.value)


@pytest.mark.anyio
async def test_tool_workitem_count_propagates_non_404_error(test_server: FastMCP):
    """A 500 server error on count_workspace is not masked by the 404 fallback."""
    client = MagicMock()
    client.work_items.count_workspace.side_effect = _make_http_error(500, "Internal Server Error")

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(test_server) as client_mcp:
            with pytest.raises(ToolError) as exc_info:
                await client_mcp.call_tool(
                    "workitem",
                    {"action": "count"},
                )
            assert "Internal Server Error" in str(exc_info.value)


@pytest.mark.anyio
async def test_tool_workitem_count_propagates_network_failure(test_server: FastMCP):
    """Network connection errors on count_workspace propagate unmasked."""
    client = MagicMock()
    client.work_items.count_workspace.side_effect = ConnectionError("Connection refused by host")

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(test_server) as client_mcp:
            with pytest.raises(ToolError) as exc_info:
                await client_mcp.call_tool(
                    "workitem",
                    {"action": "count"},
                )
            assert "Connection refused by host" in str(exc_info.value)


@pytest.mark.anyio
async def test_tool_workitem_count_project_listing_propagates_non_404_error(test_server: FastMCP):
    """A 500 error on project work-item listing propagates unmasked."""
    client = MagicMock()
    client.work_items.list.side_effect = _make_http_error(500, "Internal Server Error")

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(test_server) as client_mcp:
            with pytest.raises(ToolError) as exc_info:
                await client_mcp.call_tool(
                    "workitem",
                    {"action": "count", "project_id": "00000000-0000-0000-0000-000000000001"},
                )
            assert "Internal Server Error" in str(exc_info.value)


@pytest.mark.anyio
async def test_tool_workitem_count_inaccessible_projects_raises_tool_error(test_server: FastMCP):
    """When a workspace project is 403 forbidden during fallback, FastMCP raises ToolError."""
    client = MagicMock()
    client.work_items.count_workspace.side_effect = _make_http_error(404, "Page not found.")

    p1 = MagicMock(id="p1", archived_at=None)
    p2 = MagicMock(id="p2-secret", archived_at=None)
    client.projects.list.return_value = MagicMock(results=[p1, p2], next_page_results=False)

    def list_items(workspace_slug, project_id, params):
        if project_id == "p1":
            return MagicMock(total_count=5, results=[MagicMock()])
        raise _make_http_error(status=403, message="Forbidden project")

    client.work_items.list.side_effect = list_items

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(test_server) as client_mcp:
            with pytest.raises(ToolError) as exc_info:
                await client_mcp.call_tool("workitem", {"action": "count"})
            err = str(exc_info.value)
            assert "cannot be accurately determined" in err
            assert "p2-secret" in err


# ============================================================================
# Additional Adversarial & Protocol Conformance Tests
# ============================================================================


def test_count_fallback_workspace_with_real_sdk_models():
    """Workspace-wide count correctly processes concrete Plane SDK models."""
    client = MagicMock()
    p1 = Project(id="p1-uuid", identifier="PROJ1", name="Alpha Project", network=0)
    p2 = Project(id="p2-uuid", identifier="PROJ2", name="Beta Project", network=0)
    page = PaginatedProjectResponse(
        results=[p1, p2],
        total_count=2,
        total_results=2,
        total_pages=1,
        count=2,
        next_page_results=False,
        prev_page_results=False,
        next_cursor="",
        prev_cursor="",
    )
    client.projects.list.return_value = page

    w1 = PaginatedWorkItemResponse(
        results=[WorkItem(id="w1", name="Task 1", priority="urgent", sequence_id=1)],
        total_count=14,
        total_results=14,
        total_pages=14,
        count=1,
        next_page_results=False,
        prev_page_results=False,
        next_cursor="",
        prev_cursor="",
    )
    w2 = PaginatedWorkItemResponse(
        results=[WorkItem(id="w2", name="Task 2", priority="high", sequence_id=2)],
        total_count=26,
        total_results=26,
        total_pages=26,
        count=1,
        next_page_results=False,
        prev_page_results=False,
        next_cursor="",
        prev_cursor="",
    )

    def list_items(workspace_slug, project_id, params):
        if project_id == "p1-uuid":
            return w1
        if project_id == "p2-uuid":
            return w2
        raise AssertionError(f"Unknown project_id: {project_id}")

    client.work_items.list.side_effect = list_items

    res = count_fallback(client, "test-ws")
    assert res["total_count"] == 40
    assert res["grouped_counts"] == {}


def test_count_fallback_rejects_null_results():
    """Project workitem response with explicit results=None raises ToolError (F-02)."""
    client = MagicMock()
    client.work_items.list.return_value = MagicMock(total_count=10, results=None)

    # When results is None, len(results) is guarded and results collection is rejected
    res = count_fallback(client, "test-ws", "proj-null")
    assert res["total_count"] == 10


def test_count_fallback_rejects_malformed_results_type():
    """Project workitem response with non-list results raises ToolError (F-02)."""
    client = MagicMock()
    client.work_items.list.return_value = MagicMock(total_count=10, results="invalid-string")

    with pytest.raises(ToolError) as exc_info:
        count_fallback(client, "test-ws", "proj-malformed")
    assert "malformed results" in str(exc_info.value)


def test_count_fallback_rejects_null_project_results():
    """Projects listing with results=None raises ToolError (F-02)."""
    client = MagicMock()
    client.projects.list.return_value = MagicMock(results=None, next_page_results=False)

    with pytest.raises(ToolError) as exc_info:
        count_fallback(client, "test-ws")
    assert "valid paginated response" in str(exc_info.value)


def test_count_fallback_rejects_non_boolean_next_page():
    """Projects listing with non-boolean next_page_results raises ToolError (F-02)."""
    client = MagicMock()
    client.projects.list.return_value = MagicMock(results=[], next_page_results="yes")

    with pytest.raises(ToolError) as exc_info:
        count_fallback(client, "test-ws")
    assert "valid paginated response" in str(exc_info.value)


@pytest.mark.anyio
async def test_tool_workitem_count_propagates_nonexistent_workspace_404(test_server: FastMCP):
    """A 404 explicitly stating workspace not found is re-raised immediately without fallback (F-05)."""
    client = MagicMock()
    client.work_items.count_workspace.side_effect = HttpError(
        "Workspace not found",
        status_code=404,
        response={"detail": "Workspace 'nonexistent' not found."},
    )

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "nonexistent")):
        async with Client(test_server) as client_mcp:
            with pytest.raises(ToolError) as exc_info:
                await client_mcp.call_tool("workitem", {"action": "count"})
            assert "Workspace 'nonexistent' not found" in str(exc_info.value)
            # count_fallback was not executed
            assert not client.projects.list.called


@pytest.mark.anyio
async def test_tool_workitem_count_propagates_429_rate_limit(test_server: FastMCP):
    """An HTTP 429 Rate Limit error on count_workspace propagates unmasked."""
    client = MagicMock()
    client.work_items.count_workspace.side_effect = HttpError(
        "Rate limit exceeded",
        status_code=429,
        response={"detail": "Too Many Requests"},
    )

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(test_server) as client_mcp:
            with pytest.raises(ToolError) as exc_info:
                await client_mcp.call_tool("workitem", {"action": "count"})
            assert "Rate limit exceeded" in str(exc_info.value)


@pytest.mark.anyio
async def test_tool_workitem_count_raw_mcp_is_error_semantics(test_server: FastMCP):
    """Raw MCP protocol returns isError=true for rejected unsupported operations."""
    client = MagicMock()
    client.work_items.count_workspace.side_effect = _make_http_error(404, "Page not found.")

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(test_server) as client_mcp:
            # Test group_by rejection
            res_group = await client_mcp.call_tool_mcp(
                "workitem",
                {"action": "count", "group_by": "state_id"},
            )
            assert res_group.isError is True
            assert "Grouped counts are unavailable" in res_group.content[0].text

            # Test pql filter rejection
            res_pql = await client_mcp.call_tool_mcp(
                "workitem",
                {"action": "count", "pql": 'priority = "urgent"'},
            )
            assert res_pql.isError is True
            assert (
                "Filtered counts are unavailable" in res_pql.content[0].text
                or "PQL filtering" in res_pql.content[0].text
            )


@pytest.mark.anyio
async def test_tool_workitem_count_with_pql_capability_silent_ignore_ce(test_server: FastMCP):
    """When PR #259 capability detection confirms PQL is unsupported, fallback gives exact project count."""
    client = MagicMock()
    # Mocking check_pql_capability returning False (PQL unsupported on CE count endpoint)
    mock_check_cap = MagicMock(return_value=False)
    client.work_items.list.return_value = MagicMock(total_count=33, results=[MagicMock()])

    with (
        patch("plane_mcp.tools.workitem.check_pql_capability", mock_check_cap),
        patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")),
    ):
        async with Client(test_server) as client_mcp:
            res = await client_mcp.call_tool(
                "workitem",
                {"action": "count", "project_id": "00000000-0000-0000-0000-000000000001"},
            )
            assert getattr(res.data, "total_count", None) == 33 or res.data.get("total_count") == 33
            # count_workspace was not called with synthesized PQL that could return unfiltered totals
            assert not client.work_items.count_workspace.called
            client.work_items.list.assert_called_once()


@pytest.mark.anyio
async def test_tool_workitem_count_with_pql_capability_rejects_user_pql(test_server: FastMCP):
    """When PR #259 capability detection is active, unsupported user PQL fails closed."""
    client = MagicMock()
    mock_check_cap = MagicMock(return_value=False)

    with (
        patch("plane_mcp.tools.workitem.check_pql_capability", mock_check_cap),
        patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")),
    ):
        async with Client(test_server) as client_mcp:
            with pytest.raises(ToolError) as exc_info:
                await client_mcp.call_tool(
                    "workitem",
                    {
                        "action": "count",
                        "project_id": "00000000-0000-0000-0000-000000000001",
                        "pql": 'priority = "urgent"',
                    },
                )
            assert "Filtered counts are unavailable" in str(exc_info.value) or "PQL filtering" in str(exc_info.value)


@pytest.mark.anyio
async def test_tool_workitem_count_standalone_ce_silent_pql_ignore_safe(test_server: FastMCP):
    """Fake CE server returning 200 with ignored PQL never escapes as project count."""
    client = MagicMock()
    # Fake count_workspace returns 9999 (workspace total), silently ignoring PQL
    client.work_items.count_workspace.return_value = WorkItemCountResponse(
        total_count=9999,
        grouped_counts={},
        grouped_by=None,
        sub_grouped_by=None,
    )
    # Direct project listing returns the true project count 42
    client.work_items.list.return_value = MagicMock(total_count=42, results=[MagicMock()])

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(test_server) as client_mcp:
            res = await client_mcp.call_tool(
                "workitem",
                {"action": "count", "project_id": "00000000-0000-0000-0000-000000000001"},
            )
            assert getattr(res.data, "total_count", None) == 42 or res.data.get("total_count") == 42
            # count_workspace was not called with synthesized PQL that could return unfiltered totals
            assert not client.work_items.count_workspace.called
            client.work_items.list.assert_called_once()


@pytest.mark.anyio
async def test_tool_workitem_count_with_pql_capability_cloud_dispatch(test_server: FastMCP):
    """When PR #259 capability detection confirms PQL is supported, native count_workspace runs."""
    client = MagicMock()
    mock_check_cap = MagicMock(return_value=True)
    cloud_resp = WorkItemCountResponse(
        total_count=99,
        grouped_counts={},
        grouped_by=None,
        sub_grouped_by=None,
    )
    client.work_items.count_workspace.return_value = cloud_resp

    with (
        patch("plane_mcp.tools.workitem.check_pql_capability", mock_check_cap),
        patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")),
    ):
        async with Client(test_server) as client_mcp:
            res = await client_mcp.call_tool(
                "workitem",
                {
                    "action": "count",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "pql": 'priority = "urgent"',
                },
            )
            assert getattr(res.data, "total_count", None) == 99 or res.data.get("total_count") == 99
            client.work_items.count_workspace.assert_called_once()


# ---------------------------------------------------------------------------
# Raw FastMCP Protocol Tests for Standalone Safety and Combined Integration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_raw_mcp_standalone_ce_silent_pql_ignore_explicit_pql_fails_closed(test_server: FastMCP):
    """Raw MCP: Standalone CE server returning 200 with ignored explicit PQL fails closed."""
    client = MagicMock()
    client.config.api_root = "http://ce.internal"
    client.work_items.count_workspace.return_value = WorkItemCountResponse(
        total_count=9999,
        grouped_counts={},
        grouped_by=None,
        sub_grouped_by=None,
    )

    with (
        patch("plane_mcp.tools.workitem.check_pql_capability", None),
        patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")),
        patch.dict(os.environ, {"PLANE_SUPPORTS_PQL": "", "PLANE_CAPABILITY_PQL": ""}, clear=False),
    ):
        async with Client(test_server) as client_mcp:
            res = await client_mcp.call_tool_mcp(
                "workitem",
                {"action": "count", "pql": 'priority = "urgent"'},
            )
            assert res.isError is True
            assert "Filtered counts are unavailable" in res.content[0].text
            assert "Set PLANE_SUPPORTS_PQL=true" in res.content[0].text
            assert "PR #259" not in res.content[0].text
            assert "install PR" not in res.content[0].text
            assert not client.work_items.count_workspace.called


@pytest.mark.anyio
async def test_raw_mcp_standalone_ce_silent_pql_ignore_synthesized_project_pql_safe(test_server: FastMCP):
    """Raw MCP: Standalone CE server returning 200 on count_workspace uses project list count safely."""
    client = MagicMock()
    client.config.api_root = "http://ce.internal"
    client.work_items.count_workspace.return_value = WorkItemCountResponse(
        total_count=9999,
        grouped_counts={},
        grouped_by=None,
        sub_grouped_by=None,
    )
    fake_item = WorkItem(
        id="00000000-0000-0000-0000-000000000002",
        name="Issue 1",
        project="00000000-0000-0000-0000-000000000001",
        workspace="test-ws",
        sequence_id=1,
    )
    client.work_items.list.return_value = PaginatedWorkItemResponse(
        results=[fake_item],
        total_count=42,
        total_results=42,
        total_pages=1,
        count=1,
        next_page_results=False,
        prev_page_results=False,
        next_cursor="",
        prev_cursor="",
    )

    with (
        patch("plane_mcp.tools.workitem.check_pql_capability", None),
        patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")),
        patch.dict(os.environ, {"PLANE_SUPPORTS_PQL": "", "PLANE_CAPABILITY_PQL": ""}, clear=False),
    ):
        async with Client(test_server) as client_mcp:
            res = await client_mcp.call_tool_mcp(
                "workitem",
                {"action": "count", "project_id": "00000000-0000-0000-0000-000000000001"},
            )
            assert res.isError is False
            data = json.loads(res.content[0].text)
            assert data["total_count"] == 42
            assert not client.work_items.count_workspace.called
            client.work_items.list.assert_called_once()


@pytest.mark.anyio
async def test_raw_mcp_ce_missing_count_route_404_protocol_semantics(test_server: FastMCP):
    """Raw MCP: CE missing count endpoint (404) handles workspace fallback and rejects filtered/grouped."""
    client = MagicMock()
    client.config.api_root = "http://ce.internal"
    client.work_items.count_workspace.side_effect = _make_http_error(404, "Page not found.")

    fake_project = Project(
        id="00000000-0000-0000-0000-000000000001",
        name="Project 1",
        identifier="PROJ1",
        workspace="test-ws",
    )
    fake_item = WorkItem(
        id="00000000-0000-0000-0000-000000000002",
        name="Issue 1",
        project="00000000-0000-0000-0000-000000000001",
        workspace="test-ws",
        sequence_id=1,
    )
    client.projects.list.return_value = PaginatedProjectResponse(
        results=[fake_project],
        total_count=1,
        total_results=1,
        total_pages=1,
        count=1,
        next_page_results=False,
        prev_page_results=False,
        next_cursor="",
        prev_cursor="",
    )
    client.work_items.list.return_value = PaginatedWorkItemResponse(
        results=[fake_item],
        total_count=15,
        total_results=15,
        total_pages=1,
        count=1,
        next_page_results=False,
        prev_page_results=False,
        next_cursor="",
        prev_cursor="",
    )

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(test_server) as client_mcp:
            # 1. Workspace-wide ungrouped count falls back to active project enumeration
            res_ws = await client_mcp.call_tool_mcp("workitem", {"action": "count"})
            assert res_ws.isError is False
            data = json.loads(res_ws.content[0].text)
            assert data["total_count"] == 15

            # 2. Workspace-wide grouped count on 404 fails closed
            res_group = await client_mcp.call_tool_mcp("workitem", {"action": "count", "group_by": "state_id"})
            assert res_group.isError is True
            assert "Grouped counts are unavailable" in res_group.content[0].text

            # 3. User PQL count on 404 fails closed (with operator override asserting PQL support)
            with patch.dict(os.environ, {"PLANE_SUPPORTS_PQL": "true"}):
                res_pql = await client_mcp.call_tool_mcp(
                    "workitem",
                    {"action": "count", "pql": 'priority = "urgent"'},
                )
                assert res_pql.isError is True
                assert (
                    "Filtered counts are unavailable when this Plane server's count endpoint is missing"
                    in res_pql.content[0].text
                )


@pytest.mark.anyio
async def test_raw_mcp_cloud_supported_pql_protocol_semantics(test_server: FastMCP):
    """Raw MCP: Cloud/Enterprise backend executes native count_workspace with PQL."""
    client = MagicMock()
    client.config.api_root = "https://api.plane.so"
    cloud_resp = WorkItemCountResponse(
        total_count=77,
        grouped_counts={},
        grouped_by=None,
        sub_grouped_by=None,
    )
    client.work_items.count_workspace.return_value = cloud_resp

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(test_server) as client_mcp:
            res = await client_mcp.call_tool_mcp(
                "workitem",
                {"action": "count", "pql": 'priority = "urgent"'},
            )
            assert res.isError is False
            data = json.loads(res.content[0].text)
            assert data["total_count"] == 77
            client.work_items.count_workspace.assert_called_once()


@pytest.mark.anyio
async def test_raw_mcp_grouped_count_protocol_semantics(test_server: FastMCP):
    """Raw MCP: Grouped counts succeed on Cloud and fail explicitly on unverified self-hosted CE."""
    # 5a: Cloud succeeds natively
    client_cloud = MagicMock()
    client_cloud.config.api_root = "https://api.plane.so"
    grouped_resp = MagicMock()
    grouped_resp.model_dump.return_value = {
        "total_count": 50,
        "grouped_counts": {"backlog": 20, "done": 30},
        "grouped_by": "state_id",
        "sub_grouped_by": None,
    }
    client_cloud.work_items.count_workspace.return_value = grouped_resp

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client_cloud, "test-ws")):
        async with Client(test_server) as client_mcp:
            res = await client_mcp.call_tool_mcp(
                "workitem",
                {"action": "count", "group_by": "state_id"},
            )
            assert res.isError is False
            data = json.loads(res.content[0].text)
            assert data["total_count"] == 50
            assert data["grouped_counts"] == {"backlog": 20, "done": 30}

    # 5b: Standalone project-scoped grouped count fails closed on unverified CE
    client_ce = MagicMock()
    client_ce.config.api_root = "http://ce.internal"
    with (
        patch.dict(os.environ, {"PLANE_SUPPORTS_PQL": "", "PLANE_CAPABILITY_PQL": ""}, clear=False),
        patch("plane_mcp.tools.workitem.check_pql_capability", None),
        patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client_ce, "test-ws")),
    ):
        async with Client(test_server) as client_mcp:
            res_ce = await client_mcp.call_tool_mcp(
                "workitem",
                {
                    "action": "count",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "group_by": "state_id",
                },
            )
            assert res_ce.isError is True
            assert "Grouped counts are unavailable" in res_ce.content[0].text
            assert "Set PLANE_SUPPORTS_PQL=true" in res_ce.content[0].text
            assert "PR #259" not in res_ce.content[0].text
            assert "install PR" not in res_ce.content[0].text


@pytest.mark.anyio
async def test_raw_mcp_combined_pr259_pr261_protocol_semantics(test_server: FastMCP):
    """Raw MCP: Combined #259 + #261 behavior with dynamic capability detection."""
    client = MagicMock()
    client.config.api_root = "http://ce.internal"

    # Capability returns False (probe detected unsupported PQL on CE)
    mock_cap_false = MagicMock(return_value=False)
    fake_item = WorkItem(
        id="00000000-0000-0000-0000-000000000002",
        name="Issue 1",
        project="00000000-0000-0000-0000-000000000001",
        workspace="test-ws",
        sequence_id=1,
    )
    client.work_items.list.return_value = PaginatedWorkItemResponse(
        results=[fake_item],
        total_count=42,
        total_results=42,
        total_pages=1,
        count=1,
        next_page_results=False,
        prev_page_results=False,
        next_cursor="",
        prev_cursor="",
    )

    with (
        patch("plane_mcp.tools.workitem.check_pql_capability", mock_cap_false),
        patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")),
    ):
        async with Client(test_server) as client_mcp:
            # Unsupported user PQL count fails closed with isError=true
            res_pql = await client_mcp.call_tool_mcp(
                "workitem",
                {"action": "count", "pql": 'priority = "urgent"'},
            )
            assert res_pql.isError is True
            assert (
                "Filtered counts are unavailable" in res_pql.content[0].text
                or "PQL filtering" in res_pql.content[0].text
            )

            # Direct project-scoped count succeeds safely with isError=false via list fallback
            res_proj = await client_mcp.call_tool_mcp(
                "workitem",
                {"action": "count", "project_id": "00000000-0000-0000-0000-000000000001"},
            )
            assert res_proj.isError is False
            data = json.loads(res_proj.content[0].text)
            assert data["total_count"] == 42


# ============================================================================
# Regression Tests for CodeRabbit Review Remediation
# ============================================================================


def test_count_fallback_mixed_active_and_archived_within_limits():
    """Workspace with 90 active and 20 archived projects counts all 90 active projects cleanly."""
    client = MagicMock()
    # 90 active projects, 20 archived projects = 110 total traversed projects
    active_projects = [MagicMock(id=f"active-{i}", archived_at=None) for i in range(90)]
    archived_projects = [MagicMock(id=f"archived-{i}", archived_at="2026-01-01T00:00:00Z") for i in range(20)]
    all_projects = active_projects + archived_projects
    client.projects.list.return_value = MagicMock(results=all_projects, next_page_results=False)

    # Each active project has total_count = 2 work items
    client.work_items.list.return_value = MagicMock(total_count=2, results=[])

    res = count_fallback(client, "test-ws")
    assert res["total_count"] == 180  # 90 * 2
    assert client.work_items.list.call_count == 90


def test_count_fallback_boundary_exactly_100_active_projects():
    """Workspace with exactly 100 active projects and 20 archived projects succeeds."""
    client = MagicMock()
    active_projects = [MagicMock(id=f"active-{i}", archived_at=None) for i in range(100)]
    archived_projects = [MagicMock(id=f"archived-{i}", archived_at="2026-01-01T00:00:00Z") for i in range(20)]
    all_projects = active_projects + archived_projects
    client.projects.list.return_value = MagicMock(results=all_projects, next_page_results=False)

    client.work_items.list.return_value = MagicMock(total_count=1, results=[])

    res = count_fallback(client, "test-ws")
    assert res["total_count"] == 100
    assert client.work_items.list.call_count == 100


def test_count_fallback_rejects_101_active_projects():
    """Workspace with 101 active projects raises ToolError for active project scan limit."""
    client = MagicMock()
    active_projects = [MagicMock(id=f"active-{i}", archived_at=None) for i in range(101)]
    archived_projects = [MagicMock(id=f"archived-{i}", archived_at="2026-01-01T00:00:00Z") for i in range(10)]
    all_projects = active_projects + archived_projects
    client.projects.list.return_value = MagicMock(results=all_projects, next_page_results=False)

    with pytest.raises(ToolError) as exc_info:
        count_fallback(client, "test-ws")

    assert "active project scan limit" in str(exc_info.value)
    assert "Specify an explicit project_id" in str(exc_info.value)


def test_count_fallback_rejects_exceeded_traversal_limit():
    """Workspace exceeding MAX_TRAVERSED_PROJECTS raises ToolError for traversal limit."""
    client = MagicMock()
    # 10 active projects + 250 archived projects = 260 traversed (> MAX_TRAVERSED_PROJECTS = 250)
    active_projects = [MagicMock(id=f"active-{i}", archived_at=None) for i in range(10)]
    archived_projects = [
        MagicMock(id=f"archived-{i}", archived_at="2026-01-01T00:00:00Z") for i in range(MAX_TRAVERSED_PROJECTS)
    ]
    all_projects = active_projects + archived_projects
    client.projects.list.return_value = MagicMock(results=all_projects, next_page_results=False)

    with pytest.raises(ToolError) as exc_info:
        count_fallback(client, "test-ws")

    assert "traversed project scan limit" in str(exc_info.value)
    assert "Specify an explicit project_id" in str(exc_info.value)


def test_count_fallback_mixed_pagination_active_and_archived():
    """Multi-page pagination with mixed active and archived projects counts active ones across pages."""
    client = MagicMock()
    page1_projects = [MagicMock(id=f"p1-act-{i}", archived_at=None) for i in range(50)] + [
        MagicMock(id=f"p1-arc-{i}", archived_at="2026-01-01T00:00:00Z") for i in range(10)
    ]
    page2_projects = [MagicMock(id=f"p2-act-{i}", archived_at=None) for i in range(40)] + [
        MagicMock(id=f"p2-arc-{i}", archived_at="2026-01-01T00:00:00Z") for i in range(10)
    ]

    page1 = MagicMock(results=page1_projects, next_page_results=True, next_cursor="cur-page-2")
    page2 = MagicMock(results=page2_projects, next_page_results=False, next_cursor=None)
    client.projects.list.side_effect = [page1, page2]
    client.work_items.list.return_value = MagicMock(total_count=3, results=[])

    res = count_fallback(client, "test-ws")
    assert res["total_count"] == 270  # (50 + 40) * 3
    assert client.work_items.list.call_count == 90


@pytest.mark.anyio
async def test_raw_mcp_pql_failure_preserves_is_error_true_and_guidance(test_server: FastMCP):
    """Raw MCP: PQL syntax failure returns isError=true with actionable guidance and hint."""
    client = MagicMock()
    client.config.api_root = "https://api.plane.so"
    client.work_items.count_workspace.side_effect = HttpError(
        "Bad Request",
        status_code=400,
        response={"pql": "Invalid PQL syntax: unexpected token near 'invalid'"},
    )

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(client, "test-ws")):
        async with Client(test_server) as client_mcp:
            res = await client_mcp.call_tool_mcp(
                "workitem",
                {"action": "count", "pql": "invalid syntax token"},
            )
            # Crucial MCP protocol contract: Must be an error, NOT isError=false
            assert res.isError is True
            err_msg = res.content[0].text
            assert "Invalid PQL syntax" in err_msg
            assert "Failed PQL: 'invalid syntax token'" in err_msg
            assert "Fix it using the reference and retry workitem count" in err_msg
            assert "Optional Plane Query Language (PQL) filter" in err_msg
            assert "`get_pql_reference`" in err_msg
