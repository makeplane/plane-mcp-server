"""Work item type fixtures for evaluation projects."""

from __future__ import annotations

from typing import Any

from plane import PlaneClient
from plane.models.work_item_types import CreateWorkItemType

from .projects import is_plan_gate


def seed_item_type(plane: PlaneClient, workspace_slug: str, context: dict[str, Any]) -> None:
    """Create or resolve a 'Bug' work item type.

    Genuine plan-gate responses set bug_type=None + skip reason; all other failures raise.
    Workspace feature probe uses the real key `is_work_item_types_enabled` (F10).
    """
    project_id = context["project_id"]
    target = "Bug"
    try:
        features = plane.workspaces.get_features(workspace_slug=workspace_slug)
        dump = features.model_dump() if hasattr(features, "model_dump") else {}
        # Real API key (extra='allow' on WorkspaceFeature); never trust the fictional work_item_types key alone.
        workspace_owns = bool(dump.get("is_work_item_types_enabled"))

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
            context["bug_type_skip_reason"] = f"bug_type plan-gated: {exc}"
            return
        raise
