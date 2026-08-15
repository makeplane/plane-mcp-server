"""Fixture dispatch and workspace preclean for evaluation runs."""

from __future__ import annotations

import os
from typing import Any

from plane import PlaneClient

from evals.errors import TaskSkipped

from .customers import (
    CUSTOMER_NAME,
    EVALUATION_CUSTOMER_PROPERTY_NAME,
    is_evaluation_customer_name,
    seed_customer,
)
from .cycles import seed_cycles
from .intake import seed_intake
from .item_types import (
    BUG_TYPE_NAME,
    INCIDENT_TYPE_NAME,
    SEVERITY_PROPERTY_NAME,
    is_severity_property,
    is_work_item_type_named,
    list_workspace_properties_for_type,
    list_workspace_work_item_types,
    seed_item_type,
    workspace_owns_work_item_types,
)
from .labels import seed_labels
from .modules import seed_module
from .projects import (
    create_project_with_identifier_retry,
    enable_project_features,
    enable_workspace_features,
    seed_second_project,
)
from .releases import EVALUATION_RELEASE_TAG_VERSION, seed_release
from .states import seed_r7_state_oracle
from .work_items import (
    CHECKOUT_COMMENT_PHRASES,
    CHECKOUT_TIMEOUT_TITLE,
    DUE_THIS_WEEK_TITLES,
    UNFINISHED_CYCLE_TITLES,
    require_activities,
    seed_work_items,
)
from .workspace import list_workspace_rows

_WORKSPACE_BASELINE_CATEGORIES = (
    "customers",
    "release_tags",
    "customer_properties",
    "work_item_types",
    "work_item_properties",
)
_TASK_COLLISION_CATEGORIES = {
    "C1": {"customers"},
    "L3": {"release_tags"},
    "S1": {"work_item_properties"},
    "S3": {"work_item_types"},
}


def _snapshot_workspace_baseline(
    plane: PlaneClient,
    workspace_slug: str,
    categories: set[str] | None = None,
) -> dict[str, set[str] | None]:
    """Capture fixed-name workspace fixtures that existed before the agent runs."""
    baseline: dict[str, set[str] | None] = dict.fromkeys(_WORKSPACE_BASELINE_CATEGORIES)
    # Preserve the established always-on snapshots. Type APIs may be plan-gated, so
    # their ownership baselines are read only for tasks that can create those fixtures.
    wanted = {"customers", "release_tags", "customer_properties"} | set(categories or ())
    customers = getattr(plane, "customers", None)
    releases = getattr(plane, "releases", None)
    specs = (
        (
            "customers",
            customers,
            lambda row: is_evaluation_customer_name(getattr(row, "name", None)),
        ),
        (
            "release_tags",
            getattr(releases, "tags", None) if releases is not None else None,
            lambda row: (getattr(row, "version", None) or "").strip() == EVALUATION_RELEASE_TAG_VERSION,
        ),
        (
            "customer_properties",
            getattr(customers, "properties", None) if customers is not None else None,
            lambda row: (
                (getattr(row, "display_name", None) or getattr(row, "name", None) or "").strip().casefold()
                == EVALUATION_CUSTOMER_PROPERTY_NAME.casefold()
            ),
        ),
    )
    for category, api, matches in specs:
        if category not in wanted:
            continue
        if api is None:
            continue
        try:
            rows = list_workspace_rows(api, workspace_slug)
        except Exception as exc:
            raise RuntimeError(f"workspace baseline snapshot: list {category} failed: {exc}") from exc
        baseline[category] = {str(row.id) for row in rows if getattr(row, "id", None) is not None and matches(row)}

    if "work_item_types" in wanted:
        api = getattr(plane, "workspace_work_item_types", None)
        if callable(getattr(api, "list", None)):
            try:
                rows = (
                    list_workspace_work_item_types(plane, workspace_slug)
                    if workspace_owns_work_item_types(plane, workspace_slug)
                    else []
                )
            except Exception as exc:
                raise RuntimeError(f"workspace baseline snapshot: list work_item_types failed: {exc}") from exc
            baseline["work_item_types"] = {
                str(row.id)
                for row in rows
                if getattr(row, "id", None) is not None
                and (is_work_item_type_named(row, BUG_TYPE_NAME) or is_work_item_type_named(row, INCIDENT_TYPE_NAME))
            }

    if "work_item_properties" in wanted:
        type_api = getattr(plane, "workspace_work_item_types", None)
        property_api = getattr(plane, "workspace_work_item_properties", None)
        links_api = getattr(type_api, "properties", None)
        if (
            callable(getattr(type_api, "list", None))
            and callable(getattr(links_api, "list", None))
            and callable(getattr(property_api, "list", None))
        ):
            try:
                rows = (
                    list_workspace_properties_for_type(plane, workspace_slug, BUG_TYPE_NAME)
                    if workspace_owns_work_item_types(plane, workspace_slug)
                    else []
                )
            except Exception as exc:
                raise RuntimeError(f"workspace baseline snapshot: list work_item_properties failed: {exc}") from exc
            baseline["work_item_properties"] = {
                str(row.id) for row in rows if getattr(row, "id", None) is not None and is_severity_property(row)
            }
    return baseline


