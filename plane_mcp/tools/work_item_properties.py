"""Work item property-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.errors import HttpError
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
        Create a new work item property.

        Args:
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            type_id: UUID of the work item type
            display_name: Display name for the property
            property_type: Type of property (TEXT, DATETIME, DECIMAL, BOOLEAN,
                OPTION, RELATION, URL, EMAIL, FILE)
            relation_type: Relation type (ISSUE, USER) - required for RELATION properties
            description: Property description
            is_required: Whether the property is required
            default_value: Default value(s) for the property
            settings: Settings dictionary - required for TEXT and DATETIME properties
                     For TEXT: {"display_format": "single-line"|"multi-line"|"readonly"}
                     For DATETIME: {"display_format":
                     "MMM dd, yyyy"|"dd/MM/yyyy"|"MM/dd/yyyy"|"yyyy/MM/dd"}
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
            workspace_slug: The workspace slug identifier
            project_id: UUID of the project
            type_id: UUID of the work item type
            work_item_property_id: UUID of the property
            display_name: Display name for the property
            property_type: Type of property (TEXT, DATETIME, DECIMAL, BOOLEAN,
                OPTION, RELATION, URL, EMAIL, FILE)
            relation_type: Relation type (ISSUE, USER) - required when updating to RELATION
            description: Property description
            is_required: Whether the property is required
            default_value: Default value(s) for the property
            settings: Settings dictionary - required when updating to TEXT or DATETIME
                     For TEXT: {"display_format": "single-line"|"multi-line"|"readonly"}
                     For DATETIME: {"display_format":
                     "MMM dd, yyyy"|"dd/MM/yyyy"|"MM/dd/yyyy"|"yyyy/MM/dd"}
            is_active: Whether the property is active
            is_multi: Whether the property supports multiple values
            validation_rules: Validation rules dictionary
            external_source: External system source name
            external_id: External system identifier

        Returns:
            Updated WorkItemProperty object
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

    @mcp.tool()
    def list_work_item_property_values(
        project_id: str,
        work_item_id: str,
        include_unset: bool = False,
    ) -> list[dict[str, Any]]:
        """
        List custom property values currently set on a work item.

        Resolves the work item's type, lists all active property definitions for
        that type, and fetches the value (if any) for each property. Useful for
        retrieving member-type custom fields such as ``Responsible`` or ``Review``
        whose values do not appear in ``retrieve_work_item``.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            include_unset: If True, include properties without a set value
                (``value`` will be ``None``). Default False — only properties
                with values are returned.

        Returns:
            List of dicts, one per property. Each dict contains:
              - ``property_id``: UUID of the property definition
              - ``display_name``: Human-readable name (e.g. "Responsible")
              - ``name``: Internal name (slug)
              - ``property_type``: TEXT/DATETIME/DECIMAL/BOOLEAN/OPTION/RELATION/URL/EMAIL/FILE
              - ``relation_type``: USER/ISSUE for RELATION properties, else None
              - ``is_multi``: Whether the property accepts multiple values
              - ``value``: Single value or list of values (for multi). For
                RELATION/USER it is the user UUID(s).
              - ``value_record_ids``: ID(s) of the underlying value records.

        Notes:
            * Returns an empty list if the work item has no type assigned.
            * Properties marked ``is_active=False`` are skipped.
        """
        client, workspace_slug = get_plane_client_context()

        work_item = client.work_items.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
        )
        type_id = getattr(work_item, "type_id", None)
        if not type_id:
            return []

        properties = client.work_item_properties.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            type_id=type_id,
        )

        result: list[dict[str, Any]] = []
        for prop in properties:
            if not getattr(prop, "is_active", True):
                continue

            value: Any = None
            value_record_ids: Any = None
            try:
                value_obj = client.work_item_properties.values.retrieve(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    work_item_id=work_item_id,
                    property_id=prop.id,
                )
            except HttpError as exc:
                if exc.status_code != 404:
                    raise
                value_obj = None

            if value_obj is None:
                if not include_unset:
                    continue
            elif isinstance(value_obj, list):
                value = [item.value for item in value_obj]
                value_record_ids = [item.id for item in value_obj]
            else:
                value = value_obj.value
                value_record_ids = value_obj.id

            result.append(
                {
                    "property_id": prop.id,
                    "display_name": prop.display_name,
                    "name": getattr(prop, "name", None),
                    "property_type": _enum_value(prop.property_type),
                    "relation_type": _enum_value(prop.relation_type),
                    "is_multi": bool(getattr(prop, "is_multi", False)),
                    "value": value,
                    "value_record_ids": value_record_ids,
                }
            )

        return result

    @mcp.tool()
    def retrieve_work_item_property_value(
        project_id: str,
        work_item_id: str,
        property_id: str,
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        """
        Retrieve the value(s) of a single custom property for a work item.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            property_id: UUID of the property definition

        Returns:
            For single-value properties: a dict (or ``None`` if the value is
            not set). For multi-value properties: a list of dicts.
            Each dict mirrors ``WorkItemPropertyValueDetail`` (id, value,
            value_type, property_id, issue_id, created_at, updated_at, ...).
        """
        client, workspace_slug = get_plane_client_context()

        try:
            value_obj = client.work_item_properties.values.retrieve(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
                property_id=property_id,
            )
        except HttpError as exc:
            if exc.status_code == 404:
                return None
            raise

        if isinstance(value_obj, list):
            return [item.model_dump() for item in value_obj]
        return value_obj.model_dump()


def _enum_value(value: Any) -> Any:
    """Convert an enum-like field to its plain value, leave other types alone."""
    if value is None:
        return None
    return getattr(value, "value", value)
