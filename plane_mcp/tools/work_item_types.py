"""Work item type-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.projects import ProjectFeature
from plane.models.work_item_types import (
    CreateWorkItemType,
    UpdateWorkItemType,
    WorkItemType,
)

from plane_mcp.client import get_plane_client_context


def register_work_item_type_tools(mcp: FastMCP) -> None:
    """Register all work item type-related tools with the MCP server."""

    @mcp.tool()
    def list_work_item_types(
        project_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[WorkItemType]:
        """
        List work item types. Omit project_id for workspace-level types.

        Each result's `id` is the `work_item_type_id` needed by list_work_item_properties
        to look up custom property and option UUIDs for PQL cf[] filters.
        """
        client, workspace_slug = get_plane_client_context()
        if project_id:
            return client.work_item_types.list(
                workspace_slug=workspace_slug, project_id=project_id, params=params
            )
        return client.workspace_work_item_types.list(workspace_slug=workspace_slug)

    @mcp.tool()
    def create_work_item_type(
        name: str,
        project_id: str | None = None,
        description: str | None = None,
        project_ids: list[str] | None = None,
        is_active: bool | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> WorkItemType:
        """
        Create a new work item type.

        Args:
            name: Work item type name
            project_id: UUID of the project. Omit for workspace-level type.
            description: Work item type description
            project_ids: List of project IDs this type applies to
            is_active: Whether the type is active
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Created WorkItemType object
        """
        client, workspace_slug = get_plane_client_context()

        data = CreateWorkItemType(
            name=name,
            description=description,
            project_ids=project_ids,
            is_active=is_active,
            external_source=external_source,
            external_id=external_id,
        )

        if project_id:
            return client.work_item_types.create(
                workspace_slug=workspace_slug, project_id=project_id, data=data
            )
        return client.workspace_work_item_types.create(workspace_slug=workspace_slug, data=data)

    @mcp.tool()
    def import_work_item_types_to_project(
        project_id: str,
        work_item_type_ids: list[str],
    ) -> None:
        """
        Bulk-link workspace-level work item types to a project.

        Imports one or more workspace-scoped work item types into a project so
        that they become available for use within that project.

        Args:
            project_id: UUID of the project
            work_item_type_ids: List of workspace-level work item type UUIDs to import
        """
        client, workspace_slug = get_plane_client_context()
        client.work_item_types.import_to_project(
            workspace_slug=workspace_slug, project_id=project_id, work_item_type_ids=work_item_type_ids
        )

    @mcp.tool()
    def resolve_work_item_type(
        project_id: str,
        name: str,
    ) -> WorkItemType:
        """
        Find a work item type by name for a project, create it if missing, and
        guarantee it is usable inside that project. Use this to resolve the
        type_id for a typed work item such as an "Epic" or "Initiative" before
        calling create_work_item(type_id=...).

        Handles workspace-level and project-level work item types automatically,
        so the caller never has to decide which mode the workspace is in:
        - If the workspace owns work item types, the type is found (or created)
          at the workspace level and imported into the project. Project-level
          creation is blocked in this mode, so importing is the only valid path.
        - Otherwise the type is found (or created) at the project level, enabling
          the project's work item types feature first if it is off.

        Matching is exact (case-sensitive, whitespace-stripped); an existing type is never duplicated.

        Args:
            project_id: UUID of the project the type must be usable in
            name: Work item type name, e.g. "Epic" or "Initiative"

        Returns:
            The WorkItemType. Its `id` is the `type_id` for create_work_item.
        """
        client, workspace_slug = get_plane_client_context()
        target = name.strip()

        workspace_features = client.workspaces.get_features(workspace_slug=workspace_slug)
        workspace_owns_types = bool(workspace_features.model_dump().get("work_item_types"))

        if workspace_owns_types:
            in_project = next(
                (t for t in client.work_item_types.list(workspace_slug=workspace_slug, project_id=project_id) if (t.name or "").strip() == target),
                None,
            )
            if in_project is not None:
                return in_project
            at_workspace = next(
                (t for t in client.workspace_work_item_types.list(workspace_slug=workspace_slug) if (t.name or "").strip() == target),
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

        # Mode B — types are per-project; enable the feature if needed, then find or create.
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
            (t for t in client.work_item_types.list(workspace_slug=workspace_slug, project_id=project_id) if (t.name or "").strip() == target),
            None,
        )
        if existing is None:
            existing = client.work_item_types.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                data=CreateWorkItemType(name=name),
            )
        return existing

    @mcp.tool()
    def retrieve_work_item_type(
        work_item_type_id: str,
        project_id: str | None = None,
    ) -> WorkItemType:
        """
        Retrieve a work item type by ID.

        Args:
            work_item_type_id: UUID of the work item type
            project_id: UUID of the project. Omit for workspace scope.

        Returns:
            WorkItemType object
        """
        client, workspace_slug = get_plane_client_context()
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

    @mcp.tool()
    def update_work_item_type(
        work_item_type_id: str,
        project_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        project_ids: list[str] | None = None,
        is_active: bool | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> WorkItemType:
        """
        Update a work item type by ID.

        Args:
            work_item_type_id: UUID of the work item type
            project_id: UUID of the project. Omit for workspace scope.
            name: Work item type name
            description: Work item type description
            project_ids: List of project IDs this type applies to
            is_active: Whether the type is active
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Updated WorkItemType object
        """
        client, workspace_slug = get_plane_client_context()

        data = UpdateWorkItemType(
            name=name,
            description=description,
            project_ids=project_ids,
            is_active=is_active,
            external_source=external_source,
            external_id=external_id,
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

    @mcp.tool()
    def delete_work_item_type(
        work_item_type_id: str,
        project_id: str | None = None,
    ) -> None:
        """
        Delete a work item type by ID.

        Args:
            work_item_type_id: UUID of the work item type
            project_id: UUID of the project. Omit for workspace scope.
        """
        client, workspace_slug = get_plane_client_context()
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
