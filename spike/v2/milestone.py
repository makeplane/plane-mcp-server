"""Consolidated `milestone` tool -- collapses the 7 verb-per-resource milestone tools.

Mirrors spike/v2/intake.py. Source of truth: plane_mcp/tools/milestones.py.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from plane.models.milestones import (
    CreateMilestone,
    Milestone,
    MilestoneWorkItem,
    PaginatedMilestoneResponse,
    PaginatedMilestoneWorkItemResponse,
    UpdateMilestone,
)

from plane_mcp.client import get_plane_client_context
from spike.v2._common import bad_action, json_out, missing, opt

ACTIONS = [
    "list",
    "retrieve",
    "create",
    "update",
    "delete",
    "list_work_items",
    "manage_work_items",
]

DOC = """Manage project milestones. Every action requires project_id. Actions:
list (project_id; optional params e.g. per_page, cursor);
retrieve (project_id, milestone_id);
create (project_id, title; optional target_date, external_source, external_id);
update (project_id, milestone_id; optional title, target_date, external_source, external_id);
delete (project_id, milestone_id);
list_work_items (project_id, milestone_id; optional params e.g. per_page, cursor);
manage_work_items (project_id, milestone_id; add_ids and/or remove_ids -- at least one required).

target_date is ISO 8601. add_ids/remove_ids are work item UUIDs."""


def _dispatch(
    action: str,
    project_id: str,
    milestone_id: str,
    title: str,
    target_date: str,
    external_source: str,
    external_id: str,
    add_ids: list[str] | None,
    remove_ids: list[str] | None,
    params: dict[str, Any] | None,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)
    if not project_id:
        return missing(action, "project_id")

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        response: PaginatedMilestoneResponse = client.milestones.list(
            workspace_slug=workspace_slug, project_id=project_id, params=params
        )
        return response.results

    if action == "create":
        if not title:
            return missing(action, "title")
        return client.milestones.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=CreateMilestone(
                title=title,
                target_date=opt(target_date),
                external_source=opt(external_source),
                external_id=opt(external_id),
            ),
        )

    if not milestone_id:
        return missing(action, "milestone_id")

    if action == "retrieve":
        return client.milestones.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, milestone_id=milestone_id
        )

    if action == "update":
        return client.milestones.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            milestone_id=milestone_id,
            data=UpdateMilestone(
                title=opt(title),
                target_date=opt(target_date),
                external_source=opt(external_source),
                external_id=opt(external_id),
            ),
        )

    if action == "delete":
        client.milestones.delete(
            workspace_slug=workspace_slug, project_id=project_id, milestone_id=milestone_id
        )
        return None

    if action == "manage_work_items":
        if not add_ids and not remove_ids:
            return missing(action, "add_ids and/or remove_ids (at least one)")
        if add_ids:
            client.milestones.add_work_items(
                workspace_slug=workspace_slug,
                project_id=project_id,
                milestone_id=milestone_id,
                issue_ids=add_ids,
            )
        if remove_ids:
            client.milestones.remove_work_items(
                workspace_slug=workspace_slug,
                project_id=project_id,
                milestone_id=milestone_id,
                issue_ids=remove_ids,
            )
        return None

    # action == "list_work_items"
    work_items: PaginatedMilestoneWorkItemResponse = client.milestones.list_work_items(
        workspace_slug=workspace_slug,
        project_id=project_id,
        milestone_id=milestone_id,
        params=params,
    )
    return work_items.results


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="milestone", description=DOC)
    def _milestone(
        action: str,
        project_id: str = "",
        milestone_id: str = "",
        title: str = "",
        target_date: str = "",
        external_source: str = "",
        external_id: str = "",
        add_ids: list[str] | None = None,
        remove_ids: list[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Milestone | list[Milestone] | list[MilestoneWorkItem] | str | None:
        return _dispatch(
            action, project_id, milestone_id, title, target_date, external_source,
            external_id, add_ids, remove_ids, params,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="milestone", description=DOC)
    def _milestone(
        action: str,
        project_id: str = "",
        milestone_id: str = "",
        title: str = "",
        target_date: str = "",
        external_source: str = "",
        external_id: str = "",
        add_ids: list[str] | None = None,
        remove_ids: list[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, project_id, milestone_id, title, target_date, external_source,
                    external_id, add_ids, remove_ids, params,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
