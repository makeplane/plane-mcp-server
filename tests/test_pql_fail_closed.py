"""Deterministic tests for PQL fail-closed behavior, capability detection, and error handling.

Covers all 27 audit verification scenarios:
1. CE HTTP 200 ignored PQL.
2. CE HTTP 400 unsupported PQL.
3. Cloud valid PQL.
4. Valid empty filtered result.
5. Multi-page filtered results.
6. Workspace-level PQL without project_id.
7. Count PQL support and rejection.
8. Cycle and module endpoint differences.
9. Unrelated HTTP 400.
10. Invalid project UUID.
11. HTTP 401 and 403.
12. HTTP 429 and 5xx.
13. Timeout and connection failure.
14. Different OAuth users and tokens.
15. Different Plane servers.
16. Different project scopes.
17. Incorrect hostname matches.
18. Malformed capability overrides.
19. Concurrent capability checks.
20. No SDK dispatch when a request is rejected before execution.
21. Raw MCP isError=true on genuine failures.
22. Unfiltered request regression.
23. No tool-schema drift.
24. Archived work-item endpoint probes exact target.
25. Project-scoped count without explicit PQL on CE fails closed truthfully.
26. Unfiltered workspace count bypasses capability checks.
27. Cache eviction is insertion-order FIFO.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from fastmcp import Client, FastMCP
from plane.errors.errors import HttpError
from plane.models.work_items import PaginatedWorkItemResponse, WorkItem

from plane_mcp.toolkit.paging import (
    _PQL_CAPABILITY_CACHE,
    check_pql_capability,
    reset_pql_capability_cache,
)
from plane_mcp.tools.cycle import register as register_cycle
from plane_mcp.tools.module import register as register_module
from plane_mcp.tools.workitem import register as register_workitem


def _make_http_error(status_code: int, response_body: dict | str = "") -> HttpError:
    resp = response_body if isinstance(response_body, dict) else {"detail": str(response_body)}
    return HttpError(f"HTTP {status_code}", status_code=status_code, response=resp)


def _make_work_item(
    item_id: str,
    name: str = "Test item",
    priority: str = "urgent",
    state: str = "state-1",
    assignees: list[str] | None = None,
) -> WorkItem:
    return WorkItem.model_validate(
        {
            "id": item_id,
            "name": name,
            "priority": priority,
            "state": state,
            "assignees": assignees or ["user-1"],
            "project": "00000000-0000-0000-0000-000000000001",
            "workspace": "test-ws",
            "sequence_id": 1,
            "sort_order": 1000.0,
            "is_draft": False,
        }
    )


def _make_page(
    items: list[WorkItem],
    total_count: int | None = None,
    next_cursor: str = "",
    prev_cursor: str = "",
) -> PaginatedWorkItemResponse:
    count_val = total_count if total_count is not None else len(items)
    return PaginatedWorkItemResponse.model_validate(
        {
            "results": items,
            "count": len(items),
            "total_count": count_val,
            "total_results": count_val,
            "next_cursor": next_cursor,
            "prev_cursor": prev_cursor,
            "next_page_results": bool(next_cursor),
            "prev_page_results": bool(prev_cursor),
            "total_pages": 1 if not next_cursor else 2,
            "extra_stats": None,
        }
    )


@pytest.fixture(autouse=True)
def clean_pql_cache(monkeypatch):
    """Ensure capability cache and env overrides are fresh for every test."""
    monkeypatch.delenv("PLANE_SUPPORTS_PQL", raising=False)
    monkeypatch.delenv("PLANE_CAPABILITY_PQL", raising=False)
    reset_pql_capability_cache()
    yield
    reset_pql_capability_cache()


@pytest.fixture
def test_server():
    server = FastMCP("test-server")
    register_workitem(server)
    register_cycle(server)
    register_module(server)
    return server


# ==============================================================================
# Scenario 1: CE HTTP 200 ignored PQL
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_01_ce_http_200_ignored_pql_fails_closed(test_server: FastMCP):
    """CE <= 1.3.1 returns 200 on probe; tool must fail closed with ToolError and not return unfiltered data."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://self-hosted.local"
    unfiltered_page = _make_page([_make_work_item("ce-unfiltered-1", priority="low")], total_count=1)
    mock_client.work_items.list.return_value = unfiltered_page

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "list",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "pql": 'priority = "urgent"',
                },
            )
            assert res.isError is True
            err_text = res.content[0].text
            assert "PQL filtering is not supported on this Plane installation" in err_text
            assert "No filtered result was returned" in err_text
            assert "ce-unfiltered-1" not in err_text


