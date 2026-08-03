"""Consolidated `module` tool -- collapses the 8 verb-per-resource module tools.

Mirrors spike/v2/intake.py. Source of truth: plane_mcp/tools/modules.py.
"""

from __future__ import annotations

from typing import Any, get_args

from fastmcp import FastMCP
from fastmcp.utilities.logging import get_logger
from plane.errors.errors import HttpError
from plane.models.enums import ModuleStatusEnum
from plane.models.modules import (
    CreateModule,
    Module,
    PaginatedArchivedModuleResponse,
    PaginatedModuleLiteResponse,
    PaginatedModuleWorkItemResponse,
    UpdateModule,
)
from plane.models.query_params import LiteListQueryParams, WorkItemQueryParams

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.pql_reference import PQL_FULL_REFERENCE
from spike.v2._common import bad_action, json_out, missing, opt

logger = get_logger(__name__)

ACTIONS = [
    "list",
    "retrieve",
    "create",
    "update",
    "delete",
    "archive",
    "unarchive",
    "list_work_items",
    "manage_work_items",
]

DOC = """Manage modules (feature groupings). Every action requires project_id. Actions:
list (project_id; optional archived, cursor, per_page, order_by);
retrieve (project_id, module_id);
create (project_id, name; plus optional module fields*);
update (project_id, module_id; optional name, plus optional module fields*);
delete (project_id, module_id);
archive (project_id, module_id);
unarchive (project_id, module_id);
list_work_items (project_id, module_id; optional pql, order_by, per_page, cursor, expand, fields);
manage_work_items (project_id, module_id; add_ids and/or remove_ids -- at least one required).

*optional module fields: description, start_date, target_date, status, lead, members,
external_source, external_id.
status must be one of backlog, planned, in-progress, paused, completed, cancelled; any other value is
ignored (sent as unset).
lead is a user UUID; members is a list of user UUIDs. Dates are ISO 8601.
pql: Plane Query Language filter; call get_pql_reference for syntax. An invalid pql returns the full
PQL reference in the response instead of raising."""


