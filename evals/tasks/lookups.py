"""Plane reads used by task verifiers to establish truth."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from plane.errors.errors import HttpError
from plane.models.query_params import WorkItemQueryParams


def as_id(obj: Any) -> str | None:
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj
    return getattr(obj, "id", None) or (obj.get("id") if isinstance(obj, dict) else None)


def ids(items: Any) -> set[str]:
    out: set[str] = set()
    for item in items or []:
        i = as_id(item)
        if i:
            out.add(str(i))
    return out


def collect_paginated(fetch_page: Callable[[str | None], Any]) -> list[Any]:
    """Collect every result from a cursor-paginated SDK endpoint.

    ``fetch_page`` receives ``None`` for the first request and the prior response's
    ``next_cursor`` thereafter. Endpoints documented as unpaginated may return a bare
    list; in that case the list is already complete.
    """
    rows: list[Any] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    while True:
        page = fetch_page(cursor)
        if isinstance(page, list):
            rows.extend(page)
            return rows
        results = page.results if hasattr(page, "results") else page
        rows.extend(list(results or []))
        if not bool(getattr(page, "next_page_results", False)):
            return rows
        next_cursor = str(getattr(page, "next_cursor", None) or "")
        if not next_cursor or next_cursor in seen_cursors:
            raise RuntimeError("paginated API reported another page without a new next_cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor


def find_items_by_name(plane: Any, workspace_slug: str, project_id: str, name: str) -> list[Any]:
    """Return all work items with exact name, newest first (by created_at)."""
    matches: list[Any] = []
    cursor = None
    while True:
        params = WorkItemQueryParams(cursor=cursor, per_page=100) if cursor else WorkItemQueryParams(per_page=100)
        page = plane.work_items.list(workspace_slug=workspace_slug, project_id=project_id, params=params)
        for item in page.results or []:
            if (item.name or "").strip() == name:
                matches.append(item)
        if not page.next_page_results:
            break
        cursor = page.next_cursor

    def _created_key(item: Any) -> str:
        return str(getattr(item, "created_at", None) or "")

    matches.sort(key=_created_key, reverse=True)
    return matches


def find_item_by_name(plane: Any, workspace_slug: str, project_id: str, name: str) -> Any | None:
    """Locate a work item by exact name; when duplicates exist, prefer the newest."""
    matches = find_items_by_name(plane, workspace_slug, project_id, name)
    return matches[0] if matches else None


def state_name(plane: Any, workspace_slug: str, project_id: str, state_ref: Any) -> str | None:
    """Resolve a state UUID or expanded object to its display name."""
    if state_ref is None:
        return None
    if hasattr(state_ref, "name") and state_ref.name:
        return str(state_ref.name)
    if isinstance(state_ref, dict) and state_ref.get("name"):
        return str(state_ref["name"])
    state_id = as_id(state_ref)
    if not state_id:
        return None
    try:
        state = plane.states.retrieve(workspace_slug=workspace_slug, project_id=project_id, state_id=state_id)
        return state.name
    except HttpError as exc:
        if exc.status_code not in (404, 405):
            raise
    # Fall back to listing states and matching by id.
    page = plane.states.list(workspace_slug=workspace_slug, project_id=project_id)
    results = page.results if hasattr(page, "results") else page
    for s in results or []:
        if str(s.id) == str(state_id):
            return s.name
    return None


def state_group(plane: Any, workspace_slug: str, project_id: str, state_ref: Any) -> str | None:
    if state_ref is None:
        return None
    if hasattr(state_ref, "group") and state_ref.group:
        return str(state_ref.group)
    if isinstance(state_ref, dict) and state_ref.get("group"):
        return str(state_ref["group"])
    state_id = as_id(state_ref)
    if not state_id:
        return None
    page = plane.states.list(workspace_slug=workspace_slug, project_id=project_id)
    results = page.results if hasattr(page, "results") else page
    for s in results or []:
        if str(s.id) == str(state_id):
            return getattr(s, "group", None)
    return None


def is_not_found(exc: BaseException) -> bool:
    return isinstance(exc, HttpError) and exc.status_code in (404, 405)


def count_open_urgent(plane: Any, workspace_slug: str, project_id: str) -> int:
    """Count urgent items whose state group is not completed/cancelled (resolve at verify)."""
    page = plane.states.list(workspace_slug=workspace_slug, project_id=project_id)
    closed_ids = {str(s.id) for s in (page.results or []) if getattr(s, "group", None) in ("completed", "cancelled")}
    n = 0
    cursor = None
    while True:
        params = WorkItemQueryParams(cursor=cursor, per_page=100) if cursor else WorkItemQueryParams(per_page=100)
        resp = plane.work_items.list(workspace_slug=workspace_slug, project_id=project_id, params=params)
        for item in resp.results or []:
            if (getattr(item, "priority", None) or "").lower() != "urgent":
                continue
            sid = as_id(item.state)
            if sid and str(sid) in closed_ids:
                continue
            n += 1
        if not resp.next_page_results:
            break
        cursor = resp.next_cursor
    return n


__all__ = [
    "as_id",
    "collect_paginated",
    "count_open_urgent",
    "find_item_by_name",
    "find_items_by_name",
    "ids",
    "is_not_found",
    "state_group",
    "state_name",
]
