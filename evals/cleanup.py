"""Delete leftover eval projects or fixed-name workspace sentinels.

``python -m evals.cleanup [--prefix "EVAL " | --sentinels] [--yes]`` — dry-run lists only;
``--yes`` is required before anything is deleted. Credentials come from EVAL_PLANE_*.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from plane.models.query_params import PaginatedQueryParams

from evals.seed.customers import (
    EVALUATION_CUSTOMER_PROPERTY_NAME,
    is_evaluation_customer_name,
)
from evals.seed.item_types import (
    BUG_TYPE_NAME,
    FIXTURE_WORK_ITEM_TYPE_NAMES,
    is_severity_property,
    is_work_item_type_named,
    list_workspace_properties_for_type,
    list_workspace_work_item_types,
)
from evals.seed.releases import EVALUATION_RELEASE_TAG_VERSION
from evals.seed.workspace import list_workspace_rows


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


def list_sentinel_workspace_artifacts(plane: Any, workspace_slug: str) -> list[dict[str, Any]]:
    """Return fixed-name workspace fixtures that can false-pass eval tasks."""
    customers = plane.customers
    specs = (
        (
            "customer",
            customers,
            lambda row: is_evaluation_customer_name(getattr(row, "name", None)),
            lambda row: (getattr(row, "name", None) or "").strip(),
        ),
        (
            "release_tag",
            plane.releases.tags,
            lambda row: (getattr(row, "version", None) or "").strip() == EVALUATION_RELEASE_TAG_VERSION,
            lambda row: (getattr(row, "version", None) or "").strip(),
        ),
        (
            "customer_property",
            customers.properties,
            lambda row: (
                (getattr(row, "display_name", None) or getattr(row, "name", None) or "").strip().casefold()
                == EVALUATION_CUSTOMER_PROPERTY_NAME.casefold()
            ),
            lambda row: (getattr(row, "display_name", None) or getattr(row, "name", None) or "").strip(),
        ),
    )
    artifacts: list[dict[str, Any]] = []
    for kind, api, matches, display_name in specs:
        for row in list_workspace_rows(api, workspace_slug):
            object_id = getattr(row, "id", None)
            if object_id is not None and matches(row):
                artifacts.append({"kind": kind, "id": object_id, "name": display_name(row)})

    type_api = getattr(plane, "workspace_work_item_types", None)
    if callable(getattr(type_api, "list", None)):
        for row in list_workspace_work_item_types(plane, workspace_slug):
            object_id = getattr(row, "id", None)
            if object_id is None:
                continue
            # Every fixture name, not just Incident. Bug used to be reachable only as the type
            # whose Severity property gets removed, so leftover Bug types were both undeletable
            # by this tool and counted as "nothing to delete" -- a workspace reported clean while
            # holding types that skew any task reading the workspace-level list. Duplicates of
            # one name each match, so a double-seeded type is fully removed.
            matched = next(
                (name for name in FIXTURE_WORK_ITEM_TYPE_NAMES if is_work_item_type_named(row, name)),
                None,
            )
            if matched is not None:
                artifacts.append({"kind": "work_item_type", "id": object_id, "name": matched})

    property_api = getattr(plane, "workspace_work_item_properties", None)
    links_api = getattr(type_api, "properties", None)
    if callable(getattr(property_api, "list", None)) and callable(getattr(links_api, "list", None)):
        for row in list_workspace_properties_for_type(plane, workspace_slug, BUG_TYPE_NAME):
            object_id = getattr(row, "id", None)
            if object_id is not None and is_severity_property(row):
                display = getattr(row, "display_name", None) or getattr(row, "name", None) or ""
                artifacts.append({"kind": "work_item_property", "id": object_id, "name": display.strip()})
    return artifacts


def list_unowned_workspace_work_item_types(plane: Any, workspace_slug: str) -> list[dict[str, Any]]:
    """Return workspace-level work item types this harness never creates.

    Reported rather than deleted by default: a type the harness did not create may be a real
    workspace's configuration, and this runs against instances it does not own. They still
    have to be *visible*, because a workspace holding types another workspace lacks skews
    every task that reads the workspace-level list, and silence there reads as clean.
    """
    type_api = getattr(plane, "workspace_work_item_types", None)
    if not callable(getattr(type_api, "list", None)):
        return []
    unowned: list[dict[str, Any]] = []
    for row in list_workspace_work_item_types(plane, workspace_slug):
        object_id = getattr(row, "id", None)
        if object_id is None:
            continue
        if any(is_work_item_type_named(row, name) for name in FIXTURE_WORK_ITEM_TYPE_NAMES):
            continue
        unowned.append({"kind": "work_item_type", "id": object_id, "name": (getattr(row, "name", "") or "").strip()})
    return unowned


def _sentinel_description(artifact: dict[str, Any]) -> str:
    kind = str(artifact["kind"]).replace("_", " ")
    return f"{kind} {artifact['name']!r} ({artifact['id']})"


def delete_sentinel_workspace_artifacts(
    plane: Any,
    workspace_slug: str,
    artifacts: list[dict[str, Any]],
    *,
    yes: bool,
) -> tuple[int, int]:
    """Delete explicitly selected sentinel artifacts. Returns (deleted, failed)."""
    if not yes:
        return 0, 0
    deleted = failed = 0
    for artifact in artifacts:
        try:
            if artifact["kind"] == "customer":
                plane.customers.delete(workspace_slug=workspace_slug, customer_id=artifact["id"])
            elif artifact["kind"] == "release_tag":
                plane.releases.tags.delete(workspace_slug=workspace_slug, tag_id=artifact["id"])
            elif artifact["kind"] == "customer_property":
                plane.customers.properties.delete(workspace_slug=workspace_slug, property_id=artifact["id"])
            elif artifact["kind"] == "work_item_type":
                plane.workspace_work_item_types.delete(workspace_slug=workspace_slug, type_id=artifact["id"])
            elif artifact["kind"] == "work_item_property":
                plane.workspace_work_item_properties.delete(
                    workspace_slug=workspace_slug,
                    property_id=artifact["id"],
                )
            else:
                raise ValueError(f"unknown sentinel kind: {artifact['kind']}")
            deleted += 1
            print(f"  deleted sentinel {_sentinel_description(artifact)}")
        except Exception as exc:
            failed += 1
            print(f"  FAILED sentinel {_sentinel_description(artifact)}: {exc}", file=sys.stderr)
    return deleted, failed


def _cleanup_sentinels(plane: Any, workspace_slug: str, *, yes: bool, unowned: bool = False) -> int:
    artifacts = list_sentinel_workspace_artifacts(plane, workspace_slug)
    others = list_unowned_workspace_work_item_types(plane, workspace_slug)
    if unowned:
        artifacts = artifacts + others
    print(f"workspace={workspace_slug} sentinel_matches={len(artifacts)}")
    if others and not unowned:
        # Never let a zero match count imply a clean workspace while these sit here.
        print(f"note: {len(others)} workspace work item type(s) present that this tool did not create:")
        for artifact in others:
            print(f"  {_sentinel_description(artifact)}")
        print("  add --unowned to delete them too")
    if not artifacts:
        print("nothing to delete")
        return 0
    if not yes:
        for artifact in artifacts:
            print(f"  would delete sentinel {_sentinel_description(artifact)}")
        print("dry-run: re-run with --sentinels --yes to delete these sentinel fixture(s)")
        return 0
    deleted, failed = delete_sentinel_workspace_artifacts(plane, workspace_slug, artifacts, yes=True)
    print(f"summary: deleted={deleted} failed={failed} matched={len(artifacts)}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Clean up leftover eval fixtures (dry-run by default)")
    p.add_argument("--prefix", type=str, default="EVAL ", help='Project name prefix (default: "EVAL ")')
    p.add_argument(
        "--sentinels",
        action="store_true",
        help="Clean fixed-name workspace sentinels instead of projects",
    )
    p.add_argument(
        "--unowned",
        action="store_true",
        help="With --sentinels, also delete workspace work item types this harness never creates",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete matched objects (default is dry-run list only)",
    )
    args = p.parse_args(argv)

    from evals.seed import make_plane_client

    try:
        plane, workspace_slug = make_plane_client()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.sentinels:
        return _cleanup_sentinels(plane, workspace_slug, yes=args.yes, unowned=args.unowned)
    if args.unowned:
        print("error: --unowned only applies with --sentinels", file=sys.stderr)
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
