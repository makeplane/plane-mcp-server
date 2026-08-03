"""Consolidated `cycle` tool -- collapses the 10 verb-per-resource cycle tools.

Mirrors spike/v2/intake.py. Source of truth: plane_mcp/tools/cycles.py.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastmcp import FastMCP
from fastmcp.utilities.logging import get_logger
from plane.errors.errors import HttpError
from plane.models.cycles import (
    CreateCycle,
    Cycle,
    PaginatedArchivedCycleResponse,
    PaginatedCycleLiteResponse,
    PaginatedCycleWorkItemResponse,
    TransferCycleWorkItemsRequest,
    UpdateCycle,
)
from plane.models.query_params import CycleLiteListQueryParams, LiteListQueryParams, WorkItemQueryParams

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
    "complete",
    "archive",
    "unarchive",
    "list_work_items",
    "manage_work_items",
    "transfer_work_items",
]

DOC = """Manage cycles (time-boxed sprints). Every action requires project_id. Actions:
list (project_id; optional archived, status, cursor, per_page, order_by);
retrieve (project_id, cycle_id);
create (project_id, name, owned_by; plus optional cycle fields*);
update (project_id, cycle_id; optional name, owned_by, plus optional cycle fields*);
delete (project_id, cycle_id);
complete (project_id, cycle_id);
archive (project_id, cycle_id);
unarchive (project_id, cycle_id);
list_work_items (project_id, cycle_id; optional pql, order_by, per_page, cursor, expand, fields);
manage_work_items (project_id, cycle_id; add_ids and/or remove_ids -- at least one required);
transfer_work_items (project_id, cycle_id, new_cycle_id).

