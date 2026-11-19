"""Read-only fallback for standard Plane Community Edition list endpoints.

Some self-hosted Plane Community Edition versions (prior to v1.4.0) lack the
dedicated lightweight endpoints (/projects-lite/, /members-lite/) and return
HTTP 404. This module provides bounded, truthful fallbacks to the standard
endpoints with honest client-side filtering and pagination envelopes.
"""

from __future__ import annotations

from functools import cmp_to_key
from math import ceil
from typing import Any

from fastmcp.exceptions import ToolError
from plane.models.projects import PaginatedProjectLiteResponse, ProjectLite
from plane.models.query_params import PaginatedQueryParams
from plane.models.workspaces import PaginatedWorkspaceMemberResponse
from pydantic import BaseModel

_MAX_PROJECTS = 1_000
_MAX_MEMBERS = 10_000
_MAX_PAGES = 10
_BACKEND_PAGE_SIZE = 100
_MAX_CURSOR_OFFSET_DIGITS = 10
_PROJECT_LITE_FIELDS = set(ProjectLite.model_fields.keys())


def _get_field(obj: Any, field: str, default: Any = None) -> Any:
    """Safely extract a field value from a model or dict without raising."""
    if isinstance(obj, dict):
        val = obj.get(field)
    else:
        val = getattr(obj, field, default)
    return default if val is None else val


def _field_presence_and_value(obj: Any, field: str) -> tuple[bool, Any]:
    """Distinguish between an explicitly present field (even if None) and an omitted field.

    For Pydantic models, checks model_fields_set to verify the field was supplied in the raw payload.
    For dictionaries, checks key membership.
    For arbitrary objects, checks hasattr.
    """
    if isinstance(obj, BaseModel):
        if field in obj.model_fields_set:
            return True, getattr(obj, field)
        return False, None
    if isinstance(obj, dict):
        if field in obj:
            return True, obj[field]
        return False, None
    if hasattr(obj, field):
        return True, getattr(obj, field)
    return False, None


def _ce_page(items: list[Any], *, cursor: str, per_page: int, model: type):
    """Create an honest lite-style envelope with CE-specific offset cursors."""
    if not 1 <= per_page <= 1000:
        raise ToolError(f"CE per_page must be in the range 1..1000, got {per_page}.")
    cursor_str = (cursor or "").strip()
    if not cursor_str:
        offset = 0
    elif cursor_str.startswith("ce:") and cursor_str[3:].isascii() and cursor_str[3:].isdigit():
        digits = cursor_str[3:]
        if len(digits) > _MAX_CURSOR_OFFSET_DIGITS:
            safe_cursor = f"'{cursor_str[:32]}...'" if len(cursor_str) > 32 else f"'{cursor_str}'"
            raise ToolError(
                f"Invalid CE pagination cursor: {safe_cursor}. "
                f"Offset exceeds maximum supported digits ({_MAX_CURSOR_OFFSET_DIGITS})."
            )
        offset = int(digits)
    else:
        safe_cursor = f"'{cursor_str[:32]}...'" if len(cursor_str) > 32 else (f"'{cursor_str}'" if cursor_str else "''")
        raise ToolError(f"Invalid CE pagination cursor: {safe_cursor}. Expected empty string or 'ce:<offset>'.")

    total = len(items)
    if offset > total:
        raise ToolError(f"CE pagination cursor offset {offset} is out of range for total {total} items.")
    result = items[offset : offset + per_page]
    has_next = offset + len(result) < total
    has_prev = offset > 0
    payload = {
        "results": result,
        "count": len(result),
        "total_count": total,
        "total_results": total,
        "total_pages": ceil(total / per_page) if per_page else 1,
        "next_page_results": has_next,
        "prev_page_results": has_prev,
        "next_cursor": f"ce:{offset + len(result)}" if has_next else "",
        "prev_cursor": f"ce:{max(0, offset - per_page)}" if has_prev else "",
    }
    return model.model_validate(payload)