def _raise_fixture_collision(category: str, name: str, object_id: Any) -> None:
    raise TaskSkipped(
        f"env:fixture-collision:{category}:{name}; pre-existing object id={object_id}; "
        "run `python -m evals.cleanup --sentinels --yes`, then retry"
    )


def collision_categories(needs: set[str], task_id: str | None) -> set[str]:
    categories: set[str] = set()
    if "customer" in needs:
        categories.update({"customers", "customer_properties"})
    categories.update(_TASK_COLLISION_CATEGORIES.get(task_id or "", set()))
    return categories


def check_workspace_fixture_collisions(
    plane: PlaneClient,
    workspace_slug: str,
    categories: set[str],
) -> None:
    """Reject fixed-name workspace artifacts that would let a no-op agent false-pass.

    Missing API surfaces are silent. List failures raise so an unread workspace is never
    mistaken for a clean one.
    """
    customers = getattr(plane, "customers", None)
    releases = getattr(plane, "releases", None)
    specs = (
        (
            "customers",
            customers if callable(getattr(customers, "list", None)) else None,
            CUSTOMER_NAME,
            lambda row: is_evaluation_customer_name(getattr(row, "name", None)),
        ),
        (
            "release_tags",
            getattr(releases, "tags", None) if releases is not None else None,
            EVALUATION_RELEASE_TAG_VERSION,
            lambda row: (getattr(row, "version", None) or "").strip() == EVALUATION_RELEASE_TAG_VERSION,
        ),
        (
            "customer_properties",
            getattr(customers, "properties", None) if customers is not None else None,
            EVALUATION_CUSTOMER_PROPERTY_NAME,
            lambda row: (
                (getattr(row, "display_name", None) or getattr(row, "name", None) or "").strip().casefold()
                == EVALUATION_CUSTOMER_PROPERTY_NAME.casefold()
            ),
        ),
    )
    for category, api, fixture_name, matches in specs:
        if category not in categories:
            continue
        if not callable(getattr(api, "list", None)):
            continue
        try:
            rows = list_workspace_rows(api, workspace_slug)
        except Exception as exc:
            raise RuntimeError(f"workspace fixture collision check: list {category} failed: {exc}") from exc
        collision = next((row for row in rows if matches(row)), None)
        if collision is not None:
            _raise_fixture_collision(category, fixture_name, getattr(collision, "id", "unknown"))

    if "work_item_types" in categories:
        api = getattr(plane, "workspace_work_item_types", None)
        if callable(getattr(api, "list", None)):
            try:
                if not workspace_owns_work_item_types(plane, workspace_slug):
                    rows = []
                else:
                    rows = list_workspace_work_item_types(plane, workspace_slug)
            except Exception as exc:
                raise RuntimeError(f"workspace fixture collision check: list work_item_types failed: {exc}") from exc
            collision = next((row for row in rows if is_work_item_type_named(row, INCIDENT_TYPE_NAME)), None)
            if collision is not None:
                _raise_fixture_collision(
                    "work_item_types",
                    INCIDENT_TYPE_NAME,
                    getattr(collision, "id", "unknown"),
                )

    if "work_item_properties" in categories:
        type_api = getattr(plane, "workspace_work_item_types", None)
        property_api = getattr(plane, "workspace_work_item_properties", None)
        links_api = getattr(type_api, "properties", None)
        if (
            callable(getattr(type_api, "list", None))
            and callable(getattr(links_api, "list", None))
            and callable(getattr(property_api, "list", None))
        ):
            try:
                if not workspace_owns_work_item_types(plane, workspace_slug):
                    rows = []
                else:
                    rows = list_workspace_properties_for_type(plane, workspace_slug, BUG_TYPE_NAME)
            except Exception as exc:
                raise RuntimeError(
                    f"workspace fixture collision check: list work_item_properties failed: {exc}"
                ) from exc
            collision = next((row for row in rows if is_severity_property(row)), None)
            if collision is not None:
                _raise_fixture_collision(
                    "work_item_properties",
                    SEVERITY_PROPERTY_NAME,
                    getattr(collision, "id", "unknown"),
                )


