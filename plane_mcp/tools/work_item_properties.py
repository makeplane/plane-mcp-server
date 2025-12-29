"""Work item property-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.enums import PropertyType, RelationType
from plane.models.work_item_properties import (
    CreateWorkItemProperty,
    UpdateWorkItemProperty,
    WorkItemProperty,
)
from plane.models.work_item_property_configurations import (
    DateAttributeSettings,
    TextAttributeSettings,
)

from plane_mcp.client import get_plane_client_context

# Type alias for settings
PropertySettings = TextAttributeSettings | DateAttributeSettings | dict | None


def register_work_item_property_tools(mcp: FastMCP) -> None:
    """Register all work item property-related tools with the MCP server."""

    @mcp.tool()
    def list_work_item_properties(
        project_id: str,
        type_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[WorkItemProperty]:
        """
        List work item properties for a work item type.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            type_id: UUID of the work item type
            params: Optional query parameters as a dictionary

        Returns:
            List of WorkItemProperty objects
        """
        client, workspace_slug = get_plane_client_context()
        return client.work_item_properties.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            type_id=type_id,
            params=params,
        )

    @mcp.tool()
    def create_work_item_property(
        project_id: str,
        type_id: str,
        display_name: str,
        property_type: PropertyType | str,
        relation_type: RelationType | str | None = None,
        description: str | None = None,
        is_required: bool | None = None,
        default_value: list[str] | None = None,
        settings: dict | None = None,
        is_active: bool | None = None,
        is_multi: bool | None = None,
        validation_rules: dict | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
        options: list[dict] | None = None,
    ) -> WorkItemProperty:
        """
        Create a new work item property.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            type_id: UUID of the work item type
            display_name: Display name for the property
            property_type: Type of property (TEXT, DATETIME, DECIMAL, BOOLEAN, OPTION, RELATION, URL, EMAIL, FILE)
            relation_type: Relation type (ISSUE, USER) - required for RELATION properties
            description: Property description
            is_required: Whether the property is required
            default_value: Default value(s) for the property
            settings: Settings dictionary - required for TEXT and DATETIME properties
                     For TEXT: {"display_format": "single-line"|"multi-line"|"readonly"}
                     For DATETIME: {"display_format": "MMM dd, yyyy"|"dd/MM/yyyy"|"MM/dd/yyyy"|"yyyy/MM/dd"}
            is_active: Whether the property is active
            is_multi: Whether the property supports multiple values
            validation_rules: Validation rules dictionary
            external_source: External system source name
            external_id: External system identifier
            options: List of option dictionaries for OPTION properties

        Returns:
            Created WorkItemProperty object
        """
        client, workspace_slug = get_plane_client_context()

        # Convert settings dict to appropriate settings object if needed
        processed_settings: PropertySettings = None
        if settings:
            prop_type = (
                property_type.value if isinstance(property_type, PropertyType) else property_type
            )
            if prop_type == "TEXT" and isinstance(settings, dict):
                processed_settings = TextAttributeSettings(**settings)
            elif prop_type == "DATETIME" and isinstance(settings, dict):
                processed_settings = DateAttributeSettings(**settings)
            else:
                processed_settings = settings

        data = CreateWorkItemProperty(
            display_name=display_name,
            property_type=property_type,
            relation_type=relation_type,
            description=description,
            is_required=is_required,
            default_value=default_value,
            settings=processed_settings,
            is_active=is_active,
            is_multi=is_multi,
            validation_rules=validation_rules,
            external_source=external_source,
            external_id=external_id,
            options=options,
        )

        return client.work_item_properties.create(
            workspace_slug=workspace_slug, project_id=project_id, type_id=type_id, data=data
        )

    @mcp.tool()
    def retrieve_work_item_property(
        project_id: str,
        type_id: str,
        work_item_property_id: str,
    ) -> WorkItemProperty:
        """
        Retrieve a work item property by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            type_id: UUID of the work item type
            work_item_property_id: UUID of the property

        Returns:
            WorkItemProperty object
        """
        client, workspace_slug = get_plane_client_context()
        return client.work_item_properties.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            type_id=type_id,
            work_item_property_id=work_item_property_id,
        )

    @mcp.tool()
    def update_work_item_property(
        project_id: str,
        type_id: str,
        work_item_property_id: str,
        display_name: str | None = None,
        property_type: PropertyType | str | None = None,
        relation_type: RelationType | str | None = None,
        description: str | None = None,
        is_required: bool | None = None,
        default_value: list[str] | None = None,
        settings: dict | None = None,
        is_active: bool | None = None,
        is_multi: bool | None = None,
        validation_rules: dict | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> WorkItemProperty:
        """
        Update a work item property by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            type_id: UUID of the work item type
            work_item_property_id: UUID of the property
            display_name: Display name for the property
            property_type: Type of property (TEXT, DATETIME, DECIMAL, BOOLEAN, OPTION, RELATION, URL, EMAIL, FILE)
            relation_type: Relation type (ISSUE, USER) - required when updating to RELATION
            description: Property description
            is_required: Whether the property is required
            default_value: Default value(s) for the property
            settings: Settings dictionary - required when updating to TEXT or DATETIME
                     For TEXT: {"display_format": "single-line"|"multi-line"|"readonly"}
                     For DATETIME: {"display_format": "MMM dd, yyyy"|"dd/MM/yyyy"|"MM/dd/yyyy"|"yyyy/MM/dd"}
            is_active: Whether the property is active
            is_multi: Whether the property supports multiple values
            validation_rules: Validation rules dictionary
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Updated WorkItemProperty object
        """
        client, workspace_slug = get_plane_client_context()

        # Convert settings dict to appropriate settings object if needed
        processed_settings: PropertySettings = None
        if settings and property_type:
            prop_type = (
                property_type.value if isinstance(property_type, PropertyType) else property_type
            )
            if prop_type == "TEXT" and isinstance(settings, dict):
                processed_settings = TextAttributeSettings(**settings)
            elif prop_type == "DATETIME" and isinstance(settings, dict):
                processed_settings = DateAttributeSettings(**settings)
            else:
                processed_settings = settings

        data = UpdateWorkItemProperty(
            display_name=display_name,
            property_type=property_type,
            relation_type=relation_type,
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

        return client.work_item_properties.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            type_id=type_id,
            work_item_property_id=work_item_property_id,
            data=data,
        )

    @mcp.tool()
    def delete_work_item_property(
        project_id: str,
        type_id: str,
        work_item_property_id: str,
    ) -> None:
        """
        Delete a work item property by ID.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            type_id: UUID of the work item type
            work_item_property_id: UUID of the property
        """
        client, workspace_slug = get_plane_client_context()
        client.work_item_properties.delete(
            workspace_slug=workspace_slug,
            project_id=project_id,
            type_id=type_id,
            work_item_property_id=work_item_property_id,
        )