def list_projects_ce(
    client: Any,
    workspace_slug: str,
    *,
    cursor: str = "",
    per_page: int = 100,
    order_by: str = "",
) -> PaginatedProjectLiteResponse:
    """Read standard CE project pages, exclude archived records, then paginate.

    Refuses excessively large workspaces instead of returning partial or truncated results.
    Detects pagination stalls and repeated IDs during scan.
    """
    projects: list[Any] = []
    seen_ids: set[str] = set()
    seen_cursors: set[str] = set()
    backend_cursor: str | None = None

    for _ in range(_MAX_PAGES):
        page = client.projects.list(
            workspace_slug=workspace_slug,
            params=PaginatedQueryParams(
                cursor=backend_cursor,
                per_page=_BACKEND_PAGE_SIZE,
                order_by=order_by or None,
            ),
        )
        results = getattr(page, "results", None)
        if results is None or not isinstance(results, (list, tuple)):
            raise ToolError("CE project listing did not return a valid paginated response.")
        for project in results:
            pid = _get_field(project, "id")
            if not pid:
                raise ToolError("CE project pagination returned missing project IDs.")
            if pid in seen_ids:
                raise ToolError(
                    "CE project pagination returned duplicate project IDs during scan. "
                    "Concurrent workspace changes may have shifted pagination boundaries; retry the request."
                )
            seen_ids.add(pid)
            projects.append(project)

        if len(projects) > _MAX_PROJECTS:
            raise ToolError(f"CE project listing exceeds maximum scan limit of {_MAX_PROJECTS} projects.")

        if not getattr(page, "next_page_results", False):
            break

        following = getattr(page, "next_cursor", None)
        if not following or following in seen_cursors or not results:
            raise ToolError("CE project pagination stalled during scan.")
        seen_cursors.add(following)
        backend_cursor = following
    else:
        raise ToolError(f"CE project listing exceeded maximum limit of {_MAX_PAGES} pages.")

    active_projects = [
        project.model_dump(include=_PROJECT_LITE_FIELDS)
        if hasattr(project, "model_dump")
        else {k: v for k, v in project.items() if k in _PROJECT_LITE_FIELDS}
        for project in projects
        if _get_field(project, "archived_at") is None
    ]
    return _ce_page(
        active_projects,
        cursor=cursor,
        per_page=per_page,
        model=PaginatedProjectLiteResponse,
    )


def list_workspace_members_ce(
    client: Any,
    workspace_slug: str,
    *,
    cursor: str = "",
    per_page: int = 100,
    order_by: str = "",
    first_name: str = "",
    last_name: str = "",
    email: str = "",
    display_name: str = "",
    role_slug: str = "",
    is_active: bool | None = None,
    is_bot: bool | None = None,
) -> PaginatedWorkspaceMemberResponse:
    """Use the standard CE members endpoint with truthful client-side filters and pagination."""
    members = client.workspaces.get_members(workspace_slug=workspace_slug)
    if not isinstance(members, (list, tuple)):
        raise ToolError("CE workspace member listing did not return a valid list.")
    members = list(members)
    if len(members) > _MAX_MEMBERS:
        raise ToolError(f"CE workspace member listing exceeds maximum limit of {_MAX_MEMBERS} members.")

    text_filters = {
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "display_name": display_name,
    }
    for field, value in text_filters.items():
        if value:
            needle = value.casefold()
            filtered_text: list[Any] = []
            for m in members:
                present, val = _field_presence_and_value(m, field)
                if not present:
                    raise ToolError(f"CE cannot reliably filter by {field}: API field unavailable on records.")
                if val is not None and needle in str(val).casefold():
                    filtered_text.append(m)
            members = filtered_text

    exact_filters = (
        ("role_slug", role_slug),
        ("is_active", is_active),
        ("is_bot", is_bot),
    )
    for field, value in exact_filters:
        if value is None or value == "":
            continue
        filtered: list[Any] = []
        for m in members:
            present, val = _field_presence_and_value(m, field)
            if not present:
                raise ToolError(f"CE cannot reliably filter by {field}: API field unavailable on records.")
            if field == "role_slug":
                if val == value:
                    filtered.append(m)
            else:
                if val is value:
                    filtered.append(m)
        members = filtered

    if order_by:
        reverse = order_by.startswith("-")
        field = order_by[1:] if reverse else order_by
        allowed_sort_fields = {
            "first_name",
            "last_name",
            "email",
            "display_name",
            "id",
        }
        if field not in allowed_sort_fields:
            raise ToolError(
                f"Unsupported CE workspace member order_by: '{order_by}'. "
                f"Allowed fields: {', '.join(sorted(allowed_sort_fields))}."
            )

        def _member_comparator(a: Any, b: Any) -> int:
            a_val = _get_field(a, field, None)
            b_val = _get_field(b, field, None)
            a_none = a_val is None
            b_none = b_val is None

            # Nulls always placed at the end in both ASC and DESC
            if a_none and not b_none:
                return 1
            if b_none and not a_none:
                return -1
            if not a_none and not b_none:
                a_str = str(a_val).casefold()
                b_str = str(b_val).casefold()
                if a_str != b_str:
                    if reverse:
                        return -1 if a_str > b_str else 1
                    return -1 if a_str < b_str else 1

            # Deterministic tie-breaker: always ascending ID
            a_id = str(_get_field(a, "id", ""))
            b_id = str(_get_field(b, "id", ""))
            if a_id < b_id:
                return -1
            if a_id > b_id:
                return 1
            return 0

        members.sort(key=cmp_to_key(_member_comparator))

    rows = [m.model_dump() if hasattr(m, "model_dump") else m for m in members]
    return _ce_page(
        rows,
        cursor=cursor,
        per_page=per_page,
        model=PaginatedWorkspaceMemberResponse,
    )
