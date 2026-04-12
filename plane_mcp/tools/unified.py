"""Unified list and retrieve tools for Plane MCP Server."""

from typing import Any, Literal

from fastmcp import FastMCP

from plane_mcp.client import get_plane_client_context

# Config format: entity_type -> (client_attr_path, method, container_id_kwarg, scope)
# scope: "workspace" | "project" | "cycle" | "module" | "milestone" | "work_item" | "work_item_type"
# container_id_kwarg: the kwarg name for the container ID (cycle_id, module_id, etc.), or None if not needed
ENTITY_LIST_CONFIG: dict[str, tuple[str, str, str | None, str]] = {
    "projects": ("projects", "list", None, "workspace"),
    "initiatives": ("initiatives", "list", None, "workspace"),
    "workspace_members": ("workspaces", "get_members", None, "workspace"),
    "work_items": ("work_items", "list", None, "project"),
    "epics": ("epics", "list", None, "project"),
    "cycles": ("cycles", "list", None, "project"),
    "archived_cycles": ("cycles", "list_archived", None, "project"),
    "modules": ("modules", "list", None, "project"),
    "archived_modules": ("modules", "list_archived", None, "project"),
    "labels": ("labels", "list", None, "project"),
    "states": ("states", "list", None, "project"),
    "intake_work_items": ("intake", "list", None, "project"),
    "work_item_types": ("work_item_types", "list", None, "project"),
    "milestones": ("milestones", "list", None, "project"),
    "project_members": ("projects", "get_members", None, "project"),
    "cycle_work_items": ("cycles", "list_work_items", "cycle_id", "cycle"),
    "module_work_items": ("modules", "list_work_items", "module_id", "module"),
    "milestone_work_items": ("milestones", "list_work_items", "milestone_id", "milestone"),
    "work_item_activities": ("work_items.activities", "list", "work_item_id", "work_item"),
    "work_item_comments": ("work_items.comments", "list", "work_item_id", "work_item"),
    "work_item_links": ("work_items.links", "list", "work_item_id", "work_item"),
    "work_item_relations": ("work_items.relations", "list", "work_item_id", "work_item"),
    "work_logs": ("work_items.work_logs", "list", "work_item_id", "work_item"),
    "work_item_properties": ("work_item_properties", "list", "type_id", "work_item_type"),
}

# Config format: entity_type -> (client_attr_path, method, entity_id_kwarg, scope)
# entity_id_kwarg: the kwarg name for entity_id (e.g. "cycle_id"), or None for special cases
ENTITY_RETRIEVE_CONFIG: dict[str, tuple[str, str, str | None, str]] = {
    "project": ("projects", "retrieve", "project_id", "workspace"),
    "initiative": ("initiatives", "retrieve", "initiative_id", "workspace"),
    "workspace_page": ("pages", "retrieve_workspace_page", "page_id", "workspace"),
    "work_item": ("work_items", "retrieve", "work_item_id", "project"),
    "work_item_by_identifier": ("work_items", "retrieve_by_identifier", None, "identifier"),
    "epic": ("epics", "retrieve", "epic_id", "project"),
    "cycle": ("cycles", "retrieve", "cycle_id", "project"),
    "module": ("modules", "retrieve", "module_id", "project"),
    "label": ("labels", "retrieve", "label_id", "project"),
    "state": ("states", "retrieve", "state_id", "project"),
    "intake_work_item": ("intake", "retrieve", "work_item_id", "project"),
    "work_item_type": ("work_item_types", "retrieve", "work_item_type_id", "project"),
    "milestone": ("milestones", "retrieve", "milestone_id", "project"),
    "project_page": ("pages", "retrieve_project_page", "page_id", "project"),
    "work_item_activity": ("work_items.activities", "retrieve", "activity_id", "work_item"),
    "work_item_comment": ("work_items.comments", "retrieve", "comment_id", "work_item"),
    "work_item_link": ("work_items.links", "retrieve", "link_id", "work_item"),
    "work_item_property": ("work_item_properties", "retrieve", "work_item_property_id", "work_item_type"),
}

# These methods don't accept a `params` kwarg
_NO_PARAMS_METHODS = {
    "workspaces.get_members",
    "projects.get_members",
    "work_items.relations.list",
}

