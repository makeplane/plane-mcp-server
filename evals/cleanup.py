"""Delete leftover projects whose names start with a prefix (default ``"EVAL "``).

``python -m evals.cleanup [--prefix "EVAL " | --yes]`` — dry-run lists only; ``--yes`` is
required before anything is deleted. Credentials come from EVAL_PLANE_* via make_plane_client.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from plane.models.query_params import PaginatedQueryParams


def list_projects_with_prefix(plane: Any, workspace_slug: str, prefix: str) -> list[Any]:
    """Return projects whose name starts with ``prefix`` (paginated list).

    Matches the SDK contract used elsewhere in the repo: pass
    ``params=PaginatedQueryParams(...)`` and stop when ``not page.next_page_results``.
    Do not fall back on ``next_cursor`` alone — the SDK always populates it.
    """
    matches: list[Any] = []
    cursor = None
    while True:
        params = PaginatedQueryParams(per_page=100, cursor=cursor)
        page = plane.projects.list(workspace_slug=workspace_slug, params=params)
        results = page.results if hasattr(page, "results") else page
        for proj in results or []:
            # Prefix may include a trailing space (default "EVAL ") so "EVALUATION" is excluded.
            name = getattr(proj, "name", None) or ""
            if name.startswith(prefix):
                matches.append(proj)
        if not getattr(page, "next_page_results", False):
            break
        cursor = page.next_cursor
    return matches


def delete_projects(
    plane: Any,
    workspace_slug: str,
    projects: list[Any],
    *,
    yes: bool,
) -> tuple[int, int]:
    """Delete projects when yes=True. Returns (deleted, failed). Dry-run: (0, 0)."""
    if not yes:
        return 0, 0
    deleted = failed = 0
    for proj in projects:
        pid = getattr(proj, "id", None)
        name = getattr(proj, "name", pid)
        try:
            plane.projects.delete(workspace_slug=workspace_slug, project_id=pid)
            deleted += 1
            print(f"  deleted {name!r} ({pid})")
        except Exception as exc:
            failed += 1
            print(f"  FAILED {name!r} ({pid}): {exc}", file=sys.stderr)
    return deleted, failed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Clean up leftover EVAL projects (dry-run by default)")
    p.add_argument("--prefix", type=str, default="EVAL ", help='Project name prefix (default: "EVAL ")')
    p.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete matched projects (default is dry-run list only)",
    )
    args = p.parse_args(argv)

    from evals.seed import make_plane_client

    try:
        plane, workspace_slug = make_plane_client()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    projects = list_projects_with_prefix(plane, workspace_slug, args.prefix)
    print(f"workspace={workspace_slug} prefix={args.prefix!r} matches={len(projects)}")
    for proj in projects:
        pid = getattr(proj, "id", "?")
        name = getattr(proj, "name", "?")
        ident = getattr(proj, "identifier", "")
        print(f"  {name!r}  id={pid}  identifier={ident}")

    if not projects:
        print("nothing to delete")
        return 0

    if not args.yes:
        print(f"dry-run: would delete {len(projects)} project(s); re-run with --yes to delete")
        return 0

    deleted, failed = delete_projects(plane, workspace_slug, projects, yes=True)
    print(f"summary: deleted={deleted} failed={failed} matched={len(projects)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