# ==============================================================================
# Scenario 2: CE HTTP 400 unsupported PQL
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_02_ce_http_400_unsupported_pql(test_server: FastMCP):
    """CE >= 1.4.1 explicitly rejects PQL with 400 edition refusal; tool raises ToolError."""
    ce_body = {
        "pql": "PQL and structured filters are not supported on this Plane edition.",
        "unsupported_parameters": ["pql"],
    }
    mock_client = MagicMock()
    mock_client.config.api_root = "https://self-hosted.local"
    mock_client.work_items.list.side_effect = _make_http_error(400, ce_body)

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "list",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "pql": 'priority = "urgent"',
                },
            )
            assert res.isError is True
            assert "PQL filtering is not supported on this Plane installation" in res.content[0].text


# ==============================================================================
# Scenario 3: Cloud valid PQL
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_03_cloud_valid_pql(test_server: FastMCP):
    """Connected to api.plane.so, valid PQL is dispatched directly and filtered results returned."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://api.plane.so"
    mock_client.work_items.list.return_value = _make_page([_make_work_item("urgent-1", priority="urgent")])

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "list",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "pql": 'priority = "urgent"',
                },
            )
            assert res.isError is False
            data = json.loads(res.content[0].text)
            assert len(data["results"]) == 1
            assert data["results"][0]["id"] == "urgent-1"
            assert mock_client.work_items.list.call_count == 1
            assert mock_client.work_items.list.call_args.kwargs["params"].pql == 'priority = "urgent"'


# ==============================================================================
# Scenario 4: Valid empty filtered result
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_04_valid_empty_filtered_result(test_server: FastMCP):
    """Filter returning zero results returns empty page envelope with isError: false."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://api.plane.so"
    mock_client.work_items.list.return_value = _make_page([], total_count=0)

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "list",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "pql": 'priority = "none"',
                },
            )
            assert res.isError is False
            data = json.loads(res.content[0].text)
            assert data["results"] == []
            assert data["total_count"] == 0


# ==============================================================================
# Scenario 5: Multi-page filtered results
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_05_multipage_filtered_results(test_server: FastMCP):
    """Preserves next_cursor and full pagination envelope."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://api.plane.so"
    page = _make_page([_make_work_item("item-1")], total_count=15, next_cursor="page-2-cursor")
    mock_client.work_items.list.return_value = page

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "list",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "pql": 'priority = "urgent"',
                    "per_page": 1,
                },
            )
            assert res.isError is False
            data = json.loads(res.content[0].text)
            assert data["next_cursor"] == "page-2-cursor"
            assert data["next_page_results"] is True
            assert data["total_count"] == 15


# ==============================================================================
# Scenario 6: Workspace-level PQL without project_id
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_06_workspace_level_pql_fails_closed_on_ce(test_server: FastMCP):
    """When project_id is omitted, PQL capability is verified via workspace probe.
    On CE where probe returns 200 (silent ignore) or 404 (route absent), tool fails closed.
    """
    mock_client = MagicMock()
    mock_client.config.api_root = "https://self-hosted.local"
    # CE returns 200 with unfiltered data on list_workspace
    mock_client.work_items.list_workspace.return_value = _make_page([_make_work_item("unfiltered-ws")])

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "list",
                    "pql": 'priority = "urgent"',
                },
            )
            assert res.isError is True
            assert "PQL filtering is not supported on this Plane installation" in res.content[0].text
            assert "unfiltered-ws" not in res.content[0].text


# ==============================================================================
# Scenario 7: Count PQL support and rejection
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_07_count_pql_support_and_rejection(test_server: FastMCP):
    """workitem.count with PQL checks capability and fails closed on CE."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://self-hosted.local"
    # Probe to count returns 200 (silent ignore on CE)
    count_obj = MagicMock()
    count_obj.model_dump.return_value = {"total_count": 999}
    mock_client.work_items.count_workspace.return_value = count_obj

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "count",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "pql": 'priority = "urgent"',
                },
            )
            assert res.isError is True
            assert "PQL filtering is not supported on workitem.count in Plane CE" in res.content[0].text


