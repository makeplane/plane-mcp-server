"""Fixture removal for evaluation runs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from plane import PlaneClient
from plane.errors.errors import HttpError
from plane.models.workspaces import WorkspaceFeature

from .customers import CUSTOMER_NAME, EVALUATION_CUSTOMER_PROPERTY_NAME, is_evaluation_customer_name
from .item_types import (
    BUG_TYPE_NAME,
    INCIDENT_TYPE_NAME,
    SEVERITY_PROPERTY_NAME,
    is_severity_property,
    is_work_item_type_named,
    list_workspace_properties_for_type,
    list_workspace_work_item_types,
    workspace_owns_work_item_types,
)
from .releases import EVALUATION_RELEASE_TAG_VERSION
from .workspace import list_workspace_rows


@dataclass(frozen=True, slots=True)
class CleanupFailure:
    """One cleanup operation that failed after teardown attempted it."""

    operation: str
    target: str
    error_type: str
    message: str

    def __str__(self) -> str:
        return f"{self.operation} {self.target}: {self.error_type}: {self.message}"


class TeardownError(RuntimeError):
    """All cleanup failures from one teardown, raised after every target was attempted."""

    def __init__(self, failures: list[CleanupFailure]):
        self.failures = tuple(failures)
        details = "; ".join(str(failure) for failure in self.failures)
        super().__init__(f"{len(self.failures)} cleanup operation(s) failed: {details}")


def _record_failure(
    failures: list[CleanupFailure],
    *,
    operation: str,
    target: Any,
    exc: BaseException,
) -> None:
    failures.append(
        CleanupFailure(
            operation=operation,
            target=str(target),
            error_type=type(exc).__name__,
            message=str(exc),
        )
    )


def _baseline_ids(ctx: dict[str, Any], category: str) -> set[str] | None:
    baseline = ctx.get("workspace_baseline")
    if not isinstance(baseline, dict) or baseline.get(category) is None:
        return None
    return {str(object_id) for object_id in baseline[category]}


def _warn_unavailable_baseline(
    category: str,
    fixture_name: str,
    object_ids: list[str],
    failures: list[CleanupFailure],
) -> None:
    if object_ids:
        joined_ids = ", ".join(sorted(object_ids))
        print(
            f"teardown warning: {category} baseline unavailable; leaving name-matched {fixture_name!r} ids={joined_ids}"
        )
        _record_failure(
            failures,
            operation="preserve name-matched fixture without baseline",
            target=f"{category} {fixture_name!r} ids={joined_ids}",
            exc=RuntimeError("workspace baseline unavailable; ownership cannot be determined safely"),
        )


def _remove_severity_property(
    plane: PlaneClient,
    context: dict[str, Any],
    failures: list[CleanupFailure],
) -> None:
    """Delete only run-owned Severity properties attached to the seeded Bug type."""
    bug = context.get("bug_type")
    if not bug:
        return
    bug_type_id = bug.get("id") if isinstance(bug, dict) else bug
    if not bug_type_id:
        return
    workspace_slug = context.get("workspace_slug") or ""
    project_id = context.get("project_id")

    # Workspace properties can appear through the project/type endpoint. Resolve both
    # scopes and let the workspace scope win for deletion when an ID appears in both.
    properties: dict[str, tuple[Any, str]] = {}
    try:
        if project_id:
            project_properties = list(
                plane.work_item_properties.list(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    type_id=str(bug_type_id),
                )
                or []
            )
            for row in project_properties:
                if getattr(row, "id", None) is not None:
                    properties[str(row.id)] = (row, "project")
    except HttpError as exc:
        if exc.status_code not in (404, 405):
            _record_failure(failures, operation="list", target="Severity properties", exc=exc)
    except Exception as exc:
        _record_failure(failures, operation="list", target="Severity properties", exc=exc)

    if context.get("bug_type_workspace_level"):
        try:
            for row in list_workspace_properties_for_type(plane, workspace_slug, BUG_TYPE_NAME):
                if getattr(row, "id", None) is not None:
                    properties[str(row.id)] = (row, "workspace")
        except Exception as exc:
            _record_failure(failures, operation="list", target="workspace Severity properties", exc=exc)

    baseline = _baseline_ids(context, "work_item_properties")
    tracked = {
        str(obj.get("id"))
        for obj in (context.get("workspace_objects") or [])
        if obj.get("kind") == "work_item_property" and obj.get("id") is not None
    }
    skipped_ids: list[str] = []
    for property_id, (work_item_property, scope) in properties.items():
        if not is_severity_property(work_item_property):
            continue
        if property_id not in tracked:
            if baseline is None:
                skipped_ids.append(property_id)
                continue
            if property_id in baseline:
                continue
        try:
            if scope == "workspace":
                plane.workspace_work_item_properties.delete(
                    workspace_slug=workspace_slug,
                    property_id=property_id,
                )
            elif project_id:
                plane.work_item_properties.delete(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    type_id=str(bug_type_id),
                    work_item_property_id=property_id,
                )
        except Exception as exc:
            _record_failure(
                failures,
                operation="delete Severity property",
                target=property_id,
                exc=exc,
            )
    _warn_unavailable_baseline(
        "work item properties",
        SEVERITY_PROPERTY_NAME,
        skipped_ids,
        failures,
    )


def _remove_incident_type(
    plane: PlaneClient,
    context: dict[str, Any],
    failures: list[CleanupFailure],
) -> None:
    """Clean up S3 Incident types while preserving seed-time workspace ownership."""
    if context.get("task_id") != "S3":
        return
    workspace_slug = context.get("workspace_slug") or ""
    project_id = context.get("project_id")
    try:
        workspace_owns = workspace_owns_work_item_types(plane, workspace_slug)
    except Exception as exc:
        _record_failure(failures, operation="detect ownership", target="Incident work item types", exc=exc)
        return

    try:
        if workspace_owns:
            item_types = list_workspace_work_item_types(plane, workspace_slug)
            scope = "workspace"
        elif project_id:
            item_types = list(plane.work_item_types.list(workspace_slug=workspace_slug, project_id=project_id) or [])
            scope = "project"
        else:
            return
    except Exception as exc:
        _record_failure(failures, operation="list", target="Incident work item types", exc=exc)
        return

    for item_type in item_types:
        if not is_work_item_type_named(item_type, INCIDENT_TYPE_NAME):
            continue
        item_type_id = str(item_type.id)
        if scope == "workspace":
            baseline = _baseline_ids(context, "work_item_types")
            tracked = {
                str(obj.get("id"))
                for obj in (context.get("workspace_objects") or [])
                if obj.get("kind") == "work_item_type" and obj.get("id") is not None
            }
            if item_type_id not in tracked:
                if baseline is None:
                    _warn_unavailable_baseline(
                        "work item types",
                        INCIDENT_TYPE_NAME,
                        [item_type_id],
                        failures,
                    )
                    continue
                if item_type_id in baseline:
                    continue
        try:
            if scope == "workspace":
                plane.workspace_work_item_types.delete(workspace_slug=workspace_slug, type_id=item_type_id)
            else:
                assert project_id is not None
                plane.work_item_types.delete(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    work_item_type_id=item_type_id,
                )
        except Exception as exc:
            _record_failure(
                failures,
                operation="delete Incident work item type",
                target=item_type_id,
                exc=exc,
            )


def teardown(plane: PlaneClient, ctx: dict[str, Any]) -> None:
    """Attempt every fixture deletion, then raise all failures as one structured error."""
    if not ctx:
        return
    failures: list[CleanupFailure] = []
    workspace_slug = ctx.get("workspace_slug") or os.environ.get("EVAL_PLANE_WORKSPACE_SLUG", "")
    project_id = ctx.get("project_id")

    # Put workspace toggles back where the run found them. Seeding writes them explicitly
    # (a task may need one off), and the workspace outlives the run, so leaving this run's
    # requirements behind is drift on an instance the harness does not own.
    prior = ctx.get("workspace_features_prior") or {}
    prior_customers = prior.get("customers")
    if prior_customers is not None:
        try:
            plane.workspaces.update_features(
                workspace_slug=workspace_slug,
                data=WorkspaceFeature(customers=bool(prior_customers)),
            )
        except Exception as exc:
            _record_failure(
                failures,
                operation="restore workspace customers feature",
                target=prior_customers,
                exc=exc,
            )

    # Drop agent-created Severity on Bug before project/type teardown (F8 multi-rep pollution).
    try:
        _remove_severity_property(plane, ctx, failures)
    except Exception as exc:
        _record_failure(failures, operation="clean up", target="Severity properties", exc=exc)
    try:
        _remove_incident_type(plane, ctx, failures)
    except Exception as exc:
        _record_failure(failures, operation="clean up", target="Incident work item types", exc=exc)

    # Best-effort: agent-created Acme Corp customers (C1) that never hit workspace_objects.
    try:
        rows = list_workspace_rows(plane.customers, workspace_slug)
        tracked = {
            str(obj.get("id"))
            for obj in (ctx.get("workspace_objects") or [])
            if obj.get("kind") == "customer" and obj.get("id") is not None
        }
        baseline = _baseline_ids(ctx, "customers")
        skipped_ids: list[str] = []
        for customer in rows or []:
            if not is_evaluation_customer_name(getattr(customer, "name", None)):
                continue
            customer_id = getattr(customer, "id", None)
            if customer_id is None or str(customer_id) in tracked:
                continue
            if baseline is None:
                skipped_ids.append(str(customer_id))
            elif str(customer_id) not in baseline:
                ctx.setdefault("workspace_objects", []).append({"kind": "customer", "id": customer_id})
        _warn_unavailable_baseline("customers", CUSTOMER_NAME, skipped_ids, failures)
    except Exception as exc:
        _record_failure(failures, operation="scan", target="evaluation customers", exc=exc)

    # Workspace-scoped cleanup first (survive project deletion).
    seen_workspace_objects: set[str] = set()
    for obj in ctx.get("workspace_objects") or []:
        try:
            kind = obj.get("kind")
            object_id = obj.get("id")
        except Exception as exc:
            _record_failure(failures, operation="read tracked workspace object", target=repr(obj), exc=exc)
            continue
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
            else:
                raise ValueError(f"unsupported workspace object kind {kind!r}")
        except Exception as exc:
            _record_failure(
                failures,
                operation=f"delete workspace {kind}",
                target=object_id,
                exc=exc,
            )

    # Sweep by well-known WS3 names in case tracking missed an agent-created row.
    try:
        rows = list_workspace_rows(plane.releases.tags, workspace_slug)
        baseline = _baseline_ids(ctx, "release_tags")
        skipped_ids: list[str] = []
        for tag in rows or []:
            if (getattr(tag, "version", None) or "").strip() == EVALUATION_RELEASE_TAG_VERSION:
                tag_id = getattr(tag, "id", None)
                if tag_id and f"release_tag:{tag_id}" not in seen_workspace_objects:
                    if baseline is None:
                        skipped_ids.append(str(tag_id))
                    elif str(tag_id) not in baseline:
                        try:
                            plane.releases.tags.delete(workspace_slug=workspace_slug, tag_id=tag_id)
                        except Exception as exc:
                            _record_failure(
                                failures,
                                operation="sweep release tag",
                                target=tag_id,
                                exc=exc,
                            )
        _warn_unavailable_baseline("release tags", EVALUATION_RELEASE_TAG_VERSION, skipped_ids, failures)
    except Exception as exc:
        _record_failure(failures, operation="scan", target="evaluation release tags", exc=exc)
    try:
        rows = list_workspace_rows(plane.customers.properties, workspace_slug)
        baseline = _baseline_ids(ctx, "customer_properties")
        skipped_ids: list[str] = []
        target = EVALUATION_CUSTOMER_PROPERTY_NAME.casefold()
        for customer_property in rows or []:
            display = (
                getattr(customer_property, "display_name", None) or getattr(customer_property, "name", None) or ""
            ).strip()
            if display.casefold() == target:
                property_id = getattr(customer_property, "id", None)
                if property_id and f"customer_property:{property_id}" not in seen_workspace_objects:
                    if baseline is None:
                        skipped_ids.append(str(property_id))
                    elif str(property_id) not in baseline:
                        try:
                            plane.customers.properties.delete(
                                workspace_slug=workspace_slug,
                                property_id=property_id,
                            )
                        except Exception as exc:
                            _record_failure(
                                failures,
                                operation="sweep customer property",
                                target=property_id,
                                exc=exc,
                            )
        _warn_unavailable_baseline(
            "customer properties",
            EVALUATION_CUSTOMER_PROPERTY_NAME,
            skipped_ids,
            failures,
        )
    except Exception as exc:
        _record_failure(failures, operation="scan", target="evaluation customer properties", exc=exc)

    # Second project before main (no dependency either way, but be thorough).
    second_project_ids = [ctx.get("second_project_id"), *(ctx.get("second_project_ids") or [])]
    for second_project_id in dict.fromkeys(second_project_ids):
        if not second_project_id or second_project_id == project_id:
            continue
        try:
            plane.projects.delete(workspace_slug=workspace_slug, project_id=second_project_id)
        except Exception as exc:
            _record_failure(failures, operation="delete second project", target=second_project_id, exc=exc)

    if project_id:
        try:
            plane.projects.delete(workspace_slug=workspace_slug, project_id=project_id)
        except Exception as exc:
            name = ctx.get("project_name", project_id)
            print(f"orphaned project: {name}")
            _record_failure(failures, operation="delete project", target=name, exc=exc)

    if failures:
        raise TeardownError(failures)
