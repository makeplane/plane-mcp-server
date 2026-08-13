"""Cycles (time-boxed iterations) in a project."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal, get_args

from fastmcp import FastMCP
from plane.models.cycles import (
    CreateCycle,
    Cycle,
    PaginatedArchivedCycleResponse,
    PaginatedCycleLiteResponse,
    TransferCycleWorkItemsRequest,
    UpdateCycle,
)
from plane.models.enums import CycleStatusEnum
from plane.models.query_params import CycleLiteListQueryParams, LiteListQueryParams
from pydantic import Field

from plane_mcp.client import get_plane_client_context
from plane_mcp.pql_reference import PQL_FIELD_HINT
from plane_mcp.toolkit import (
    Action,
    build_annotations,
    build_description,
    coerce_list,
    missing,
    needs,
    one_of,
    opt,
    workitem_page,
)

NAME = "cycle"
TITLE = "Cycles"

STATUSES = get_args(CycleStatusEnum)

ACTIONS = (
    Action("list", ("project_id",), ("archived", "status", "cursor", "per_page", "order_by"), read=True),
    Action("retrieve", ("project_id", "cycle_id"), read=True),
    Action(
        "create",
        ("project_id", "name", "owned_by"),
        ("description", "start_date", "end_date", "timezone", "external_source", "external_id"),
    ),
    Action(
        "update",
        ("project_id", "cycle_id"),
        ("name", "description", "start_date", "end_date", "owned_by", "timezone", "external_source", "external_id"),
        note="only the fields you pass are changed",
    ),
    Action("delete", ("project_id", "cycle_id"), destructive=True),
    Action(
        "list_workitems",
        ("project_id", "cycle_id"),
        ("pql", "order_by", "cursor", "per_page", "expand", "fields"),
        read=True,
    ),
    Action(
        "manage_workitems",
        ("project_id", "cycle_id"),
        ("add_ids", "remove_ids"),
        note="pass at least one of add_ids or remove_ids; returns nothing, read back with list_workitems",
    ),
    Action("transfer_workitems", ("project_id", "cycle_id", "new_cycle_id"), note="moves everything to new_cycle_id"),
    Action("complete", ("project_id", "cycle_id"), note="sets end_date to today"),
    Action("archive", ("project_id", "cycle_id"), note="ends the cycle first if it is still running"),
    Action("unarchive", ("project_id", "cycle_id")),
)

FOOTER = (
    f"status filters active cycles: {', '.join(STATUSES)}; it is ignored when archived is true. "
    "Dates are ISO 8601 (YYYY-MM-DD). owned_by is a member id. " + PQL_FIELD_HINT
)

LEGACY = {
    "list_cycles": "list",
    "retrieve_cycle": "retrieve",
    "create_cycle": "create",
    "update_cycle": "update",
    "delete_cycle": "delete",
    "list_cycle_work_items": "list_workitems",
    "manage_cycle_work_items": "manage_workitems",
    "transfer_cycle_work_items": "transfer_workitems",
    "complete_cycle": "complete",
}

LEGACY_UNMAPPED = {
    "manage_cycle_archive": "took archive=bool, which spans two actions: use archive or unarchive",
}


def _still_running(end_date: str | None) -> bool:
    """Whether a cycle has yet to end. Absent means open-ended, so it is still running."""
    if not end_date:
        return True
    try:
        return datetime.fromisoformat(end_date).date() > date.today()
    except ValueError:
        return end_date[:10] > date.today().isoformat()


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("Cycles (time-boxed iterations) in a project.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def cycle(
        action: Literal[
            "list",
            "retrieve",
            "create",
            "update",
            "delete",
            "list_workitems",
            "manage_workitems",
            "transfer_workitems",
            "complete",
            "archive",
            "unarchive",
        ],
        project_id: str = "",
        cycle_id: str = "",
        new_cycle_id: str = "",
        name: str = "",
        owned_by: str = "",
        description: str = "",
        start_date: str = "",
        end_date: str = "",
        timezone: str = "",
        status: str = "",
        archived: bool = False,
        add_ids: str = "",
        remove_ids: str = "",
        pql: Annotated[str, Field(description=PQL_FIELD_HINT)] = "",
        expand: str = "",
        fields: str = "",
        external_source: str = "",
        external_id: str = "",
        cursor: str = "",
        per_page: int = 0,
        order_by: str = "",
    ) -> Cycle | PaginatedCycleLiteResponse | PaginatedArchivedCycleResponse | dict[str, Any] | bool | str | None:
        client, workspace_slug = get_plane_client_context()

        if not project_id:
            return missing(action, "project_id")

        if action == "list":
            if archived:
                params = LiteListQueryParams(cursor=opt(cursor), per_page=opt(per_page), order_by=opt(order_by))
                return client.cycles.list_archived(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    params=params.model_dump(exclude_none=True),
                )
            if error := one_of("status", status, STATUSES):
                return error
            return client.cycles.list_lite(
                workspace_slug=workspace_slug,
                project_id=project_id,
                params=CycleLiteListQueryParams(
                    cursor=opt(cursor),
                    per_page=opt(per_page),
                    order_by=opt(order_by),
                    status=opt(status),
                ),
            )

        if action == "create":
            if error := needs(action, name=name, owned_by=owned_by):
                return error
            return client.cycles.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                data=CreateCycle(
                    name=name,
                    owned_by=owned_by,
                    description=opt(description),
                    start_date=opt(start_date),
                    end_date=opt(end_date),
                    timezone=opt(timezone),
                    external_source=opt(external_source),
                    external_id=opt(external_id),
                    project_id=project_id,
                ),
            )

        if not cycle_id:
            return missing(action, "cycle_id")

        if action == "retrieve":
            return client.cycles.retrieve(workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id)

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
                    timezone=opt(timezone),
                    external_source=opt(external_source),
                    external_id=opt(external_id),
                ),
            )

        if action == "delete":
            client.cycles.delete(workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id)
            return None

        if action == "list_workitems":
            return workitem_page(
                NAME,
                action,
                client.cycles.list_work_items,
                pql=pql,
                order_by=order_by,
                cursor=cursor,
                per_page=per_page,
                expand=expand,
                fields=fields,
                workspace_slug=workspace_slug,
                project_id=project_id,
                cycle_id=cycle_id,
            )

        if action == "manage_workitems":
            add = coerce_list(add_ids)
            remove = coerce_list(remove_ids)
            if not add and not remove:
                return missing(action, "add_ids or remove_ids")
            if add:
                client.cycles.add_work_items(
                    workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id, issue_ids=add
                )
            for workitem_id in remove or []:
                client.cycles.remove_work_item(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    cycle_id=cycle_id,
                    work_item_id=workitem_id,
                )
            return None

        if action == "transfer_workitems":
            if not new_cycle_id:
                return missing(action, "new_cycle_id")
            client.cycles.transfer_work_items(
                workspace_slug=workspace_slug,
                project_id=project_id,
                cycle_id=cycle_id,
                data=TransferCycleWorkItemsRequest(new_cycle_id=new_cycle_id),
            )
            return None

        today = date.today().isoformat()

        if action == "complete":
            # Plane has no explicit complete action: a cycle is complete once its end_date has passed.
            return client.cycles.update(
                workspace_slug=workspace_slug,
                project_id=project_id,
                cycle_id=cycle_id,
                data=UpdateCycle(end_date=today),
            )

        if action == "archive":
            current = client.cycles.retrieve(workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id)
            if _still_running(current.end_date):
                client.cycles.update(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    cycle_id=cycle_id,
                    data=UpdateCycle(end_date=today),
                )
            return client.cycles.archive(workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id)

        return client.cycles.unarchive(workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id)
