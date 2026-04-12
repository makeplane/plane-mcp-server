"""Work item property-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.enums import PropertyType, RelationType
from plane.models.work_item_properties import (
    CreateWorkItemProperty,
    CreateWorkItemPropertyOption,
    PropertySettings,
    UpdateWorkItemProperty,
    WorkItemProperty,
)
from plane.models.work_item_property_configurations import (
    DateAttributeSettings,
    TextAttributeSettings,
)

from plane_mcp.client import get_plane_client_context


def register_work_item_property_tools(mcp: FastMCP) -> None:
    """
    Create a new work item property for the given project and type.
    
    Parameters:
        project_id (str): UUID of the project.
        type_id (str): UUID of the work item type.
        display_name (str): Display name for the property.
        property_type (str): Property type (e.g., "TEXT", "DATETIME", "DECIMAL", "BOOLEAN", "OPTION", "RELATION", "URL", "EMAIL", "FILE").
        relation_type (str | None): Relation type (e.g., "ISSUE", "USER"); required when `property_type` is "RELATION".
        description (str | None): Property description.
        is_required (bool | None): Whether the property is required.
        default_value (list[str] | None): Default value(s) for the property.
        settings (dict | None): Typed settings for specific property types; for "TEXT" provide {"display_format": "single-line" | "multi-line" | "readonly"}, for "DATETIME" provide {"display_format": "MMM dd, yyyy" | "dd/MM/yyyy" | "MM/dd/yyyy" | "yyyy/MM/dd"}.
        is_active (bool | None): Whether the property is active.
        is_multi (bool | None): Whether the property supports multiple values.
        validation_rules (dict | None): Validation rules for the property.
        external_source (str | None): External system source name.
        external_id (str | None): External system identifier.
        options (list[dict] | None): List of option dicts; required when `property_type` is "OPTION".
    
    Returns:
        WorkItemProperty: The created work item property object.
    """
    """
    Update an existing work item property.
    
    Parameters:
        project_id (str): UUID of the project.
        type_id (str): UUID of the work item type.
        work_item_property_id (str): UUID of the property to update.
        display_name (str | None): New display name for the property.
        property_type (str | None): New property type (see `create_work_item_property` for allowed values).
        relation_type (str | None): Relation type when updating to a relation property (e.g., "ISSUE", "USER").
        description (str | None): New property description.
        is_required (bool | None): Whether the property is required.
        default_value (list[str] | None): Default value(s) for the property.
        settings (dict | None): Typed settings when updating to "TEXT" or "DATETIME" (see `create_work_item_property`).
        is_active (bool | None): Whether the property is active.
        is_multi (bool | None): Whether the property supports multiple values.
        validation_rules (dict | None): Validation rules for the property.
        external_source (str | None): External system source name.
        external_id (str | None): External system identifier.
    
    Returns:
        WorkItemProperty: The updated work item property object.
    """
    """
    Delete a work item property by its identifier.
    
    Parameters:
        project_id (str): UUID of the project.
        type_id (str): UUID of the work item type.
        work_item_property_id (str): UUID of the property to delete.
    """

    @mcp.tool()
    def create_work_item_property(
        project_id: str,
        type_id: str,
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
        options: list[dict] | None = None,
    ) -> WorkItemProperty:
        """
        Create a new work item property in the current workspace and project.
        
        Parameters:
            project_id (str): UUID of the project that will contain the property.
            type_id (str): UUID of the work item type to attach the property to.
            display_name (str): Human-readable name for the property.
            property_type (str): Property type name. One of: "TEXT", "DATETIME", "DECIMAL", "BOOLEAN", "OPTION", "RELATION", "URL", "EMAIL", "FILE".
            relation_type (str | None): Relation kind required when `property_type` is "RELATION" (e.g., "ISSUE", "USER").
            description (str | None): Optional description explaining the property's purpose.
            is_required (bool | None): Whether the property must have a value.
            default_value (list[str] | None): Default value(s) for the property.
            settings (dict | None): Type-specific settings. For "TEXT", supply {"display_format": "single-line" | "multi-line" | "readonly"}. For "DATETIME", supply {"display_format": "MMM dd, yyyy" | "dd/MM/yyyy" | "MM/dd/yyyy" | "yyyy/MM/dd"}.
            is_active (bool | None): Whether the property is active and available for use.
            is_multi (bool | None): Whether the property accepts multiple values.
            validation_rules (dict | None): Optional validation rules for the property values.
            external_source (str | None): External system source name, if the property is synced from outside.
            external_id (str | None): Identifier in the external system.
            options (list[dict] | None): For "OPTION" properties, a list of option definitions (each dict will be converted to a CreateWorkItemPropertyOption).
        
        Returns:
            WorkItemProperty: The created work item property object.
        """
        client, workspace_slug = get_plane_client_context()

        # Convert string to PropertyType enum
        validated_property_type = PropertyType(property_type)

        # Convert string to RelationType enum if provided
        validated_relation_type: RelationType | None = None
        if relation_type:
            validated_relation_type = RelationType(relation_type)

        # Convert settings dict to appropriate settings object if needed
        processed_settings: PropertySettings = None
        if settings:
            if property_type == "TEXT":
                processed_settings = TextAttributeSettings(**settings)
            elif property_type == "DATETIME":
                processed_settings = DateAttributeSettings(**settings)

        # Convert options dicts to CreateWorkItemPropertyOption objects
        processed_options: list[CreateWorkItemPropertyOption] | None = None
        if options:
            processed_options = [CreateWorkItemPropertyOption(**opt) for opt in options]

        data = CreateWorkItemProperty(
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
            options=processed_options,
        )

        return client.work_item_properties.create(
            workspace_slug=workspace_slug, project_id=project_id, type_id=type_id, data=data
        )

    @mcp.tool()
    def update_work_item_property(
        project_id: str,
        type_id: str,
        work_item_property_id: str,
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
    ) -> WorkItemProperty:
        """
        Update an existing work item property for a work item type within a project.
        
        Parameters:
            project_id: UUID of the project containing the work item type.
            type_id: UUID of the work item type that owns the property.
            work_item_property_id: UUID of the work item property to update.
            display_name: New display name for the property.
            property_type: Property type name (e.g., "TEXT", "DATETIME", "DECIMAL",
                "BOOLEAN", "OPTION", "RELATION", "URL", "EMAIL", "FILE").
            relation_type: Relation type name (e.g., "ISSUE", "USER"); required when changing
                the property to a relation type.
            description: New description for the property.
            is_required: Whether the property is required.
            default_value: Default value or list of default values for the property.
            settings: Settings dictionary for the property. When `property_type` is "TEXT",
                expected keys include `display_format` with values "single-line", "multi-line",
                or "readonly". When `property_type` is "DATETIME", expected key `display_format`
                may be a date format string such as "MMM dd, yyyy", "dd/MM/yyyy", "MM/dd/yyyy",
                or "yyyy/MM/dd".
            is_active: Whether the property is active.
            is_multi: Whether the property supports multiple values.
            validation_rules: Validation rules dictionary for the property.
            external_source: External system source name.
            external_id: External system identifier.
        
        Returns:
            Updated WorkItemProperty object.
        """
        client, workspace_slug = get_plane_client_context()

        # Convert string to PropertyType enum if provided
        validated_property_type: PropertyType | None = None
        if property_type:
            validated_property_type = PropertyType(property_type)

        # Convert string to RelationType enum if provided
        validated_relation_type: RelationType | None = None
        if relation_type:
            validated_relation_type = RelationType(relation_type)

        # Convert settings dict to appropriate settings object if needed
        processed_settings: PropertySettings = None
        if settings and property_type:
            if property_type == "TEXT":
                processed_settings = TextAttributeSettings(**settings)
            elif property_type == "DATETIME":
                processed_settings = DateAttributeSettings(**settings)

        data = UpdateWorkItemProperty(
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
