"""Customer property tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.models.customers import (
    CreateCustomerProperty,
    CustomerProperty,
    PaginatedCustomerPropertyResponse,
    UpdateCustomerProperty,
)
from plane.models.enums import PropertyType, RelationType

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.customers._helpers import build_settings, page_params


def register_customer_property_tools(mcp: FastMCP) -> None:
    """Register the customer property tools with the MCP server."""

    @mcp.tool()
    def list_customer_properties(
        cursor: str | None = None,
        per_page: int | None = None,
    ) -> PaginatedCustomerPropertyResponse:
        """
        List customer properties (paginated).

        Customer properties are custom fields defined once per workspace and applied
        to every customer. Use get_customer_property_values to read what a specific
        customer holds for them.

        Args:
            cursor: Prior response's next_cursor; omit for first page.
            per_page: Results per page (1-1000, default 1000).

        Returns:
            Paginated envelope: results + total_count, next_cursor,
            next_page_results (page again while next_page_results is true).
        """
        client, workspace_slug = get_plane_client_context()
        return client.customers.properties.list(workspace_slug=workspace_slug, params=page_params(cursor, per_page))

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
        options: list[dict] | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> CustomerProperty:
        """
        Create a customer property (a workspace-wide custom field on customers).

        property_type, is_multi and settings are fixed once created — recreate the
        property to change them.

        Args:
            name: Required, but discarded — the stored name is a slug of display_name.
                Pass display_name here too if you have nothing else.
            display_name: User-facing label. Must be unique in the workspace: it derives
                the property's slug, so a display name already in use is rejected.
            property_type: TEXT | DATETIME | DECIMAL | BOOLEAN | OPTION | RELATION | URL | EMAIL | FILE
            relation_type: ISSUE | USER — required when property_type=RELATION
            description: Property description
            is_required: Whether a value must be set; forces default_value to be empty
            default_value: Default value(s); at most one unless is_multi
            settings: Required for TEXT/DATETIME.
                TEXT:     {"display_format": "single-line"|"multi-line"|"readonly"}
                DATETIME: {"display_format": "MMM dd, yyyy"|"dd/MM/yyyy"|"MM/dd/yyyy"|"yyyy/MM/dd"}
            is_active: Whether the property is active
            is_multi: Whether the property holds multiple values
            options: For OPTION type — [{"name": str, "description"?: str, "is_default"?: bool}]
            external_source: External system this property came from, e.g. "salesforce"
            external_id: The property's ID in that external system

        Returns:
            The created CustomerProperty, including its options for OPTION type
        """
        client, workspace_slug = get_plane_client_context()
        data = CreateCustomerProperty(
            name=name,
            display_name=display_name,
            property_type=PropertyType(property_type),
            relation_type=RelationType(relation_type) if relation_type else None,
            description=description,
            is_required=is_required,
            default_value=default_value,
            settings=build_settings(property_type, settings),
            is_active=is_active,
            is_multi=is_multi,
            options=options,
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
            The CustomerProperty, including its options for OPTION type
        """
        client, workspace_slug = get_plane_client_context()
        return client.customers.properties.retrieve(workspace_slug=workspace_slug, property_id=property_id)

    @mcp.tool()
    def update_customer_property(
        property_id: str,
        display_name: str | None = None,
        relation_type: str | None = None,
        description: str | None = None,
        is_required: bool | None = None,
        default_value: list[str] | None = None,
        is_active: bool | None = None,
        options: list[dict] | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> CustomerProperty:
        """
        Update a customer property. Only the fields you pass are changed.

        property_type, is_multi and settings cannot be changed after creation — delete
        and recreate the property instead.

        Args:
            property_id: UUID of the customer property
            display_name: User-facing label. Also re-slugs the property's internal name,
                so it must stay unique in the workspace.
            relation_type: ISSUE | USER — only for RELATION properties
            description: Property description
            is_required: Whether a value must be set; forces default_value to be empty
            default_value: Default value(s); at most one unless the property is is_multi
            is_active: Whether the property is active
            options: For OPTION type — [{"id": str, ...}] to edit an existing option,
                or [{"name": str, ...}] without an id to add one
            external_source: External system this property came from
            external_id: The property's ID in that external system

        Returns:
            The updated CustomerProperty
        """
        client, workspace_slug = get_plane_client_context()
        data = UpdateCustomerProperty(
            display_name=display_name,
            relation_type=RelationType(relation_type) if relation_type else None,
            description=description,
            is_required=is_required,
            default_value=default_value,
            is_active=is_active,
            options=options,
            external_source=external_source,
            external_id=external_id,
        )
        return client.customers.properties.update(workspace_slug=workspace_slug, property_id=property_id, data=data)

    @mcp.tool()
    def delete_customer_property(property_id: str) -> None:
        """
        Delete a customer property, and every customer's values for it.

        Args:
            property_id: UUID of the customer property
        """
        client, workspace_slug = get_plane_client_context()
        client.customers.properties.delete(workspace_slug=workspace_slug, property_id=property_id)
