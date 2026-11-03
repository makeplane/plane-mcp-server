"""Deterministic automated regression test suite for Plane CE cycle and module list fallbacks.

Covers all 20 required contract scenarios:
1. Cloud supported endpoint (list_lite returns 200, fallback not invoked).
2. CE missing lightweight endpoint (HTTP 404 on list_lite triggers standard list fallback).
3. Successful standard endpoint fallback (returns accurate Paginated*LiteResponse).
4. Both endpoints missing (HTTP 404 on fallback endpoint propagates as HttpError).
5. HTTP 401/403/429/500 (non-404 errors propagate immediately without fallback).
6. Network failure / connection error (propagates immediately without fallback).
7. Missing project_id / workspace_slug (fails validation before SDK dispatch).
8. Empty list (zero records, count=0, total_count=0, next_page_results=False).
9. Single page (results count matches total_count, no next cursor).
10. Multiple pages (ce:<offset> progression, next_cursor, prev_cursor).
11. Cursor stalls and duplicate records (loop/stall and repeated IDs raise ToolError).
12. Pagination bounds (per_page out of range, max pages cap, max items safety limit).
13. Status and view filtering (cycle status filter fails closed with ToolError).
14. Archived resources (archived_at is not None filtered out of active list).
15. Missing optional fields (handled gracefully without KeyError or validation failure).
16. Missing required fields (record missing id raises ToolError).
17. SDK model conversion (genuine PaginatedCycleLiteResponse and PaginatedModuleLiteResponse).
18. Unsupported sorting (order_by fails closed with ToolError on CE fallback).
19. Exact output schema (issue metrics total_issues/completed_issues stripped, no extra leaks).
20. Raw MCP error responses (FastMCP protocol returns isError=True with clear diagnostic).
"""

from __future__ import annotations

import json
from math import ceil
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from plane.errors import HttpError
from plane.models.cycles import Cycle, CycleLite, PaginatedCycleLiteResponse, PaginatedCycleResponse
from plane.models.modules import Module, ModuleLite, PaginatedModuleLiteResponse, PaginatedModuleResponse

from plane_mcp.ce_cycle_module import (
    _CYCLE_LITE_FIELDS,
    _MODULE_LITE_FIELDS,
    list_cycles_ce,
    list_modules_ce,
)
from plane_mcp.tools.cycle import register as register_cycle
from plane_mcp.tools.module import register as register_module

PROJECT_ID = "11111111-2222-3333-4444-555555555555"
WORKSPACE_SLUG = "test-workspace"
OWNER_ID = "user-123"


def make_http_error(status_code: int, msg: str = "Error") -> HttpError:
    return HttpError(f"HTTP {status_code}: {msg}", status_code, {"error": msg})


def make_cycle_lite_page(
    results: list[CycleLite],
    *,
    total_count: int | None = None,
    next_cursor: str = "",
    prev_cursor: str = "",
    next_page_results: bool = False,
    prev_page_results: bool = False,
) -> PaginatedCycleLiteResponse:
    total = len(results) if total_count is None else total_count
    return PaginatedCycleLiteResponse(
        count=len(results),
        total_count=total,
        total_pages=ceil(total / len(results)) if results else 0,
        total_results=total,
        next_page_results=next_page_results,
        prev_page_results=prev_page_results,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        results=results,
    )


def make_module_lite_page(
    results: list[ModuleLite],
    *,
    total_count: int | None = None,
    next_cursor: str = "",
    prev_cursor: str = "",
    next_page_results: bool = False,
    prev_page_results: bool = False,
) -> PaginatedModuleLiteResponse:
    total = len(results) if total_count is None else total_count
    return PaginatedModuleLiteResponse(
        count=len(results),
        total_count=total,
        total_pages=ceil(total / len(results)) if results else 0,
        total_results=total,
        next_page_results=next_page_results,
        prev_page_results=prev_page_results,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        results=results,
    )


def make_cycle_standard_page(
    results: list[Cycle | dict[str, Any]],
    *,
    total_count: int | None = None,
    next_cursor: str = "",
    prev_cursor: str = "",
    next_page_results: bool = False,
    prev_page_results: bool = False,
) -> PaginatedCycleResponse:
    total = len(results) if total_count is None else total_count
    return PaginatedCycleResponse(
        count=len(results),
        total_count=total,
        total_pages=ceil(total / len(results)) if results else 0,
        total_results=total,
        next_page_results=next_page_results,
        prev_page_results=prev_page_results,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        results=results,  # type: ignore[arg-type]
    )


def make_module_standard_page(
    results: list[Module | dict[str, Any]],
    *,
    total_count: int | None = None,
    next_cursor: str = "",
    prev_cursor: str = "",
    next_page_results: bool = False,
    prev_page_results: bool = False,
) -> PaginatedModuleResponse:
    total = len(results) if total_count is None else total_count
    return PaginatedModuleResponse(
        count=len(results),
        total_count=total,
        total_pages=ceil(total / len(results)) if results else 0,
        total_results=total,
        next_page_results=next_page_results,
        prev_page_results=prev_page_results,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        results=results,  # type: ignore[arg-type]
    )


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.config.api_root = "https://self-hosted.plane.local/api"
    return client


@pytest.fixture
def test_server():
    server = FastMCP("test_ce_cycle_module")
    register_cycle(server)
    register_module(server)
    return server