*optional cycle fields: description, start_date, end_date, external_source, external_id, timezone.
status filters active cycles: "current" (running now), "upcoming", "completed", "draft" (no dates),
"incomplete" (not yet finished). Ignored when archived=True.
complete: Plane has no explicit complete action -- a cycle is complete once end_date is past, so this
sets end_date to today.
archive: Plane requires end_date in the past, so end_date is set to today first when missing or future.
pql: Plane Query Language filter; call get_pql_reference for syntax. An invalid pql returns the full
PQL reference in the response instead of raising.
Dates are ISO 8601. owned_by / lead values are user UUIDs."""


def _dispatch(
    action: str,
    project_id: str,
    cycle_id: str,
    archived: bool,
    status: str,
    cursor: str,
    per_page: int,
    order_by: str,
    name: str,
    owned_by: str,
    description: str,
    start_date: str,
    end_date: str,
    external_source: str,
    external_id: str,
    timezone: str,
    pql: str,
    expand: str,
    fields: str,
    add_ids: list[str] | None,
    remove_ids: list[str] | None,
    new_cycle_id: str,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)
    if not project_id:
        return missing(action, "project_id")

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        if archived:
            archived_params = LiteListQueryParams(
                cursor=opt(cursor), per_page=opt(per_page), order_by=opt(order_by)
            )
            return client.cycles.list_archived(
                workspace_slug=workspace_slug,
                project_id=project_id,
                params=archived_params.model_dump(exclude_none=True),
            )
        lite_params = CycleLiteListQueryParams(
            cursor=opt(cursor), per_page=opt(per_page), order_by=opt(order_by), status=opt(status)
        )
        return client.cycles.list_lite(
            workspace_slug=workspace_slug, project_id=project_id, params=lite_params
        )

    if action == "create":
        if not name or not owned_by:
            return missing(action, "name", "owned_by")
        return client.cycles.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=CreateCycle(
                name=name,
                owned_by=owned_by,
                description=opt(description),
                start_date=opt(start_date),
                end_date=opt(end_date),
                external_source=opt(external_source),
                external_id=opt(external_id),
                timezone=opt(timezone),
                project_id=project_id,
            ),
        )

    if not cycle_id:
        return missing(action, "cycle_id")

    if action == "retrieve":
        return client.cycles.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id
        )

    if action == "update":
        return client.cycles.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=cycle_id,
            data=UpdateCycle(
                name=opt(name),
                description=opt(description),
                start_date=opt(start_date),
                end_date=opt(end_date),
                owned_by=opt(owned_by),
                external_source=opt(external_source),
                external_id=opt(external_id),
                timezone=opt(timezone),
            ),
        )

    if action == "delete":
        client.cycles.delete(workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id)
        return None

    if action == "complete":
        return client.cycles.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=cycle_id,
            data=UpdateCycle(end_date=date.today().isoformat()),
        )

    if action == "unarchive":
        return client.cycles.unarchive(
            workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id
        )

    if action == "archive":
        today = date.today().isoformat()
        cycle = client.cycles.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id
        )
        cycle_end_date = cycle.end_date if hasattr(cycle, "end_date") else None
        if not cycle_end_date or cycle_end_date > today:
            client.cycles.update(
                workspace_slug=workspace_slug,
                project_id=project_id,
                cycle_id=cycle_id,
                data=UpdateCycle(end_date=today),
            )
        return client.cycles.archive(
            workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id
        )

    if action == "manage_work_items":
        if not add_ids and not remove_ids:
            return missing(action, "add_ids and/or remove_ids (at least one)")
        if add_ids:
            client.cycles.add_work_items(
                workspace_slug=workspace_slug,
                project_id=project_id,
                cycle_id=cycle_id,
                issue_ids=add_ids,
            )
        if remove_ids:
            for work_item_id in remove_ids:
                client.cycles.remove_work_item(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    cycle_id=cycle_id,
                    work_item_id=work_item_id,
                )
        return None

    if action == "transfer_work_items":
        if not new_cycle_id:
            return missing(action, "new_cycle_id")
        client.cycles.transfer_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=cycle_id,
            data=TransferCycleWorkItemsRequest(new_cycle_id=new_cycle_id),
        )
        return None

    # action == "list_work_items"
    params = WorkItemQueryParams(
        pql=opt(pql),
        order_by=opt(order_by),
        per_page=opt(per_page),
        cursor=opt(cursor),
        expand=opt(expand),
        fields=opt(fields),
    )
    try:
        response: PaginatedCycleWorkItemResponse = client.cycles.list_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=cycle_id,
            params=params,
        )
    except HttpError as e:
        if pql and e.status_code == 400 and isinstance(e.response, dict) and "pql" in e.response:
            logger.warning("cycle.list_work_items: invalid PQL %r -> %s", pql, e.response)
            return {
                "error": e.response["pql"],
                "failed_pql": pql,
                "pql_reference": PQL_FULL_REFERENCE,
                "hint": "The PQL above failed. Fix it using the reference and retry cycle(action='list_work_items').",
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
    @mcp.tool(name="cycle", description=DOC)
    def _cycle(
        action: str,
        project_id: str = "",
        cycle_id: str = "",
        archived: bool = False,
        status: str = "",
        cursor: str = "",
        per_page: int = 0,
        order_by: str = "",
        name: str = "",
        owned_by: str = "",
        description: str = "",
        start_date: str = "",
        end_date: str = "",
        external_source: str = "",
        external_id: str = "",
        timezone: str = "",
        pql: str = "",
        expand: str = "",
        fields: str = "",
        add_ids: list[str] | None = None,
        remove_ids: list[str] | None = None,
        new_cycle_id: str = "",
    ) -> (
        Cycle
        | PaginatedCycleLiteResponse
        | PaginatedArchivedCycleResponse
        | dict[str, Any]
        | bool
        | str
        | None
    ):
        return _dispatch(
            action, project_id, cycle_id, archived, status, cursor, per_page, order_by,
            name, owned_by, description, start_date, end_date, external_source, external_id,
            timezone, pql, expand, fields, add_ids, remove_ids, new_cycle_id,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="cycle", description=DOC)
    def _cycle(
        action: str,
        project_id: str = "",
        cycle_id: str = "",
        archived: bool = False,
        status: str = "",
        cursor: str = "",
        per_page: int = 0,
        order_by: str = "",
        name: str = "",
        owned_by: str = "",
        description: str = "",
        start_date: str = "",
        end_date: str = "",
        external_source: str = "",
        external_id: str = "",
        timezone: str = "",
        pql: str = "",
        expand: str = "",
        fields: str = "",
        add_ids: list[str] | None = None,
        remove_ids: list[str] | None = None,
        new_cycle_id: str = "",
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, project_id, cycle_id, archived, status, cursor, per_page, order_by,
                    name, owned_by, description, start_date, end_date, external_source, external_id,
                    timezone, pql, expand, fields, add_ids, remove_ids, new_cycle_id,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
