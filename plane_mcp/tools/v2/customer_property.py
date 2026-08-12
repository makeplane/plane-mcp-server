"""Custom properties on customers, and the values a customer holds for them."""

from __future__ import annotations

import json
from typing import Literal, get_args

from fastmcp import FastMCP
from plane.models.customers import (
    CreateCustomerProperty,
    CustomerProperty,
    PaginatedCustomerPropertyResponse,
    PropertySettings,
    PropertyType,
    RelationType,
    SetCustomerPropertyValues,
    UpdateCustomerProperty,
)
from plane.models.work_item_property_configurations import DateAttributeSettings, TextAttributeSettings

from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import (
    Action,
    build_annotations,
    build_description,
    coerce_list,
    missing,
    needs,
    opt,
    page_params,
)

NAME = "customer_property"
TITLE = "Customer properties"

PROPERTY_TYPES = tuple(e.value for e in PropertyType)
RELATION_TYPES = tuple(e.value for e in RelationType)
TEXT_FORMATS = get_args(TextAttributeSettings.model_fields["display_format"].annotation)
DATE_FORMATS = get_args(DateAttributeSettings.model_fields["display_format"].annotation)

ACTIONS = (
    Action("list", (), ("cursor", "per_page"), read=True),
    Action("retrieve", ("property_id",), read=True),
    Action(
        "create",
        ("display_name", "property_type"),
        (
            "relation_type",
            "description",
            "is_required",
            "is_multi",
            "is_active",
            "default_value",
            "options",
            "display_format",
            "external_source",
            "external_id",
        ),
    ),
    Action(
        "update",
        ("property_id",),
        (
            "display_name",
            "relation_type",
            "description",
            "is_required",
            "is_multi",
            "is_active",
            "default_value",
            "options",
            "external_source",
            "external_id",
        ),
        note="only the fields you pass are changed",
    ),
    Action("delete", ("property_id",), destructive=True),
    Action("get_values", ("customer_id",), ("property_id",), note="omit property_id to read them all", read=True),
    Action(
        "set_values",
        ("customer_id", "values"),
        note="replaces the values of the properties named; others keep theirs",
    ),
)

FOOTER = (
    "display_name is the user-facing label and must be unique in the workspace -- the stored "
    "name is derived from it. "
    f"property_type is one of: {', '.join(PROPERTY_TYPES)}. relation_type (required for RELATION) "
    f"is one of: {', '.join(RELATION_TYPES)}. display_format is required by TEXT "
    f"({', '.join(TEXT_FORMATS)}) and DATETIME ({', '.join(DATE_FORMATS)}). "
    'options takes a JSON array of {"name", "description", "is_default"} objects. '
    'values takes a JSON object of property id to a list of strings, e.g. {"<id>": ["Enterprise"]} '
    "-- every value is a string whatever the property type, and a single-item list unless is_multi."
)

LEGACY = {
    "list_customer_properties": "list",
    "retrieve_customer_property": "retrieve",
    "create_customer_property": "create",
    "update_customer_property": "update",
    "delete_customer_property": "delete",
    "get_customer_property_values": "get_values",
    "set_customer_property_values": "set_values",
}


def _settings(property_type: str, display_format: str) -> PropertySettings:
    """TEXT and DATETIME both require a display_format; default rather than fail."""
    if property_type == "TEXT":
        return TextAttributeSettings(display_format=display_format or "single-line")
    if property_type == "DATETIME":
        return DateAttributeSettings(display_format=display_format or "MMM dd, yyyy")
    return None


def _json(raw: str, expected: type):
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, expected) else None


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("Custom properties on customers.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def customer_property(
        action: Literal["list", "retrieve", "create", "update", "delete", "get_values", "set_values"],
        property_id: str = "",
        customer_id: str = "",
        display_name: str = "",
        property_type: str = "",
        relation_type: str = "",
        description: str = "",
        default_value: str = "",
        options: str = "",
        display_format: str = "",
        values: str = "",
        is_required: bool | None = None,
        is_multi: bool | None = None,
        is_active: bool | None = None,
        external_source: str = "",
        external_id: str = "",
        cursor: str = "",
        per_page: int = 0,
    ) -> CustomerProperty | PaginatedCustomerPropertyResponse | dict[str, list[str]] | str | None:
        client, workspace_slug = get_plane_client_context()

        if property_type and property_type not in PROPERTY_TYPES:
            return f"Error: property_type must be one of: {', '.join(PROPERTY_TYPES)}."
        if relation_type and relation_type not in RELATION_TYPES:
            return f"Error: relation_type must be one of: {', '.join(RELATION_TYPES)}."

        properties = client.customers.properties
        property_values = client.customers.property_values

        if action == "list":
            return properties.list(workspace_slug=workspace_slug, params=page_params(cursor, per_page))

        if action == "create":
            if error := needs(action, display_name=display_name, property_type=property_type):
                return error
            return properties.create(
                workspace_slug=workspace_slug,
                data=CreateCustomerProperty(
                    # The API requires `name` but stores a slug of display_name.
                    name=display_name,
                    display_name=display_name,
                    property_type=PropertyType(property_type),
                    relation_type=RelationType(relation_type) if relation_type else None,
                    description=opt(description),
                    is_required=is_required,
                    default_value=coerce_list(default_value),
                    settings=_settings(property_type, display_format),
                    is_active=is_active,
                    is_multi=is_multi,
                    options=_json(options, list) if options else None,
                    external_source=opt(external_source),
                    external_id=opt(external_id),
                ),
            )

        if action == "get_values":
            if not customer_id:
                return missing(action, "customer_id")
            if property_id:
                return {property_id: property_values.retrieve(workspace_slug, customer_id, property_id)}
            return property_values.list(workspace_slug, customer_id)

        if action == "set_values":
            if error := needs(action, customer_id=customer_id, values=values):
                return error
            parsed = _json(values, dict)
            if parsed is None:
                return 'Error: values must be a JSON object, for example {"<property_id>": ["Enterprise"]}.'
            property_values.create(
                workspace_slug,
                customer_id,
                SetCustomerPropertyValues(customer_property_values=parsed),
            )
            return None

        if not property_id:
            return missing(action, "property_id")

        if action == "retrieve":
            return properties.retrieve(workspace_slug=workspace_slug, property_id=property_id)

        if action == "update":
            return properties.update(
                workspace_slug=workspace_slug,
                property_id=property_id,
                data=UpdateCustomerProperty(
                    display_name=opt(display_name),
                    relation_type=RelationType(relation_type) if relation_type else None,
                    description=opt(description),
                    is_required=is_required,
                    default_value=coerce_list(default_value),
                    is_active=is_active,
                    options=_json(options, list) if options else None,
                    external_source=opt(external_source),
                    external_id=opt(external_id),
                ),
            )

        properties.delete(workspace_slug=workspace_slug, property_id=property_id)
        return None
