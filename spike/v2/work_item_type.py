"""Consolidated `work_item_type` tool.

Collapses work_item_types.py (7 tools) into one action-dispatch tool.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from plane.models.projects import ProjectFeature
from plane.models.work_item_types import (
    CreateWorkItemType,
    UpdateWorkItemType,
    WorkItemType,
)

from plane_mcp.client import get_plane_client_context
from spike.v2._common import bad_action, json_out, missing, opt

ACTIONS = ["list", "retrieve", "create", "update", "delete", "resolve", "import_to_project"]

DOC = """Manage work item types (e.g. "Epic", "Bug"). Actions:
list (optional project_id, params) - omit project_id for workspace-level types;
retrieve (work_item_type_id; optional project_id);
create (name; optional project_id, description, project_ids, is_active, external_source, external_id);
update (work_item_type_id; optional project_id, name, description, project_ids, is_active, external_source, external_id);
delete (work_item_type_id; optional project_id);
resolve (project_id, name);
import_to_project (project_id, work_item_type_ids).

Omit project_id on retrieve/create/update/delete for workspace scope.

Prefer resolve for the common case: it finds a type by exact name (case-sensitive,
whitespace-stripped) for a project, creates it if missing, and guarantees it is usable
in that project - handling both workspace-owned and project-owned work item type modes,
enabling the project's work_item_types feature when needed. It never duplicates a type.
The returned `id` is the `type_id` for creating a typed work item.

import_to_project bulk-links existing workspace-level types into a project.

A type's `id` is the `work_item_type_id` used by the work_item_property tool to look up
custom property and option UUIDs for PQL cf[] filters."""


def _dispatch(
    action: str,
    project_id: str,
    work_item_type_id: str,
    name: str,
    description: str,
    project_ids: list[str] | None,
    work_item_type_ids: list[str] | None,
    is_active: bool | None,
    external_source: str,
    external_id: str,
    params: dict[str, Any] | None,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        if project_id:
            return client.work_item_types.list(
                workspace_slug=workspace_slug, project_id=project_id, params=params
            )
        return client.workspace_work_item_types.list(workspace_slug=workspace_slug)

    if action == "create":
        if not name:
            return missing(action, "name")
        data = CreateWorkItemType(
            name=name,
            description=opt(description),
            project_ids=project_ids,
            is_active=is_active,
            external_source=opt(external_source),
            external_id=opt(external_id),
        )
        if project_id:
            return client.work_item_types.create(
                workspace_slug=workspace_slug, project_id=project_id, data=data
            )
        return client.workspace_work_item_types.create(workspace_slug=workspace_slug, data=data)

    if action == "import_to_project":
        if not project_id or not work_item_type_ids:
            return missing(action, "project_id", "work_item_type_ids")
        client.work_item_types.import_to_project(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_type_ids=work_item_type_ids,
        )
        return None

    if action == "resolve":
        if not project_id or not name:
            return missing(action, "project_id", "name")
        return _resolve(client, workspace_slug, project_id, name)

    if not work_item_type_id:
        return missing(action, "work_item_type_id")

    if action == "retrieve":
        if project_id:
            return client.work_item_types.retrieve(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_type_id=work_item_type_id,
            )
        return client.workspace_work_item_types.retrieve(
            workspace_slug=workspace_slug,
            type_id=work_item_type_id,
        )

    if action == "delete":
        if project_id:
            client.work_item_types.delete(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_type_id=work_item_type_id,
            )
        else:
            client.workspace_work_item_types.delete(
                workspace_slug=workspace_slug,
                type_id=work_item_type_id,
            )
        return None

    data = UpdateWorkItemType(
        name=opt(name),
        description=opt(description),
        project_ids=project_ids,
        is_active=is_active,
        external_source=opt(external_source),
        external_id=opt(external_id),
    )
    if project_id:
        return client.work_item_types.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_type_id=work_item_type_id,
            data=data,
        )
    return client.workspace_work_item_types.update(
        workspace_slug=workspace_slug,
        type_id=work_item_type_id,
        data=data,
    )


def _resolve(client, workspace_slug: str, project_id: str, name: str):
    """Find-or-create a work item type and guarantee it is usable in the project."""
    target = name.strip()

    workspace_features = client.workspaces.get_features(workspace_slug=workspace_slug)
    workspace_owns_types = bool(workspace_features.model_dump().get("work_item_types"))

    if workspace_owns_types:
        in_project = next(
            (
                t
                for t in client.work_item_types.list(
                    workspace_slug=workspace_slug, project_id=project_id
                )
                if (t.name or "").strip() == target
            ),
            None,
        )
        if in_project is not None:
            return in_project
        at_workspace = next(
            (
                t
                for t in client.workspace_work_item_types.list(workspace_slug=workspace_slug)
                if (t.name or "").strip() == target
            ),
            None,
        )
        if at_workspace is None:
            at_workspace = client.workspace_work_item_types.create(
                workspace_slug=workspace_slug, data=CreateWorkItemType(name=name)
            )
        client.work_item_types.import_to_project(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_type_ids=[at_workspace.id],
        )
        return at_workspace

    # Mode B -- types are per-project; enable the feature if needed, then find or create.
    project_features = client.projects.get_features(
        workspace_slug=workspace_slug, project_id=project_id
    )
    if not project_features.model_dump().get("work_item_types"):
        client.projects.update_features(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=ProjectFeature(work_item_types=True),
        )

    existing = next(
        (
            t
            for t in client.work_item_types.list(
                workspace_slug=workspace_slug, project_id=project_id
            )
            if (t.name or "").strip() == target
        ),
        None,
    )
    if existing is None:
        existing = client.work_item_types.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=CreateWorkItemType(name=name),
        )
    return existing


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="work_item_type", description=DOC)
    def _work_item_type(
        action: str,
        project_id: str = "",
        work_item_type_id: str = "",
        name: str = "",
        description: str = "",
        project_ids: list[str] | None = None,
        work_item_type_ids: list[str] | None = None,
        is_active: bool | None = None,
        external_source: str = "",
        external_id: str = "",
        params: dict[str, Any] | None = None,
    ) -> WorkItemType | list[WorkItemType] | str | None:
        return _dispatch(
            action, project_id, work_item_type_id, name, description, project_ids,
            work_item_type_ids, is_active, external_source, external_id, params,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="work_item_type", description=DOC)
    def _work_item_type(
        action: str,
        project_id: str = "",
        work_item_type_id: str = "",
        name: str = "",
        description: str = "",
        project_ids: list[str] | None = None,
        work_item_type_ids: list[str] | None = None,
        is_active: bool | None = None,
        external_source: str = "",
        external_id: str = "",
        params: dict[str, Any] | None = None,
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, project_id, work_item_type_id, name, description, project_ids,
                    work_item_type_ids, is_active, external_source, external_id, params,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