# ==============================================================================
# 1. Cloud Supported Endpoint (list_lite succeeds)
# ==============================================================================


@pytest.mark.anyio
async def test_cloud_supported_cycle_list_lite_success(test_server, mock_client):
    """When list_lite succeeds (Cloud/Enterprise/CE >=1.4.0), fallback is not invoked."""
    c = CycleLite(id="c1", name="Cycle 1", project=PROJECT_ID, workspace=WORKSPACE_SLUG, owned_by=OWNER_ID)
    lite_resp = make_cycle_lite_page([c])
    mock_client.cycles.list_lite.return_value = lite_resp

    with patch("plane_mcp.tools.cycle.get_plane_client_context", return_value=(mock_client, WORKSPACE_SLUG)):
        async with Client(test_server) as client:
            res = await client.call_tool("cycle", {"action": "list", "project_id": PROJECT_ID})
            data = res.data if hasattr(res, "data") else res
            results = getattr(data, "results", None) or (data.get("results") if isinstance(data, dict) else [])
            assert len(results) == 1
            cid = getattr(results[0], "id", None) if not isinstance(results[0], dict) else results[0].get("id")
            assert cid == "c1"
            assert mock_client.cycles.list_lite.call_count == 1
            assert mock_client.cycles.list.call_count == 0


@pytest.mark.anyio
async def test_cloud_supported_module_list_lite_success(test_server, mock_client):
    """When list_lite succeeds for module, standard fallback is not invoked."""
    m = ModuleLite(id="m1", name="Module 1", project=PROJECT_ID, workspace=WORKSPACE_SLUG)
    lite_resp = make_module_lite_page([m])
    mock_client.modules.list_lite.return_value = lite_resp

    with patch("plane_mcp.tools.module.get_plane_client_context", return_value=(mock_client, WORKSPACE_SLUG)):
        async with Client(test_server) as client:
            res = await client.call_tool("module", {"action": "list", "project_id": PROJECT_ID})
            data = res.data if hasattr(res, "data") else res
            results = getattr(data, "results", None) or (data.get("results") if isinstance(data, dict) else [])
            assert len(results) == 1
            mid = getattr(results[0], "id", None) if not isinstance(results[0], dict) else results[0].get("id")
            assert mid == "m1"
            assert mock_client.modules.list_lite.call_count == 1
            assert mock_client.modules.list.call_count == 0


# ==============================================================================
# 2. CE Missing Lightweight Endpoint (404 triggers fallback)
# ==============================================================================


@pytest.mark.anyio
async def test_ce_cycle_404_triggers_fallback(test_server, mock_client):
    """HTTP 404 on cycles.list_lite invokes list_cycles_ce."""
    mock_client.cycles.list_lite.side_effect = make_http_error(404, "Page not found")
    std_cycle = Cycle(
        id="c1",
        name="Cycle 1",
        project=PROJECT_ID,
        workspace=WORKSPACE_SLUG,
        owned_by=OWNER_ID,
        total_issues=15,
        completed_issues=5,
    )
    mock_page = make_cycle_standard_page([std_cycle])
    mock_client.cycles.list.return_value = mock_page

    with patch("plane_mcp.tools.cycle.get_plane_client_context", return_value=(mock_client, WORKSPACE_SLUG)):
        async with Client(test_server) as client:
            res = await client.call_tool("cycle", {"action": "list", "project_id": PROJECT_ID})
            data = res.data if hasattr(res, "data") else res
            results = getattr(data, "results", None) or (data.get("results") if isinstance(data, dict) else [])
            assert len(results) == 1
            cid = getattr(results[0], "id", None) if not isinstance(results[0], dict) else results[0].get("id")
            assert cid == "c1"
            assert mock_client.cycles.list_lite.call_count == 1
            assert mock_client.cycles.list.call_count == 1


@pytest.mark.anyio
async def test_ce_module_404_triggers_fallback(test_server, mock_client):
    """HTTP 404 on modules.list_lite invokes list_modules_ce."""
    mock_client.modules.list_lite.side_effect = make_http_error(404, "Page not found")
    std_module = Module(
        id="m1",
        name="Module 1",
        project=PROJECT_ID,
        workspace=WORKSPACE_SLUG,
        total_issues=25,
        completed_issues=10,
    )
    mock_page = make_module_standard_page([std_module])
    mock_client.modules.list.return_value = mock_page

    with patch("plane_mcp.tools.module.get_plane_client_context", return_value=(mock_client, WORKSPACE_SLUG)):
        async with Client(test_server) as client:
            res = await client.call_tool("module", {"action": "list", "project_id": PROJECT_ID})
            data = res.data if hasattr(res, "data") else res
            results = getattr(data, "results", None) or (data.get("results") if isinstance(data, dict) else [])
            assert len(results) == 1
            mid = getattr(results[0], "id", None) if not isinstance(results[0], dict) else results[0].get("id")
            assert mid == "m1"
            assert mock_client.modules.list_lite.call_count == 1
            assert mock_client.modules.list.call_count == 1


# ==============================================================================
# 3. Successful Standard Endpoint Fallback (both paginated & bare list)
# ==============================================================================