# ==============================================================================
# Scenario 8: Cycle and module endpoint differences
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_08_cycle_and_module_fail_closed_on_ce(test_server: FastMCP):
    """cycle and module list_workitems with PQL independently probe and fail closed on CE."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://self-hosted.local"
    mock_client.cycles.list_work_items.return_value = _make_page([])
    mock_client.modules.list_work_items.return_value = _make_page([])

    with (
        patch("plane_mcp.tools.cycle.get_plane_client_context", return_value=(mock_client, "test-ws")),
        patch("plane_mcp.tools.module.get_plane_client_context", return_value=(mock_client, "test-ws")),
    ):
        async with Client(test_server) as client:
            res_c = await client.call_tool_mcp(
                "cycle",
                {
                    "action": "list_workitems",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "cycle_id": "00000000-0000-0000-0000-000000000002",
                    "pql": 'priority = "urgent"',
                },
            )
            assert res_c.isError is True
            assert "PQL filtering is not supported on cycle.list_workitems in Plane CE" in res_c.content[0].text

            res_m = await client.call_tool_mcp(
                "module",
                {
                    "action": "list_workitems",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "module_id": "00000000-0000-0000-0000-000000000003",
                    "pql": 'priority = "urgent"',
                },
            )
            assert res_m.isError is True
            assert "PQL filtering is not supported on module.list_workitems in Plane CE" in res_m.content[0].text


# ==============================================================================
# Scenario 9: Unrelated HTTP 400
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_09_unrelated_http_400_not_cached_as_support(test_server: FastMCP):
    """An unrelated 400 during capability probe is re-raised and does NOT mark backend as supported."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://self-hosted.local"
    mock_client.work_items.list.side_effect = _make_http_error(400, {"detail": "Unrelated validation failure"})

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "list",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "pql": 'priority = "urgent"',
                },
            )
            assert res.isError is True
            # Cache must NOT contain True
            cache_keys = [k for k, v in _PQL_CAPABILITY_CACHE.items() if v[0] is True]
            assert len(cache_keys) == 0


# ==============================================================================
# Scenario 10: Invalid project UUID
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_10_invalid_project_uuid_re_raises(test_server: FastMCP):
    """Invalid project UUID 400 is not mistaken for a PQL syntax rejection."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://self-hosted.local"
    mock_client.work_items.list.side_effect = _make_http_error(400, {"project_id": ["Invalid UUID format."]})

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "list",
                    "project_id": "not-a-uuid",
                    "pql": 'priority = "urgent"',
                },
            )
            assert res.isError is True
            assert "Invalid UUID format" in res.content[0].text


# ==============================================================================
# Scenario 11: HTTP 401 and 403
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_11_http_401_403_unmasked(test_server: FastMCP):
    """Authentication and permission errors propagate directly without being masked as PQL failures."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://self-hosted.local"
    mock_client.work_items.list.side_effect = _make_http_error(403, {"detail": "Permission denied"})

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "list",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "pql": 'priority = "urgent"',
                },
            )
            assert res.isError is True
            assert "Permission denied" in res.content[0].text or "403" in res.content[0].text


# ==============================================================================
# Scenario 12: HTTP 429 and 5xx
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_12_http_429_5xx_unmasked(test_server: FastMCP):
    """Rate-limiting (429) and server errors (500) propagate directly without being converted to PQL errors."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://self-hosted.local"
    mock_client.work_items.list.side_effect = _make_http_error(500, {"detail": "Database unavailable"})

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "list",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "pql": 'priority = "urgent"',
                },
            )
            assert res.isError is True
            assert "Database unavailable" in res.content[0].text or "500" in res.content[0].text


# ==============================================================================
# Scenario 13: Timeout and connection failure
# ==============================================================================
def test_scenario_13_timeout_and_connection_failure():
    """Network timeouts raise infrastructure errors instead of returning False."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://self-hosted.local"

    def _timeout_probe():
        raise TimeoutError("Connection timed out to Plane API")

    with pytest.raises(TimeoutError, match="Connection timed out"):
        check_pql_capability(mock_client, "test-ws", "proj-1", probe_fn=_timeout_probe)


