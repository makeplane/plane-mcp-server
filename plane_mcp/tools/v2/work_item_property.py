"""Custom work item properties: their definitions, options, and values.

Scope is chosen by which ids you supply: project_id plus work_item_type_id is
type-scoped, project_id alone is project-flat, neither is workspace-level. The
same rule applies to the option actions.

The *_value actions are the other half of the same subject -- defining a property
and setting it on a work item are one job -- and keeping them here avoids a
near-duplicate tool name that a model has to disambiguate on every call.
"""

from __future__ import annotations

import json
from typing import Any, Literal, get_args

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.work_item_properties import (
    CreateWorkItemProperty,
    CreateWorkItemPropertyOption,
    CreateWorkItemPropertyValue,
    DateAttributeSettings,
    PropertySettings,
    PropertyType,
    RelationType,
    TextAttributeSettings,
    UpdateWorkItemProperty,
    UpdateWorkItemPropertyOption,
    WorkItemProperty,
    WorkItemPropertyOption,
    WorkItemPropertyValueDetail,
)
from pydantic import ValidationError

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

NAME = "work_item_property"
TITLE = "Work item properties"

PROPERTY_TYPES = tuple(e.value for e in PropertyType)
RELATION_TYPES = tuple(e.value for e in RelationType)
TEXT_FORMATS = get_args(TextAttributeSettings.model_fields["display_format"].annotation)
DATE_FORMATS = get_args(DateAttributeSettings.model_fields["display_format"].annotation)

ACTIONS = (
    Action(
        "list",
        (),
        ("project_id", "work_item_type_id", "cursor", "per_page"),
        note="no ids lists every workspace property in one call -- the fast path for PQL",
        read=True,
    ),
    Action("retrieve", ("work_item_property_id",), ("project_id", "work_item_type_id"), read=True),
    Action(
        "create",
        ("display_name", "property_type"),
        (
            "project_id",
            "work_item_type_id",
            "description",
            "relation_type",
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
        ("work_item_property_id",),
        (
            "project_id",
            "work_item_type_id",
            "display_name",
            "property_type",
            "description",
            "relation_type",
            "is_required",
            "is_multi",
            "is_active",
            "default_value",
            "display_format",
            "external_source",
            "external_id",
        ),
        note="only the fields you pass are changed",
    ),
    Action("delete", ("work_item_property_id",), ("project_id", "work_item_type_id"), destructive=True),
    Action(
        "manage_type_properties",
        ("project_id", "work_item_type_id"),
        ("attach_ids", "detach_ids"),
        note="detach removes the association only; it does not delete the property",
    ),
    Action("list_options", ("property_id",), ("project_id",), read=True),
    Action("retrieve_option", ("property_id", "option_id"), ("project_id",), read=True),
    Action(
        "create_option",
        ("property_id", "name"),
        ("project_id", "description", "color", "is_default", "external_source", "external_id"),
    ),
    Action(
        "update_option",
        ("property_id", "option_id"),
        ("project_id", "name", "description", "color", "is_default", "external_source", "external_id"),
    ),
    Action("delete_option", ("property_id", "option_id"), ("project_id",), destructive=True),
    Action("get_value", ("project_id", "work_item_id", "property_id"), read=True),
    Action(
        "set_value",
        ("project_id", "work_item_id", "property_id", "value"),
        ("external_source", "external_id"),
        note="upsert; for a multi-value property this replaces every existing value",
    ),
    Action("delete_value", ("project_id", "work_item_id", "property_id"), destructive=True),
)

FOOTER = (
    f"property_type is one of: {', '.join(PROPERTY_TYPES)}. "
    f"relation_type (for RELATION properties) is one of: {', '.join(RELATION_TYPES)}. "
    'A property id is what goes in a PQL cf["<id>"] filter; for OPTION properties the value is an option id. '
    'options takes a JSON array of {"name", "color", "is_default"} objects. '
    f"display_format is required by TEXT ({', '.join(TEXT_FORMATS)}) and "
    f"DATETIME ({', '.join(DATE_FORMATS)}) properties. "
    "list resolves scope in this order: project_id + work_item_type_id is type-scoped (falling "
    "back to project-flat then workspace when empty), project_id alone is every property in the "
    "project, and neither is every workspace property. To filter by property name in PQL, call "
    "list with no ids -- one workspace-wide fetch beats iterating types -- then match "
    "display_name in memory to get the id for a cf[] condition. "
    "The *_value actions read and write a property on one work item: pass value in the type the "
    "property expects -- TEXT/URL/EMAIL/FILE as a string; DATETIME as a YYYY-MM-DD or "
    "YYYY-MM-DD HH:MM:SS string; DECIMAL as a number; BOOLEAN as true or false; OPTION and "
    "RELATION as an option or record id string, or an array of them when the property is "
    'multi-value. Send the value\'s own type, not a stringified form: "007" stays the text 007, '
    "whereas 7 is the number."
)

LEGACY = {
    "list_work_item_properties": "list",
    "retrieve_work_item_property": "retrieve",
    "create_work_item_property": "create",
    "update_work_item_property": "update",
    "delete_work_item_property": "delete",
    "manage_work_item_type_properties": "manage_type_properties",
    "list_work_item_property_options": "list_options",
    "retrieve_work_item_property_option": "retrieve_option",
    "create_work_item_property_option": "create_option",
    "update_work_item_property_option": "update_option",
    "delete_work_item_property_option": "delete_option",
    "get_work_item_property_value": "get_value",
    "set_work_item_property_value": "set_value",
    "delete_work_item_property_value": "delete_value",
}


def _settings(property_type: str, display_format: str) -> PropertySettings:
    """TEXT and DATETIME are the only types with settings, and both require one.

    The API rejects either type without a display_format, so an unset one falls
    back to the most common choice rather than failing the call.
    """
    if property_type == "TEXT":
        return TextAttributeSettings(display_format=display_format or "single-line")
    if property_type == "DATETIME":
        return DateAttributeSettings(display_format=display_format or "MMM dd, yyyy")
    return None


OPTIONS_SHAPE = 'options must be a JSON array of {"name", "color", "is_default"} objects'


def _options(options: str) -> list[CreateWorkItemPropertyOption] | None:
    """Parse the options array, raising ValueError with a correctable message.

    Silently returning None on malformed input created the property with no
    options at all and reported success -- the caller had no way to notice.
    """
    if not options:
        return None
    try:
        parsed = json.loads(options)
    except ValueError as exc:
        raise ValueError(f"{OPTIONS_SHAPE}; it is not valid JSON ({exc})") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"{OPTIONS_SHAPE}; got {type(parsed).__name__}")
    try:
        return [CreateWorkItemPropertyOption(**item) for item in parsed]
    except (TypeError, ValidationError) as exc:
        raise ValueError(f"{OPTIONS_SHAPE}; one entry is unusable ({exc})") from exc


