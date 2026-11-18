"""Read-only fallback for standard Plane Community Edition cycle and module list endpoints.

Some self-hosted Plane Community Edition versions (prior to v1.4.0) lack the
dedicated lightweight endpoints (/cycles-lite/, /modules-lite/) and return
HTTP 404. This module provides bounded, truthful fallbacks to the standard
endpoints (/cycles/, /modules/) with honest client-side pagination envelopes
and field trimming conforming to the *Lite models.
"""

from __future__ import annotations

import uuid
from math import ceil
from typing import Any

from fastmcp.exceptions import ToolError
from plane.models.cycles import CycleLite, PaginatedCycleLiteResponse
from plane.models.modules import ModuleLite, PaginatedModuleLiteResponse

from plane_mcp.client import current_workspace

_MAX_ITEMS = 1_000
_MAX_PAGES = 10
_BACKEND_PAGE_SIZE = 100
_MAX_CURSOR_OFFSET_DIGITS = 10

_CYCLE_LITE_FIELDS = set(CycleLite.model_fields.keys())
_MODULE_LITE_FIELDS = set(ModuleLite.model_fields.keys())


def _get_field(obj: Any, field: str, default: Any = None) -> Any:
    """Safely extract a field value from a model or dict without raising."""
    if isinstance(obj, dict):
        val = obj.get(field)
    else:
        val = getattr(obj, field, default)
    return default if val is None else val


def _is_uuid(val: Any) -> bool:
    """Determine whether a string is a valid UUID."""
    try:
        uuid.UUID(str(val))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _validate_scope(
    item: Any,
    *,
    requested_project: str,
    workspace_slug: str,
    workspace_id: str | None = None,
    resource_name: str,
) -> tuple[str, str]:
    """Validate project and workspace scoping, preventing cross-project or cross-workspace leakage.

    Scope Trust Boundary:
    - Plane's standard API endpoints are strictly scoped by URL path:
      `/api/v1/workspaces/{workspace_slug}/projects/{project_id}/...`
    - Project scope:
      If the record explicitly contains a project field, it must match
      ``requested_project``. Otherwise, the endpoint-enforced project scope is used.
    - Workspace scope:
      1. If the record contains a slug string matching ``workspace_slug``, it is accepted.
      2. If an authoritative ``workspace_id`` is known (e.g. from OAuth token claims or caller),
         a record workspace field matching ``workspace_id`` is verified and accepted.
         Any conflicting UUID or slug is rejected.
      3. If no authoritative ``workspace_id`` is known (e.g. API key / stdio auth where
         credentials only specify the slug):
         - If the record contains a UUID, it cannot be independently verified against the slug
           without additional network requests. Under the documented trust boundary of the
           backend's workspace/project-scoped endpoint, the record's UUID is retained without
           claiming independent UUID identity verification.
         - If the record contains a slug string that does not match ``workspace_slug``, it is rejected.
      4. If the workspace field is missing or null, it defaults to ``workspace_slug``.

    Returns (effective_project, effective_workspace).
    """
    rec_project = _get_field(item, "project")
    if rec_project:
        if str(rec_project) != str(requested_project):
            raise ToolError(f"CE {resource_name} record does not match the requested project scope.")
        effective_project = str(rec_project)
    else:
        # Standard endpoint is strictly project-scoped (/projects/{project_id}/...)
        effective_project = requested_project

    rec_workspace = _get_field(item, "workspace")
    if rec_workspace:
        rec_ws_str = str(rec_workspace).strip()
        if workspace_id is not None:
            known_ws_id = workspace_id.strip()
        else:
            try:
                known_ws_id = str(current_workspace().get("id") or "").strip()
            except Exception:
                known_ws_id = ""

        if rec_ws_str == str(workspace_slug):
            effective_workspace = rec_ws_str
        elif known_ws_id and rec_ws_str == known_ws_id:
            effective_workspace = rec_ws_str
        elif not known_ws_id and _is_uuid(rec_ws_str):
            # Endpoint trust boundary: preserve backend record's UUID without asserting
            # independent identity verification, avoiding extra network requests.
            effective_workspace = rec_ws_str
        else:
            raise ToolError(f"CE {resource_name} record does not match the requested workspace scope.")
    else:
        # Standard endpoint is strictly workspace-scoped by URL path
        effective_workspace = workspace_slug

    return effective_project, effective_workspace


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


