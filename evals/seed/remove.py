"""Fixture removal for evaluation runs."""

from __future__ import annotations

import os
from typing import Any

from plane import PlaneClient
from plane.errors.errors import HttpError
from plane.models.workspaces import WorkspaceFeature

from .customers import CUSTOMER_NAME, EVALUATION_CUSTOMER_PROPERTY_NAME
from .releases import EVALUATION_RELEASE_TAG_VERSION


def _remove_severity_property(plane: PlaneClient, context: dict[str, Any]) -> None:
    """Delete Severity properties attached to the seeded Bug type (avoids multi-rep pollution)."""
    bug = context.get("bug_type")
    if not bug:
        return
    bug_type_id = bug.get("id") if isinstance(bug, dict) else bug
    if not bug_type_id:
        return
    workspace_slug = context.get("workspace_slug") or ""
    project_id = context.get("project_id")

    properties: list[Any] = []
    try:
        if project_id:
            properties = list(
                plane.work_item_properties.list(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    type_id=str(bug_type_id),
                )
                or []
            )
    except HttpError as exc:
        if exc.status_code not in (404, 405):
            print(f"teardown warning: list Severity props failed: {exc}")
            return
    except Exception as exc:
        print(f"teardown warning: list Severity props failed: {exc}")
        return

    for work_item_property in properties:
        display = (
            getattr(work_item_property, "display_name", None) or getattr(work_item_property, "name", None) or ""
        ).strip()
        if display.lower() != "severity":
            continue
        try:
            if project_id:
                plane.work_item_properties.delete(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    type_id=str(bug_type_id),
                    work_item_property_id=work_item_property.id,
                )
            context.setdefault("workspace_objects", [])  # no-op anchor
        except Exception as exc:
            print(f"teardown warning: failed to delete Severity property {work_item_property.id}: {exc}")


def _remove_incident_type(plane: PlaneClient, context: dict[str, Any]) -> None:
    """Best-effort cleanup of agent-created Incident type (S3 multi-rep pollution)."""
    workspace_slug = context.get("workspace_slug") or ""
    project_id = context.get("project_id")
    try:
        if context.get("bug_type_workspace_level"):
            for item_type in plane.workspace_work_item_types.list(workspace_slug=workspace_slug) or []:
                if (item_type.name or "").strip().casefold() == "incident":
                    plane.workspace_work_item_types.delete(workspace_slug=workspace_slug, type_id=item_type.id)
        elif project_id:
            for item_type in plane.work_item_types.list(workspace_slug=workspace_slug, project_id=project_id) or []:
                if (item_type.name or "").strip().casefold() == "incident":
                    plane.work_item_types.delete(
                        workspace_slug=workspace_slug,
                        project_id=project_id,
                        work_item_type_id=item_type.id,
                    )
    except Exception as exc:
        print(f"teardown warning: Incident type cleanup failed: {exc}")