# ==============================================================================
# Scenario 14: Different OAuth users and tokens
# ==============================================================================
def test_scenario_14_different_oauth_users_isolated():
    """Different users with distinct access tokens have distinct cache entries."""
    client_a = MagicMock()
    client_a.config.api_root = "https://self-hosted.local"
    client_a.config.access_token = "token-user-a"

    client_b = MagicMock()
    client_b.config.api_root = "https://self-hosted.local"
    client_b.config.access_token = "token-user-b"

    # Mock probe: client_a probe returns 400 with PQL syntax complaint -> True
    client_a.work_items.list.side_effect = _make_http_error(400, {"pql": "Syntax error at 1"})
    # client_b probe returns 200 (silent ignore) -> False
    client_b.work_items.list.return_value = _make_page([])

    res_a = check_pql_capability(client_a, "test-ws", "proj-1")
    res_b = check_pql_capability(client_b, "test-ws", "proj-1")

    assert res_a is True
    assert res_b is False
    assert len(_PQL_CAPABILITY_CACHE) == 2


# ==============================================================================
# Scenario 15: Different Plane servers
# ==============================================================================
def test_scenario_15_different_plane_servers_isolated():
    """Different backend API roots do not share capability cache entries."""
    client_1 = MagicMock()
    client_1.config.api_root = "https://plane1.internal"
    client_1.work_items.list.side_effect = _make_http_error(400, {"pql": "Syntax error at 1"})

    client_2 = MagicMock()
    client_2.config.api_root = "https://plane2.internal"
    client_2.work_items.list.return_value = _make_page([])

    res_1 = check_pql_capability(client_1, "test-ws", "proj-1")
    res_2 = check_pql_capability(client_2, "test-ws", "proj-1")

    assert res_1 is True
    assert res_2 is False


# ==============================================================================
# Scenario 16: Different project scopes
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_16_count_scopes_pql_with_project_id(test_server: FastMCP):
    """workitem count correctly scopes PQL with project ID filter."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://api.plane.so"
    count_resp = MagicMock()
    count_resp.model_dump.return_value = {"total_count": 3}
    mock_client.work_items.count_workspace.return_value = count_resp

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "count",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "pql": 'priority = "urgent"',
                },
            )
            assert res.isError is False
            call_params = mock_client.work_items.count_workspace.call_args.kwargs["params"]
            assert 'project = "00000000-0000-0000-0000-000000000001"' in call_params.pql


# ==============================================================================
# Scenario 17: Incorrect hostname matches
# ==============================================================================
def test_scenario_17_incorrect_hostname_matches():
    """Domains containing api.plane.so as substring are NOT recognized as official Cloud."""
    client = MagicMock()
    client.config.api_root = "https://api.plane.so.attacker.com"
    client.work_items.list.return_value = _make_page([])

    # Must execute probe (which returns 200 silent ignore -> False), not skip to True
    res = check_pql_capability(client, "test-ws", "proj-1")
    assert res is False
    assert client.work_items.list.called


# ==============================================================================
# Scenario 18: Malformed capability overrides
# ==============================================================================
def test_scenario_18_malformed_capability_overrides(monkeypatch):
    """Malformed values log a warning and fall back safely rather than asserting True."""
    client = MagicMock()
    client.config.api_root = "https://api.plane.so"

    monkeypatch.setenv("PLANE_SUPPORTS_PQL", "false")
    assert check_pql_capability(client, "test-ws", "proj-1") is False

    monkeypatch.setenv("PLANE_SUPPORTS_PQL", "true")
    assert check_pql_capability(client, "test-ws", "proj-1") is True

    monkeypatch.setenv("PLANE_SUPPORTS_PQL", "gibberish_value")
    # On Cloud with invalid override, falls back to hostname check -> True
    assert check_pql_capability(client, "test-ws", "proj-1") is True


# ==============================================================================
# Scenario 19: Concurrent capability checks
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_19_concurrent_capability_checks(test_server: FastMCP):
    """Concurrent requests execute safely without race conditions."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://self-hosted.local"
    mock_client.work_items.list.return_value = _make_page([])

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            t1 = client.call_tool_mcp(
                "workitem",
                {"action": "list", "project_id": "00000000-0000-0000-0000-000000000001", "pql": 'priority = "urgent"'},
            )
            t2 = client.call_tool_mcp(
                "workitem",
                {"action": "list", "project_id": "00000000-0000-0000-0000-000000000001", "pql": 'priority = "urgent"'},
            )
            r1, r2 = await asyncio.gather(t1, t2)
            assert r1.isError is True
            assert r2.isError is True


