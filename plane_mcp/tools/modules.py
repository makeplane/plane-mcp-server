"""Module-related tools for Plane MCP Server."""

from typing import Annotated, Any, get_args

from fastmcp import FastMCP
from fastmcp.utilities.logging import get_logger
from plane.errors.errors import HttpError
from plane.models.enums import ModuleStatusEnum
from plane.models.modules import (
    CreateModule,
    Module,
    PaginatedArchivedModuleResponse,
    PaginatedModuleResponse,
    PaginatedModuleWorkItemResponse,
    UpdateModule,
)
from plane.models.query_params import WorkItemQueryParams
from pydantic import Field

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.pql_reference import PQL_FIELD_HINT, PQL_FULL_REFERENCE

logger = get_logger(__name__)


def register_module_tools(mcp: FastMCP) -> None:
    """Register all module-related tools with the MCP server."""

    @mcp.tool()
    def list_modules(
        project_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[Module]:
        """
        List all modules in a project.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            params: Optional query parameters as a dictionary

        Returns:
            List of Module objects
        """
        client, workspace_slug = get_plane_client_context()
        response: PaginatedModuleResponse = client.modules.list(
            workspace_slug=workspace_slug, project_id=project_id, params=params
        )
        return response.results

    @mcp.tool()
    def create_module(
        project_id: str,
        name: str,
        description: str | None = None,
        start_date: str | None = None,
        target_date: str | None = None,
        status: str | None = None,
        lead: str | None = None,
        members: list[str] | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> Module:
        """
        Create a new module.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            name: Module name
            description: Module description
            start_date: Module start date (ISO 8601 format)
            target_date: Module target/end date (ISO 8601 format)
            status: Module status (backlog, planned, in-progress, paused, completed, cancelled)
            lead: UUID of the user who leads the module
            members: List of user IDs who are members of the module
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Created Module object
        """
        client, workspace_slug = get_plane_client_context()

        # Validate status against allowed literal values
        validated_status: ModuleStatusEnum | None = (
            status if status in get_args(ModuleStatusEnum) else None  # type: ignore[assignment]
        )

        data = CreateModule(
            name=name,
            description=description,
            start_date=start_date,
            target_date=target_date,
            status=validated_status,
            lead=lead,
            members=members,
            external_source=external_source,
            external_id=external_id,
        )

        return client.modules.create(workspace_slug=workspace_slug, project_id=project_id, data=data)

    @mcp.tool()
    def retrieve_module(project_id: str, module_id: str) -> Module:
        """
        Retrieve a module by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            module_id: UUID of the module

        Returns:
            Module object
        """
        client, workspace_slug = get_plane_client_context()
        return client.modules.retrieve(workspace_slug=workspace_slug, project_id=project_id, module_id=module_id)

    @mcp.tool()
    def update_module(
        project_id: str,
        module_id: str,
        name: str | None = None,
        description: str | None = None,
        start_date: str | None = None,
        target_date: str | None = None,
        status: str | None = None,
        lead: str | None = None,
        members: list[str] | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> Module:
        """
        Update a module by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            module_id: UUID of the module
            name: Module name
            description: Module description
            start_date: Module start date (ISO 8601 format)
            target_date: Module target/end date (ISO 8601 format)
            status: Module status (backlog, planned, in-progress, paused, completed, cancelled)
            lead: UUID of the user who leads the module
            members: List of user IDs who are members of the module
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Updated Module object
        """
        client, workspace_slug = get_plane_client_context()

        # Validate status against allowed literal values
        validated_status: ModuleStatusEnum | None = (
            status if status in get_args(ModuleStatusEnum) else None  # type: ignore[assignment]
        )

        data = UpdateModule(
            name=name,
            description=description,
            start_date=start_date,
            target_date=target_date,
            status=validated_status,
            lead=lead,
            members=members,
            external_source=external_source,
            external_id=external_id,
        )

        return client.modules.update(
            workspace_slug=workspace_slug, project_id=project_id, module_id=module_id, data=data
        )

    @mcp.tool()
    def delete_module(project_id: str, module_id: str) -> None:
        """
        Delete a module by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            module_id: UUID of the module
        """
        client, workspace_slug = get_plane_client_context()
        client.modules.delete(workspace_slug=workspace_slug, project_id=project_id, module_id=module_id)

    @mcp.tool()
    def list_archived_modules(
        project_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[Module]:
        """
        List archived modules in a project.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            params: Optional query parameters as a dictionary

        Returns:
            List of archived Module objects
        """
        client, workspace_slug = get_plane_client_context()
        response: PaginatedArchivedModuleResponse = client.modules.list_archived(
            workspace_slug=workspace_slug, project_id=project_id, params=params
        )
        return response.results

    @mcp.tool()
    def manage_module_work_items(
        project_id: str,
        module_id: str,
        add_ids: list[str] | None = None,
        remove_ids: list[str] | None = None,
    ) -> None:
        """
        Add or remove work items on a module in a single call.

        At least one of add_ids or remove_ids must be provided.

        Args:
            project_id: UUID of the project
            module_id: UUID of the module
            add_ids: UUIDs of work items to add to the module
            remove_ids: UUIDs of work items to remove from the module
        """
        client, workspace_slug = get_plane_client_context()
        if add_ids:
            client.modules.add_work_items(
                workspace_slug=workspace_slug,
                project_id=project_id,
                module_id=module_id,
                issue_ids=add_ids,
            )
        if remove_ids:
            for work_item_id in remove_ids:
                client.modules.remove_work_item(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    module_id=module_id,
                    work_item_id=work_item_id,
                )

    @mcp.tool()
    def list_module_work_items(
        project_id: str,
        module_id: str,
        pql: Annotated[str | None, Field(description=PQL_FIELD_HINT)] = None,
        order_by: str | None = None,
        per_page: int | None = None,
        cursor: str | None = None,
        expand: str | None = None,
        fields: str | None = None,
    ) -> dict[str, Any]:
        """
        List work items in a module with optional PQL filtering.

        Args:
            project_id: UUID of the project
            module_id: UUID of the module
            pql: PQL filter expression. See field description for syntax.
                Omit to list all items in the module.
            order_by: Field to sort by; prefix with `-` for descending.
            per_page: Results per page, 1-100 (default 25).
            cursor: Pagination cursor from a previous response's `next_cursor`.
            expand: Comma-separated related fields to expand.
            fields: Comma-separated sparse fieldset.

        Returns:
            Paginated envelope with results, total_count, next_cursor, prev_cursor.
        """
        client, workspace_slug = get_plane_client_context()
        params = WorkItemQueryParams(
            pql=pql,
            order_by=order_by,
            per_page=per_page,
            cursor=cursor,
            expand=expand,
            fields=fields,
        )
        try:
            response: PaginatedModuleWorkItemResponse = client.modules.list_work_items(
                workspace_slug=workspace_slug,
                project_id=project_id,
                module_id=module_id,
                params=params,
            )
        except HttpError as e:
            if pql and e.status_code == 400 and isinstance(e.response, dict) and "pql" in e.response:
                logger.warning("list_module_work_items: invalid PQL %r → %s", pql, e.response)
                return {
                    "error": e.response["pql"],
                    "failed_pql": pql,
                    "pql_reference": PQL_FULL_REFERENCE,
                    "hint": "The PQL above failed. Fix it using the reference and retry list_module_work_items.",
                }
            raise
        return {
            "results": [item.model_dump() if hasattr(item, "model_dump") else item for item in (response.results or [])],
            "total_count": response.total_count,
            "count": response.count,
            "next_cursor": response.next_cursor,
            "prev_cursor": response.prev_cursor,
            "next_page_results": response.next_page_results,
            "prev_page_results": response.prev_page_results,
        }

    @mcp.tool()
    def manage_module_archive(project_id: str, module_id: str, archive: bool) -> None:
        """
        Archive or unarchive a module.

        Args:
            project_id: UUID of the project
            module_id: UUID of the module
            archive: True to archive the module, False to unarchive it
        """
        client, workspace_slug = get_plane_client_context()
        if archive:
            client.modules.archive(workspace_slug=workspace_slug, project_id=project_id, module_id=module_id)
        else:
            client.modules.unarchive(workspace_slug=workspace_slug, project_id=project_id, module_id=module_id)
