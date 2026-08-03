"""Consolidated `customer_property` tool.

Collapses customers/properties.py (5 tools) and customers/property_values.py
(2 tools) into one action-dispatch tool.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from plane.models.customers import (
    CreateCustomerProperty,
    CustomerProperty,
    PaginatedCustomerPropertyResponse,
    PropertySettings,
    SetCustomerPropertyValues,
    UpdateCustomerProperty,
)
from plane.models.enums import PropertyType, RelationType
from plane.models.work_item_property_configurations import (
    DateAttributeSettings,
    TextAttributeSettings,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools_v2._common import bad_action, json_out, missing, opt

ACTIONS = [
    "list",
    "retrieve",
    "create",
    "update",
    "delete",
    "set_values",
    "get_values",
]

DOC = """Manage customer properties (workspace-wide custom fields on customers) and the
values customers hold for them. Actions:
list (no required params; optional cursor, per_page);
retrieve (property_id);
create (name, display_name, property_type; optional relation_type, description,
    is_required, default_value, settings, is_active, is_multi, options,
    external_source, external_id);
update (property_id; optional display_name, relation_type, description, is_required,
    default_value, is_active, options, external_source, external_id);
delete (property_id);
set_values (customer_id, values);
get_values (customer_id; optional property_id to read just one).

property_type: TEXT | DATETIME | DECIMAL | BOOLEAN | OPTION | RELATION | URL | EMAIL | FILE
relation_type: ISSUE | USER -- required when property_type=RELATION.
settings is required for TEXT and DATETIME:
    TEXT:     {"display_format": "single-line"|"multi-line"|"readonly"}
    DATETIME: {"display_format": "MMM dd, yyyy"|"dd/MM/yyyy"|"MM/dd/yyyy"|"yyyy/MM/dd"}
options is for OPTION type -- on create [{"name": str, "description"?: str, "is_default"?: bool}];
on update [{"id": str, ...}] edits an existing option, omitting id adds one.
name is required on create but discarded -- the stored name is a slug of display_name;
pass display_name here too if you have nothing else. display_name must be unique in the
workspace. property_type, is_multi and settings are fixed once created -- delete and
recreate the property to change them. is_required forces default_value to be empty.
default_value holds at most one entry unless is_multi.

set_values replaces the current values of only the properties named; the rest keep
theirs. Every value is a string, whatever the property's type:
    TEXT/URL/EMAIL/FILE: the text
    DATETIME: "YYYY-MM-DD"
    DECIMAL: the number as a string, e.g. "12.5"
    BOOLEAN: "True" or "False"
    OPTION/RELATION: the option or related record's UUID