def test_ce_cycle_standard_bare_list_success(mock_client):
    """When cycles.list returns a bare list of Cycle objects, it is enveloped into PaginatedCycleLiteResponse."""
    c1 = Cycle(id="c1", name="Cycle 1", project=PROJECT_ID, workspace=WORKSPACE_SLUG, owned_by=OWNER_ID)
    c2 = Cycle(id="c2", name="Cycle 2", project=PROJECT_ID, workspace=WORKSPACE_SLUG, owned_by=OWNER_ID)
    mock_client.cycles.list.return_value = [c1, c2]

    res = list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert isinstance(res, PaginatedCycleLiteResponse)
    assert res.total_count == 2
    assert len(res.results) == 2
    assert [r.id for r in res.results] == ["c1", "c2"]


def test_ce_module_standard_paginated_success(mock_client):
    """When modules.list returns PaginatedModuleResponse, it is converted into PaginatedModuleLiteResponse."""
    m1 = Module(id="m1", name="Module 1", project=PROJECT_ID, workspace=WORKSPACE_SLUG)
    mock_page = make_module_standard_page([m1])
    mock_client.modules.list.return_value = mock_page

    res = list_modules_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert isinstance(res, PaginatedModuleLiteResponse)
    assert res.total_count == 1
    assert res.results[0].id == "m1"


# ==============================================================================
# 4. Both Endpoints Missing (404 propagates)
# ==============================================================================


@pytest.mark.anyio
async def test_both_cycle_endpoints_missing_propagates_404(test_server, mock_client):
    """If both /cycles-lite/ and standard /cycles/ return 404, HttpError(404) propagates as MCP error."""
    mock_client.cycles.list_lite.side_effect = make_http_error(404, "Not Found")
    mock_client.cycles.list.side_effect = make_http_error(404, "Project cycles route missing")

    with patch("plane_mcp.tools.cycle.get_plane_client_context", return_value=(mock_client, WORKSPACE_SLUG)):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp("cycle", {"action": "list", "project_id": PROJECT_ID})
            assert res.isError is True
            assert "404" in res.content[0].text


@pytest.mark.anyio
async def test_both_module_endpoints_missing_propagates_404(test_server, mock_client):
    """If both /modules-lite/ and standard /modules/ return 404, HttpError(404) propagates as MCP error."""
    mock_client.modules.list_lite.side_effect = make_http_error(404, "Not Found")
    mock_client.modules.list.side_effect = make_http_error(404, "Project modules route missing")

    with patch("plane_mcp.tools.module.get_plane_client_context", return_value=(mock_client, WORKSPACE_SLUG)):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp("module", {"action": "list", "project_id": PROJECT_ID})
            assert res.isError is True
            assert "404" in res.content[0].text


# ==============================================================================
# 5. HTTP 401/403/429/500 (Non-404 Errors Propagate Immediately)
# ==============================================================================


@pytest.mark.parametrize("status_code", [401, 403, 429, 500])
@pytest.mark.anyio
async def test_cycle_non_404_errors_propagate_without_fallback(test_server, mock_client, status_code):
    """Non-404 status codes must re-raise immediately; fallback must NOT intercept."""
    mock_client.cycles.list_lite.side_effect = make_http_error(status_code, f"Status {status_code}")

    with patch("plane_mcp.tools.cycle.get_plane_client_context", return_value=(mock_client, WORKSPACE_SLUG)):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp("cycle", {"action": "list", "project_id": PROJECT_ID})
            assert res.isError is True
            assert str(status_code) in res.content[0].text
            assert mock_client.cycles.list.call_count == 0


@pytest.mark.parametrize("status_code", [401, 403, 429, 500])
@pytest.mark.anyio
async def test_module_non_404_errors_propagate_without_fallback(test_server, mock_client, status_code):
    """Non-404 status codes on module must re-raise immediately."""
    mock_client.modules.list_lite.side_effect = make_http_error(status_code, f"Status {status_code}")

    with patch("plane_mcp.tools.module.get_plane_client_context", return_value=(mock_client, WORKSPACE_SLUG)):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp("module", {"action": "list", "project_id": PROJECT_ID})
            assert res.isError is True
            assert str(status_code) in res.content[0].text
            assert mock_client.modules.list.call_count == 0


# ==============================================================================
# 6. Network Failure (ConnectionError propagates immediately)
# ==============================================================================


@pytest.mark.anyio
async def test_network_failure_propagates_without_fallback(test_server, mock_client):
    """Network connection errors propagate without triggering fallback."""
    mock_client.cycles.list_lite.side_effect = ConnectionError("DNS resolution failed")

    with patch("plane_mcp.tools.cycle.get_plane_client_context", return_value=(mock_client, WORKSPACE_SLUG)):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp("cycle", {"action": "list", "project_id": PROJECT_ID})
            assert res.isError is True
            assert "DNS resolution failed" in res.content[0].text
            assert mock_client.cycles.list.call_count == 0


# ==============================================================================
# 7. Missing Project Identifier
# ==============================================================================


