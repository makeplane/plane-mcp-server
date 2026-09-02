"""Shared helpers for workspace-scoped evaluation fixtures."""

from __future__ import annotations

from typing import Any

from plane.models.query_params import PaginatedQueryParams


def list_workspace_rows(api: Any, workspace_slug: str) -> list[Any]:
    """List every row from a paginated workspace-scoped API."""
    rows: list[Any] = []
    cursor = None
    while True:
        page = api.list(
            workspace_slug=workspace_slug,
            params=PaginatedQueryParams(per_page=100, cursor=cursor),
        )
        rows.extend((page.results if hasattr(page, "results") else page) or [])
        if not getattr(page, "next_page_results", False):
            return rows
        cursor = page.next_cursor
