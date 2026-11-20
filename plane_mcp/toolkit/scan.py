"""Work items across projects, for an edition that cannot list a whole workspace.

Plane Community Edition v1.4.2 answers `work-items/` at workspace level with 404
and rejects PQL with 400, so "my open issues, everywhere" has no server-side
form. This walks the projects and pages through each one, filtering here.

Two things keep that honest:

* A project the caller cannot read answers 403. It is reported in
  `skipped_projects` and the walk goes on, rather than one closed project
  discarding every readable one.
* The walk is bounded. Plane rate-limits an API key (`API_KEY_RATE_LIMIT`, 60 a
  minute in this deployment), so it stops after `MAX_REQUESTS` calls or on a 429
  and says so in `truncated`. A partial answer that does not say it is partial
  reads as a complete one.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from plane.errors.errors import HttpError
from plane.models.query_params import ProjectLiteListQueryParams, WorkItemQueryParams

from plane_mcp.toolkit.paging import dump_results
from plane_mcp.toolkit.runtime import ids_of

PAGE = 100
MAX_REQUESTS = 50
MAX_MATCHES = 500

# What a hit carries when the caller names no `fields`; enough to read a result
# list without a second call per item.
LEAN_FIELDS = ("id", "sequence_id", "name", "state", "priority", "project", "assignees")


@dataclass
class Scan:
    results: list[dict[str, Any]] = field(default_factory=list)
    by_project: Counter[str] = field(default_factory=Counter)
    scanned_projects: list[str] = field(default_factory=list)
    skipped_projects: list[str] = field(default_factory=list)
    requests: int = 0
    truncated: str | None = None

    def _coverage(self) -> dict[str, Any]:
        return {
            "projects_scanned": len(self.scanned_projects),
            "skipped_projects": self.skipped_projects,
            "truncated": self.truncated,
        }

    def listing(self) -> dict[str, Any]:
        return {"results": self.results, "total_count": len(self.results)} | self._coverage()

    def counts(self) -> dict[str, Any]:
        return {"count": sum(self.by_project.values()), "by_project": dict(self.by_project)} | self._coverage()


class _Stop(Exception):
    """The walk reached a limit; what it has so far is the answer."""


def _spend(scan: Scan) -> None:
    if scan.requests >= MAX_REQUESTS:
        raise _Stop(f"stopped after {MAX_REQUESTS} Plane API calls (rate limit); narrow the query")
    scan.requests += 1


def _projects(client: Any, workspace_slug: str, scan: Scan) -> list[tuple[str, str]]:
    """(id, identifier) of every project the workspace lists, readable or not."""
    found: list[tuple[str, str]] = []
    cursor = None
    while True:
        _spend(scan)
        page = client.projects.list_lite(
            workspace_slug=workspace_slug,
            params=ProjectLiteListQueryParams(per_page=PAGE, cursor=cursor, include_archived=False),
        )
        found += [(p.id, p.identifier) for p in page.results]
        cursor = page.next_cursor
        if not page.next_page_results or not cursor:
            return found


def _matches(item: Any, assignee_id: str | None, state_groups: list[str] | None) -> bool:
    if assignee_id and assignee_id not in ids_of(getattr(item, "assignees", None)):
        return False
    return not state_groups or getattr(getattr(item, "state", None), "group", None) in state_groups


def resolve_me(client: Any) -> str | None:
    return client.users.get_me().id or None


def scan_work_items(
    client: Any,
    workspace_slug: str,
    *,
    project_id: str = "",
    assignee_id: str | None = None,
    state_groups: list[str] | None = None,
    fields: str = "",
    max_matches: int | None = MAX_MATCHES,
) -> Scan | str:
    """Matching work items of every readable project, or of `project_id` alone.

    Returns an error string when `project_id` is not a project of the workspace.
    """
    scan = Scan()
    wanted = ",".join(dict.fromkeys([*LEAN_FIELDS, *(f.strip() for f in fields.split(",") if f.strip())]))
    try:
        projects = _projects(client, workspace_slug, scan)
        if project_id:
            projects = [p for p in projects if project_id in p]
            if not projects:
                return f"Error: project {project_id!r} is not a project of this workspace."
        for pid, identifier in projects:
            hits, cursor = [], None
            try:
                while True:
                    _spend(scan)
                    page = client.work_items.list(
                        workspace_slug=workspace_slug,
                        project_id=pid,
                        params=WorkItemQueryParams(per_page=PAGE, cursor=cursor, expand="state", fields=wanted),
                    )
                    hits += [i for i in page.results if _matches(i, assignee_id, state_groups)]
                    cursor = page.next_cursor
                    if not page.next_page_results or not cursor:
                        break
            except HttpError as exc:
                if exc.status_code == 403:
                    scan.skipped_projects.append(identifier)
                    continue
                raise
            scan.scanned_projects.append(identifier)
            scan.by_project[identifier] += len(hits)
            for row, item in zip(dump_results(hits, wanted), hits, strict=True):
                scan.results.append({"identifier": f"{identifier}-{item.sequence_id}", **row})
            if max_matches and len(scan.results) >= max_matches:
                scan.truncated = f"stopped at {max_matches} matches; add a filter"
                break
    except _Stop as stop:
        scan.truncated = str(stop)
    except HttpError as exc:
        if exc.status_code != 429:
            raise
        scan.truncated = "Plane answered 429 (rate limit); the result is partial, retry in a minute"
    return scan