# These methods return a list directly (no .results attribute)
_DIRECT_RETURN_METHODS = {
    "work_item_types.list",
    "work_item_properties.list",
    "workspaces.get_members",
    "projects.get_members",
    "work_items.relations.list",
    "work_items.work_logs.list",
}


def _resolve_client_attr(client: Any, attr_path: str) -> Any:
    """Walk a dotted attribute path on the client object."""
    obj = client
    for part in attr_path.split("."):
        obj = getattr(obj, part)
    return obj


def register_unified_tools(mcp: FastMCP) -> None:
    """Register unified list and retrieve tools with the MCP server."""

    @mcp.tool()
    def entity_list(
        entity_type: Literal[
            "projects",
            "initiatives",
            "workspace_members",
            "work_items",
            "epics",
            "cycles",
            "archived_cycles",
            "modules",
            "archived_modules",
            "labels",
            "states",
            "intake_work_items",
            "work_item_types",
            "milestones",
            "project_members",
            "cycle_work_items",
            "module_work_items",
            "milestone_work_items",
            "work_item_activities",
            "work_item_comments",
            "work_item_links",
            "work_item_relations",
            "work_logs",
            "work_item_properties",
        ],
        project_id: str | None = None,
        cycle_id: str | None = None,
        module_id: str | None = None,
        milestone_id: str | None = None,
        work_item_id: str | None = None,
        type_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[Any]:
        """
        List entities of any supported type in Plane.

        Use this tool to retrieve collections of Plane entities. The required scope
        parameters depend on the entity_type:
        - Workspace-scoped (no extra IDs needed): projects, initiatives, workspace_members
        - Project-scoped (project_id required): work_items, epics, cycles, archived_cycles,
          modules, archived_modules, labels, states, intake_work_items, work_item_types,
          milestones, project_members
        - Cycle-scoped (project_id + cycle_id required): cycle_work_items
        - Module-scoped (project_id + module_id required): module_work_items
        - Milestone-scoped (project_id + milestone_id required): milestone_work_items
        - Work item-scoped (project_id + work_item_id required): work_item_activities,
          work_item_comments, work_item_links, work_item_relations, work_logs
        - Work item type-scoped (project_id + type_id required): work_item_properties

        Args:
            entity_type: The type of entity to list
            project_id: UUID of the project (required for project-scoped and deeper entities)
            cycle_id: UUID of the cycle (required for cycle_work_items)
            module_id: UUID of the module (required for module_work_items)
            milestone_id: UUID of the milestone (required for milestone_work_items)
            work_item_id: UUID of the work item (required for work item-scoped entities)
            type_id: UUID of the work item type (required for work_item_properties)
            params: Optional query parameters as a dictionary (e.g., per_page, cursor, order_by)

        Returns:
            List of entity objects of the requested type
        """
        client, workspace_slug = get_plane_client_context()

        attr_path, method_name, container_id_kwarg, scope = ENTITY_LIST_CONFIG[entity_type]
        method_key = f"{attr_path}.{method_name}"

        # Validate required scope params
        if scope in ("project", "cycle", "module", "milestone", "work_item", "work_item_type"):
            if project_id is None:
                raise ValueError(f"project_id is required for entity_type='{entity_type}'")
        if scope == "cycle" and cycle_id is None:
            raise ValueError(f"cycle_id is required for entity_type='{entity_type}'")
        if scope == "module" and module_id is None:
            raise ValueError(f"module_id is required for entity_type='{entity_type}'")
        if scope == "milestone" and milestone_id is None:
            raise ValueError(f"milestone_id is required for entity_type='{entity_type}'")
        if scope == "work_item" and work_item_id is None:
            raise ValueError(f"work_item_id is required for entity_type='{entity_type}'")
        if scope == "work_item_type" and type_id is None:
            raise ValueError(f"type_id is required for entity_type='{entity_type}'")

        resource = _resolve_client_attr(client, attr_path)
        method = getattr(resource, method_name)

        # Build kwargs
        kwargs: dict[str, Any] = {"workspace_slug": workspace_slug}

        if scope in ("project", "cycle", "module", "milestone", "work_item", "work_item_type"):
            kwargs["project_id"] = project_id
        if scope == "cycle":
            kwargs["cycle_id"] = cycle_id
        elif scope == "module":
            kwargs["module_id"] = module_id
        elif scope == "milestone":
            kwargs["milestone_id"] = milestone_id
        elif scope == "work_item":
            kwargs["work_item_id"] = work_item_id
        elif scope == "work_item_type":
            kwargs["type_id"] = type_id

        if method_key not in _NO_PARAMS_METHODS:
            kwargs["params"] = params

        result = method(**kwargs)

        if method_key in _DIRECT_RETURN_METHODS:
            return result
        return result.results

    @mcp.tool()
    def entity_retrieve(
        entity_type: Literal[
            "project",
            "initiative",
            "workspace_page",
            "work_item",
            "work_item_by_identifier",
            "epic",
            "cycle",
            "module",
            "label",
            "state",
            "intake_work_item",
            "work_item_type",
            "milestone",
            "project_page",
            "work_item_activity",
            "work_item_comment",
            "work_item_link",
            "work_item_property",
        ],
        entity_id: str | None = None,
        project_id: str | None = None,
        work_item_id: str | None = None,
        type_id: str | None = None,
        project_identifier: str | None = None,
        issue_identifier: int | None = None,
    ) -> Any:
        """
        Retrieve a single entity by ID in Plane.

        Use this tool to fetch a specific Plane entity by its ID. The required parameters
        depend on the entity_type:
        - Workspace-scoped (entity_id required): project, initiative, workspace_page
        - Project-scoped (project_id + entity_id required): work_item, epic, cycle, module,
          label, state, intake_work_item, work_item_type, milestone, project_page
        - Work item-scoped (project_id + work_item_id + entity_id required):
          work_item_activity, work_item_comment, work_item_link
        - Work item type-scoped (project_id + type_id + entity_id required): work_item_property
        - Special: work_item_by_identifier requires project_identifier + issue_identifier
          instead of entity_id

        Args:
            entity_type: The type of entity to retrieve
            entity_id: UUID of the entity to retrieve (not used for work_item_by_identifier)
            project_id: UUID of the project (required for project-scoped and deeper entities)
            work_item_id: UUID of the work item (required for work item-scoped entities)
            type_id: UUID of the work item type (required for work_item_property)
            project_identifier: Project identifier string, e.g. "MP" (for work_item_by_identifier)
            issue_identifier: Issue sequence number, e.g. 42 (for work_item_by_identifier)

        Returns:
            The requested entity object
        """
        client, workspace_slug = get_plane_client_context()

        attr_path, method_name, entity_id_kwarg, scope = ENTITY_RETRIEVE_CONFIG[entity_type]

        resource = _resolve_client_attr(client, attr_path)
        method = getattr(resource, method_name)

        # Special case: work_item_by_identifier
        if entity_type == "work_item_by_identifier":
            if project_identifier is None or issue_identifier is None:
                raise ValueError("project_identifier and issue_identifier are required for work_item_by_identifier")
            return method(
                workspace_slug=workspace_slug,
                project_identifier=project_identifier,
                issue_identifier=issue_identifier,
            )

        # Validate required params
        if entity_id is None:
            raise ValueError(f"entity_id is required for entity_type='{entity_type}'")
        if scope in ("project", "work_item", "work_item_type"):
            if project_id is None:
                raise ValueError(f"project_id is required for entity_type='{entity_type}'")
        if scope == "work_item" and work_item_id is None:
            raise ValueError(f"work_item_id is required for entity_type='{entity_type}'")
        if scope == "work_item_type" and type_id is None:
            raise ValueError(f"type_id is required for entity_type='{entity_type}'")

        # Build kwargs
        kwargs: dict[str, Any] = {"workspace_slug": workspace_slug}

        if scope in ("project", "work_item", "work_item_type"):
            kwargs["project_id"] = project_id
        if scope == "work_item":
            kwargs["work_item_id"] = work_item_id
        if scope == "work_item_type":
            kwargs["type_id"] = type_id

        kwargs[entity_id_kwarg] = entity_id

        return method(**kwargs)