def _dispatch(
    action: str,
    project_id: str,
    module_id: str,
    archived: bool,
    cursor: str,
    per_page: int,
    order_by: str,
    name: str,
    description: str,
    start_date: str,
    target_date: str,
    status: str,
    lead: str,
    members: list[str] | None,
    external_source: str,
    external_id: str,
    pql: str,
    expand: str,
    fields: str,
    add_ids: list[str] | None,
    remove_ids: list[str] | None,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)
    if not project_id:
        return missing(action, "project_id")

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        params = LiteListQueryParams(
            cursor=opt(cursor), per_page=opt(per_page), order_by=opt(order_by)
        )
        if archived:
            return client.modules.list_archived(
                workspace_slug=workspace_slug,
                project_id=project_id,
                params=params.model_dump(exclude_none=True),
            )
        return client.modules.list_lite(
            workspace_slug=workspace_slug, project_id=project_id, params=params
        )

    # Validate status against allowed literal values
    validated_status: ModuleStatusEnum | None = (
        status if status in get_args(ModuleStatusEnum) else None  # type: ignore[assignment]
    )

    if action == "create":
        if not name:
            return missing(action, "name")
        return client.modules.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=CreateModule(
                name=name,
                description=opt(description),
                start_date=opt(start_date),
                target_date=opt(target_date),
                status=validated_status,
                lead=opt(lead),
                members=members,
                external_source=opt(external_source),
                external_id=opt(external_id),
            ),
        )

    if not module_id:
        return missing(action, "module_id")

    if action == "retrieve":
        return client.modules.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, module_id=module_id
        )

    if action == "update":
        return client.modules.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            module_id=module_id,
            data=UpdateModule(
                name=opt(name),
                description=opt(description),
                start_date=opt(start_date),
                target_date=opt(target_date),
                status=validated_status,
                lead=opt(lead),
                members=members,
                external_source=opt(external_source),
                external_id=opt(external_id),
            ),
        )

    if action == "delete":
        client.modules.delete(
            workspace_slug=workspace_slug, project_id=project_id, module_id=module_id
        )
        return None

    if action == "archive":
        client.modules.archive(
            workspace_slug=workspace_slug, project_id=project_id, module_id=module_id
        )
        return None

    if action == "unarchive":
        client.modules.unarchive(
            workspace_slug=workspace_slug, project_id=project_id, module_id=module_id
        )
        return None

    if action == "manage_work_items":
        if not add_ids and not remove_ids:
            return missing(action, "add_ids and/or remove_ids (at least one)")
        if add_ids:
            client.modules.add_work_items(
                workspace_slug=workspace_slug,
                project_id=project_id,
                module_id=module_id,
                issue_ids=add_ids,
            )
        if remove_ids:
            for work_item_id in remove_ids:
                client.modules.remove_work_item(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    module_id=module_id,
                    work_item_id=work_item_id,
                )
        return None

    # action == "list_work_items"
    work_item_params = WorkItemQueryParams(
        pql=opt(pql),
        order_by=opt(order_by),
        per_page=opt(per_page),
        cursor=opt(cursor),
        expand=opt(expand),
        fields=opt(fields),
    )
    try:
        response: PaginatedModuleWorkItemResponse = client.modules.list_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            module_id=module_id,
            params=work_item_params,
        )
    except HttpError as e:
        if pql and e.status_code == 400 and isinstance(e.response, dict) and "pql" in e.response:
            logger.warning("module.list_work_items: invalid PQL %r -> %s", pql, e.response)
            return {
                "error": e.response["pql"],
                "failed_pql": pql,
                "pql_reference": PQL_FULL_REFERENCE,
                "hint": "The PQL above failed. Fix it using the reference and retry module(action='list_work_items').",
            }
        raise
    return {
        "results": [
            item.model_dump() if hasattr(item, "model_dump") else item
            for item in (response.results or [])
        ],
        "total_count": response.total_count,
        "count": response.count,
        "next_cursor": response.next_cursor,
        "prev_cursor": response.prev_cursor,
        "next_page_results": response.next_page_results,
        "prev_page_results": response.prev_page_results,
    }


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="module", description=DOC)
    def _module(
        action: str,
        project_id: str = "",
        module_id: str = "",
        archived: bool = False,
        cursor: str = "",
        per_page: int = 0,
        order_by: str = "",
        name: str = "",
        description: str = "",
        start_date: str = "",
        target_date: str = "",
        status: str = "",
        lead: str = "",
        members: list[str] | None = None,
        external_source: str = "",
        external_id: str = "",
        pql: str = "",
        expand: str = "",
        fields: str = "",
        add_ids: list[str] | None = None,
        remove_ids: list[str] | None = None,
    ) -> (
        Module
        | PaginatedModuleLiteResponse
        | PaginatedArchivedModuleResponse
        | dict[str, Any]
        | str
        | None
    ):
        return _dispatch(
            action, project_id, module_id, archived, cursor, per_page, order_by, name,
            description, start_date, target_date, status, lead, members, external_source,
            external_id, pql, expand, fields, add_ids, remove_ids,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="module", description=DOC)
    def _module(
        action: str,
        project_id: str = "",
        module_id: str = "",
        archived: bool = False,
        cursor: str = "",
        per_page: int = 0,
        order_by: str = "",
        name: str = "",
        description: str = "",
        start_date: str = "",
        target_date: str = "",
        status: str = "",
        lead: str = "",
        members: list[str] | None = None,
        external_source: str = "",
        external_id: str = "",
        pql: str = "",
        expand: str = "",
        fields: str = "",
        add_ids: list[str] | None = None,
        remove_ids: list[str] | None = None,
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, project_id, module_id, archived, cursor, per_page, order_by, name,
                    description, start_date, target_date, status, lead, members, external_source,
                    external_id, pql, expand, fields, add_ids, remove_ids,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