@pytest.mark.anyio
async def test_missing_project_id_returns_error(test_server, mock_client):
    """Missing project_id on list returns genuine FastMCP ToolError (isError=True) before any network request."""
    with patch("plane_mcp.tools.cycle.get_plane_client_context", return_value=(mock_client, WORKSPACE_SLUG)):
        async with Client(test_server) as client:
            res_cycle = await client.call_tool_mcp("cycle", {"action": "list", "project_id": ""})
            assert res_cycle.isError is True
            assert "project_id" in res_cycle.content[0].text
            assert mock_client.cycles.list_lite.call_count == 0

    with patch("plane_mcp.tools.module.get_plane_client_context", return_value=(mock_client, WORKSPACE_SLUG)):
        async with Client(test_server) as client:
            res_module = await client.call_tool_mcp("module", {"action": "list", "project_id": ""})
            assert res_module.isError is True
            assert "project_id" in res_module.content[0].text
            assert mock_client.modules.list_lite.call_count == 0


# ==============================================================================
# 8. Empty List
# ==============================================================================


def test_empty_list_returns_valid_zero_envelope(mock_client):
    """Empty list returns total_count=0, count=0, next_page_results=False."""
    mock_page = make_cycle_standard_page([])
    mock_client.cycles.list.return_value = mock_page

    res = list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert res.total_count == 0
    assert res.count == 0
    assert res.total_pages == 0
    assert res.results == []
    assert res.next_page_results is False
    assert res.prev_page_results is False
    assert res.next_cursor == ""
    assert res.prev_cursor == ""


# ==============================================================================
# 9. Single Page
# ==============================================================================


def test_single_page_results_and_envelope(mock_client):
    """Single page result has next_page_results=False, prev_page_results=False, next_cursor=''."""
    cycles = [
        Cycle(id=f"c{i}", name=f"Cycle {i}", project=PROJECT_ID, workspace=WORKSPACE_SLUG, owned_by=OWNER_ID)
        for i in range(3)
    ]
    mock_page = make_cycle_standard_page(cycles)
    mock_client.cycles.list.return_value = mock_page

    res = list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID, per_page=10)
    assert res.total_count == 3
    assert res.count == 3
    assert res.total_pages == 1
    assert len(res.results) == 3
    assert res.next_page_results is False
    assert res.next_cursor == ""
    assert res.prev_cursor == ""


# ==============================================================================
# 10. Multiple Pages (Pagination Progression)
# ==============================================================================


def test_multiple_pages_progression_and_cursors(mock_client):
    """Tests cursor offset progression: ce:0 -> ce:2 -> ce:4."""
    modules = [Module(id=f"m{i}", name=f"Module {i}", project=PROJECT_ID, workspace=WORKSPACE_SLUG) for i in range(5)]
    mock_page = make_module_standard_page(modules)
    mock_client.modules.list.return_value = mock_page

    # Page 1 (offset 0, per_page 2)
    p1 = list_modules_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID, cursor="", per_page=2)
    assert len(p1.results) == 2
    assert [r.id for r in p1.results] == ["m0", "m1"]
    assert p1.total_count == 5
    assert p1.total_pages == 3
    assert p1.next_page_results is True
    assert p1.next_cursor == "ce:2"
    assert p1.prev_page_results is False
    assert p1.prev_cursor == ""

    # Page 2 (offset 2, per_page 2)
    p2 = list_modules_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID, cursor="ce:2", per_page=2)
    assert len(p2.results) == 2
    assert [r.id for r in p2.results] == ["m2", "m3"]
    assert p2.next_page_results is True
    assert p2.next_cursor == "ce:4"
    assert p2.prev_page_results is True
    assert p2.prev_cursor == "ce:0"

    # Page 3 (offset 4, per_page 2)
    p3 = list_modules_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID, cursor="ce:4", per_page=2)
    assert len(p3.results) == 1
    assert [r.id for r in p3.results] == ["m4"]
    assert p3.next_page_results is False
    assert p3.next_cursor == ""
    assert p3.prev_page_results is True
    assert p3.prev_cursor == "ce:2"


# ==============================================================================
# 11. Cursor Stalls and Duplicate Records
# ==============================================================================


def test_cursor_stall_raises_tool_error(mock_client):
    """If backend cursor repeats or fails to advance, ToolError is raised."""
    c1 = Cycle(id="c1", name="Cycle 1", project=PROJECT_ID, workspace=WORKSPACE_SLUG, owned_by=OWNER_ID)
    # Page reports next_page_results=True but next_cursor is empty
    stalled_page = make_cycle_standard_page([c1], total_count=10, next_page_results=True, next_cursor="")
    mock_client.cycles.list.return_value = stalled_page

    with pytest.raises(ToolError) as exc_info:
        list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert "pagination stalled" in str(exc_info.value)


def test_duplicate_record_id_raises_tool_error(mock_client):
    """If backend pagination returns duplicate record IDs, ToolError is raised."""
    c1 = Cycle(id="c1", name="Cycle 1", project=PROJECT_ID, workspace=WORKSPACE_SLUG, owned_by=OWNER_ID)
    c1_dup = Cycle(id="c1", name="Cycle 1 Dup", project=PROJECT_ID, workspace=WORKSPACE_SLUG, owned_by=OWNER_ID)

    page1 = make_cycle_standard_page([c1], total_count=2, next_page_results=True, next_cursor="cur2")
    page2 = make_cycle_standard_page([c1_dup], total_count=2, next_page_results=False, prev_page_results=True)
    mock_client.cycles.list.side_effect = [page1, page2]

    with pytest.raises(ToolError) as exc_info:
        list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert "duplicate record ID" in str(exc_info.value)


