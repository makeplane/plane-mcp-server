"""Fixture dispatch and workspace preclean for evaluation runs."""

from __future__ import annotations

import os
from typing import Any

from plane import PlaneClient

from .customers import EVALUATION_CUSTOMER_PROPERTY_NAME, seed_customer
from .cycles import seed_cycles
from .intake import seed_intake
from .item_types import seed_item_type
from .labels import seed_labels
from .modules import seed_module
from .projects import (
    create_project_with_identifier_retry,
    enable_project_features,
    enable_workspace_features,
    seed_second_project,
)
from .releases import EVALUATION_RELEASE_TAG_VERSION, seed_release
from .work_items import (
    CHECKOUT_COMMENT_PHRASES,
    CHECKOUT_TIMEOUT_TITLE,
    DUE_THIS_WEEK_TITLES,
    UNFINISHED_CYCLE_TITLES,
    require_activities,
    seed_work_items,
)


def remove_stale_workspace_artifacts(plane: PlaneClient, workspace_slug: str) -> None:
    """Delete leftover WS3 long-tail artifacts so a dirty workspace cannot false-pass.

    Removes any existing release tag ``eval-rc1`` and customer property
    ``Eval Industry`` before the rep seeds.

    Empty / not-found lists are silent. Clients without the API surface (offline
    test stubs) are skipped silently. If a matching artifact is **found** and
    cannot be deleted — or list fails on a present API — raises so the harness
    records ``infra_seed`` rather than running against dirty state.
    """
    releases = getattr(plane, "releases", None)
    tags_api = getattr(releases, "tags", None) if releases is not None else None
    if tags_api is not None:
        try:
            page = tags_api.list(workspace_slug=workspace_slug)
        except Exception as exc:
            raise RuntimeError(f"WS3 preclean: list release tags failed: {exc}") from exc
        rows = page.results if hasattr(page, "results") else page
        for tag in rows or []:
            version = (getattr(tag, "version", None) or "").strip()
            if version != EVALUATION_RELEASE_TAG_VERSION:
                continue
            tag_id = getattr(tag, "id", None)
            if not tag_id:
                continue
            try:
                tags_api.delete(workspace_slug=workspace_slug, tag_id=tag_id)
            except Exception as exc:
                raise RuntimeError(
                    f"WS3 preclean: failed to delete stale release tag "
                    f"{EVALUATION_RELEASE_TAG_VERSION!r} id={tag_id}: {exc}"
                ) from exc

    customers = getattr(plane, "customers", None)
    properties_api = getattr(customers, "properties", None) if customers is not None else None
    if properties_api is not None:
        try:
            page = properties_api.list(workspace_slug=workspace_slug)
        except Exception as exc:
            raise RuntimeError(f"WS3 preclean: list customer properties failed: {exc}") from exc
        rows = page.results if hasattr(page, "results") else page
        target = EVALUATION_CUSTOMER_PROPERTY_NAME.casefold()
        for customer_property in rows or []:
            display = (
                getattr(customer_property, "display_name", None) or getattr(customer_property, "name", None) or ""
            ).strip()
            if display.casefold() != target:
                continue
            property_id = getattr(customer_property, "id", None)
            if not property_id:
                continue
            try:
                properties_api.delete(workspace_slug=workspace_slug, property_id=property_id)
            except Exception as exc:
                raise RuntimeError(
                    f"WS3 preclean: failed to delete stale customer property "
                    f"{EVALUATION_CUSTOMER_PROPERTY_NAME!r} id={property_id}: {exc}"
                ) from exc


def seed(plane: PlaneClient, run_id: str, needs: set[str], ctx: dict[str, Any]) -> dict[str, Any]:
    """Create the eval project and declared fixture groups.

    Mutates the caller-provided `ctx` in place so project_id is visible to teardown
    even if a later fixture step raises (F5).
    """
    run_prefix = run_id[:8]
    project_name = f"EVAL {run_prefix}"
    workspace_slug = os.environ["EVAL_PLANE_WORKSPACE_SLUG"]

    # Defensive: drop WS3 workspace artifacts that would make a no-op agent pass.
    remove_stale_workspace_artifacts(plane, workspace_slug)

    # Reset known keys while preserving object identity for the caller.
    ctx.clear()
    ctx.update(
        {
            "run_id": run_id,
            "run8": run_prefix,
            "workspace_slug": workspace_slug,
            "project_id": None,
            "project_name": project_name,
            "project_identifier": None,  # filled after create (may retry suffix)
            "labels": {},
            "items": {},
            "item_identifiers": {},  # title -> PROJ-N for ID-in-hand prompts
            "item_ids": [],
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
        }
    )

    # EV + 4 hex chars; retry with a new suffix on soft-delete identifier collisions.
    project = create_project_with_identifier_retry(
        plane,
        workspace_slug,
        name=project_name,
        identifier_prefix="EV",
        initial_suffix=run_prefix[:4].upper(),
    )
    ctx["project_id"] = project.id
    ctx["project_identifier"] = getattr(project, "identifier", None)

    # Feature enablement (workspace first, then project).
    #
    # Ordering for S5 vs C1 on a shared eval workspace:
    # - Each task-rep has its own seed/teardown; there is no multi-task seed batch.
    # - Default tasks: enable workspace customers=True so C1 create_customer works.
    # - S5 (needs leave_cycles_worklogs_off): leave project cycles+worklogs AND
    #   workspace customers OFF so the agent must flip all three; teardown then
    #   re-enables customers=True so a later C1 rep is not left 403ing.
    # - We do not try to "run workspace enable after S5 check" — seed is per-task.
    feature_exclude: set[str] = set()
    workspace_feature_exclude: set[str] = set()
    if "leave_cycles_worklogs_off" in needs:
        feature_exclude = {"cycles", "worklogs"}
        workspace_feature_exclude = {"customers"}
        ctx["s5_left_customers_off"] = True
    ctx["feature_exclude"] = sorted(feature_exclude)
    ctx["ws_feature_exclude"] = sorted(workspace_feature_exclude)
    enable_workspace_features(plane, workspace_slug, exclude=workspace_feature_exclude)
    enable_project_features(plane, workspace_slug, project.id, exclude=feature_exclude)

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