def _ce_collect_standard(
    method: Any,
    workspace_slug: str,
    project_id: str,
    *,
    resource_name: str,
) -> list[Any]:
    """Read every standard API page, detecting pagination stalls, duplicate IDs, and bounds."""
    items: list[Any] = []
    seen_ids: set[str] = set()
    seen_cursors: set[str] = set()
    backend_cursor: str | None = None

    for _ in range(_MAX_PAGES):
        params: dict[str, Any] = {"per_page": _BACKEND_PAGE_SIZE}
        if backend_cursor:
            params["cursor"] = backend_cursor

        page = method(
            workspace_slug=workspace_slug,
            project_id=project_id,
            params=params,
        )

        # Handle bare list response (e.g. non-paginated endpoints)
        if isinstance(page, list):
            for item in page:
                item_id = _get_field(item, "id")
                if not item_id:
                    raise ToolError(f"CE {resource_name} record missing required 'id' attribute.")
                item_id_str = str(item_id)
                if item_id_str in seen_ids:
                    raise ToolError(f"CE {resource_name} listing returned duplicate record ID '{item_id_str}'.")
                seen_ids.add(item_id_str)
                items.append(item)
                if len(items) > _MAX_ITEMS:
                    raise ToolError(f"CE {resource_name} listing exceeds maximum safety limit of {_MAX_ITEMS} items.")
            return items

        # Handle paginated response
        results = getattr(page, "results", None)
        if results is None or not isinstance(results, (list, tuple)):
            raise ToolError(f"CE standard {resource_name} list did not return a valid results list.")

        for item in results:
            item_id = _get_field(item, "id")
            if not item_id:
                raise ToolError(f"CE {resource_name} record missing required 'id' attribute.")
            item_id_str = str(item_id)
            if item_id_str in seen_ids:
                raise ToolError(f"CE {resource_name} listing returned duplicate record ID '{item_id_str}'.")
            seen_ids.add(item_id_str)
            items.append(item)
            if len(items) > _MAX_ITEMS:
                raise ToolError(f"CE {resource_name} listing exceeds maximum safety limit of {_MAX_ITEMS} items.")

        if not getattr(page, "next_page_results", False):
            backend_total = getattr(page, "total_count", None)
            if backend_total is None:
                backend_total = getattr(page, "total_results", None)
            if backend_total is not None and isinstance(backend_total, int):
                if backend_total != len(items):
                    raise ToolError(
                        f"CE standard {resource_name} listing incomplete: backend reported "
                        f"total={backend_total} but only {len(items)} items were collected."
                    )
            return items

        following = getattr(page, "next_cursor", None)
        if not following or following in seen_cursors or not results:
            raise ToolError(f"CE {resource_name} pagination stalled during scan.")
        seen_cursors.add(following)
        backend_cursor = following
    else:
        raise ToolError(f"CE {resource_name} listing exceeded maximum limit of {_MAX_PAGES} pages.")


def list_cycles_ce(
    client: Any,
    workspace_slug: str,
    project_id: str,
    *,
    cursor: str = "",
    per_page: int = 100,
    status: str = "",
    order_by: str = "",
    workspace_id: str | None = None,
) -> PaginatedCycleLiteResponse:
    """Use standard cycles API, exclude archived records, and paginate honestly.

    Fails closed if unsupported status or order_by filters are requested.
    """
    if not 1 <= per_page <= 1000:
        raise ToolError(f"CE per_page must be in the range 1..1000, got {per_page}.")
    if status:
        raise ToolError(
            f"Filtering by status='{status}' is not supported on this Plane CE cycle fallback endpoint. "
            "Please retry without status."
        )
    if order_by:
        raise ToolError(
            f"Sorting by '{order_by}' is not supported on this Plane CE cycle fallback endpoint. "
            "Please retry without order_by."
        )

    cycles = _ce_collect_standard(
        client.cycles.list,
        workspace_slug=workspace_slug,
        project_id=project_id,
        resource_name="cycle",
    )

    active_cycles = [c for c in cycles if _get_field(c, "archived_at") is None]
    rows: list[dict[str, Any]] = []
    for c in active_cycles:
        row = (
            c.model_dump(include=_CYCLE_LITE_FIELDS)
            if hasattr(c, "model_dump")
            else {k: v for k, v in c.items() if k in _CYCLE_LITE_FIELDS}
        )
        eff_proj, eff_ws = _validate_scope(
            c,
            requested_project=project_id,
            workspace_slug=workspace_slug,
            workspace_id=workspace_id,
            resource_name="cycle",
        )
        row["project"] = eff_proj
        row["workspace"] = eff_ws

        owner = _get_field(c, "owned_by")
        if not owner:
            item_id = _get_field(c, "id", "")
            raise ToolError(f"CE cycle record '{item_id}' missing required 'owned_by' attribute.")
        row["owned_by"] = str(owner)
        rows.append(row)
    return _ce_page(
        rows,
        cursor=cursor,
        per_page=per_page,
        model=PaginatedCycleLiteResponse,
    )


def list_modules_ce(
    client: Any,
    workspace_slug: str,
    project_id: str,
    *,
    cursor: str = "",
    per_page: int = 100,
    order_by: str = "",
    status: str = "",
    workspace_id: str | None = None,
) -> PaginatedModuleLiteResponse:
    """Use standard modules API, exclude archived records, and paginate honestly.

    Fails closed if unsupported status or order_by filters are requested.
    """
    if not 1 <= per_page <= 1000:
        raise ToolError(f"CE per_page must be in the range 1..1000, got {per_page}.")
    if status:
        raise ToolError(
            f"Filtering by status='{status}' is not supported on this Plane CE module fallback endpoint. "
            "Please retry without status."
        )
    if order_by:
        raise ToolError(
            f"Sorting by '{order_by}' is not supported on this Plane CE module fallback endpoint. "
            "Please retry without order_by."
        )

    modules = _ce_collect_standard(
        client.modules.list,
        workspace_slug=workspace_slug,
        project_id=project_id,
        resource_name="module",
    )

    active_modules = [m for m in modules if _get_field(m, "archived_at") is None]
    rows: list[dict[str, Any]] = []
    for m in active_modules:
        row = (
            m.model_dump(include=_MODULE_LITE_FIELDS)
            if hasattr(m, "model_dump")
            else {k: v for k, v in m.items() if k in _MODULE_LITE_FIELDS}
        )
        eff_proj, eff_ws = _validate_scope(
            m,
            requested_project=project_id,
            workspace_slug=workspace_slug,
            workspace_id=workspace_id,
            resource_name="module",
        )
        row["project"] = eff_proj
        row["workspace"] = eff_ws
        rows.append(row)
    return _ce_page(
        rows,
        cursor=cursor,
        per_page=per_page,
        model=PaginatedModuleLiteResponse,
    )