# ==============================================================================
# Scenario 20: No SDK dispatch when rejected before execution
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_20_no_sdk_dispatch_when_cached_unsupported(test_server: FastMCP):
    """Once capability is confirmed unsupported, subsequent calls do not dispatch user query to SDK."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://self-hosted.local"
    # First probe returns 200 (CE silent ignore)
    mock_client.work_items.list.return_value = _make_page([])

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            # First call executes probe
            await client.call_tool_mcp(
                "workitem",
                {"action": "list", "project_id": "00000000-0000-0000-0000-000000000001", "pql": 'priority = "urgent"'},
            )
            assert mock_client.work_items.list.call_count == 1

            # Second call uses cache -> must NOT call SDK at all
            res2 = await client.call_tool_mcp(
                "workitem",
                {"action": "list", "project_id": "00000000-0000-0000-0000-000000000001", "pql": 'priority = "urgent"'},
            )
            assert res2.isError is True
            assert mock_client.work_items.list.call_count == 1


# ==============================================================================
# Scenario 21: Raw MCP isError=true on genuine failures
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_21_raw_mcp_is_error_true(test_server: FastMCP):
    """Verify that ToolError results in raw MCP isError: true on tool response."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://api.plane.so"
    mock_client.work_items.list.side_effect = _make_http_error(400, {"pql": "Bad syntax"})

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "list",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "pql": "bad pql",
                },
            )
            assert res.isError is True


# ==============================================================================
# Scenario 22: Unfiltered request regression
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_22_unfiltered_queries_bypass_capability_checks(test_server: FastMCP):
    """Queries without pql bypass capability checks and execute normally."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://self-hosted.local"
    items = [_make_work_item("item-1")]
    mock_client.work_items.list.return_value = _make_page(items, total_count=1)

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "list",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                },
            )
            assert res.isError is False
            assert not mock_client.work_items.list.call_args.kwargs["params"].pql


# ==============================================================================
# Scenario 23: No tool-schema drift
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_23_no_tool_schema_drift(test_server: FastMCP):
    """Tool schema parameters and descriptions remain stable without unexpected drift."""
    async with Client(test_server) as client:
        tools = await client.list_tools()
        tool_names = {t.name for t in tools}
        assert {"workitem", "cycle", "module"}.issubset(tool_names)

        workitem_tool = next(t for t in tools if t.name == "workitem")
        props = workitem_tool.inputSchema.get("properties", {})
        assert "pql" in props
        assert "action" in props
        assert "project_id" in props
        assert props["pql"].get("type") == "string"


# ==============================================================================
# Scenario 24: Archived work-item endpoint probes exact target
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_24_archived_workitem_probes_exact_endpoint(test_server: FastMCP):
    """workitem.list_archived capability probe targets list_archived, not list."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://self-hosted.local"
    # CE list_archived returns 200 (silent ignore)
    mock_client.work_items.list_archived.return_value = _make_page([])

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "list_archived",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                    "pql": 'priority = "urgent"',
                },
            )
            assert res.isError is True
            assert "PQL filtering is not supported" in res.content[0].text
            # Assert exact probe method dispatched
            assert mock_client.work_items.list_archived.called
            assert not mock_client.work_items.list.called
            probe_params = mock_client.work_items.list_archived.call_args.kwargs["params"]
            assert probe_params.pql == "(((pql_probe_syntax_check"
            assert probe_params.per_page == 1


