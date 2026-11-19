"""Bounded CE read fallback for missing workspace work-item count routes."""

from __future__ import annotations

from typing import Any

from fastmcp.exceptions import ToolError
from plane.errors.errors import HttpError
from plane.models.query_params import PaginatedQueryParams, WorkItemQueryParams

MAX_PROJECTS = 100
MAX_TRAVERSED_PROJECTS = 250
MAX_PAGES = 10


def _project_total(client: Any, slug: str, project_id: str) -> int:
    """Read the authoritative total_count from a project work-item listing.

    Issues a single DRF listing request with per_page=1. DRF computes total_count
    via a database COUNT query filtered by project_id, providing an exact
    single-query snapshot for the project.
    """
    response = client.work_items.list(
        workspace_slug=slug,
        project_id=project_id,
        params=WorkItemQueryParams(per_page=1),
    )
    total = getattr(response, "total_count", None)
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise ToolError(f"CE project '{project_id}' work-item response has no trustworthy total_count.")
    results = getattr(response, "results", None)
    if results is not None:
        if not isinstance(results, (list, tuple)):
            raise ToolError(f"CE project '{project_id}' work-item response has malformed results collection.")
        if len(results) > total:
            raise ToolError(f"CE project '{project_id}' work-item response contradicts total_count.")
    return total


def _workspace_project_ids(client: Any, slug: str) -> list[str]:
    """Enumerate active projects up to bounded page and project limits."""
    ids: list[str] = []
    seen_ids: set[str] = set()
    seen_cursors: set[str] = set()
    cursor: str | None = None
    for _ in range(MAX_PAGES):
        page = client.projects.list(
            workspace_slug=slug,
            params=PaginatedQueryParams(per_page=100, cursor=cursor),
        )
        results = getattr(page, "results", None)
        next_page = getattr(page, "next_page_results", None)
        if results is None or not isinstance(results, (list, tuple)) or not isinstance(next_page, bool):
            raise ToolError("CE project API did not return a valid paginated response.")
        for project in results:
            pid = getattr(project, "id", None)
            if not pid or pid in seen_ids:
                raise ToolError("CE project pagination repeated or omitted a project ID.")
            seen_ids.add(pid)
            if getattr(project, "archived_at", None) is None:
                ids.append(pid)
            if len(ids) > MAX_PROJECTS:
                raise ToolError(
                    f"CE workspace count exceeds {MAX_PROJECTS} active project scan limit. "
                    "Specify an explicit project_id to count individual projects."
                )
            if len(seen_ids) > MAX_TRAVERSED_PROJECTS:
                raise ToolError(
                    f"CE workspace count exceeds {MAX_TRAVERSED_PROJECTS} traversed project scan limit. "
                    "Specify an explicit project_id to count individual projects."
                )
        if not next_page:
            return ids
        following = getattr(page, "next_cursor", None)
        if not following or following in seen_cursors or not results:
            raise ToolError("CE project pagination stalled during count scan.")
        seen_cursors.add(following)
        cursor = following
    raise ToolError(
        f"CE workspace count exceeded {MAX_PAGES} page scan limit. "
        "Specify an explicit project_id to count individual projects."
    )


def count_fallback(client: Any, workspace_slug: str, project_id: str = "") -> dict[str, Any]:
    """Accurate unfiltered, ungrouped project or workspace count fallback.

    Note on snapshot semantics:
    - Project-scoped count (project_id provided) performs a single request to
      the project's DRF work-item list endpoint with per_page=1, obtaining an
      atomic database COUNT snapshot for that project.
    - Workspace-wide count (project_id omitted) is a bounded, best-effort aggregate
      computed across multiple sequential HTTP requests (up to MAX_PAGES project
      listing pages + up to MAX_PROJECTS individual project counts). It is not an
      ACID snapshot-isolated transaction; concurrent writes across projects during
      the scan window can result in slight count drift.

    Bound constraints & outbound requests:
    - Max project pages: MAX_PAGES (10).
    - Max active projects scanned: MAX_PROJECTS (100).
    - Max traversed projects: MAX_TRAVERSED_PROJECTS (250).
    - Max outbound requests: 1 (count attempt) + 10 (projects) + 100 (workitems) = 111.
    """
    if project_id:
        total = _project_total(client, workspace_slug, project_id)
        return {
            "grouped_by": None,
            "sub_grouped_by": None,
            "total_count": total,
            "grouped_counts": {},
        }

    ids = _workspace_project_ids(client, workspace_slug)
    total = 0
    inaccessible: list[str] = []
    for pid in ids:
        try:
            total += _project_total(client, workspace_slug, pid)
        except HttpError as exc:
            if exc.status_code in (401, 403):
                inaccessible.append(pid)
            else:
                raise

    if inaccessible:
        raise ToolError(
            f"Workspace-wide count cannot be accurately determined because {len(inaccessible)} project(s) "
            f"({', '.join(inaccessible)}) are not accessible under the current API credentials. "
            "To prevent returning an inaccurate partial count, specify an accessible project_id."
        )

    return {
        "grouped_by": None,
        "sub_grouped_by": None,
        "total_count": total,
        "grouped_counts": {},
    }
