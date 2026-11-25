"""Relations between work items, and the workspace definitions that type them.

Two systems behind one tool: built-in dependencies (six fixed directional types)
and custom relations (workspace-defined, each with an outward and inward label).
`create` routes between them by which arguments are supplied.

Self-hosted Community Edition has neither. It serves one route, GET and POST on
`/work-items/{id}/relations/`, with eight fixed types and no removal, so with
PLANE_COMMUNITY_EDITION=true `list` and `create` use that route and every other
action is refused by name instead of answering a bare 404.
"""

from __future__ import annotations

import os
from typing import Any, Literal, get_args

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from plane.models.enums import WorkItemRelationTypeEnum
from plane.models.work_item_relation_definitions import (
    CreateWorkItemRelationDefinition,
    PaginatedWorkItemRelationDefinitionResponse,
    UpdateWorkItemRelationDefinition,
    WorkItemRelationDefinition,
)
from plane.models.work_items import (
    CreateWorkItemCustomRelation,
    CreateWorkItemDependency,
    CreateWorkItemRelation,
    DependencyTypeEnum,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import (
    Action,
    build_annotations,
    build_description,
    coerce_list,
    missing,
    needs,
    one_of,
    opt,
)

NAME = "workitem_relation"
TITLE = "Work item relations"

DEPENDENCY_TYPES: tuple[str, ...] = get_args(DependencyTypeEnum)
# What Community Edition's /relations/ route accepts.
RELATION_TYPES: tuple[str, ...] = get_args(WorkItemRelationTypeEnum)

_OTHER_RELATIONS = (
    "For any other relationship pass relation_definition_id and "
    "relation_definition_label from the list_definitions action."
)

ACTIONS = (
    Action("list", ("project_id", "workitem_id"), read=True),
    Action(
        "create",
        ("project_id", "workitem_id", "workitem_ids"),
        ("relation_type", "relation_definition_id", "relation_definition_label"),
        note="pass relation_type for a dependency, or definition id + label for a custom relation",
    ),
    Action(
        "delete",
        ("project_id", "workitem_id", "related_workitem_id"),
        ("is_dependency",),
        note="removes one relation; dependencies and custom relations are independent, so "
        "is_dependency must match the kind that was created (default false)",
        destructive=True,
    ),
    Action("list_definitions", optional=("is_default", "is_active"), read=True),
    Action("create_definition", ("name",), ("outward", "inward", "is_active", "color")),
    Action("update_definition", ("definition_id",), ("name", "outward", "inward", "is_active", "color")),
    Action("delete_definition", ("definition_id",), destructive=True),
)

FOOTER = (
    "Call list_definitions first and match the user's wording to an entry. A "
    f"built_in_dependencies value ({', '.join(DEPENDENCY_TYPES)}) goes in relation_type; a "
    "custom definition needs its id in relation_definition_id and the matched outward or "
    "inward label in relation_definition_label, which sets direction."
)

LEGACY = {
    "list_work_item_relations": "list",
    "create_work_item_relation": "create",
    "remove_work_item_relation": "delete",
    "list_work_item_relation_definitions": "list_definitions",
    "create_work_item_relation_definition": "create_definition",
    "update_work_item_relation_definition": "update_definition",
    "delete_work_item_relation_definition": "delete_definition",
}


def _all_definitions(client, workspace_slug: str, is_default, is_active) -> list[WorkItemRelationDefinition]:
    """Definitions are a small set an agent must see whole, so page through them."""
    results: list[WorkItemRelationDefinition] = []
    cursor: str | None = None
    while True:
        page: PaginatedWorkItemRelationDefinitionResponse = client.work_item_relation_definitions.list(
            workspace_slug=workspace_slug,
            is_default=is_default,
            is_active=is_active,
            per_page=100,
            cursor=cursor,
        )
        results.extend(page.results)
        cursor = page.next_cursor
        if not page.next_page_results or not cursor:
            return results


def _community_edition() -> bool:
    return os.getenv("PLANE_COMMUNITY_EDITION", "false").lower() == "true"


def _not_on_community_edition(what: str) -> ToolError:
    return ToolError(
        f"Plane Community Edition has no endpoint for {what}. It serves only the list and create "
        f"actions, with relation_type one of: {', '.join(RELATION_TYPES)}. "
        "Remove a relation, or manage relation definitions, in the Plane web app."
    )


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description(
            "Relations between work items, and the definitions that type them.", ACTIONS, FOOTER
        ),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def workitem_relation(
        action: Literal[
            "list",
            "create",
            "delete",
            "list_definitions",
            "create_definition",
            "update_definition",
            "delete_definition",
        ],
        project_id: str = "",
        workitem_id: str = "",
        workitem_ids: list[str] | None = None,
        related_workitem_id: str = "",
        relation_type: str = "",
        relation_definition_id: str = "",
        relation_definition_label: str = "",
        definition_id: str = "",
        name: str = "",
        outward: str = "",
        inward: str = "",
        color: str = "",
        # Tri-state: False is a real filter value, distinct from "no filter".
        is_default: bool | None = None,
        is_active: bool | None = None,
        is_dependency: bool = False,
    ) -> Any:
        client, workspace_slug = get_plane_client_context()
        community = _community_edition()

        if community and action not in ("list", "create"):
            raise _not_on_community_edition(f"the {action} action")

        if action == "list_definitions":
            return {
                "built_in_dependencies": list(DEPENDENCY_TYPES),
                "custom_definitions": [
                    d.model_dump() for d in _all_definitions(client, workspace_slug, is_default, is_active)
                ],
            }

        if action == "create_definition":
            if not name:
                return missing(action, "name")
            return client.work_item_relation_definitions.create(
                workspace_slug=workspace_slug,
                data=CreateWorkItemRelationDefinition(
                    name=name,
                    outward=opt(outward),
                    inward=opt(inward),
                    is_active=is_active,
                    color=opt(color),
                ),
            )

        if action in ("update_definition", "delete_definition"):
            if not definition_id:
                return missing(action, "definition_id")
            if action == "update_definition":
                return client.work_item_relation_definitions.update(
                    workspace_slug=workspace_slug,
                    definition_id=definition_id,
                    data=UpdateWorkItemRelationDefinition(
                        name=opt(name),
                        outward=opt(outward),
                        inward=opt(inward),
                        is_active=is_active,
                        color=opt(color),
                    ),
                )
            client.work_item_relation_definitions.delete(workspace_slug=workspace_slug, definition_id=definition_id)
            return None

        if error := needs(action, project_id=project_id, workitem_id=workitem_id):
            return error

        if action == "list" and community:
            # Read untyped: the SDK's WorkItemRelationResponse wants each entry as an id string, and this route
            # answers with {"project_id", "issue_id"} objects, so the typed list() fails on any real relation.
            return {
                "relations": client.work_items.relations._get(
                    f"{workspace_slug}/projects/{project_id}/work-items/{workitem_id}/relations"
                )
            }

        if action == "list":
            dependencies = client.work_items.dependencies.list(
                workspace_slug=workspace_slug, project_id=project_id, work_item_id=workitem_id
            )
            custom = client.work_items.custom_relations.list(
                workspace_slug=workspace_slug, project_id=project_id, work_item_id=workitem_id
            )
            return {
                "dependencies": dependencies.model_dump(),
                "custom": {label: [item.model_dump() for item in items] for label, items in custom.items()},
            }

        if action == "create":
            targets = coerce_list(workitem_ids)
            if not targets:
                return missing(action, "workitem_ids")
            if community:
                if relation_definition_id or relation_definition_label:
                    raise _not_on_community_edition("custom relations")
                if not relation_type:
                    return missing(action, "relation_type")
                if error := one_of("relation_type", relation_type, RELATION_TYPES):
                    return error
                return client.work_items.relations.create(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    work_item_id=workitem_id,
                    data=CreateWorkItemRelation(
                        relation_type=relation_type,  # type: ignore[arg-type]
                        issues=targets,
                    ),
                )
            if relation_type:
                if error := one_of("relation_type", relation_type, DEPENDENCY_TYPES, _OTHER_RELATIONS):
                    return error
                return client.work_items.dependencies.create(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    work_item_id=workitem_id,
                    data=CreateWorkItemDependency(
                        relation_type=relation_type,  # type: ignore[arg-type]
                        work_item_ids=targets,
                    ),
                )
            if relation_definition_id and relation_definition_label:
                return client.work_items.custom_relations.create(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    work_item_id=workitem_id,
                    data=CreateWorkItemCustomRelation(
                        relation_definition_id=relation_definition_id,
                        relation_definition_type=relation_definition_label,
                        work_item_ids=targets,
                    ),
                )
            return (
                "Error: provide relation_type for a built-in dependency, or both "
                "relation_definition_id and relation_definition_label for a custom relation. "
                "Call the list_definitions action to find one."
            )

        if not related_workitem_id:
            return missing(action, "related_workitem_id")
        remove = client.work_items.dependencies.remove if is_dependency else client.work_items.custom_relations.remove
        remove(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=workitem_id,
            related_work_item_id=related_workitem_id,
        )
        return None
