"""Work item types, at project or workspace scope.

Plane runs types in one of two modes: owned by the workspace and imported into
projects, or created per project. `resolve` hides that split -- prefer it over
create when all you need is a usable type id.
"""

from __future__ import annotations

from typing import Literal

from fastmcp import FastMCP
from plane.models.projects import ProjectFeature
from plane.models.work_item_types import CreateWorkItemType, UpdateWorkItemType, WorkItemType

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.v2._runtime import coerce_list, missing, opt, page_params
from plane_mcp.tools.v2._scope import WORK_ITEM_TYPE
from plane_mcp.tools.v2._spec import Action, build_annotations, build_description

NAME = "work_item_type"
TITLE = "Work item types"

ACTIONS = (
    Action(
        "list", (), ("project_id", "cursor", "per_page"), note="workspace scope when project_id is omitted", read=True
    ),
    Action("retrieve", ("work_item_type_id",), ("project_id",), read=True),
    Action(
        "resolve",
        ("project_id", "name"),
        note="finds or creates a named type usable in the project; never duplicates",
    ),
    Action(
        "create",
        ("name",),
        ("project_id", "description", "project_ids", "is_active", "external_source", "external_id"),
    ),
    Action(
        "update",
        ("work_item_type_id",),
        ("project_id", "name", "description", "project_ids", "is_active", "external_source", "external_id"),
        note="only the fields you pass are changed",
    ),
    Action("delete", ("work_item_type_id",), ("project_id",), destructive=True),
    Action("import_to_project", ("project_id", "work_item_type_ids"), note="links workspace types to a project"),
)

FOOTER = (
    "Omit project_id to work at workspace scope. A type's id is the type_id for `work_item create` "
    "and the work_item_type_id for `work_item_property list`. "
    "Prefer resolve over create when you just need a usable type such as Epic or Initiative: it "
    "handles both modes, matches exactly (case-sensitive, whitespace-stripped) and never "
    "duplicates. Where the workspace owns the vocabulary, creating a type on a project is "
    "rejected and importing is the only valid path -- resolve does that for you."
)

LEGACY = {
    "list_work_item_types": "list",
    "retrieve_work_item_type": "retrieve",
    "resolve_work_item_type": "resolve",
    "create_work_item_type": "create",
    "update_work_item_type": "update",
    "delete_work_item_type": "delete",
    "import_work_item_types_to_project": "import_to_project",
}


def _named(types, target: str):
    return next((t for t in types if (t.name or "").strip() == target), None)


def _resolve(client, workspace_slug: str, project_id: str, name: str) -> WorkItemType:
    """Find or create `name` so it is usable in the project, whichever mode is on."""
    target = name.strip()
    features = client.workspaces.get_features(workspace_slug=workspace_slug)

    if features.model_dump().get("work_item_types"):
        # Workspace owns the vocabulary: find in project, else find or create at
        # workspace scope and import.
        in_project = _named(client.work_item_types.list(workspace_slug=workspace_slug, project_id=project_id), target)
        if in_project is not None:
            return in_project
        at_workspace = _named(client.workspace_work_item_types.list(workspace_slug=workspace_slug), target)
        if at_workspace is None:
            at_workspace = client.workspace_work_item_types.create(
                workspace_slug=workspace_slug, data=CreateWorkItemType(name=name)
            )
        client.work_item_types.import_to_project(
            workspace_slug=workspace_slug, project_id=project_id, work_item_type_ids=[at_workspace.id]
        )
        return at_workspace

    # Types are per-project: turn the feature on if needed, then find or create.
    project_features = client.projects.get_features(workspace_slug=workspace_slug, project_id=project_id)
    if not project_features.model_dump().get("work_item_types"):
        client.projects.update_features(
            workspace_slug=workspace_slug, project_id=project_id, data=ProjectFeature(work_item_types=True)
        )
    existing = _named(client.work_item_types.list(workspace_slug=workspace_slug, project_id=project_id), target)
    if existing is not None:
        return existing
    return client.work_item_types.create(
        workspace_slug=workspace_slug, project_id=project_id, data=CreateWorkItemType(name=name)
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("Work item types, at project or workspace scope.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def work_item_type(
        action: Literal["list", "retrieve", "resolve", "create", "update", "delete", "import_to_project"],
        project_id: str = "",
        work_item_type_id: str = "",
        work_item_type_ids: str = "",
        name: str = "",
        description: str = "",
        project_ids: str = "",
        is_active: bool | None = None,
        external_source: str = "",
        external_id: str = "",
        cursor: str = "",
        per_page: int = 0,
    ) -> WorkItemType | list[WorkItemType] | str | None:
        client, workspace_slug = get_plane_client_context()
        # project_id present -> the project's own types; absent -> the workspace's.
        scope = WORK_ITEM_TYPE.bind(client, project_id)

        if action == "list":
            # Only the project endpoint paginates; the workspace one returns all.
            page = {"params": page_params(cursor, per_page)} if not scope.workspace else {}
            return scope.namespace.list(workspace_slug=workspace_slug, **scope.scope_kwargs, **page)

        if action == "resolve":
            if not project_id or not name:
                return missing(action, "project_id", "name")
            return _resolve(client, workspace_slug, project_id, name)

        if action == "import_to_project":
            ids = coerce_list(work_item_type_ids)
            if not project_id or not ids:
                return missing(action, "project_id", "work_item_type_ids")
            client.work_item_types.import_to_project(
                workspace_slug=workspace_slug, project_id=project_id, work_item_type_ids=ids
            )
            return None

        if action == "create":
            if not name:
                return missing(action, "name")
            return scope.namespace.create(
                workspace_slug=workspace_slug,
                **scope.scope_kwargs,
                data=CreateWorkItemType(
                    name=name,
                    description=opt(description),
                    project_ids=coerce_list(project_ids),
                    is_active=is_active,
                    external_source=opt(external_source),
                    external_id=opt(external_id),
                ),
            )

        if not work_item_type_id:
            return missing(action, "work_item_type_id")
        if not scope.supports(action):
            return scope.unsupported(action)

        target = {**scope.scope_kwargs, **scope.ids(work_item_type_id)}

        if action == "retrieve":
            return scope.namespace.retrieve(workspace_slug=workspace_slug, **target)

        if action == "update":
            return scope.namespace.update(
                workspace_slug=workspace_slug,
                **target,
                data=UpdateWorkItemType(
                    name=opt(name),
                    description=opt(description),
                    project_ids=coerce_list(project_ids),
                    is_active=is_active,
                    external_source=opt(external_source),
                    external_id=opt(external_id),
                ),
            )

        scope.namespace.delete(workspace_slug=workspace_slug, **target)
        return None
