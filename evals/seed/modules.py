"""Fixtures for the Plane Module object, not for Python modules."""

from __future__ import annotations

from typing import Any

from plane import PlaneClient
from plane.models.modules import CreateModule
from plane.models.work_items import CreateWorkItem, UpdateWorkItem

from .work_items import find_completed_state, list_states

MODULE_NAME = "Checkout revamp"
MODULE_COMPLETED_TITLES = (
    "Module done: cart totals",
    "Module done: tax lines",
    "Module done: shipping quote",
)


def seed_module(plane: PlaneClient, workspace_slug: str, context: dict[str, Any]) -> None:
    project_id = context["project_id"]
    states = list_states(plane, workspace_slug, project_id)
    done = find_completed_state(states)
    if done is None:
        raise RuntimeError("seed module: no completed-group state to place module items")

    module = plane.modules.create(
        workspace_slug=workspace_slug,
        project_id=project_id,
        data=CreateModule(name=MODULE_NAME, status="in-progress"),
    )
    context["module"] = {"id": module.id, "name": MODULE_NAME}
    completed_ids: list[str] = []
    for title in MODULE_COMPLETED_TITLES:
        item = plane.work_items.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=CreateWorkItem(name=title, priority="medium", state=str(done.id)),  # type: ignore[arg-type]
        )
        # Force completed state if create ignored it.
        current = getattr(item, "state", None)
        current_id = current if isinstance(current, str) else getattr(current, "id", None)
        if str(current_id) != str(done.id):
            item = plane.work_items.update(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=item.id,
                data=UpdateWorkItem(state=str(done.id)),
            )
        completed_ids.append(item.id)
        context["items"][title] = item.id
        context["item_ids"].append(item.id)
    plane.modules.add_work_items(
        workspace_slug=workspace_slug,
        project_id=project_id,
        module_id=module.id,
        issue_ids=completed_ids,
    )
    context["module_completed_ids"] = completed_ids
    context["module_completed_state_id"] = done.id