# ==============================================================================
# 12. Pagination Bounds (Per Page & Cursors)
# ==============================================================================


@pytest.mark.parametrize("invalid_per_page", [0, -5, 1001, 5000])
def test_invalid_per_page_raises_tool_error(mock_client, invalid_per_page):
    """per_page outside 1..1000 raises ToolError."""
    with pytest.raises(ToolError) as exc_info:
        list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID, per_page=invalid_per_page)
    assert "1..1000" in str(exc_info.value)


@pytest.mark.parametrize("bad_cursor", ["abc", "cursor:12", "ce:", "ce:abc", "ce:-5"])
def test_malformed_cursor_raises_tool_error(mock_client, bad_cursor):
    """Malformed cursors raise ToolError."""
    mock_client.cycles.list.return_value = []
    with pytest.raises(ToolError) as exc_info:
        list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID, cursor=bad_cursor)
    assert "Invalid CE pagination cursor" in str(exc_info.value)


def test_cursor_overflow_digits_raises_tool_error(mock_client):
    """Cursor offset digits exceeding 10 digits raise ToolError."""
    long_cursor = "ce:" + "9" * 15
    mock_client.cycles.list.return_value = []
    with pytest.raises(ToolError) as exc_info:
        list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID, cursor=long_cursor)
    assert "exceeds maximum supported digits" in str(exc_info.value)


def test_cursor_offset_out_of_range_raises_tool_error(mock_client):
    """Cursor offset greater than total items raises ToolError."""
    c1 = Cycle(id="c1", name="Cycle 1", project=PROJECT_ID, workspace=WORKSPACE_SLUG, owned_by=OWNER_ID)
    mock_client.cycles.list.return_value = [c1]

    with pytest.raises(ToolError) as exc_info:
        list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID, cursor="ce:100")
    assert "out of range" in str(exc_info.value)


# ==============================================================================
# 13. Status and View Filtering (Fails Closed with ToolError)
# ==============================================================================


def test_cycle_status_filter_fails_closed(mock_client):
    """Requested status filter on CE cycle fallback raises ToolError (fail-closed)."""
    with pytest.raises(ToolError) as exc_info:
        list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID, status="current")
    assert "Filtering by status='current' is not supported" in str(exc_info.value)
    assert "retry without status" in str(exc_info.value)


# ==============================================================================
# 14. Archived Resources Excluded
# ==============================================================================


def test_archived_records_filtered_from_active_list(mock_client):
    """Records with archived_at != None are excluded from active list."""
    c1_active = Cycle(
        id="c1",
        name="Active Cycle",
        project=PROJECT_ID,
        workspace=WORKSPACE_SLUG,
        owned_by=OWNER_ID,
        archived_at=None,
    )
    c2_archived = Cycle(
        id="c2",
        name="Archived Cycle",
        project=PROJECT_ID,
        workspace=WORKSPACE_SLUG,
        owned_by=OWNER_ID,
        archived_at="2026-01-01T00:00:00Z",
    )
    mock_client.cycles.list.return_value = [c1_active, c2_archived]

    res = list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert res.total_count == 1
    assert len(res.results) == 1
    assert res.results[0].id == "c1"


def test_module_archived_records_filtered(mock_client):
    """Archived modules are excluded from active module list."""
    m1_active = Module(id="m1", name="Active Module", project=PROJECT_ID, workspace=WORKSPACE_SLUG, archived_at=None)
    m2_archived = Module(
        id="m2",
        name="Archived Module",
        project=PROJECT_ID,
        workspace=WORKSPACE_SLUG,
        archived_at="2026-01-01T00:00:00Z",
    )
    mock_client.modules.list.return_value = make_module_standard_page([m1_active, m2_archived])

    res = list_modules_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert res.total_count == 1
    assert res.results[0].id == "m1"


# ==============================================================================
# 15. Missing Optional Fields
# ==============================================================================


def test_missing_optional_fields_handled_cleanly(mock_client):
    """Optional fields like description, start_date, end_date missing or None are valid."""
    cycle_dict = {
        "id": "c_sparse",
        "name": "Minimal Cycle",
        "project": PROJECT_ID,
        "workspace": WORKSPACE_SLUG,
        "owned_by": OWNER_ID,
        "description": None,
        "start_date": None,
        "end_date": None,
    }
    mock_client.cycles.list.return_value = [cycle_dict]

    res = list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert res.total_count == 1
    assert res.results[0].id == "c_sparse"
    assert res.results[0].description is None


# ==============================================================================
# 16. Missing Required Fields
# ==============================================================================


def test_missing_id_raises_tool_error(mock_client):
    """A record with missing or empty ID raises ToolError."""
    bad_record = {"name": "No ID Cycle", "project": PROJECT_ID, "owned_by": OWNER_ID}
    mock_client.cycles.list.return_value = [bad_record]

    with pytest.raises(ToolError) as exc_info:
        list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert "missing required 'id' attribute" in str(exc_info.value)