def teardown(plane: PlaneClient, ctx: dict[str, Any]) -> None:
    """Delete the project and any workspace-scoped objects we created."""
    if not ctx:
        return
    workspace_slug = ctx.get("workspace_slug") or os.environ.get("EVAL_PLANE_WORKSPACE_SLUG", "")
    project_id = ctx.get("project_id")

    # S5 left customers off (or agent enabled them): re-enable for subsequent task-reps
    # on the shared eval workspace. We always set customers=True — we do not restore a
    # prior false (S5's job is to leave the workspace usable for C1).
    if ctx.get("s5_left_customers_off"):
        try:
            plane.workspaces.update_features(
                workspace_slug=workspace_slug,
                data=WorkspaceFeature(customers=True),
            )
        except Exception as exc:
            print(f"teardown warning: re-enable workspace customers failed: {exc}")

    # Drop agent-created Severity on Bug before project/type teardown (F8 multi-rep pollution).
    try:
        _remove_severity_property(plane, ctx)
    except Exception as exc:
        print(f"teardown warning: Severity cleanup failed: {exc}")
    try:
        _remove_incident_type(plane, ctx)
    except Exception as exc:
        print(f"teardown warning: Incident cleanup failed: {exc}")

    # Best-effort: agent-created Acme Corp customers (C1) that never hit workspace_objects.
    try:
        page = plane.customers.list(workspace_slug=workspace_slug)
        rows = page.results if hasattr(page, "results") else page
        for customer in rows or []:
            if (customer.name or "").strip().casefold() in (CUSTOMER_NAME.casefold(), "acme"):
                # Only delete if we seeded or created during this run (tracked or name match + run).
                tracked = {
                    obj.get("id") for obj in (ctx.get("workspace_objects") or []) if obj.get("kind") == "customer"
                }
                if str(customer.id) in tracked or ctx.get("customer") is None:
                    # Avoid deleting long-lived Acme if we pre-seeded and tracked it — still delete tracked.
                    if str(customer.id) in tracked or not ctx.get("customer"):
                        ctx.setdefault("workspace_objects", []).append({"kind": "customer", "id": customer.id})
    except Exception as exc:
        print(f"teardown warning: customer scan failed: {exc}")

    # Workspace-scoped cleanup first (survive project deletion).
    seen_workspace_objects: set[str] = set()
    for obj in ctx.get("workspace_objects") or []:
        kind = obj.get("kind")
        object_id = obj.get("id")
        if not object_id:
            continue
        key = f"{kind}:{object_id}"
        if key in seen_workspace_objects:
            continue
        seen_workspace_objects.add(key)
        try:
            if kind == "work_item_type":
                plane.workspace_work_item_types.delete(workspace_slug=workspace_slug, type_id=object_id)
            elif kind == "work_item_property":
                plane.workspace_work_item_properties.delete(workspace_slug=workspace_slug, property_id=object_id)
            elif kind == "customer":
                plane.customers.delete(workspace_slug=workspace_slug, customer_id=object_id)
            elif kind == "release":
                plane.releases.delete(workspace_slug=workspace_slug, release_id=object_id)
            elif kind == "release_tag":
                plane.releases.tags.delete(workspace_slug=workspace_slug, tag_id=object_id)
            elif kind == "customer_property":
                plane.customers.properties.delete(workspace_slug=workspace_slug, property_id=object_id)
        except Exception as exc:
            print(f"teardown warning: failed to delete workspace {kind} {object_id}: {exc}")

    # Sweep by well-known WS3 names in case tracking missed an agent-created row.
    try:
        page = plane.releases.tags.list(workspace_slug=workspace_slug)
        rows = page.results if hasattr(page, "results") else page
        for tag in rows or []:
            if (getattr(tag, "version", None) or "").strip() == EVALUATION_RELEASE_TAG_VERSION:
                tag_id = getattr(tag, "id", None)
                if tag_id and f"release_tag:{tag_id}" not in seen_workspace_objects:
                    try:
                        plane.releases.tags.delete(workspace_slug=workspace_slug, tag_id=tag_id)
                    except Exception as exc:
                        print(f"teardown warning: sweep release tag {tag_id}: {exc}")
    except Exception as exc:
        print(f"teardown warning: sweep release tags failed: {exc}")
    try:
        page = plane.customers.properties.list(workspace_slug=workspace_slug)
        rows = page.results if hasattr(page, "results") else page
        target = EVALUATION_CUSTOMER_PROPERTY_NAME.casefold()
        for customer_property in rows or []:
            display = (
                getattr(customer_property, "display_name", None) or getattr(customer_property, "name", None) or ""
            ).strip()
            if display.casefold() == target:
                property_id = getattr(customer_property, "id", None)
                if property_id and f"customer_property:{property_id}" not in seen_workspace_objects:
                    try:
                        plane.customers.properties.delete(
                            workspace_slug=workspace_slug,
                            property_id=property_id,
                        )
                    except Exception as exc:
                        print(f"teardown warning: sweep customer property {property_id}: {exc}")
    except Exception as exc:
        print(f"teardown warning: sweep customer properties failed: {exc}")

    # Second project before main (no dependency either way, but be thorough).
    for second_project_id in ctx.get("second_project_ids") or []:
        if not second_project_id or second_project_id == project_id:
            continue
        try:
            plane.projects.delete(workspace_slug=workspace_slug, project_id=second_project_id)
        except Exception as exc:
            print(f"teardown warning: failed to delete second project {second_project_id}: {exc}")

    if project_id:
        try:
            plane.projects.delete(workspace_slug=workspace_slug, project_id=project_id)
        except Exception as exc:
            name = ctx.get("project_name", project_id)
            print(f"teardown warning: failed to delete project {name!r}: {exc}")
            print(f"orphaned project: {name}")
