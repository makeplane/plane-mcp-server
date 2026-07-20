"""Customer request tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.models.customers import (
    CreateCustomerRequest,
    CustomerRequest,
    PaginatedCustomerRequestResponse,
    UpdateCustomerRequest,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.customers._helpers import page_params


def register_customer_request_tools(mcp: FastMCP) -> None:
    """Register the customer request tools with the MCP server."""

    @mcp.tool()
    def list_customer_requests(
        customer_id: str,
        query: str | None = None,
        cursor: str | None = None,
        per_page: int | None = None,
    ) -> PaginatedCustomerRequestResponse:
        """
        List a customer's requests (paginated).

        A customer request records something the customer asked for. The work items
        addressing it are read with list_customer_work_items, not from here.

        Args:
            customer_id: UUID of the customer
            query: Filter to requests whose name contains this text.
            cursor: Prior response's next_cursor; omit for first page.
            per_page: Results per page (1-1000, default 1000).

        Returns:
            Paginated envelope: results + total_count, next_cursor,
            next_page_results (page again while next_page_results is true).
        """
        client, workspace_slug = get_plane_client_context()
        return client.customers.requests.list(
            workspace_slug=workspace_slug,
            customer_id=customer_id,
            params=page_params(cursor, per_page, query),
        )

    @mcp.tool()
    def create_customer_request(
        customer_id: str,
        name: str,
        description_html: str | None = None,
        link: str | None = None,
        work_item_ids: list[str] | None = None,
    ) -> CustomerRequest:
        """
        Create a request on a customer.

        Args:
            customer_id: UUID of the customer
            name: Request name
            description_html: HTML description
            link: URL associated with the request
            work_item_ids: UUIDs of work items addressing this request; links them to
                the customer as the request is created. Only settable here — use
                manage_customer_work_items to change links afterwards.

        Returns:
            The created CustomerRequest. work_item_ids is never echoed back; read the
            links with list_customer_work_items.
        """
        client, workspace_slug = get_plane_client_context()
        data = CreateCustomerRequest(
            name=name,
            description_html=description_html,
            link=link,
            work_item_ids=work_item_ids,
        )
        return client.customers.requests.create(workspace_slug=workspace_slug, customer_id=customer_id, data=data)

    @mcp.tool()
    def retrieve_customer_request(customer_id: str, request_id: str) -> CustomerRequest:
        """
        Retrieve a customer request by ID.

        Args:
            customer_id: UUID of the customer
            request_id: UUID of the customer request

        Returns:
            The CustomerRequest
        """
        client, workspace_slug = get_plane_client_context()
        return client.customers.requests.retrieve(
            workspace_slug=workspace_slug, customer_id=customer_id, request_id=request_id
        )

    @mcp.tool()
    def update_customer_request(
        customer_id: str,
        request_id: str,
        name: str | None = None,
        description_html: str | None = None,
        link: str | None = None,
    ) -> CustomerRequest:
        """
        Update a customer request. Only the fields you pass are changed.

        Work item links cannot be changed here — use manage_customer_work_items with
        this request_id as customer_request_id.

        Args:
            customer_id: UUID of the customer
            request_id: UUID of the customer request
            name: Request name
            description_html: HTML description
            link: URL associated with the request

        Returns:
            The updated CustomerRequest
        """
        client, workspace_slug = get_plane_client_context()
        data = UpdateCustomerRequest(name=name, description_html=description_html, link=link)
        return client.customers.requests.update(
            workspace_slug=workspace_slug,
            customer_id=customer_id,
            request_id=request_id,
            data=data,
        )

    @mcp.tool()
    def delete_customer_request(customer_id: str, request_id: str) -> None:
        """
        Delete a customer request, and unlink the work items linked through it.

        Args:
            customer_id: UUID of the customer
            request_id: UUID of the customer request
        """
        client, workspace_slug = get_plane_client_context()
        client.customers.requests.delete(workspace_slug=workspace_slug, customer_id=customer_id, request_id=request_id)