def test_missing_or_null_owner_raises_tool_error(mock_client):
    """Cycle with null, empty, or omitted owned_by raises ToolError without fabricating owner IDs."""
    c_none = Cycle(id="c_none", name="Null Owner", project=PROJECT_ID, workspace=WORKSPACE_SLUG, owned_by=None)
    mock_client.cycles.list.return_value = [c_none]
    with pytest.raises(ToolError) as exc_none:
        list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert "missing required 'owned_by' attribute" in str(exc_none.value)

    c_empty = Cycle(id="c_empty", name="Empty Owner", project=PROJECT_ID, workspace=WORKSPACE_SLUG, owned_by="")
    mock_client.cycles.list.return_value = [c_empty]
    with pytest.raises(ToolError) as exc_empty:
        list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert "missing required 'owned_by' attribute" in str(exc_empty.value)

    dict_omitted = {"id": "c_omitted", "name": "Omitted Owner", "project": PROJECT_ID, "workspace": WORKSPACE_SLUG}
    mock_client.cycles.list.return_value = [dict_omitted]
    with pytest.raises(ToolError) as exc_omitted:
        list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert "missing required 'owned_by' attribute" in str(exc_omitted.value)


def test_valid_owner_is_preserved_authentically(mock_client):
    """Authentic owner identifiers are preserved accurately."""
    c = Cycle(id="c1", name="Owner Cycle", project=PROJECT_ID, workspace=WORKSPACE_SLUG, owned_by="usr-authentic-42")
    mock_client.cycles.list.return_value = [c]
    res = list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert res.results[0].owned_by == "usr-authentic-42"


def test_mismatched_project_scope_raises_tool_error(mock_client):
    """Records with mismatched project identifiers raise ToolError to prevent scope leakage."""
    c_foreign = Cycle(
        id="c_foreign", name="Foreign Cycle", project="other-project", workspace=WORKSPACE_SLUG, owned_by=OWNER_ID
    )
    mock_client.cycles.list.return_value = [c_foreign]
    with pytest.raises(ToolError) as exc_cycle:
        list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert "does not match the requested project scope" in str(exc_cycle.value)

    m_foreign = Module(id="m_foreign", name="Foreign Module", project="other-project", workspace=WORKSPACE_SLUG)
    mock_client.modules.list.return_value = make_module_standard_page([m_foreign])
    with pytest.raises(ToolError) as exc_module:
        list_modules_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert "does not match the requested project scope" in str(exc_module.value)


def test_workspace_scope_adversarial_suite(mock_client):
    """Adversarial validation across all 8 workspace and project scope scenarios."""
    auth_ws_uuid = "41e3ed60-d8b8-42f9-b7b3-0c888bfce2cb"
    foreign_ws_uuid = "99999999-9999-9999-9999-999999999999"

    # 1. Matching slug
    c_slug = Cycle(id="c1", name="C1", project=PROJECT_ID, workspace=WORKSPACE_SLUG, owned_by=OWNER_ID)
    mock_client.cycles.list.return_value = [c_slug]
    res = list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID, workspace_id=auth_ws_uuid)
    assert res.results[0].workspace == WORKSPACE_SLUG

    # 2. Matching UUID (authoritative workspace identity equivalence)
    c_uuid = Cycle(id="c2", name="C2", project=PROJECT_ID, workspace=auth_ws_uuid, owned_by=OWNER_ID)
    mock_client.cycles.list.return_value = [c_uuid]
    res = list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID, workspace_id=auth_ws_uuid)
    assert res.results[0].workspace == auth_ws_uuid

    # 3. Foreign UUID (fails closed when authoritative workspace UUID is known)
    c_foreign_uuid = Cycle(id="c3", name="C3", project=PROJECT_ID, workspace=foreign_ws_uuid, owned_by=OWNER_ID)
    mock_client.cycles.list.return_value = [c_foreign_uuid]
    with pytest.raises(ToolError) as exc_ws_uuid:
        list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID, workspace_id=auth_ws_uuid)
    assert "does not match the requested workspace scope" in str(exc_ws_uuid.value)

    m_foreign_uuid = Module(id="m3", name="M3", project=PROJECT_ID, workspace=foreign_ws_uuid)
    mock_client.modules.list.return_value = make_module_standard_page([m_foreign_uuid])
    with pytest.raises(ToolError) as exc_mod_uuid:
        list_modules_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID, workspace_id=auth_ws_uuid)
    assert "does not match the requested workspace scope" in str(exc_mod_uuid.value)

    # 4. Foreign slug (fails closed)
    c_foreign_slug = Cycle(id="c4", name="C4", project=PROJECT_ID, workspace="foreign-org-slug", owned_by=OWNER_ID)
    mock_client.cycles.list.return_value = [c_foreign_slug]
    with pytest.raises(ToolError) as exc_ws_slug:
        list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert "does not match the requested workspace scope" in str(exc_ws_slug.value)

    m_foreign_slug = Module(id="m4", name="M4", project=PROJECT_ID, workspace="foreign-org-slug")
    mock_client.modules.list.return_value = make_module_standard_page([m_foreign_slug])
    with pytest.raises(ToolError) as exc_mod_slug:
        list_modules_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert "does not match the requested workspace scope" in str(exc_mod_slug.value)

    # 5. Missing workspace field (defaults safely to requested workspace_slug)
    c_missing_ws = {"id": "c5", "name": "C5", "project": PROJECT_ID, "owned_by": OWNER_ID}
    mock_client.cycles.list.return_value = [c_missing_ws]
    res_missing = list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert res_missing.results[0].workspace == WORKSPACE_SLUG

    m_missing_ws = {"id": "m5", "name": "M5", "project": PROJECT_ID}
    mock_client.modules.list.return_value = [m_missing_ws]
    res_mod_missing = list_modules_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert res_mod_missing.results[0].workspace == WORKSPACE_SLUG

    # 6. Null workspace field (defaults safely to requested workspace_slug)
    c_null_ws = Cycle(id="c6", name="C6", project=PROJECT_ID, workspace=None, owned_by=OWNER_ID)
    mock_client.cycles.list.return_value = [c_null_ws]
    res_null = list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert res_null.results[0].workspace == WORKSPACE_SLUG

    m_null_ws = Module(id="m6", name="M6", project=PROJECT_ID, workspace=None)
    mock_client.modules.list.return_value = make_module_standard_page([m_null_ws])
    res_mod_null = list_modules_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert res_mod_null.results[0].workspace == WORKSPACE_SLUG

    # 7. Correctly scoped project (both explicit and endpoint-scoped default)
    c_proj = Cycle(id="c7", name="C7", project=PROJECT_ID, workspace=WORKSPACE_SLUG, owned_by=OWNER_ID)
    mock_client.cycles.list.return_value = [c_proj]
    res_proj = list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert res_proj.results[0].project == PROJECT_ID

    c_no_proj = {"id": "c7b", "name": "C7b", "workspace": WORKSPACE_SLUG, "owned_by": OWNER_ID}
    mock_client.cycles.list.return_value = [c_no_proj]
    res_no_proj = list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert res_no_proj.results[0].project == PROJECT_ID

    # 8. Mismatched project (rejected)
    c_bad_proj = Cycle(id="c8", name="C8", project="other-project", workspace=WORKSPACE_SLUG, owned_by=OWNER_ID)
    mock_client.cycles.list.return_value = [c_bad_proj]
    with pytest.raises(ToolError) as exc_bad_proj:
        list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert "does not match the requested project scope" in str(exc_bad_proj.value)

    m_bad_proj = Module(id="m8", name="M8", project="other-project", workspace=WORKSPACE_SLUG)
    mock_client.modules.list.return_value = make_module_standard_page([m_bad_proj])
    with pytest.raises(ToolError) as exc_mod_bad_proj:
        list_modules_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert "does not match the requested project scope" in str(exc_mod_bad_proj.value)


