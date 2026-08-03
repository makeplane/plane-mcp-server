"""Consolidated `work_item_property` tool.

Collapses the eleven custom-property *definition* and *option* tools from
work_item_properties.py into one action-dispatch tool. The three value tools
(set/get/delete a property value on a work item) live in
spike/v2/work_item_property_value.py.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from plane.models.enums import PropertyType, RelationType
from plane.models.work_item_properties import (
    CreateWorkItemProperty,
    CreateWorkItemPropertyOption,
    PropertySettings,
    UpdateWorkItemProperty,
    UpdateWorkItemPropertyOption,
    WorkItemProperty,
    WorkItemPropertyOption,
)
from plane.models.work_item_property_configurations import (
    DateAttributeSettings,
    TextAttributeSettings,
)

from plane_mcp.client import get_plane_client_context
from spike.v2._common import bad_action, json_out, missing, opt

ACTIONS = [
    "list",
    "retrieve",
    "create",
    "update",
    "delete",
    "list_options",
    "retrieve_option",
    "create_option",
    "update_option",
    "delete_option",
    "manage_type_properties",
]

DOC = """Manage custom work item properties and their OPTION choices. Actions:
list (no required params; optional project_id, work_item_type_id, params);
retrieve (property_id; optional project_id, work_item_type_id);
create (display_name, property_type; optional project_id, work_item_type_id, relation_type, description, is_required, default_value, settings, is_active, is_multi, validation_rules, external_source, external_id, options);
update (property_id; optional project_id, work_item_type_id, display_name, property_type, relation_type, description, is_required, default_value, settings, is_active, is_multi, validation_rules, external_source, external_id);
delete (property_id; optional project_id, work_item_type_id);
list_options (property_id; optional project_id, params);
retrieve_option (property_id, option_id; optional project_id);
create_option (property_id, name; optional project_id, description, color, is_default, external_source, external_id);
update_option (property_id, option_id; optional project_id, name, description, color, is_default, external_source, external_id);
delete_option (property_id, option_id; optional project_id);
manage_type_properties (project_id, work_item_type_id, and at least one of attach_ids / detach_ids).

Omit project_id for workspace scope on every action except manage_type_properties.

Scope resolution for list:
  no args                        -> ALL workspace-level properties (one API call);
  work_item_type_id only         -> workspace-level properties linked to that type;
  project_id only                -> all properties in that project (any type);
  project_id + work_item_type_id -> properties linked to that type in that project,
                                    falling back to project-flat then workspace if empty.
For PQL filtering by name, prefer list with NO args - one workspace-wide fetch beats
iterating every work item type; match display_name in memory, then use the property id
as cf["<id>"] in PQL (for OPTION properties the value is the option id).

Scope resolution for create:
  project_id + work_item_type_id -> type-scoped project property (legacy);
  project_id only                -> project-level property (not yet linked to a type);
  neither                        -> workspace-level property.
On update, work_item_type_id is required when project_id is provided for type-scoped update.

property_type: TEXT | DATETIME | DECIMAL | BOOLEAN | OPTION | RELATION | URL | EMAIL | FILE | FORMULA.
relation_type: ISSUE | USER - required when property_type=RELATION.
settings is required for TEXT/DATETIME and is only applied for those types:
  TEXT:     {"display_format": "single-line"|"multi-line"|"readonly"}
  DATETIME: {"display_format": "MMM dd, yyyy"|"dd/MM/yyyy"|"MM/dd/yyyy"|"yyyy/MM/dd"}
On update, settings is only applied when property_type is also supplied.
options (create only): list of {name, color?, is_default?} dicts - for OPTION type.
color: hex string e.g. "#FF5733".