def _absent(exc: HttpError) -> bool:
    """Whether an error means "nothing here", as opposed to "the call failed".

    Only 404 is a fallback signal. Swallowing everything turned an expired token
    or a 500 into an empty list, which reads to a model as "no custom properties
    exist" -- and it then drops the `cf[]` filter it was about to build.
    """
    return exc.status_code == 404


def _workspace_props_for_type(client, workspace_slug: str, type_id: str) -> list:
    """Workspace properties linked to a type. The link endpoint returns bare ids."""
    try:
        property_ids = client.workspace_work_item_types.properties.list(workspace_slug=workspace_slug, type_id=type_id)
        if not property_ids:
            return []
        wanted = {str(pid) for pid in property_ids}
        everything = client.workspace_work_item_properties.list(workspace_slug=workspace_slug)
        return [p for p in everything if str(p.id) in wanted]
    except HttpError as exc:
        if _absent(exc):
            return []
        raise


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("Custom work item properties and their options.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def work_item_property(
        action: Literal[
            "list",
            "retrieve",
            "create",
            "update",
            "delete",
            "manage_type_properties",
            "list_options",
            "retrieve_option",
            "create_option",
            "update_option",
            "delete_option",
            "get_value",
            "set_value",
            "delete_value",
        ],
        project_id: str = "",
        work_item_id: str = "",
        work_item_type_id: str = "",
        work_item_property_id: str = "",
        property_id: str = "",
        option_id: str = "",
        display_name: str = "",
        property_type: str = "",
        relation_type: str = "",
        description: str = "",
        name: str = "",
        color: str = "",
        default_value: str = "",
        options: str = "",
        display_format: str = "",
        # A property value is genuinely polymorphic -- the property's own type
        # decides. Typing it `str` and re-deriving the type from how the string
        # looked corrupted real data ("007" -> 7). The anyOf block this renders
        # costs 92 characters of listing; the guessing cost correctness.
        value: str | bool | int | float | list[str] = "",
        attach_ids: str = "",
        detach_ids: str = "",
        is_required: bool | None = None,
        is_multi: bool | None = None,
        is_active: bool | None = None,
        is_default: bool | None = None,
        external_source: str = "",
        external_id: str = "",
        cursor: str = "",
        per_page: int = 0,
    ) -> (
        WorkItemProperty
        | list[WorkItemProperty]
        | WorkItemPropertyOption
        | list[WorkItemPropertyOption]
        | list[str]
        | WorkItemPropertyValueDetail
        | list[WorkItemPropertyValueDetail]
        | dict[str, Any]
        | str
        | None
    ):
        client, workspace_slug = get_plane_client_context()

        if property_type and property_type not in PROPERTY_TYPES:
            return f"Error: property_type must be one of: {', '.join(PROPERTY_TYPES)}."
        if relation_type and relation_type not in RELATION_TYPES:
            return f"Error: relation_type must be one of: {', '.join(RELATION_TYPES)}."

        properties = client.work_item_properties
        workspace_properties = client.workspace_work_item_properties

        if action.endswith("_value"):
            if error := needs(action, project_id=project_id, work_item_id=work_item_id, property_id=property_id):
                return error
            values = properties.values
            target = {
                "workspace_slug": workspace_slug,
                "project_id": project_id,
                "work_item_id": work_item_id,
                "property_id": property_id,
            }
            if action == "get_value":
                return values.retrieve(**target)
            if action == "set_value":
                # Compared against "" rather than tested for falsiness: `False`
                # and `0` are values a BOOLEAN or DECIMAL property can hold.
                if value == "":
                    return missing(action, "value")
                return values.create(
                    **target,
                    data=CreateWorkItemPropertyValue(
                        value=value,
                        external_id=opt(external_id),
                        external_source=opt(external_source),
                    ),
                )
            values.delete(**target)
            return None

        if action == "list":
            if not work_item_type_id and not project_id:
                return workspace_properties.list(workspace_slug=workspace_slug)
            if not project_id:
                return _workspace_props_for_type(client, workspace_slug, work_item_type_id)
            params = page_params(cursor, per_page)
            if not work_item_type_id:
                try:
                    return properties.list_project(workspace_slug=workspace_slug, project_id=project_id, params=params)
                except HttpError as exc:
                    if _absent(exc):
                        return []
                    raise
            scoped = properties.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                type_id=work_item_type_id,
                params=params,
            )
            if scoped:
                return scoped
            # A property created without a type association is invisible to the
            # type-scoped endpoint; widen before giving up.
            try:
                flat = properties.list_project(workspace_slug=workspace_slug, project_id=project_id, params=params)
                if flat:
                    return flat
            except HttpError as exc:
                if not _absent(exc):
                    raise
            return _workspace_props_for_type(client, workspace_slug, work_item_type_id)

        if action == "create":
            if error := needs(action, display_name=display_name, property_type=property_type):
                return error
            try:
                parsed_options = _options(options)
            except ValueError as exc:
                return f"Error: {exc}."
            data = CreateWorkItemProperty(
                display_name=display_name,
                property_type=PropertyType(property_type),
                relation_type=RelationType(relation_type) if relation_type else None,
                description=opt(description),
                is_required=is_required,
                default_value=coerce_list(default_value),
                settings=_settings(property_type, display_format),
                is_active=is_active,
                is_multi=is_multi,
                external_source=opt(external_source),
                external_id=opt(external_id),
                options=parsed_options,
            )
            if project_id and work_item_type_id:
                return properties.create(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    type_id=work_item_type_id,
                    data=data,
                )
            if project_id:
                return properties.create_project(workspace_slug=workspace_slug, project_id=project_id, data=data)
            return workspace_properties.create(workspace_slug=workspace_slug, data=data)

        if action == "manage_type_properties":
            attach = coerce_list(attach_ids)
            detach = coerce_list(detach_ids)
            if error := needs(action, project_id=project_id, work_item_type_id=work_item_type_id):
                return error
            if not attach and not detach:
                return missing(action, "attach_ids or detach_ids")
            attached = None
            if attach:
                attached = properties.attach_to_type(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    type_id=work_item_type_id,
                    property_ids=attach,
                )
            for one in detach or []:
                properties.detach_from_type(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    type_id=work_item_type_id,
                    property_id=one,
                )
            return attached

        if action in ("retrieve", "update", "delete"):
            if not work_item_property_id:
                return missing(action, "work_item_property_id")

            if action == "retrieve":
                if project_id and work_item_type_id:
                    return properties.retrieve(
                        workspace_slug=workspace_slug,
                        project_id=project_id,
                        type_id=work_item_type_id,
                        work_item_property_id=work_item_property_id,
                    )
                if project_id:
                    return properties.retrieve_project(
                        workspace_slug=workspace_slug,
                        project_id=project_id,
                        property_id=work_item_property_id,
                    )
                return workspace_properties.retrieve(workspace_slug=workspace_slug, property_id=work_item_property_id)

            if action == "update":
                data = UpdateWorkItemProperty(
                    display_name=opt(display_name),
                    property_type=PropertyType(property_type) if property_type else None,
                    relation_type=RelationType(relation_type) if relation_type else None,
                    description=opt(description),
                    is_required=is_required,
                    default_value=coerce_list(default_value),
                    settings=_settings(property_type, display_format),
                    is_active=is_active,
                    is_multi=is_multi,
                    external_source=opt(external_source),
                    external_id=opt(external_id),
                )
                if project_id and work_item_type_id:
                    return properties.update(
                        workspace_slug=workspace_slug,
                        project_id=project_id,
                        type_id=work_item_type_id,
                        work_item_property_id=work_item_property_id,
                        data=data,
                    )
                if project_id:
                    return properties.update_project(
                        workspace_slug=workspace_slug,
                        project_id=project_id,
                        property_id=work_item_property_id,
                        data=data,
                    )
                return workspace_properties.update(
                    workspace_slug=workspace_slug, property_id=work_item_property_id, data=data
                )

            if project_id and work_item_type_id:
                properties.delete(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    type_id=work_item_type_id,
                    work_item_property_id=work_item_property_id,
                )
            elif project_id:
                properties.delete_project(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    property_id=work_item_property_id,
                )
            else:
                workspace_properties.delete(workspace_slug=workspace_slug, property_id=work_item_property_id)
            return None

        if not property_id:
            return missing(action, "property_id")

        scoped_options = properties.options if project_id else workspace_properties.options
        scope: dict[str, Any] = {"project_id": project_id} if project_id else {}

        if action == "list_options":
            if project_id:
                return scoped_options.list(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    property_id=property_id,
                    params=page_params(cursor, per_page),
                )
            return scoped_options.list(workspace_slug=workspace_slug, property_id=property_id)

        if action == "create_option":
            if not name:
                return missing(action, "name")
            return scoped_options.create(
                workspace_slug=workspace_slug,
                property_id=property_id,
                data=CreateWorkItemPropertyOption(
                    name=name,
                    description=opt(description),
                    color=opt(color),
                    is_default=is_default,
                    external_source=opt(external_source),
                    external_id=opt(external_id),
                ),
                **scope,
            )

        if not option_id:
            return missing(action, "option_id")

        if action == "retrieve_option":
            return scoped_options.retrieve(
                workspace_slug=workspace_slug, property_id=property_id, option_id=option_id, **scope
            )

        if action == "update_option":
            return scoped_options.update(
                workspace_slug=workspace_slug,
                property_id=property_id,
                option_id=option_id,
                data=UpdateWorkItemPropertyOption(
                    name=opt(name),
                    description=opt(description),
                    color=opt(color),
                    is_default=is_default,
                    external_source=opt(external_source),
                    external_id=opt(external_id),
                ),
                **scope,
            )

        scoped_options.delete(workspace_slug=workspace_slug, property_id=property_id, option_id=option_id, **scope)
        return None