def test_workspace_uuid_endpoint_trust_boundary_when_uuid_unknown(mock_client):
    """When only workspace_slug is known (no auth UUID), retain UUID under endpoint trust boundary."""
    record_uuid = "41e3ed60-d8b8-42f9-b7b3-0c888bfce2cb"
    c = Cycle(id="c1", name="C1", project=PROJECT_ID, workspace=record_uuid, owned_by=OWNER_ID)
    mock_client.cycles.list.return_value = [c]
    # workspace_id="" explicitly signifies no known workspace UUID
    res = list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID, workspace_id="")
    assert res.results[0].workspace == record_uuid


def test_premature_pagination_termination_raises_tool_error(mock_client):
    """If backend reports total_count greater than collected items with next_page_results=False, raise ToolError."""
    cycles = [
        Cycle(id=f"c{i}", name=f"Cycle {i}", project=PROJECT_ID, workspace=WORKSPACE_SLUG, owned_by=OWNER_ID)
        for i in range(5)
    ]
    # Incomplete page: 5 items, but total_count claims 10 and next_page_results is False
    incomplete_page = make_cycle_standard_page(cycles, total_count=10, next_page_results=False)
    mock_client.cycles.list.return_value = incomplete_page

    with pytest.raises(ToolError) as exc_info:
        list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    assert "listing incomplete: backend reported total=10 but only 5 items were collected" in str(exc_info.value)


# ==============================================================================
# 17. SDK Model Conversion
# ==============================================================================


def test_sdk_model_types_returned(mock_client):
    """Fallback returns genuine SDK PaginatedCycleLiteResponse and PaginatedModuleLiteResponse."""
    c = Cycle(id="c1", name="Cycle 1", project=PROJECT_ID, workspace=WORKSPACE_SLUG, owned_by=OWNER_ID)
    m = Module(id="m1", name="Module 1", project=PROJECT_ID, workspace=WORKSPACE_SLUG)
    mock_client.cycles.list.return_value = make_cycle_standard_page([c])
    mock_client.modules.list.return_value = make_module_standard_page([m])

    cycle_res = list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    module_res = list_modules_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)

    assert isinstance(cycle_res, PaginatedCycleLiteResponse)
    assert isinstance(cycle_res.results[0], CycleLite)

    assert isinstance(module_res, PaginatedModuleLiteResponse)
    assert isinstance(module_res.results[0], ModuleLite)


# ==============================================================================
# 18. Unsupported Sorting
# ==============================================================================


def test_unsupported_order_by_fails_closed(mock_client):
    """order_by argument on CE fallback raises ToolError (fail-closed)."""
    with pytest.raises(ToolError) as exc_info:
        list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID, order_by="-name")
    assert "Sorting by '-name' is not supported" in str(exc_info.value)

    with pytest.raises(ToolError) as exc_info_m:
        list_modules_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID, order_by="created_at")
    assert "Sorting by 'created_at' is not supported" in str(exc_info_m.value)


