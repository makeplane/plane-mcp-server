"""Consolidated `work_item_property_value` tool.

Collapses the three custom-property *value* tools from work_item_properties.py
(set / get / delete) into one action-dispatch tool. The property definition
tools live in plane_mcp/tools_v2/work_item_property.py.
"""

from __future__ import annotations

from fastmcp import FastMCP
from plane.models.work_item_properties import (
    CreateWorkItemPropertyValue,
    WorkItemPropertyValueDetail,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools_v2._common import bad_action, json_out, missing, opt

ACTIONS = ["set", "get", "delete"]

DOC = """Read/write the value of a custom property on a work item. Actions:
set (project_id, work_item_id, property_id, value; optional external_id, external_source);
get (project_id, work_item_id, property_id);
delete (project_id, work_item_id, property_id).

Use the work_item_property tool (action=list) to resolve property_id from a display name.

set is an upsert. For multi-value properties (is_multi=True) it replaces all existing values.
delete removes all values for that property.

Value types by property type:
    TEXT/URL/EMAIL/FILE: string
    DATETIME: string (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS)
    DECIMAL: int or float
    BOOLEAN: true or false
    OPTION/RELATION (single): UUID string
    OPTION/RELATION (multi, is_multi=True): list of UUID strings

get and set return a single WorkItemPropertyValueDetail for non-multi properties,
or a list of them for multi-value properties."""


def _dispatch(
    action: str,
    project_id: str,
    work_item_id: str,
    property_id: str,
    value: str | bool | int | float | list[str] | None,
    external_id: str,
    external_source: str,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)
    if not project_id:
        return missing(action, "project_id")
    if not work_item_id:
        return missing(action, "work_item_id")
    if not property_id:
        return missing(action, "property_id")

    client, workspace_slug = get_plane_client_context()

    if action == "get":
        return client.work_item_properties.values.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            property_id=property_id,
        )

    if action == "delete":
        client.work_item_properties.values.delete(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            property_id=property_id,
        )
        return None

    if value is None:
        return missing(action, "value")
    data = CreateWorkItemPropertyValue(
        value=value,
        external_id=opt(external_id),
        external_source=opt(external_source),
    )
    return client.work_item_properties.values.create(
        workspace_slug=workspace_slug,
        project_id=project_id,
        work_item_id=work_item_id,
        property_id=property_id,
        data=data,
    )


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="work_item_property_value", description=DOC)
    def _work_item_property_value(
        action: str,
        project_id: str = "",
        work_item_id: str = "",
        property_id: str = "",
        value: str | bool | int | float | list[str] | None = None,
        external_id: str = "",
        external_source: str = "",
    ) -> WorkItemPropertyValueDetail | list[WorkItemPropertyValueDetail] | str | None:
        return _dispatch(
            action, project_id, work_item_id, property_id, value, external_id, external_source
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="work_item_property_value", description=DOC)
    def _work_item_property_value(
        action: str,
        project_id: str = "",
        work_item_id: str = "",
        property_id: str = "",
        value: str | bool | int | float | list[str] | None = None,
        external_id: str = "",
        external_source: str = "",
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, project_id, work_item_id, property_id,
                    value, external_id, external_source,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
