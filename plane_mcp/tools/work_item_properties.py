"""Work item property-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.enums import PropertyType, RelationType
from plane.models.work_item_properties import (
    CreateWorkItemProperty,
    CreateWorkItemPropertyOption,
    CreateWorkItemPropertyValue,
    PropertySettings,
    UpdateWorkItemProperty,
    UpdateWorkItemPropertyOption,
    WorkItemProperty,
    WorkItemPropertyOption,
    WorkItemPropertyValueDetail,
)
from plane.models.work_item_property_configurations import (
    DateAttributeSettings,
    TextAttributeSettings,
)

from plane_mcp.client import get_plane_client_context


def register_work_item_property_tools(mcp: FastMCP) -> None:
    """Register all work item property-related tools with the MCP server."""

    @mcp.tool()
    def list_work_item_properties(
        work_item_type_id: str,
        project_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[WorkItemProperty]:
        """
        List custom properties for a work item type.

        Lookup order when project_id provided:
          1. Type-scoped endpoint (properties explicitly linked to the type)
          2. Flat project endpoint (properties created without type association)
          3. Workspace-level endpoint (workspace-wide properties)

        Omit project_id to query workspace scope directly.

        Each result includes:
        - id: property UUID — use as cf["<id>"] in PQL filters
        - property_type: TEXT | OPTION | DECIMAL | BOOLEAN | DATETIME | RELATION | URL | EMAIL
        - options: for OPTION type, each option has id + name; use option id in PQL

        PQL workflow for filtering by custom property:
          1. list_work_item_types(project_id)               → get type UUIDs
          2. list_work_item_properties(work_item_type_id, project_id) → get property + option UUIDs
          3. list_work_items(pql='cf["<prop-uuid>"] = "<opt-uuid>"')
        """
        client, workspace_slug = get_plane_client_context()

        def _get_workspace_props_for_type(type_id: str) -> list:
            """Fetch workspace-level properties associated with a type. Returns [] on any error."""
            try:
                # API returns flat list of UUID strings, not full property objects
                property_ids = client.workspace_work_item_types.properties.list(
                    workspace_slug=workspace_slug,
                    type_id=type_id,
                )
                if not property_ids:
                    return []
                id_set = {str(pid) for pid in property_ids}
                all_props = client.workspace_work_item_properties.list(workspace_slug=workspace_slug)
                return [p for p in all_props if str(p.id) in id_set]
            except Exception:
                return []

        if not project_id:
            return _get_workspace_props_for_type(work_item_type_id)

        # Try type-scoped project endpoint first
        project_props = client.work_item_properties.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            type_id=work_item_type_id,
            params=params,
        )
        if project_props:
            return project_props

        # Fall back to flat project endpoint (properties created without type association)
        try:
            flat_props = client.work_item_properties.list_project(
                workspace_slug=workspace_slug,
                project_id=project_id,
                params=params,
            )
            if flat_props:
                return flat_props
        except Exception:
            pass

        # Last resort: workspace-level
        return _get_workspace_props_for_type(work_item_type_id)

    @mcp.tool()
    def create_work_item_property(
        display_name: str,
        property_type: str,
        project_id: str | None = None,
        work_item_type_id: str | None = None,
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
        Create a new work item property.

        Scope resolution:
          - project_id + work_item_type_id → type-scoped project property (legacy)
          - project_id only               → project-level property (not yet linked to a type)
          - neither                        → workspace-level property

        Args:
            display_name: Display name for the property
            property_type: TEXT | DATETIME | DECIMAL | BOOLEAN | OPTION | RELATION | URL | EMAIL | FILE | FORMULA
            project_id: UUID of the project. Omit for workspace-level property.
            work_item_type_id: UUID of the work item type — omit to create at project level
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
            options: List of {name, color?, is_default?} dicts — for OPTION type

        Returns:
            Created WorkItemProperty object
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

        if project_id and work_item_type_id:
            return client.work_item_properties.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                type_id=work_item_type_id,
                data=data,
            )
        if project_id:
            return client.work_item_properties.create_project(
                workspace_slug=workspace_slug,
                project_id=project_id,
                data=data,
            )
        return client.workspace_work_item_properties.create(
            workspace_slug=workspace_slug, data=data
        )

    @mcp.tool()
    def retrieve_work_item_property(
        work_item_property_id: str,
        project_id: str | None = None,
        work_item_type_id: str | None = None,
    ) -> WorkItemProperty:
        """
        Retrieve a work item property by ID.

        Args:
            work_item_property_id: UUID of the property
            project_id: UUID of the project. Omit for workspace scope.
            work_item_type_id: UUID of the work item type — omit to use project-level endpoint

        Returns:
            WorkItemProperty object
        """
        client, workspace_slug = get_plane_client_context()
        if project_id and work_item_type_id:
            return client.work_item_properties.retrieve(
                workspace_slug=workspace_slug,
                project_id=project_id,
                type_id=work_item_type_id,
                work_item_property_id=work_item_property_id,
            )
        if project_id:
            return client.work_item_properties.retrieve_project(
                workspace_slug=workspace_slug,
                project_id=project_id,
                property_id=work_item_property_id,
            )
        return client.workspace_work_item_properties.retrieve(
            workspace_slug=workspace_slug,
            property_id=work_item_property_id,
        )

    @mcp.tool()
    def update_work_item_property(
        work_item_property_id: str,
        project_id: str | None = None,
        work_item_type_id: str | None = None,
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
        Update a work item property by ID.

        Args:
            work_item_property_id: UUID of the property
            project_id: UUID of the project. Omit for workspace scope.
            work_item_type_id: UUID of the work item type — required when project_id is provided
            display_name: Display name for the property
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
            Updated WorkItemProperty object
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

        if project_id and work_item_type_id:
            return client.work_item_properties.update(
                workspace_slug=workspace_slug,
                project_id=project_id,
                type_id=work_item_type_id,
                work_item_property_id=work_item_property_id,
                data=data,
            )
        if project_id:
            return client.work_item_properties.update_project(
                workspace_slug=workspace_slug,
                project_id=project_id,
                property_id=work_item_property_id,
                data=data,
            )
        return client.workspace_work_item_properties.update(
            workspace_slug=workspace_slug,
            property_id=work_item_property_id,
            data=data,
        )

    @mcp.tool()
    def delete_work_item_property(
        work_item_property_id: str,
        project_id: str | None = None,
        work_item_type_id: str | None = None,
    ) -> None:
        """
        Delete a work item property by ID.

        Args:
            work_item_property_id: UUID of the property
            project_id: UUID of the project. Omit for workspace scope.
            work_item_type_id: UUID of the work item type — omit to use project-level endpoint
        """
        client, workspace_slug = get_plane_client_context()
        if project_id and work_item_type_id:
            client.work_item_properties.delete(
                workspace_slug=workspace_slug,
                project_id=project_id,
                type_id=work_item_type_id,
                work_item_property_id=work_item_property_id,
            )
        elif project_id:
            client.work_item_properties.delete_project(
                workspace_slug=workspace_slug,
                project_id=project_id,
                property_id=work_item_property_id,
            )
        else:
            client.workspace_work_item_properties.delete(
                workspace_slug=workspace_slug,
                property_id=work_item_property_id,
            )

    @mcp.tool()
    def attach_properties_to_work_item_type(
        project_id: str,
        work_item_type_id: str,
        property_ids: list[str],
    ) -> list[str]:
        """
        Attach one or more existing project-level properties to a work item type.

        Use after creating properties via create_work_item_property (project-level)
        to associate them with a specific type.

        Args:
            project_id: UUID of the project
            work_item_type_id: UUID of the work item type
            property_ids: List of property UUIDs to attach

        Returns:
            List of attached property UUIDs
        """
        client, workspace_slug = get_plane_client_context()
        return client.work_item_properties.attach_to_type(
            workspace_slug=workspace_slug,
            project_id=project_id,
            type_id=work_item_type_id,
            property_ids=property_ids,
        )

    @mcp.tool()
    def detach_property_from_work_item_type(
        project_id: str,
        work_item_type_id: str,
        work_item_property_id: str,
    ) -> None:
        """
        Detach a property from a work item type (does not delete the property).

        Args:
            project_id: UUID of the project
            work_item_type_id: UUID of the work item type
            work_item_property_id: UUID of the property to detach
        """
        client, workspace_slug = get_plane_client_context()
        client.work_item_properties.detach_from_type(
            workspace_slug=workspace_slug,
            project_id=project_id,
            type_id=work_item_type_id,
            property_id=work_item_property_id,
        )

    @mcp.tool()
    def list_work_item_property_options(
        property_id: str,
        project_id: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> list[WorkItemPropertyOption]:
        """
        List options for a work item property.

        Args:
            property_id: UUID of the work item property
            project_id: UUID of the project. Omit for workspace scope.
            params: Optional query parameters

        Returns:
            List of WorkItemPropertyOption objects
        """
        client, workspace_slug = get_plane_client_context()
        if project_id:
            return client.work_item_properties.options.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                property_id=property_id,
                params=params,
            )
        return client.workspace_work_item_properties.options.list(
            workspace_slug=workspace_slug,
            property_id=property_id,
        )

    @mcp.tool()
    def retrieve_work_item_property_option(
        property_id: str,
        option_id: str,
        project_id: str | None = None,
    ) -> WorkItemPropertyOption:
        """
        Retrieve a single option from a work item property.

        Args:
            property_id: UUID of the work item property
            option_id: UUID of the option
            project_id: UUID of the project. Omit for workspace scope.

        Returns:
            WorkItemPropertyOption object
        """
        client, workspace_slug = get_plane_client_context()
        if project_id:
            return client.work_item_properties.options.retrieve(
                workspace_slug=workspace_slug,
                project_id=project_id,
                property_id=property_id,
                option_id=option_id,
            )
        return client.workspace_work_item_properties.options.retrieve(
            workspace_slug=workspace_slug,
            property_id=property_id,
            option_id=option_id,
        )

    @mcp.tool()
    def create_work_item_property_option(
        property_id: str,
        name: str,
        project_id: str | None = None,
        description: str | None = None,
        color: str | None = None,
        is_default: bool | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> WorkItemPropertyOption:
        """
        Create an option on a work item property.

        Args:
            property_id: UUID of the work item property
            name: Display name for the option
            project_id: UUID of the project. Omit for workspace scope.
            description: Option description
            color: Hex color string e.g. "#FF5733"
            is_default: Whether this is the default option
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Created WorkItemPropertyOption object
        """
        client, workspace_slug = get_plane_client_context()
        data = CreateWorkItemPropertyOption(
            name=name,
            description=description,
            color=color,
            is_default=is_default,
            external_source=external_source,
            external_id=external_id,
        )
        if project_id:
            return client.work_item_properties.options.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                property_id=property_id,
                data=data,
            )
        return client.workspace_work_item_properties.options.create(
            workspace_slug=workspace_slug,
            property_id=property_id,
            data=data,
        )

    @mcp.tool()
    def update_work_item_property_option(
        property_id: str,
        option_id: str,
        project_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        color: str | None = None,
        is_default: bool | None = None,
        external_source: str | None = None,
        external_id: str | None = None,
    ) -> WorkItemPropertyOption:
        """
        Update an option on a work item property.

        Args:
            property_id: UUID of the work item property
            option_id: UUID of the option
            project_id: UUID of the project. Omit for workspace scope.
            name: Display name for the option
            description: Option description
            color: Hex color string e.g. "#FF5733"
            is_default: Whether this is the default option
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Updated WorkItemPropertyOption object
        """
        client, workspace_slug = get_plane_client_context()
        data = UpdateWorkItemPropertyOption(
            name=name,
            description=description,
            color=color,
            is_default=is_default,
            external_source=external_source,
            external_id=external_id,
        )
        if project_id:
            return client.work_item_properties.options.update(
                workspace_slug=workspace_slug,
                project_id=project_id,
                property_id=property_id,
                option_id=option_id,
                data=data,
            )
        return client.workspace_work_item_properties.options.update(
            workspace_slug=workspace_slug,
            property_id=property_id,
            option_id=option_id,
            data=data,
        )

    @mcp.tool()
    def delete_work_item_property_option(
        property_id: str,
        option_id: str,
        project_id: str | None = None,
    ) -> None:
        """
        Delete an option from a work item property.

        Args:
            property_id: UUID of the work item property
            option_id: UUID of the option
            project_id: UUID of the project. Omit for workspace scope.
        """
        client, workspace_slug = get_plane_client_context()
        if project_id:
            client.work_item_properties.options.delete(
                workspace_slug=workspace_slug,
                project_id=project_id,
                property_id=property_id,
                option_id=option_id,
            )
        else:
            client.workspace_work_item_properties.options.delete(
                workspace_slug=workspace_slug,
                property_id=property_id,
                option_id=option_id,
            )

    @mcp.tool()
    def get_work_item_property_value(
        project_id: str,
        work_item_id: str,
        property_id: str,
    ) -> WorkItemPropertyValueDetail | list[WorkItemPropertyValueDetail]:
        """
        Get the value(s) of a custom property on a work item.

        Use list_work_item_properties to find the property_id for a given
        property name (e.g. "Acceptance Criteria").

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            property_id: UUID of the work item property

        Returns:
            Single WorkItemPropertyValueDetail for non-multi properties,
            or list of WorkItemPropertyValueDetail for multi-value properties
        """
        client, workspace_slug = get_plane_client_context()
        return client.work_item_properties.values.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            property_id=property_id,
        )

    @mcp.tool()
    def set_work_item_property_value(
        project_id: str,
        work_item_id: str,
        property_id: str,
        value: str | bool | int | float | list[str],
        external_id: str | None = None,
        external_source: str | None = None,
    ) -> WorkItemPropertyValueDetail | list[WorkItemPropertyValueDetail]:
        """
        Set (create or update) the value of a custom property on a work item.

        Acts as an upsert — creates the value if it does not exist, updates it
        if it does. For multi-value properties (is_multi=True), replaces all
        existing values with the new ones.

        Value types by property type:
            TEXT/URL/EMAIL/FILE: string
            DATETIME: string (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)
            DECIMAL: int or float
            BOOLEAN: true or false
            OPTION/RELATION (single): UUID string
            OPTION/RELATION (multi, is_multi=True): list of UUID strings

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            property_id: UUID of the work item property
            value: The value to set (type depends on the property type — see above)
            external_id: Optional external identifier for syncing
            external_source: Optional external source name (e.g. "github", "jira")

        Returns:
            Single WorkItemPropertyValueDetail for non-multi properties,
            or list of WorkItemPropertyValueDetail for multi-value properties
        """
        client, workspace_slug = get_plane_client_context()
        data = CreateWorkItemPropertyValue(
            value=value,
            external_id=external_id,
            external_source=external_source,
        )
        return client.work_item_properties.values.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            property_id=property_id,
            data=data,
        )

    @mcp.tool()
    def delete_work_item_property_value(
        project_id: str,
        work_item_id: str,
        property_id: str,
    ) -> None:
        """
        Delete the value(s) of a custom property on a work item.

        For multi-value properties, deletes all values for that property.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            property_id: UUID of the work item property
        """
        client, workspace_slug = get_plane_client_context()
        client.work_item_properties.values.delete(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            property_id=property_id,
        )
