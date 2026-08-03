"""Variants C and D: consolidated intake tool, action-dispatch.

Both collapse the 5 current intake tools into 1. They differ only in the return
annotation, which is what drives `outputSchema` generation:

  C -- `-> IntakeWorkItem | list[IntakeWorkItem] | None`  (typed union, schema kept)
  D -- `-> str`                                           (JSON string, no schema)

Behaviour is otherwise identical, so the measured delta between them isolates
the cost of the output schema from the cost of consolidation.
"""

from __future__ import annotations

import json
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


def _missing(action: str, *names: str) -> str:
    return f"Error: action '{action}' requires: {', '.join(names)}."


def _bad_action(action: str) -> str:
    return f"Error: unknown action '{action}'. Must be one of: {', '.join(ACTIONS)}."


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
    """Shared handler. Returns a model/list/None, or a str on validation error."""
    if action not in ACTIONS:
        return _bad_action(action)
    if not project_id:
        return _missing(action, "project_id")

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
            return _missing(action, "data")
        return client.intake.create(
            workspace_slug=workspace_slug, project_id=project_id, data=CreateIntakeWorkItem(**data)
        )

    # retrieve / update / delete all need work_item_id
    if not work_item_id:
        return _missing(action, "work_item_id")

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

    # update
    if status == 0 and not snoozed_till:
        return _missing(action, "snoozed_till (required when status=0)")
    if status == 2 and not duplicate_to:
        return _missing(action, "duplicate_to (required when status=2)")

    intake_data = UpdateIntakeWorkItem(
        status=status,
        snoozed_till=snoozed_till or None,
        duplicate_to=duplicate_to or None,
        source=source or None,
        source_email=source_email or None,
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


def register_variant_c(mcp: FastMCP) -> None:
    """Consolidated, typed union return -> outputSchema still generated."""

    @mcp.tool(description=DOC)
    def intake(
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


def register_variant_d(mcp: FastMCP) -> None:
    """Consolidated, JSON-string return -> no outputSchema."""

    @mcp.tool(description=DOC)
    def intake(
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
            result = _dispatch(
                action, project_id, work_item_id, data, params,
                status, snoozed_till, duplicate_to, source, source_email,
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
        if result is None:
            return "Deleted"
        if isinstance(result, str):
            return result
        if isinstance(result, list):
            return json.dumps([r.model_dump(mode="json") for r in result], indent=2, default=str)
        return json.dumps(result.model_dump(mode="json"), indent=2, default=str)

