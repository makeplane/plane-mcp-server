"""Work item type fixtures for evaluation projects."""

from __future__ import annotations

from typing import Any

from plane import PlaneClient
from plane.models.work_item_types import CreateWorkItemType

from .projects import is_plan_gate

BUG_TYPE_NAME = "Bug"
INCIDENT_TYPE_NAME = "Incident"
SEVERITY_PROPERTY_NAME = "Severity"


def is_work_item_type_named(row: Any, name: str) -> bool:
    """Return whether a type row has the exact fixture name, ignoring case/space."""
    return (getattr(row, "name", None) or "").strip().casefold() == name.casefold()


def is_severity_property(row: Any) -> bool:
    """Return whether a property row is the S1 Severity fixture."""
    display = getattr(row, "display_name", None) or getattr(row, "name", None) or ""
    return display.strip().casefold() == SEVERITY_PROPERTY_NAME.casefold()


def list_workspace_work_item_types(plane: PlaneClient, workspace_slug: str) -> list[Any]:
    """List workspace-owned work-item types using the SDK's non-paginated surface."""
    result = plane.workspace_work_item_types.list(workspace_slug=workspace_slug)
    return list((result.results if hasattr(result, "results") else result) or [])


def workspace_owns_work_item_types(plane: PlaneClient, workspace_slug: str) -> bool:
    """Return the authoritative workspace-vs-project ownership mode for types."""
    features = plane.workspaces.get_features(workspace_slug=workspace_slug)
    dump = features.model_dump() if hasattr(features, "model_dump") else {}
    return bool(dump.get("is_work_item_types_enabled"))


def list_workspace_properties_for_type(
    plane: PlaneClient,
    workspace_slug: str,
    type_name: str,
) -> list[Any]:
    """Resolve full workspace property rows linked to every type named ``type_name``."""
    item_types = list_workspace_work_item_types(plane, workspace_slug)
    target_types = [row for row in item_types if is_work_item_type_named(row, type_name)]
    if not target_types:
        return []

    linked_ids: set[str] = set()
    for item_type in target_types:
        linked = plane.workspace_work_item_types.properties.list(
            workspace_slug=workspace_slug,
            type_id=item_type.id,
        )
        for value in linked or []:
            object_id = getattr(value, "id", None) or value
            linked_ids.add(str(object_id))

    properties = plane.workspace_work_item_properties.list(workspace_slug=workspace_slug)
    rows = list((properties.results if hasattr(properties, "results") else properties) or [])
    return [row for row in rows if getattr(row, "id", None) is not None and str(row.id) in linked_ids]


def seed_item_type(plane: PlaneClient, workspace_slug: str, context: dict[str, Any]) -> None:
    """Create or resolve a 'Bug' work item type.

    Genuine plan-gate responses set bug_type=None + skip reason; all other failures raise.
    Workspace feature probe uses the real key `is_work_item_types_enabled` (F10).
    """
    project_id = context["project_id"]
    target = BUG_TYPE_NAME
    try:
        # Real API key (extra='allow' on WorkspaceFeature); never trust the fictional
        # work_item_types key alone.
        workspace_owns = workspace_owns_work_item_types(plane, workspace_slug)

        if workspace_owns:
            existing = next(
                (
                    item_type
                    for item_type in plane.workspace_work_item_types.list(workspace_slug=workspace_slug)
                    if (item_type.name or "").strip() == target
                ),
                None,
            )
            created = False
            if existing is None:
                existing = plane.workspace_work_item_types.create(
                    workspace_slug=workspace_slug, data=CreateWorkItemType(name=target)
                )
                created = True
            plane.work_item_types.import_to_project(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_type_ids=[existing.id],
            )
            context["bug_type"] = {"id": existing.id, "name": target}
            context["bug_type_created"] = created
            context["bug_type_workspace_level"] = True
            if created:
                context["workspace_objects"].append({"kind": "work_item_type", "id": existing.id})
            return

        # Per-project types. Project features expose no work-item-type toggle — do not PATCH.
        existing = next(
            (
                item_type
                for item_type in plane.work_item_types.list(workspace_slug=workspace_slug, project_id=project_id)
                if (item_type.name or "").strip() == target
            ),
            None,
        )
        created = False
        if existing is None:
            existing = plane.work_item_types.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                data=CreateWorkItemType(name=target),
            )
            created = True
        context["bug_type"] = {"id": existing.id, "name": target}
        context["bug_type_created"] = created
        context["bug_type_workspace_level"] = False
    except Exception as exc:
        if is_plan_gate(exc):
            context["bug_type"] = None
            context["bug_type_skip_reason"] = "env:plan-gated:work-item-types"
            return
        raise