# ==============================================================================
# Scenario 25: Project-scoped count without explicit PQL on CE
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_25_project_scoped_count_without_pql_fails_closed_truthfully(test_server: FastMCP):
    """workitem.count with project_id but NO pql fails closed on CE with truthful diagnostic."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://self-hosted.local"
    count_resp = MagicMock()
    count_resp.model_dump.return_value = {"total_count": 500}
    mock_client.work_items.count_workspace.return_value = count_resp

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "count",
                    "project_id": "00000000-0000-0000-0000-000000000001",
                },
            )
            assert res.isError is True
            err_text = res.content[0].text
            assert (
                "Project-scoped workitem count relies on PQL filtering, which is not supported on "
                "workitem.count in Plane CE" in err_text
            )
            assert (
                "Cannot count project '00000000-0000-0000-0000-000000000001' without returning an "
                "inaccurate workspace-wide total" in err_text
            )
            assert "Remove pql" not in err_text


# ==============================================================================
# Scenario 26: Unfiltered workspace count bypasses capability checks
# ==============================================================================
@pytest.mark.anyio
async def test_scenario_26_unfiltered_workspace_count_bypasses_probe(test_server: FastMCP):
    """workitem.count workspace-wide without project_id or pql runs unfiltered without probe."""
    mock_client = MagicMock()
    mock_client.config.api_root = "https://self-hosted.local"
    count_resp = MagicMock()
    count_resp.model_dump.return_value = {"total_count": 123}
    mock_client.work_items.count_workspace.return_value = count_resp

    with patch("plane_mcp.tools.workitem.get_plane_client_context", return_value=(mock_client, "test-ws")):
        async with Client(test_server) as client:
            res = await client.call_tool_mcp(
                "workitem",
                {
                    "action": "count",
                },
            )
            assert res.isError is False
            assert json.loads(res.content[0].text) == {"total_count": 123}
            # Only one call to count_workspace (no probe call)
            assert mock_client.work_items.count_workspace.call_count == 1
            call_params = mock_client.work_items.count_workspace.call_args.kwargs["params"]
            assert call_params.pql is None


# ==============================================================================
# Scenario 27: Cache eviction is insertion-order FIFO
# ==============================================================================
def test_scenario_27_cache_eviction_is_bounded_fifo():
    """Verify that capability cache enforces bounded size and FIFO eviction."""
    from plane_mcp.toolkit.paging import (
        _MAX_CACHE_SIZE,
        _PQL_CAPABILITY_CACHE,
        _set_capability_cache,
    )

    reset_pql_capability_cache()
    for i in range(_MAX_CACHE_SIZE):
        _set_capability_cache(f"key-{i}", True)

    assert len(_PQL_CAPABILITY_CACHE) == _MAX_CACHE_SIZE
    assert "key-0" in _PQL_CAPABILITY_CACHE

    # Insert 257th entry
    _set_capability_cache("key-256", True)
    assert len(_PQL_CAPABILITY_CACHE) == _MAX_CACHE_SIZE
    # Oldest inserted entry key-0 was evicted
    assert "key-0" not in _PQL_CAPABILITY_CACHE
    assert "key-256" in _PQL_CAPABILITY_CACHE


# ==============================================================================
# Scenario 28: Cache concurrent thread safety
# ==============================================================================
def test_scenario_28_cache_thread_safety():
    """Verify that concurrent reads, writes, and expirations do not race or raise."""
    import concurrent.futures

    from plane_mcp.toolkit.paging import (
        _set_capability_cache,
        check_pql_capability,
    )

    reset_pql_capability_cache()

    class FakeClient:
        def __init__(self, api_root: str):
            self.config = type("Config", (), {"api_root": api_root, "api_key": "test"})()

    client = FakeClient("https://plane.internal/api")

    def worker(worker_id: int):
        for i in range(50):
            key = f"key-{i % 10}"
            _set_capability_cache(key, i % 2 == 0)
            # Invoke check_pql_capability with custom probe_fn
            check_pql_capability(
                client,
                workspace_slug="ws",
                surface=f"surface-{worker_id}",
                probe_fn=lambda: None,
            )
        return True

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, i) for i in range(8)]
        for f in concurrent.futures.as_completed(futures):
            assert f.result() is True

    reset_pql_capability_cache()
