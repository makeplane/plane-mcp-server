"""
E2E test for pagination cursor fix in search_tickets.

Verifies that:
1. Basic search against TEST project works with the live API
2. Pagination cursor is correct — requesting page 2 doesn't repeat page 1 results
3. per_page=limit is respected (not hard-coded 100)
4. Label filter exception returns LLM-friendly response rather than widening search
"""

import os
import pytest
from plane_mcp.client import get_plane_client_context
from plane_mcp.resolver import EntityResolver
from plane_mcp.journey.tools.read import ReadJourney


pytestmark = pytest.mark.e2e


@pytest.fixture
def e2e_journey():
    if not (os.getenv("PLANE_API_KEY") and os.getenv("PLANE_WORKSPACE_SLUG")):
        pytest.skip("E2E test requires PLANE_API_KEY and PLANE_WORKSPACE_SLUG env vars")
    client, workspace_slug = get_plane_client_context()
    resolver = EntityResolver(client, workspace_slug)
    return ReadJourney(resolver)


def test_basic_search_returns_results(e2e_journey):
    """Basic sanity: search on TEST project returns results."""
    result = e2e_journey.search_tickets(project_slug="TEST", limit=5, lod="summary")
    assert "results" in result, f"Unexpected response shape: {result}"
    print(f"\n[E2E] Basic search: {len(result['results'])} results returned")
    assert isinstance(result["results"], list)


def test_per_page_equals_limit(e2e_journey):
    """Confirm API is called with per_page=limit, not per_page=100."""
    # search_tickets calls get_plane_client_context() internally for a fresh client.
    # We must patch that function so the recording wrapper is on the client it actually uses.
    from unittest.mock import patch
    from plane_mcp.client import get_plane_client_context

    real_client, real_ws = get_plane_client_context()
    calls = []
    real_get = real_client.work_items._get

    def recording_get(path, **kwargs):
        calls.append(kwargs.get("params", {}))
        return real_get(path, **kwargs)

    real_client.work_items._get = recording_get
    with patch("plane_mcp.journey.tools.read.get_plane_client_context", return_value=(real_client, real_ws)):
        try:
            e2e_journey.search_tickets(project_slug="TEST", limit=3, lod="summary")
        finally:
            real_client.work_items._get = real_get

    assert calls, "API was never called after patching"
    assert calls[0]["per_page"] == 3, (
        f"Expected per_page=3 (min(limit,100)), got {calls[0]['per_page']}"
    )
    print(f"\n[E2E] per_page assertion passed: per_page={calls[0]['per_page']}")


def test_pagination_cursor_no_overlap(e2e_journey):
    """
    Verify that page 2 results don't overlap with page 1.
    This catches the old bug where per_page=100 but limit=5 caused the
    returned cursor to skip items 6-100 on page 1.
    """
    page1 = e2e_journey.search_tickets(project_slug="TEST", limit=3, lod="summary")
    cursor = page1.get("next_cursor")

    if not cursor:
        pytest.skip("Not enough tickets in TEST project for pagination test (need >3)")

    page2 = e2e_journey.search_tickets(project_slug="TEST", limit=3, cursor=cursor, lod="summary")

    p1_keys = {r.get("key") or r.get("ticket_id") for r in page1["results"]}
    p2_keys = {r.get("key") or r.get("ticket_id") for r in page2["results"]}

    overlap = p1_keys & p2_keys
    print(f"\n[E2E] Page 1 keys: {p1_keys}")
    print(f"[E2E] Page 2 keys: {p2_keys}")
    print(f"[E2E] Overlap: {overlap}")

    assert not overlap, (
        f"Pagination overlap detected! These tickets appeared on both pages: {overlap}"
    )


def test_label_exception_returns_llm_friendly(e2e_journey):
    """
    Verify two distinct label failure modes:
    A) Label not found by name -> warning + results still returned (search broadened but warned)
    B) Label lookup throws exception -> empty results + warning (safe failure, no widening)
    """
    client = e2e_journey.resolver.client

    # --- Mode A: label name not found (not an exception, just missing) ---
    result_a = e2e_journey.search_tickets(
        project_slug="TEST",
        labels=["nonexistent-label-xyz-123"],
        limit=3,
        lod="summary"
    )
    print(f"\n[E2E] Mode A (label not found): {result_a.get('warnings')}")
    assert "warnings" in result_a, "Expected warnings for unknown label"
    assert any("nonexistent-label-xyz-123" in w for w in result_a["warnings"]), (
        f"Expected label name in warning, got: {result_a['warnings']}"
    )
    # results MAY or MAY NOT be empty depending on whether the filter was applied

    # --- Mode B: label lookup raises an exception -> empty results + warning ---
    # Must patch at the module level for the interception to work
    from unittest.mock import patch

    def failing_labels_list(*args, **kwargs):
        raise ConnectionError("Simulated network failure")

    from plane_mcp.client import get_plane_client_context
    real_client_b, real_ws_b = get_plane_client_context()
    real_labels_list_b = real_client_b.labels.list
    real_client_b.labels.list = failing_labels_list
    with patch("plane_mcp.journey.tools.read.get_plane_client_context", return_value=(real_client_b, real_ws_b)):
        try:
            result_b = e2e_journey.search_tickets(
                project_slug="TEST",
                labels=["some-label"],
                limit=5,
                lod="summary"
            )
        finally:
            real_client_b.labels.list = real_labels_list_b

    print(f"[E2E] Mode B (exception): {result_b}")
    assert "results" in result_b
    assert result_b["results"] == [], (
        f"Expected empty results on label exception, got: {result_b['results']}"
    )
    assert "warnings" in result_b, "Expected a 'warnings' key in the exception response"
    assert any("Label filter" in w for w in result_b["warnings"]), (
        f"Expected label-filter warning, got: {result_b['warnings']}"
    )


if __name__ == "__main__":
    # Can run directly: PLANE_API_KEY=... PLANE_WORKSPACE_SLUG=... python tests/e2e_pagination_test.py
    import sys
    sys.exit(pytest.main([__file__, "-v", "-s"]))
