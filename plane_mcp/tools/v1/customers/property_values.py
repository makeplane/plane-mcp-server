"""Customer property value tools for Plane MCP Server."""

from fastmcp import FastMCP
from plane.models.customers import SetCustomerPropertyValues

from plane_mcp.client import get_plane_client_context


def register_customer_property_value_tools(mcp: FastMCP) -> None:
    """Register the customer property value tools with the MCP server."""

    @mcp.tool()
    def get_customer_property_values(
        customer_id: str,
        property_id: str | None = None,
    ) -> dict[str, list[str]]:
        """
        Read the custom property values a customer holds.

        Args:
            customer_id: UUID of the customer
            property_id: UUID of one customer property; omit to read them all

        Returns:
            Property UUID mapped to its values. Properties with no value set, and
            inactive properties, are absent.
        """
        client, workspace_slug = get_plane_client_context()
        values = client.customers.property_values
        if property_id:
            return {property_id: values.retrieve(workspace_slug, customer_id, property_id)}
        return values.list(workspace_slug, customer_id)

    @mcp.tool()
    def set_customer_property_values(
        customer_id: str,
        values: dict[str, list[str]],
    ) -> None:
        """
        Set a customer's custom property values, replacing any current ones.

        Only the properties named are touched; the rest keep their values. Every
        value is a string, whatever the property's type:
            TEXT/URL/EMAIL/FILE: the text
            DATETIME: "YYYY-MM-DD"
            DECIMAL: the number as a string, e.g. "12.5"
            BOOLEAN: "True" or "False"
            OPTION/RELATION: the option or related record's UUID
        Pass a single-item list for properties that are not is_multi.

        Args:
            customer_id: UUID of the customer
            values: Property UUID mapped to its new values,
                e.g. {"<property_id>": ["Enterprise"]}
        """
        client, workspace_slug = get_plane_client_context()
        client.customers.property_values.create(
            workspace_slug,
            customer_id,
            SetCustomerPropertyValues(customer_property_values=values),
        )