def seed(
    plane: PlaneClient,
    run_id: str,
    needs: set[str],
    ctx: dict[str, Any],
    *,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Create the eval project and declared fixture groups.

    Mutates the caller-provided `ctx` in place so project_id is visible to teardown
    even if a later fixture step raises (F5).
    """
    run_prefix = run_id[:8]
    project_name = f"EVAL {run_prefix}"
    workspace_slug = os.environ["EVAL_PLANE_WORKSPACE_SLUG"]

    # Reset known keys while preserving object identity for the caller.
    ctx.clear()
    ctx.update(
        {
            "run_id": run_id,
            "run8": run_prefix,
            "task_id": task_id,
            "workspace_slug": workspace_slug,
            "project_id": None,
            "project_name": project_name,
            "project_identifier": None,  # filled after create (may retry suffix)
            "labels": {},
            "items": {},
            "item_identifiers": {},  # title -> PROJ-N for ID-in-hand prompts
            "item_ids": [],
            "fixture_item_ids": {},  # stable fixture title -> API-created id
            "fixture_item_titles": {},  # stable fixture title -> per-run display title
            "state_names": [],  # all project state display names (for R1 negative check)
            "r1_state_name": None,
            "bug_type": None,
            "bug_type_created": False,
            "bug_type_workspace_level": False,
            "bug_type_skip_reason": None,
            "cycles": {},
            "module": None,
            "module_completed_ids": [],
            "intake": {},
            "customer": None,
            "customer_request": None,
            "release": None,
            "second_project_id": None,
            "second_project_name": None,
            "r3_due_titles": list(DUE_THIS_WEEK_TITLES),
            "r3_due_count": len(DUE_THIS_WEEK_TITLES),
            "r5_title": CHECKOUT_TIMEOUT_TITLE,
            "r5_comment_phrases": list(CHECKOUT_COMMENT_PHRASES),
            "w6_unfinished_titles": list(UNFINISHED_CYCLE_TITLES),
            "workspace_objects": [],  # [{kind, id}, ...] surviving project delete
            "randomized_truth": {},
            "evidence_sentinels": {},
            "evidence_targets": {},
            # None means the category was unavailable and name-based teardown must fail closed.
            "workspace_baseline": dict.fromkeys(_WORKSPACE_BASELINE_CATEGORIES),
        }
    )

    # Reject only artifacts that could false-pass this task.
    task_collision_categories = collision_categories(needs, task_id)
    check_workspace_fixture_collisions(plane, workspace_slug, task_collision_categories)

    # EV + 8 hex chars; retry with a new suffix on soft-delete identifier collisions.
    project = create_project_with_identifier_retry(
        plane,
        workspace_slug,
        name=project_name,
        identifier_prefix="EV",
        initial_suffix=run_prefix.upper(),
    )
    ctx["project_id"] = project.id
    ctx["project_identifier"] = getattr(project, "identifier", None)

    # Workspace first, then project. Seeding is per task-rep, so S5 turning workspace
    # customers off must be undone in teardown or a later C1 rep 403s.
    feature_exclude: set[str] = set()
    workspace_feature_exclude: set[str] = set()
    if "leave_cycles_worklogs_off" in needs:
        feature_exclude = {"cycles", "worklogs"}
        workspace_feature_exclude = {"customers"}
        ctx["s5_left_customers_off"] = True
    if "leave_worklogs_off" in needs:
        # W11: only time tracking is off, so the agent meets one obstacle rather than a
        # project with several unrelated features disabled.
        feature_exclude |= {"worklogs"}
    ctx["feature_exclude"] = sorted(feature_exclude)
    ctx["ws_feature_exclude"] = sorted(workspace_feature_exclude)
    # Prior values are captured before the write so teardown restores the workspace
    # rather than forcing it to whatever this run happened to need.
    ownership_categories = set(task_collision_categories)
    if "bug_type" in needs:
        ownership_categories.update({"work_item_types", "work_item_properties"})
    ctx["workspace_baseline"] = _snapshot_workspace_baseline(
        plane,
        workspace_slug,
        ownership_categories,
    )
    ctx["workspace_features_prior"] = enable_workspace_features(
        plane, workspace_slug, exclude=workspace_feature_exclude
    )
    enable_project_features(plane, workspace_slug, project.id, exclude=feature_exclude)

    if task_id == "R7":
        seed_r7_state_oracle(plane, workspace_slug, ctx)

    # Labels before items so items can attach labels later if needed.
    if "labels" in needs:
        seed_labels(plane, workspace_slug, ctx)
    if "items" in needs:
        seed_work_items(plane, workspace_slug, ctx)
    # L2: comments must materialize as activities (activity worker must be running).
    if "activity_feed" in needs:
        if "items" not in needs and not ctx.get("item_ids"):
            seed_work_items(plane, workspace_slug, ctx)
        require_activities(plane, workspace_slug, ctx)
    if "bug_type" in needs:
        seed_item_type(plane, workspace_slug, ctx)
    if "cycles" in needs:
        # Cycles need items to attach unfinished work; seed items if not already.
        if "items" not in needs and not ctx["item_ids"]:
            seed_work_items(plane, workspace_slug, ctx)
        seed_cycles(plane, workspace_slug, ctx, leave_past_open="cycles_open_past" in needs)
    if "module" in needs:
        seed_module(plane, workspace_slug, ctx)
    if "intake" in needs:
        seed_intake(plane, workspace_slug, ctx)
    if "customer" in needs:
        seed_customer(plane, workspace_slug, ctx)
    if "release" in needs:
        seed_release(plane, workspace_slug, ctx)
    if "second_project" in needs:
        seed_second_project(plane, workspace_slug, ctx)

    return ctx
