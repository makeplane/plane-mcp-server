"""Consolidated `intake` tool -- REFERENCE IMPLEMENTATION.

This module is the canonical example every other v2 module should mirror.
Validated live (16/16 checks) against a real workspace; see spike/live_test.py.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from plane.models.intake import (
    CreateIntakeWorkItem,
    IntakeWorkItem,
    PaginatedIntakeWorkItemResponse,
    UpdateIntakeWorkItem,
)
from plane.models.query_params import PaginatedQueryParams, RetrieveQueryParams

from plane_mcp.client import get_plane_client_context
from spike.v2._common import bad_action, json_out, missing, opt

ACTIONS = ["list", "retrieve", "create", "update", "delete"]

DOC = """Manage intake (triage queue) work items. Actions:
list (project_id; optional params e.g. per_page, cursor);
retrieve (project_id, work_item_id; optional params e.g. expand, fields);
create (project_id, data);
update (project_id, work_item_id; plus status and/or snoozed_till, duplicate_to, source, source_email);
delete (project_id, work_item_id).

work_item_id is the `issue` field from an IntakeWorkItem response, not the intake work item's own id.

Status values for update:
    -2 = pending (default/unreviewed)
    -1 = declined
     0 = snoozed (requires snoozed_till)
     1 = accepted (converts intake item to active work item)
     2 = duplicate (requires duplicate_to)"""


def _dispatch(
    action: str,
    project_id: str,
    work_item_id: str,
    data: dict[str, Any] | None,
    params: dict[str, Any] | None,
    status: int | None,
    snoozed_till: str,
    duplicate_to: str,
    source: str,
    source_email: str,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)
    if not project_id:
        return missing(action, "project_id")

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        response: PaginatedIntakeWorkItemResponse = client.intake.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            params=PaginatedQueryParams(**params) if params else None,
        )
        return response.results

    if action == "create":
        if not data:
            return missing(action, "data")
        return client.intake.create(
            workspace_slug=workspace_slug, project_id=project_id, data=CreateIntakeWorkItem(**data)
        )

    if not work_item_id:
        return missing(action, "work_item_id")

    if action == "retrieve":
        return client.intake.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            params=RetrieveQueryParams(**params) if params else None,
        )

    if action == "delete":
        client.intake.delete(
            workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id
        )
        return None

    if status == 0 and not snoozed_till:
        return missing(action, "snoozed_till (required when status=0)")
    if status == 2 and not duplicate_to:
        return missing(action, "duplicate_to (required when status=2)")

    intake_data = UpdateIntakeWorkItem(
        status=status,
        snoozed_till=opt(snoozed_till),
        duplicate_to=opt(duplicate_to),
        source=opt(source),
        source_email=opt(source_email),
    )
    if status is not None or snoozed_till or duplicate_to:
        return client.intake.update_status(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=intake_data,
        )
    return client.intake.update(
        workspace_slug=workspace_slug,
        project_id=project_id,
        work_item_id=work_item_id,
        data=intake_data,
    )


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="intake", description=DOC)
    def _intake(
        action: str,
        project_id: str = "",
        work_item_id: str = "",
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        status: int | None = None,
        snoozed_till: str = "",
        duplicate_to: str = "",
        source: str = "",
        source_email: str = "",
    ) -> IntakeWorkItem | list[IntakeWorkItem] | str | None:
        return _dispatch(
            action, project_id, work_item_id, data, params,
            status, snoozed_till, duplicate_to, source, source_email,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="intake", description=DOC)
    def _intake(
        action: str,
        project_id: str = "",
        work_item_id: str = "",
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        status: int | None = None,
        snoozed_till: str = "",
        duplicate_to: str = "",
        source: str = "",
        source_email: str = "",
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, project_id, work_item_id, data, params,
                    status, snoozed_till, duplicate_to, source, source_email,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
