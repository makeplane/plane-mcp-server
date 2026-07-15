"""Customer-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.customers import (
    CreateCustomer,
    CreateCustomerProperty,
    Customer,
    CustomerProperty,
    CustomerRequest,
    PaginatedCustomerPropertyResponse,
    PaginatedCustomerResponse,
    UpdateCustomer,
    UpdateCustomerProperty,
    UpdateCustomerRequest,
)
from plane.models.enums import PropertyType, RelationType
from plane.models.work_item_properties import PropertySettings
from plane.models.work_item_property_configurations import (
    DateAttributeSettings,
    TextAttributeSettings,
)

from plane_mcp.client import get_plane_client_context


def register_customer_tools(mcp: FastMCP) -> None:
    """Register all customer-related tools with the MCP server."""

    # --- Customers -----------------------------------------------------------

    @mcp.tool()
    def list_customers(
        params: dict[str, Any] | None = None,
    ) -> list[Customer]:
        """
        List all customers in the workspace.

        Args:
            params: Optional query parameters as a dictionary (e.g., per_page, cursor)

        Returns:
            List of Customer objects
        """
        client, workspace_slug = get_plane_client_context()
        response: PaginatedCustomerResponse = client.customers.list(workspace_slug=workspace_slug, params=params)
        return response.results

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
        logo_props: dict | None = None,
    ) -> Customer:
        """
        Create a new customer in the workspace.

        Args:
            name: Customer name
            description_html: HTML description of the customer
            email: Primary contact email
            website_url: Customer website URL
            domain: Customer domain (e.g. "acme.com")
            employees: Number of employees
            stage: Customer lifecycle stage
            contract_status: Contract status
            revenue: Customer revenue (string, e.g. "1000000")
            logo_props: Logo properties dictionary

        Returns:
            Created Customer object
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
            logo_props=logo_props,
        )
        return client.customers.create(workspace_slug=workspace_slug, data=data)

    @mcp.tool()
    def retrieve_customer(customer_id: str) -> Customer:
        """
        Retrieve a customer by ID.

        Args:
            customer_id: UUID of the customer

        Returns:
            Customer object
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
    ) -> Customer:
        """
        Update a customer by ID.

        Args:
            customer_id: UUID of the customer
            name: Customer name
            description_html: HTML description of the customer
            email: Primary contact email
            website_url: Customer website URL
            domain: Customer domain (e.g. "acme.com")
            employees: Number of employees
            stage: Customer lifecycle stage
            contract_status: Contract status
            revenue: Customer revenue (string, e.g. "1000000")
            logo_props: Logo properties dictionary

        Returns:
            Updated Customer object
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
        )
        return client.customers.update(workspace_slug=workspace_slug, customer_id=customer_id, data=data)

    @mcp.tool()
    def delete_customer(customer_id: str) -> None:
        """
        Delete a customer by ID.

        Args:
            customer_id: UUID of the customer
        """
        client, workspace_slug = get_plane_client_context()
        client.customers.delete(workspace_slug=workspace_slug, customer_id=customer_id)

    # --- Customer properties -------------------------------------------------

    @mcp.tool()
    def list_customer_properties(
        params: dict[str, Any] | None = None,
    ) -> list[CustomerProperty]:
        """
        List all customer properties in the workspace.

        Customer properties are custom fields defined at the workspace level and
        applied to every customer.

        Args:
            params: Optional query parameters as a dictionary (e.g., per_page, cursor)

        Returns:
            List of CustomerProperty objects
        """
        client, workspace_slug = get_plane_client_context()
        response: PaginatedCustomerPropertyResponse = client.customers.properties.list(
            workspace_slug=workspace_slug, params=params
        )
        return response.results

    @mcp.tool()
    def create_customer_property(
        name: str,
        display_name: str,
        property_type: str,
        relation_type: str | None = None,
        description: str | None = None,
        is_required: bool | None = None,
        default_value: list[str] | None = None,
        settings: dict | None = None,
        is_active: bool | None = None,
        is_multi: bool | None = None,
        validation_rules: dict | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> CustomerProperty:
        """
        Create a new customer property (custom field on customers).

        Args:
            name: Internal name for the property
            display_name: User-facing label for the property
            property_type: TEXT | DATETIME | DECIMAL | BOOLEAN | OPTION | RELATION | URL | EMAIL | FILE | FORMULA
            relation_type: ISSUE | USER — required when property_type=RELATION
            description: Property description
            is_required: Whether the property is required
            default_value: Default value(s) for the property
            settings: Required for TEXT/DATETIME.
                TEXT:     {"display_format": "single-line"|"multi-line"|"readonly"}
                DATETIME: {"display_format": "MMM dd, yyyy"|"dd/MM/yyyy"|"MM/dd/yyyy"|"yyyy/MM/dd"}
            is_active: Whether the property is active
            is_multi: Whether the property supports multiple values
            validation_rules: Validation rules dictionary
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Created CustomerProperty object
        """
        client, workspace_slug = get_plane_client_context()

        validated_property_type = PropertyType(property_type)

        validated_relation_type: RelationType | None = None
        if relation_type:
            validated_relation_type = RelationType(relation_type)

        processed_settings: PropertySettings = None
        if settings:
            if property_type == "TEXT":
                processed_settings = TextAttributeSettings(**settings)
            elif property_type == "DATETIME":
                processed_settings = DateAttributeSettings(**settings)

        data = CreateCustomerProperty(
            name=name,
            display_name=display_name,
            property_type=validated_property_type,
            relation_type=validated_relation_type,
            description=description,
            is_required=is_required,
            default_value=default_value,
            settings=processed_settings,
            is_active=is_active,
            is_multi=is_multi,
            validation_rules=validation_rules,
            external_source=external_source,
            external_id=external_id,
        )
        return client.customers.properties.create(workspace_slug=workspace_slug, data=data)

    @mcp.tool()
    def retrieve_customer_property(property_id: str) -> CustomerProperty:
        """
        Retrieve a customer property by ID.

        Args:
            property_id: UUID of the customer property

        Returns:
            CustomerProperty object
        """
        client, workspace_slug = get_plane_client_context()
        return client.customers.properties.retrieve(workspace_slug=workspace_slug, property_id=property_id)

    @mcp.tool()
    def update_customer_property(
        property_id: str,
        display_name: str | None = None,
        property_type: str | None = None,
        relation_type: str | None = None,
        description: str | None = None,
        is_required: bool | None = None,
        default_value: list[str] | None = None,
        settings: dict | None = None,
        is_active: bool | None = None,
        is_multi: bool | None = None,
        validation_rules: dict | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> CustomerProperty:
        """
        Update a customer property by ID.

        Args:
            property_id: UUID of the customer property
            display_name: User-facing label for the property
            property_type: TEXT | DATETIME | DECIMAL | BOOLEAN | OPTION | RELATION | URL | EMAIL | FILE | FORMULA
            relation_type: ISSUE | USER — required when updating to RELATION
            description: Property description
            is_required: Whether the property is required
            default_value: Default value(s) for the property
            settings: Required when changing type to TEXT/DATETIME.
                TEXT:     {"display_format": "single-line"|"multi-line"|"readonly"}
                DATETIME: {"display_format": "MMM dd, yyyy"|"dd/MM/yyyy"|"MM/dd/yyyy"|"yyyy/MM/dd"}
            is_active: Whether the property is active
            is_multi: Whether the property supports multiple values
            validation_rules: Validation rules dictionary
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Updated CustomerProperty object
        """
        client, workspace_slug = get_plane_client_context()

        validated_property_type: PropertyType | None = None
        if property_type:
            validated_property_type = PropertyType(property_type)

        validated_relation_type: RelationType | None = None
        if relation_type:
            validated_relation_type = RelationType(relation_type)

        processed_settings: PropertySettings = None
        if settings and property_type:
            if property_type == "TEXT":
                processed_settings = TextAttributeSettings(**settings)
            elif property_type == "DATETIME":
                processed_settings = DateAttributeSettings(**settings)

        data = UpdateCustomerProperty(
            display_name=display_name,
            property_type=validated_property_type,
            relation_type=validated_relation_type,
            description=description,
            is_required=is_required,
            default_value=default_value,
            settings=processed_settings,
            is_active=is_active,
            is_multi=is_multi,
            validation_rules=validation_rules,
            external_source=external_source,
            external_id=external_id,
        )
        return client.customers.properties.update(workspace_slug=workspace_slug, property_id=property_id, data=data)

    @mcp.tool()
    def delete_customer_property(property_id: str) -> None:
        """
        Delete a customer property by ID.

        Args:
            property_id: UUID of the customer property
        """
        client, workspace_slug = get_plane_client_context()
        client.customers.properties.delete(workspace_slug=workspace_slug, property_id=property_id)

    # --- Customer requests ---------------------------------------------------

    @mcp.tool()
    def list_customer_requests(
        customer_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[CustomerRequest]:
        """
        List all requests belonging to a customer.

        A customer request captures a customer's ask and can reference the work
        items that address it via `work_item_ids`.

        Args:
            customer_id: UUID of the customer
            params: Optional query parameters as a dictionary (e.g., per_page, cursor)

        Returns:
            List of CustomerRequest objects
        """
        client, workspace_slug = get_plane_client_context()
        return client.customers.requests.list(workspace_slug=workspace_slug, customer_id=customer_id, params=params)

    @mcp.tool()
    def create_customer_request(
        customer_id: str,
        name: str,
        description_html: str | None = None,
        link: str | None = None,
        work_item_ids: list[str] | None = None,
    ) -> CustomerRequest:
        """
        Create a request for a customer.

        Args:
            customer_id: UUID of the customer
            name: Request name
            description_html: HTML description of the request
            link: Optional URL associated with the request
            work_item_ids: UUIDs of work items that address this request

        Returns:
            Created CustomerRequest object
        """
        client, workspace_slug = get_plane_client_context()
        data = CustomerRequest(
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
            CustomerRequest object
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
        work_item_ids: list[str] | None = None,
    ) -> CustomerRequest:
        """
        Update a customer request by ID.

        Args:
            customer_id: UUID of the customer
            request_id: UUID of the customer request
            name: Request name
            description_html: HTML description of the request
            link: Optional URL associated with the request
            work_item_ids: UUIDs of work items that address this request

        Returns:
            Updated CustomerRequest object
        """
        client, workspace_slug = get_plane_client_context()
        data = UpdateCustomerRequest(
            name=name,
            description_html=description_html,
            link=link,
            work_item_ids=work_item_ids,
        )
        return client.customers.requests.update(
            workspace_slug=workspace_slug,
            customer_id=customer_id,
            request_id=request_id,
            data=data,
        )

    @mcp.tool()
    def delete_customer_request(customer_id: str, request_id: str) -> None:
        """
        Delete a customer request by ID.

        Args:
            customer_id: UUID of the customer
            request_id: UUID of the customer request
        """
        client, workspace_slug = get_plane_client_context()
        client.customers.requests.delete(workspace_slug=workspace_slug, customer_id=customer_id, request_id=request_id)
