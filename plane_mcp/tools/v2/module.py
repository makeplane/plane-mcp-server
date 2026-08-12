"""Modules (feature groupings) in a project."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.modules import (
    CreateModule,
    Module,
    PaginatedArchivedModuleResponse,
    PaginatedModuleLiteResponse,
    PaginatedModuleWorkItemResponse,
    UpdateModule,
)
from plane.models.query_params import LiteListQueryParams, WorkItemQueryParams
from pydantic import Field

from plane_mcp.client import get_plane_client_context
from plane_mcp.pql_reference import PQL_FIELD_HINT
from plane_mcp.toolkit import (
    Action,
    build_annotations,
    build_description,
    coerce_list,
    envelope,
    missing,
    opt,
    pql_failure,
)

NAME = "module"
TITLE = "Modules"

STATUSES = ("backlog", "planned", "in-progress", "paused", "completed", "cancelled")

ACTIONS = (
    Action("list", ("project_id",), ("archived", "cursor", "per_page", "order_by"), read=True),
    Action("retrieve", ("project_id", "module_id"), read=True),
    Action(
        "create",
        ("project_id", "name"),
        ("description", "start_date", "target_date", "status", "lead", "members", "external_source", "external_id"),
    ),
    Action(
        "update",
        ("project_id", "module_id"),
        (
            "name",
            "description",
            "start_date",
            "target_date",
            "status",
            "lead",
            "members",
            "external_source",
            "external_id",
        ),
        note="only the fields you pass are changed",
    ),
    Action("delete", ("project_id", "module_id"), destructive=True),
    Action(
        "list_work_items",
        ("project_id", "module_id"),
        ("pql", "order_by", "cursor", "per_page", "expand", "fields"),
        read=True,
    ),
    Action(
        "manage_work_items",
        ("project_id", "module_id"),
        ("add_ids", "remove_ids"),
        note="pass at least one of add_ids or remove_ids",
    ),
    Action("archive", ("project_id", "module_id")),
    Action("unarchive", ("project_id", "module_id")),
)

FOOTER = (
    f"status is one of: {', '.join(STATUSES)}. Dates are ISO 8601 (YYYY-MM-DD). "
    "lead and members are member ids. " + PQL_FIELD_HINT
)

LEGACY = {
    "list_modules": "list",
    "retrieve_module": "retrieve",
    "create_module": "create",
    "update_module": "update",
    "delete_module": "delete",
    "list_module_work_items": "list_work_items",
    "manage_module_work_items": "manage_work_items",
}

LEGACY_UNMAPPED = {
    "manage_module_archive": "took archive=bool, which spans two actions: use archive or unarchive",
}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("Modules (feature groupings) in a project.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def module(
        action: Literal[
            "list",
            "retrieve",
            "create",
            "update",
            "delete",
            "list_work_items",
            "manage_work_items",
            "archive",
            "unarchive",
        ],
        project_id: str = "",
        module_id: str = "",
        name: str = "",
        description: str = "",
        start_date: str = "",
        target_date: str = "",
        status: str = "",
        lead: str = "",
        members: str = "",
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
    ) -> Module | PaginatedModuleLiteResponse | PaginatedArchivedModuleResponse | dict[str, Any] | str | None:
        client, workspace_slug = get_plane_client_context()

        if not project_id:
            return missing(action, "project_id")

        if status and status not in STATUSES:
            return f"Error: status must be one of: {', '.join(STATUSES)}."

        if action == "list":
            params = LiteListQueryParams(cursor=opt(cursor), per_page=opt(per_page), order_by=opt(order_by))
            if archived:
                return client.modules.list_archived(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    params=params.model_dump(exclude_none=True),
                )
            return client.modules.list_lite(workspace_slug=workspace_slug, project_id=project_id, params=params)

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
                    status=opt(status),
                    lead=opt(lead),
                    members=coerce_list(members),
                    external_source=opt(external_source),
                    external_id=opt(external_id),
                ),
            )

        if not module_id:
            return missing(action, "module_id")

        if action == "retrieve":
            return client.modules.retrieve(workspace_slug=workspace_slug, project_id=project_id, module_id=module_id)

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
                    status=opt(status),
                    lead=opt(lead),
                    members=coerce_list(members),
                    external_source=opt(external_source),
                    external_id=opt(external_id),
                ),
            )

        if action == "delete":
            client.modules.delete(workspace_slug=workspace_slug, project_id=project_id, module_id=module_id)
            return None

        if action == "list_work_items":
            try:
                response: PaginatedModuleWorkItemResponse = client.modules.list_work_items(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    module_id=module_id,
                    params=WorkItemQueryParams(
                        pql=opt(pql),
                        order_by=opt(order_by),
                        per_page=opt(per_page),
                        cursor=opt(cursor),
                        expand=opt(expand),
                        fields=opt(fields),
                    ),
                )
            except HttpError as exc:
                failure = pql_failure(NAME, action, pql, exc)
                if failure is None:
                    raise
                return failure
            return envelope(response, opt(fields))

        if action == "manage_work_items":
            add = coerce_list(add_ids)
            remove = coerce_list(remove_ids)
            if not add and not remove:
                return missing(action, "add_ids or remove_ids")
            if add:
                client.modules.add_work_items(
                    workspace_slug=workspace_slug, project_id=project_id, module_id=module_id, issue_ids=add
                )
            for work_item_id in remove or []:
                client.modules.remove_work_item(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    module_id=module_id,
                    work_item_id=work_item_id,
                )
            return None

        if action == "archive":
            client.modules.archive(workspace_slug=workspace_slug, project_id=project_id, module_id=module_id)
            return None

        client.modules.unarchive(workspace_slug=workspace_slug, project_id=project_id, module_id=module_id)
        return None
