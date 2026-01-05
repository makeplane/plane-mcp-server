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
        """List work item properties for a work item type."""
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
        """Create a new work item property. Property types: TEXT, DATETIME, DECIMAL, BOOLEAN, OPTION, RELATION, URL, EMAIL, FILE."""
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
        """Retrieve a work item property by ID."""
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
        """Update a work item property by ID."""
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
        """Delete a work item property by ID."""
        client, workspace_slug = get_plane_client_context()
        client.work_item_properties.delete(
            workspace_slug=workspace_slug,
            project_id=project_id,
            type_id=type_id,
            work_item_property_id=work_item_property_id,
        )
