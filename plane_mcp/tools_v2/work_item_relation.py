"""Consolidated `work_item_relation` tool.

Collapses work_item_relations.py (3 tools) and
work_item_relation_definitions.py (4 tools) into one action-dispatch tool.

Two relation systems sit behind it:
- Built-in dependencies -- six fixed directional types.
- Custom relations -- workspace-defined types, each with outward/inward labels.
"""

from __future__ import annotations

from typing import Any, get_args

from fastmcp import FastMCP
from plane.models.work_item_relation_definitions import (
    CreateWorkItemRelationDefinition,
    PaginatedWorkItemRelationDefinitionResponse,
    UpdateWorkItemRelationDefinition,
    WorkItemRelationDefinition,
)
from plane.models.work_items import (
    CreateWorkItemCustomRelation,
    CreateWorkItemDependency,
    DependencyTypeEnum,
    WorkItemWithRelationType,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools_v2._common import bad_action, json_out, missing, opt

# Built-in dependency relation_type values (sourced from the SDK contract).
_DEPENDENCY_TYPES: tuple[str, ...] = get_args(DependencyTypeEnum)

ACTIONS = [
    "list",
    "create",
    "remove",
    "list_definitions",
    "create_definition",
    "update_definition",
    "delete_definition",
]

DOC = """Manage work item relations and the workspace relation definitions behind them. Actions:
list (project_id, work_item_id);
create (project_id, work_item_id, work_item_ids; plus relation_type OR relation_definition_id + relation_definition_label);
remove (project_id, work_item_id, related_work_item_id, is_dependency);
list_definitions (no required params; optional is_default, is_active filters);
create_definition (name; optional outward, inward, is_active, color);
update_definition (definition_id; optional name, outward, inward, is_active, color);
delete_definition (definition_id).

Two relation systems exist. Always call list_definitions first and match the user's wording
to an entry there:
- built_in_dependencies: fixed values (blocking, blocked_by, start_before, start_after,
  finish_before, finish_after) -- pass the matched value as relation_type.
- custom_definitions: workspace-defined; pass that definition's id as relation_definition_id
  and the matched outward OR inward label as relation_definition_label (the label sets
  directionality). A custom label like "dependent on" is not the built-in blocked_by.

list returns dependencies (grouped by the six directions) and custom (grouped by definition label).

remove deletes ONE relation. A built-in dependency and a custom relation between the same two
items are independent -- removing one leaves the other intact. Set is_dependency=true for a
built-in dependency, false for a custom relation.

A relation definition has an outward label (how the source describes the target) and an inward
label (how the target describes the source). create/update/delete_definition manage
custom_definitions only; built_in_dependencies are fixed."""


def _dispatch(
    action: str,
    project_id: str,
    work_item_id: str,
    work_item_ids: list[str] | None,
    relation_type: str,
    relation_definition_id: str,
    relation_definition_label: str,
    related_work_item_id: str,
    is_dependency: bool | None,
    definition_id: str,
    name: str,
    outward: str,
    inward: str,
    color: str,
    is_default: bool | None,
    is_active: bool | None,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)

    client, workspace_slug = get_plane_client_context()

    if action == "list_definitions":
        results: list[WorkItemRelationDefinition] = []
        cursor: str | None = None
        while True:
            page: PaginatedWorkItemRelationDefinitionResponse = (
                client.work_item_relation_definitions.list(
                    workspace_slug=workspace_slug,
                    is_default=is_default,
                    is_active=is_active,
                    per_page=100,
                    cursor=cursor,
                )
            )
            results.extend(page.results)
            cursor = page.next_cursor
            if not page.next_page_results or not cursor:
                break
        return {
            "built_in_dependencies": list(get_args(DependencyTypeEnum)),
            "custom_definitions": [d.model_dump() for d in results],
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

    if action == "update_definition":
        if not definition_id:
            return missing(action, "definition_id")
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

    if action == "delete_definition":
        if not definition_id:
            return missing(action, "definition_id")
        client.work_item_relation_definitions.delete(
            workspace_slug=workspace_slug,
            definition_id=definition_id,
        )
        return None

    if not project_id:
        return missing(action, "project_id")
    if not work_item_id:
        return missing(action, "work_item_id")

    if action == "list":
        dependencies = client.work_items.dependencies.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
        )
        custom = client.work_items.custom_relations.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
        )
        return {
            "dependencies": dependencies.model_dump(),
            "custom": {
                label: [item.model_dump() for item in items] for label, items in custom.items()
            },
        }

    if action == "remove":
        if not related_work_item_id:
            return missing(action, "related_work_item_id")
        if is_dependency is None:
            return missing(action, "is_dependency")
        remove = (
            client.work_items.dependencies.remove
            if is_dependency
            else client.work_items.custom_relations.remove
        )
        remove(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            related_work_item_id=related_work_item_id,
        )
        return None

    # action == "create"
    if not work_item_ids:
        return missing(action, "work_item_ids")
    if relation_type:
        if relation_type not in _DEPENDENCY_TYPES:
            return (
                f"Error: relation_type must be one of {list(_DEPENDENCY_TYPES)}. For any "
                "other relationship, pass relation_definition_id + "
                "relation_definition_label from action='list_definitions'."
            )
        return client.work_items.dependencies.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=CreateWorkItemDependency(
                relation_type=relation_type,  # type: ignore[arg-type]
                work_item_ids=work_item_ids,
            ),
        )
    if relation_definition_id and relation_definition_label:
        return client.work_items.custom_relations.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=CreateWorkItemCustomRelation(
                relation_definition_id=relation_definition_id,
                relation_definition_type=relation_definition_label,
                work_item_ids=work_item_ids,
            ),
        )
    return (
        "Error: action 'create' requires relation_type for a built-in dependency, or "
        "relation_definition_id + relation_definition_label for a custom relation "
        "(call action='list_definitions' to find one)."
    )


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="work_item_relation", description=DOC)
    def _work_item_relation(
        action: str,
        project_id: str = "",
        work_item_id: str = "",
        work_item_ids: list[str] | None = None,
        relation_type: str = "",
        relation_definition_id: str = "",
        relation_definition_label: str = "",
        related_work_item_id: str = "",
        is_dependency: bool | None = None,
        definition_id: str = "",
        name: str = "",
        outward: str = "",
        inward: str = "",
        color: str = "",
        is_default: bool | None = None,
        is_active: bool | None = None,
    ) -> (
        dict[str, Any]
        | WorkItemRelationDefinition
        | list[WorkItemWithRelationType]
        | str
        | None
    ):
        return _dispatch(
            action, project_id, work_item_id, work_item_ids, relation_type,
            relation_definition_id, relation_definition_label, related_work_item_id,
            is_dependency, definition_id, name, outward, inward, color,
            is_default, is_active,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="work_item_relation", description=DOC)
    def _work_item_relation(
        action: str,
        project_id: str = "",
        work_item_id: str = "",
        work_item_ids: list[str] | None = None,
        relation_type: str = "",
        relation_definition_id: str = "",
        relation_definition_label: str = "",
        related_work_item_id: str = "",
        is_dependency: bool | None = None,
        definition_id: str = "",
        name: str = "",
        outward: str = "",
        inward: str = "",
        color: str = "",
        is_default: bool | None = None,
        is_active: bool | None = None,
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, project_id, work_item_id, work_item_ids, relation_type,
                    relation_definition_id, relation_definition_label, related_work_item_id,
                    is_dependency, definition_id, name, outward, inward, color,
                    is_default, is_active,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