manage_type_properties attaches and/or detaches properties on a type in one call. Detach
does not delete the property, it only removes the association. Returns the attached
property UUIDs if attach_ids was given, else nothing."""


def _workspace_props_for_type(client, workspace_slug: str, type_id: str) -> list:
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
    except Exception:  # noqa: BLE001 - mirrors source behaviour
        return []


def _list(client, workspace_slug: str, project_id: str, work_item_type_id: str, params):
    # Fast path -- no args: return every workspace-level property in ONE call.
    if not work_item_type_id and not project_id:
        return client.workspace_work_item_properties.list(workspace_slug=workspace_slug)

    if not project_id:
        return _workspace_props_for_type(client, workspace_slug, work_item_type_id)

    if not work_item_type_id:
        # project-flat endpoint: all properties in the project, any type
        try:
            return client.work_item_properties.list_project(
                workspace_slug=workspace_slug,
                project_id=project_id,
                params=params,
            )
        except Exception:  # noqa: BLE001 - mirrors source behaviour
            return []

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
    except Exception:  # noqa: BLE001 - mirrors source behaviour
        pass

    # Last resort: workspace-level
    return _workspace_props_for_type(client, workspace_slug, work_item_type_id)


def _settings_for(property_type: str, settings: dict | None) -> PropertySettings:
    processed: PropertySettings = None
    if settings:
        if property_type == "TEXT":
            processed = TextAttributeSettings(**settings)
        elif property_type == "DATETIME":
            processed = DateAttributeSettings(**settings)
    return processed


def _dispatch(  # noqa: PLR0911, PLR0912, C901 - flat action dispatch
    action: str,
    project_id: str,
    work_item_type_id: str,
    property_id: str,
    option_id: str,
    display_name: str,
    property_type: str,
    relation_type: str,
    name: str,
    description: str,
    color: str,
    is_required: bool | None,
    is_default: bool | None,
    is_active: bool | None,
    is_multi: bool | None,
    default_value: list[str] | None,
    settings: dict | None,
    validation_rules: dict | None,
    external_source: str,
    external_id: str,
    options: list[dict] | None,
    attach_ids: list[str] | None,
    detach_ids: list[str] | None,
    params: dict[str, Any] | None,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        return _list(client, workspace_slug, project_id, work_item_type_id, params)

    if action == "create":
        if not display_name or not property_type:
            return missing(action, "display_name", "property_type")
        validated_property_type = PropertyType(property_type)
        validated_relation_type: RelationType | None = None
        if relation_type:
            validated_relation_type = RelationType(relation_type)

        processed_options: list[CreateWorkItemPropertyOption] | None = None
        if options:
            processed_options = [CreateWorkItemPropertyOption(**o) for o in options]

        data = CreateWorkItemProperty(
            display_name=display_name,
            property_type=validated_property_type,
            relation_type=validated_relation_type,
            description=opt(description),
            is_required=is_required,
            default_value=default_value,
            settings=_settings_for(property_type, settings),
            is_active=is_active,
            is_multi=is_multi,
            validation_rules=validation_rules,
            external_source=opt(external_source),
            external_id=opt(external_id),
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

    if action == "manage_type_properties":
        if not project_id or not work_item_type_id:
            return missing(action, "project_id", "work_item_type_id")
        if not attach_ids and not detach_ids:
            return missing(action, "attach_ids and/or detach_ids")
        result = None
        if attach_ids:
            result = client.work_item_properties.attach_to_type(
                workspace_slug=workspace_slug,
                project_id=project_id,
                type_id=work_item_type_id,
                property_ids=attach_ids,
            )
        if detach_ids:
            for pid in detach_ids:
                client.work_item_properties.detach_from_type(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    type_id=work_item_type_id,
                    property_id=pid,
                )
        return result

    if not property_id:
        return missing(action, "property_id")

    if action == "retrieve":
        if project_id and work_item_type_id:
            return client.work_item_properties.retrieve(
                workspace_slug=workspace_slug,
                project_id=project_id,
                type_id=work_item_type_id,
                work_item_property_id=property_id,
            )
        if project_id:
            return client.work_item_properties.retrieve_project(
                workspace_slug=workspace_slug,
                project_id=project_id,
                property_id=property_id,
            )
        return client.workspace_work_item_properties.retrieve(
            workspace_slug=workspace_slug,
            property_id=property_id,
        )

    if action == "update":
        validated_property_type_u: PropertyType | None = None
        if property_type:
            validated_property_type_u = PropertyType(property_type)
        validated_relation_type_u: RelationType | None = None
        if relation_type:
            validated_relation_type_u = RelationType(relation_type)

        processed_settings: PropertySettings = None
        if settings and property_type:
            processed_settings = _settings_for(property_type, settings)

        data_u = UpdateWorkItemProperty(
            display_name=opt(display_name),
            property_type=validated_property_type_u,
            relation_type=validated_relation_type_u,
            description=opt(description),
            is_required=is_required,
            default_value=default_value,
            settings=processed_settings,
            is_active=is_active,
            is_multi=is_multi,
            validation_rules=validation_rules,
            external_source=opt(external_source),
            external_id=opt(external_id),
        )

        if project_id and work_item_type_id:
            return client.work_item_properties.update(
                workspace_slug=workspace_slug,
                project_id=project_id,
                type_id=work_item_type_id,
                work_item_property_id=property_id,
                data=data_u,
            )
        if project_id:
            return client.work_item_properties.update_project(
                workspace_slug=workspace_slug,
                project_id=project_id,
                property_id=property_id,
                data=data_u,
            )
        return client.workspace_work_item_properties.update(
            workspace_slug=workspace_slug,
            property_id=property_id,
            data=data_u,
        )

    if action == "delete":
        if project_id and work_item_type_id:
            client.work_item_properties.delete(
                workspace_slug=workspace_slug,
                project_id=project_id,
                type_id=work_item_type_id,
                work_item_property_id=property_id,
            )
        elif project_id:
            client.work_item_properties.delete_project(
                workspace_slug=workspace_slug,
                project_id=project_id,
                property_id=property_id,
            )
        else:
            client.workspace_work_item_properties.delete(
                workspace_slug=workspace_slug,
                property_id=property_id,
            )
        return None

    if action == "list_options":
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

    if action == "create_option":
        if not name:
            return missing(action, "name")
        data_o = CreateWorkItemPropertyOption(
            name=name,
            description=opt(description),
            color=opt(color),
            is_default=is_default,
            external_source=opt(external_source),
            external_id=opt(external_id),
        )
        if project_id:
            return client.work_item_properties.options.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                property_id=property_id,
                data=data_o,
            )
        return client.workspace_work_item_properties.options.create(
            workspace_slug=workspace_slug,
            property_id=property_id,
            data=data_o,
        )

    # retrieve_option / update_option / delete_option
    if not option_id:
        return missing(action, "option_id")

    if action == "retrieve_option":
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

    if action == "update_option":
        data_uo = UpdateWorkItemPropertyOption(
            name=opt(name),
            description=opt(description),
            color=opt(color),
            is_default=is_default,
            external_source=opt(external_source),
            external_id=opt(external_id),
        )
        if project_id:
            return client.work_item_properties.options.update(
                workspace_slug=workspace_slug,
                project_id=project_id,
                property_id=property_id,
                option_id=option_id,
                data=data_uo,
            )
        return client.workspace_work_item_properties.options.update(
            workspace_slug=workspace_slug,
            property_id=property_id,
            option_id=option_id,
            data=data_uo,
        )

    # action == "delete_option"
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
    return None


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="work_item_property", description=DOC)
    def _work_item_property(
        action: str,
        project_id: str = "",
        work_item_type_id: str = "",
        property_id: str = "",
        option_id: str = "",
        display_name: str = "",
        property_type: str = "",
        relation_type: str = "",
        name: str = "",
        description: str = "",
        color: str = "",
        is_required: bool | None = None,
        is_default: bool | None = None,
        is_active: bool | None = None,
        is_multi: bool | None = None,
        default_value: list[str] | None = None,
        settings: dict | None = None,
        validation_rules: dict | None = None,
        external_source: str = "",
        external_id: str = "",
        options: list[dict] | None = None,
        attach_ids: list[str] | None = None,
        detach_ids: list[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> (
        WorkItemProperty
        | WorkItemPropertyOption
        | list[WorkItemProperty]
        | list[WorkItemPropertyOption]
        | list[str]
        | str
        | None
    ):
        return _dispatch(
            action, project_id, work_item_type_id, property_id, option_id, display_name,
            property_type, relation_type, name, description, color, is_required, is_default,
            is_active, is_multi, default_value, settings, validation_rules, external_source,
            external_id, options, attach_ids, detach_ids, params,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="work_item_property", description=DOC)
    def _work_item_property(
        action: str,
        project_id: str = "",
        work_item_type_id: str = "",
        property_id: str = "",
        option_id: str = "",
        display_name: str = "",
        property_type: str = "",
        relation_type: str = "",
        name: str = "",
        description: str = "",
        color: str = "",
        is_required: bool | None = None,
        is_default: bool | None = None,
        is_active: bool | None = None,
        is_multi: bool | None = None,
        default_value: list[str] | None = None,
        settings: dict | None = None,
        validation_rules: dict | None = None,
        external_source: str = "",
        external_id: str = "",
        options: list[dict] | None = None,
        attach_ids: list[str] | None = None,
        detach_ids: list[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, project_id, work_item_type_id, property_id, option_id, display_name,
                    property_type, relation_type, name, description, color, is_required,
                    is_default, is_active, is_multi, default_value, settings, validation_rules,
                    external_source, external_id, options, attach_ids, detach_ids, params,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
