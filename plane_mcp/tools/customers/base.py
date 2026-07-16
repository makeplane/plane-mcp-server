"""Customer tools for Plane MCP Server."""

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from plane.models.customers import (
    CreateCustomer,
    Customer,
    PaginatedCustomerResponse,
    UpdateCustomer,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.customers._helpers import page_params


def register_customer_base_tools(mcp: FastMCP) -> None:
    """Register the customer CRUD tools with the MCP server."""

    @mcp.tool()
    def list_customers(
        query: str | None = None,
        cursor: str | None = None,
        per_page: int | None = None,
    ) -> PaginatedCustomerResponse:
        """
        List customers in the workspace (paginated).

        Args:
            query: Filter to customers whose name contains this text.
            cursor: Prior response's next_cursor; omit for first page.
            per_page: Results per page (1-1000, default 1000).

        Returns:
            Paginated envelope: results + total_count, next_cursor,
            next_page_results (page again while next_page_results is true).
        """
        client, workspace_slug = get_plane_client_context()
        return client.customers.list(workspace_slug=workspace_slug, params=page_params(cursor, per_page, query))

    @mcp.tool()
    def create_customer(
        name: str,
        description_html: str | None = None,
        email: str | None = None,
        website_url: str | None = None,
        domain: str | None = None,
        employees: int | None = None,
        stage: str | None = None,
        contract_status: str | None = None,
        revenue: str | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> Customer:
        """
        Create a customer, or update the existing one it matches (upsert).

        This is an upsert, not a plain create. When external_source and external_id
        are both given, the customer matching them is updated; otherwise a customer
        with the same name is updated. A new customer is created only when neither
        matches — so a repeated call never duplicates.

        Args:
            name: Name of the person or business; also the match key when no external
                reference is given
            description_html: HTML description
            email: Primary contact email
            website_url: Customer website, e.g. "https://acme.com"
            domain: The customer's industry — shown as "Industry" in Plane. Free text,
                e.g. "Retail", "e-Commerce", "Fintech", "Banking". This is NOT a web
                domain; the site belongs in website_url.
            employees: Number of employees, if the customer is a business
            stage: Lifecycle stage. Stored free-form, but Plane only renders:
                lead | sales_qualified_lead | contract_negotiation | closed_won | closed_lost
            contract_status: Stored free-form, but Plane only renders:
                active | pre_contract | signed | inactive
            revenue: Annual revenue the customer generates, as a string, e.g. "5000000"
            external_source: External system the customer came from, e.g. "salesforce"
            external_id: The customer's ID in that external system

        Returns:
            The created or updated Customer
        """
        client, workspace_slug = get_plane_client_context()
        data = CreateCustomer(
            name=name,
            description_html=description_html,
            email=email,
            website_url=website_url,
            domain=domain,
            employees=employees,
            stage=stage,
            contract_status=contract_status,
            revenue=revenue,
            external_source=external_source,
            external_id=external_id,
        )
        return client.customers.create(workspace_slug=workspace_slug, data=data)

    @mcp.tool()
    def retrieve_customer(customer_id: str) -> Customer:
        """
        Retrieve a customer by ID.

        Args:
            customer_id: UUID of the customer

        Returns:
            The Customer
        """
        client, workspace_slug = get_plane_client_context()
        return client.customers.retrieve(workspace_slug=workspace_slug, customer_id=customer_id)

    @mcp.tool()
    def update_customer(
        customer_id: str,
        name: str | None = None,
        description_html: str | None = None,
        email: str | None = None,
        website_url: str | None = None,
        domain: str | None = None,
        employees: int | None = None,
        stage: str | None = None,
        contract_status: str | None = None,
        revenue: str | None = None,
        logo_props: dict | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> Customer:
        """
        Update a customer by ID. Only the fields you pass are changed.

        Args:
            customer_id: UUID of the customer
            name: Name of the person or business
            description_html: HTML description
            email: Primary contact email
            website_url: Customer website, e.g. "https://acme.com"
            domain: The customer's industry — shown as "Industry" in Plane. Free text,
                e.g. "Retail", "e-Commerce", "Fintech", "Banking". This is NOT a web
                domain; the site belongs in website_url.
            employees: Number of employees, if the customer is a business
            stage: Lifecycle stage. Stored free-form, but Plane only renders:
                lead | sales_qualified_lead | contract_negotiation | closed_won | closed_lost
            contract_status: Stored free-form, but Plane only renders:
                active | pre_contract | signed | inactive
            revenue: Annual revenue the customer generates, as a string, e.g. "5000000"
            logo_props: Logo properties
            external_source: External system the customer came from
            external_id: The customer's ID in that external system

        Returns:
            The updated Customer
        """
        client, workspace_slug = get_plane_client_context()
        data = UpdateCustomer(
            name=name,
            description_html=description_html,
            email=email,
            website_url=website_url,
            domain=domain,
            employees=employees,
            stage=stage,
            contract_status=contract_status,
            revenue=revenue,
            logo_props=logo_props,
            external_source=external_source,
            external_id=external_id,
        )
        return client.customers.update(workspace_slug=workspace_slug, customer_id=customer_id, data=data)

    @mcp.tool()
    def delete_customer(
        customer_id: str | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> None:
        """
        Delete a customer, addressed either by ID or by its external reference.

        Pass customer_id, or pass both external_source and external_id for a
        customer synced from an external system. Deleting by an external reference
        that matches nothing succeeds silently.

        Args:
            customer_id: UUID of the customer
            external_source: External system the customer came from
            external_id: The customer's ID in that external system
        """
        client, workspace_slug = get_plane_client_context()
        if customer_id:
            client.customers.delete(workspace_slug=workspace_slug, customer_id=customer_id)
            return
        if external_source and external_id:
            client.customers.delete_by_external_id(
                workspace_slug=workspace_slug,
                external_source=external_source,
                external_id=external_id,
            )
            return
        raise ToolError("Provide customer_id, or both external_source and external_id.")