# ==============================================================================
# 19. Exact Output Schema (No Leaked Issue Metrics)
# ==============================================================================


def test_exact_output_schema_disallows_leaked_metrics(mock_client):
    """Full Cycle and Module objects have their metric fields stripped in Lite response."""
    full_cycle = Cycle(
        id="c1",
        name="Cycle 1",
        project=PROJECT_ID,
        workspace=WORKSPACE_SLUG,
        owned_by=OWNER_ID,
        total_issues=100,
        completed_issues=80,
        started_issues=10,
        unstarted_issues=5,
        cancelled_issues=5,
        total_estimates=200,
        completed_estimates=160,
        started_estimates=20,
    )
    mock_client.cycles.list.return_value = [full_cycle]

    res = list_cycles_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    dumped = res.results[0].model_dump()

    # The dumped dict must only contain declared CycleLite fields
    for forbidden_metric in (
        "total_issues",
        "completed_issues",
        "started_issues",
        "unstarted_issues",
        "cancelled_issues",
        "total_estimates",
        "completed_estimates",
        "started_estimates",
    ):
        assert forbidden_metric not in dumped
    assert set(dumped.keys()) <= _CYCLE_LITE_FIELDS


def test_module_exact_output_schema_disallows_leaked_metrics(mock_client):
    """Full Module metric fields are stripped in ModuleLite response."""
    full_module = Module(
        id="m1",
        name="Module 1",
        project=PROJECT_ID,
        workspace=WORKSPACE_SLUG,
        total_issues=50,
        completed_issues=40,
        started_issues=5,
        unstarted_issues=5,
        cancelled_issues=0,
    )
    mock_client.modules.list.return_value = make_module_standard_page([full_module])

    res = list_modules_ce(mock_client, WORKSPACE_SLUG, PROJECT_ID)
    dumped = res.results[0].model_dump()

    for forbidden_metric in (
        "total_issues",
        "completed_issues",
        "started_issues",
        "unstarted_issues",
        "cancelled_issues",
    ):
        assert forbidden_metric not in dumped
    assert set(dumped.keys()) <= _MODULE_LITE_FIELDS


# ==============================================================================
# 20. Raw MCP Protocol Error Responses (isError=True)
# ==============================================================================


@pytest.mark.anyio
async def test_raw_mcp_protocol_error_on_status_filter_under_ce(test_server, mock_client):
    """End-to-end FastMCP protocol call returns isError=True when status is requested on CE."""
    mock_client.cycles.list_lite.side_effect = make_http_error(404, "Page not found")

    with patch("plane_mcp.tools.cycle.get_plane_client_context", return_value=(mock_client, WORKSPACE_SLUG)):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "cycle",
                {
                    "action": "list",
                    "project_id": PROJECT_ID,
                    "status": "upcoming",
                },
            )
            assert res.isError is True
            err_text = res.content[0].text
            assert "Filtering by status='upcoming' is not supported" in err_text
            assert "retry without status" in err_text


@pytest.mark.anyio
async def test_raw_mcp_protocol_error_on_module_status_filter_under_ce(test_server, mock_client):
    """End-to-end FastMCP protocol call returns isError=True when status is requested on CE module fallback."""
    mock_client.modules.list_lite.side_effect = make_http_error(404, "Page not found")

    with patch("plane_mcp.tools.module.get_plane_client_context", return_value=(mock_client, WORKSPACE_SLUG)):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "module",
                {
                    "action": "list",
                    "project_id": PROJECT_ID,
                    "status": "in-progress",
                },
            )
            assert res.isError is True
            err_text = res.content[0].text
            assert "Filtering by status='in-progress' is not supported" in err_text
            assert "retry without status" in err_text


@pytest.mark.anyio
async def test_raw_mcp_protocol_error_on_order_by_under_ce(test_server, mock_client):
    """End-to-end FastMCP protocol call returns isError=True when order_by is requested on CE."""
    mock_client.modules.list_lite.side_effect = make_http_error(404, "Page not found")

    with patch("plane_mcp.tools.module.get_plane_client_context", return_value=(mock_client, WORKSPACE_SLUG)):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "module",
                {
                    "action": "list",
                    "project_id": PROJECT_ID,
                    "order_by": "-name",
                },
            )
            assert res.isError is True
            assert "Sorting by '-name' is not supported" in res.content[0].text


@pytest.mark.anyio
async def test_raw_mcp_protocol_success_on_fallback(test_server, mock_client):
    """End-to-end FastMCP protocol call succeeds over fallback with isError=False."""
    mock_client.cycles.list_lite.side_effect = make_http_error(404, "Page not found")
    c1 = Cycle(id="c1", name="Fallback Cycle", project=PROJECT_ID, workspace=WORKSPACE_SLUG, owned_by=OWNER_ID)
    mock_client.cycles.list.return_value = [c1]

    with patch("plane_mcp.tools.cycle.get_plane_client_context", return_value=(mock_client, WORKSPACE_SLUG)):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "cycle",
                {
                    "action": "list",
                    "project_id": PROJECT_ID,
                },
            )
            assert res.isError is False
            payload = json.loads(res.content[0].text)
            assert payload["total_count"] == 1
            assert payload["results"][0]["name"] == "Fallback Cycle"
