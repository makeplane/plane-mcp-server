"""Customer work item link tools for Plane MCP Server."""

from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from plane.models.customers import CustomerWorkItem, LinkCustomerWorkItems

from plane_mcp.client import get_plane_client_context


def register_customer_work_item_tools(mcp: FastMCP) -> None:
    """Register the customer work item link tools with the MCP server."""

    @mcp.tool()
    def list_customer_work_items(
        customer_id: str,
        customer_request_id: str | None = None,
        search: str | None = None,
    ) -> list[CustomerWorkItem]:
        """
        List the work items linked to a customer. Returns all of them, unpaginated.

        Args:
            customer_id: UUID of the customer
            customer_request_id: Only work items linked through this request of the customer
            search: Filter by work item name, sequence ID, or project identifier

        Returns:
            The linked work items
        """
        client, workspace_slug = get_plane_client_context()
        return client.customers.work_items.list(
            workspace_slug=workspace_slug,
            customer_id=customer_id,
            customer_request_id=customer_request_id,
            search=search,
        )

    @mcp.tool()
    def manage_customer_work_items(
        customer_id: str,
        action: Literal["link", "unlink"],
        work_item_ids: list[str],
        customer_request_id: str | None = None,
    ) -> list[CustomerWorkItem]:
        """
        Link or unlink work items on a customer. Use list_customer_work_items to read.

        Args:
            customer_id: UUID of the customer
            action: "link" to attach the work items, "unlink" to detach them
            work_item_ids: Work item UUIDs to link/unlink
            customer_request_id: Scope the links to this request of the customer. On
                unlink, omitting it drops every link to the work item, whichever
                request made it.

        Returns:
            The customer's linked work items after the operation
        """
        client, workspace_slug = get_plane_client_context()
        if not work_item_ids:
            raise ToolError("work_item_ids must not be empty.")

        work_items = client.customers.work_items
        if action == "link":
            work_items.create(
                workspace_slug=workspace_slug,
                customer_id=customer_id,
                data=LinkCustomerWorkItems(work_item_ids=work_item_ids),
                customer_request_id=customer_request_id,
            )
        else:
            for work_item_id in work_item_ids:
                work_items.delete(
                    workspace_slug=workspace_slug,
                    customer_id=customer_id,
                    work_item_id=work_item_id,
                    customer_request_id=customer_request_id,
                )

        return work_items.list(workspace_slug=workspace_slug, customer_id=customer_id)