Pass a single-item list for properties that are not is_multi, e.g.
values={"<property_id>": ["Enterprise"]}.
get_values returns property UUID mapped to its values; properties with no value set,
and inactive properties, are absent.
delete also removes every customer's values for the property.
list returns a paginated envelope (results + total_count, next_cursor,
next_page_results); page again while next_page_results is true."""


def _page_params(cursor: str, per_page: int) -> dict[str, Any]:
    """Build query params for a paginated customer endpoint, dropping unset ones."""
    params: dict[str, Any] = {}
    if cursor:
        params["cursor"] = cursor
    if per_page:
        params["per_page"] = per_page
    return params


def _build_settings(property_type: str, settings: dict | None) -> PropertySettings:
    """Turn a raw settings dict into the typed settings model for its property type."""
    if not settings:
        return None
    if property_type == "TEXT":
        return TextAttributeSettings(**settings)
    if property_type == "DATETIME":
        return DateAttributeSettings(**settings)
    return None


def _dispatch(
    action: str,
    property_id: str,
    name: str,
    display_name: str,
    property_type: str,
    relation_type: str,
    description: str,
    is_required: bool | None,
    default_value: list[str] | None,
    settings: dict | None,
    is_active: bool | None,
    is_multi: bool | None,
    options: list[dict] | None,
    external_source: str,
    external_id: str,
    cursor: str,
    per_page: int,
    customer_id: str,
    values: dict[str, list[str]] | None,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        return client.customers.properties.list(
            workspace_slug=workspace_slug, params=_page_params(cursor, per_page)
        )

    if action == "get_values":
        if not customer_id:
            return missing(action, "customer_id")
        property_values = client.customers.property_values
        if property_id:
            return {property_id: property_values.retrieve(workspace_slug, customer_id, property_id)}
        return property_values.list(workspace_slug, customer_id)

    if action == "set_values":
        if not customer_id:
            return missing(action, "customer_id")
        if not values:
            return missing(action, "values")
        client.customers.property_values.create(
            workspace_slug,
            customer_id,
            SetCustomerPropertyValues(customer_property_values=values),
        )
        return None

    if action == "create":
        if not name:
            return missing(action, "name")
        if not display_name:
            return missing(action, "display_name")
        if not property_type:
            return missing(action, "property_type")
        return client.customers.properties.create(
            workspace_slug=workspace_slug,
            data=CreateCustomerProperty(
                name=name,
                display_name=display_name,
                property_type=PropertyType(property_type),
                relation_type=RelationType(relation_type) if relation_type else None,
                description=opt(description),
                is_required=is_required,
                default_value=default_value,
                settings=_build_settings(property_type, settings),
                is_active=is_active,
                is_multi=is_multi,
                options=options,
                external_source=opt(external_source),
                external_id=opt(external_id),
            ),
        )

    if not property_id:
        return missing(action, "property_id")

    if action == "retrieve":
        return client.customers.properties.retrieve(
            workspace_slug=workspace_slug, property_id=property_id
        )

    if action == "delete":
        client.customers.properties.delete(
            workspace_slug=workspace_slug, property_id=property_id
        )
        return None

    return client.customers.properties.update(
        workspace_slug=workspace_slug,
        property_id=property_id,
        data=UpdateCustomerProperty(
            display_name=opt(display_name),
            relation_type=RelationType(relation_type) if relation_type else None,
            description=opt(description),
            is_required=is_required,
            default_value=default_value,
            is_active=is_active,
            options=options,
            external_source=opt(external_source),
            external_id=opt(external_id),
        ),
    )


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="customer_property", description=DOC)
    def _customer_property(
        action: str,
        property_id: str = "",
        name: str = "",
        display_name: str = "",
        property_type: str = "",
        relation_type: str = "",
        description: str = "",
        is_required: bool | None = None,
        default_value: list[str] | None = None,
        settings: dict | None = None,
        is_active: bool | None = None,
        is_multi: bool | None = None,
        options: list[dict] | None = None,
        external_source: str = "",
        external_id: str = "",
        cursor: str = "",
        per_page: int = 0,
        customer_id: str = "",
        values: dict[str, list[str]] | None = None,
    ) -> (
        CustomerProperty
        | PaginatedCustomerPropertyResponse
        | dict[str, list[str]]
        | str
        | None
    ):
        return _dispatch(
            action, property_id, name, display_name, property_type, relation_type,
            description, is_required, default_value, settings, is_active, is_multi,
            options, external_source, external_id, cursor, per_page, customer_id, values,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="customer_property", description=DOC)
    def _customer_property(
        action: str,
        property_id: str = "",
        name: str = "",
        display_name: str = "",
        property_type: str = "",
        relation_type: str = "",
        description: str = "",
        is_required: bool | None = None,
        default_value: list[str] | None = None,
        settings: dict | None = None,
        is_active: bool | None = None,
        is_multi: bool | None = None,
        options: list[dict] | None = None,
        external_source: str = "",
        external_id: str = "",
        cursor: str = "",
        per_page: int = 0,
        customer_id: str = "",
        values: dict[str, list[str]] | None = None,
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, property_id, name, display_name, property_type, relation_type,
                    description, is_required, default_value, settings, is_active, is_multi,
                    options, external_source, external_id, cursor, per_page, customer_id,
                    values,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
