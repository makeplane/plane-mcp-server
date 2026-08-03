"""Consolidated `project` tool.

Collapses 8 verb-per-resource tools from plane_mcp/tools/projects.py:
list_projects, retrieve_project, create_project, update_project, delete_project,
manage_project_archive, update_project_features, get_project_worklog_summary.
"""

from __future__ import annotations

from typing import get_args

from fastmcp import FastMCP
from plane.models.enums import TimezoneEnum
from plane.models.projects import (
    CreateProject,
    PaginatedProjectLiteResponse,
    Project,
    ProjectFeature,
    ProjectWorklogSummary,
    UpdateProject,
)
from plane.models.query_params import ProjectLiteListQueryParams

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools_v2._common import bad_action, json_out, missing, opt

ACTIONS = [
    "list",
    "retrieve",
    "create",
    "update",
    "delete",
    "archive",
    "unarchive",
    "update_features",
    "worklog_summary",
]

DOC = """Manage projects. Actions:
list (no required params; optional cursor, per_page 1-1000 default 1000, order_by with '-' prefix for descending);
retrieve (project_id);
create (name, identifier; optional description, project_lead, default_assignee, emoji, cover_image, module_view, cycle_view, issue_views_view, page_view, intake_view, guest_view_all_features, archive_in, close_in, timezone, external_source, external_id, is_issue_type_enabled);
update (project_id; plus any of name, description, project_lead, default_assignee, identifier, emoji, cover_image, network, module_view, cycle_view, issue_views_view, page_view, intake_view, guest_view_all_features, archive_in, close_in, timezone, external_source, external_id, is_issue_type_enabled, is_time_tracking_enabled, default_state, estimate);
delete (project_id);
archive (project_id);
unarchive (project_id);
update_features (project_id; plus any of modules, cycles, views, pages, intakes, work_item_types);
worklog_summary (project_id).

list returns a lite, paginated envelope (id, identifier, name, description, emoji,
icon_prop, cover_image, cover_image_url, archived_at) -- page again while
next_page_results is true; use retrieve for full detail.
Archiving hides a project from active lists but preserves work items, cycles and modules.
network is project visibility: 0 = secret, 2 = public.
timezone is ignored unless it matches an allowed Plane timezone value.
project_lead / default_assignee / default_state are UUIDs."""


def _dispatch(
    action: str,
    project_id: str = "",
    cursor: str = "",
    per_page: int = 0,
    order_by: str = "",
    name: str = "",
    identifier: str = "",
    description: str = "",
    project_lead: str = "",
    default_assignee: str = "",
    emoji: str = "",
    cover_image: str = "",
    network: int | None = None,
    module_view: bool | None = None,
    cycle_view: bool | None = None,
    issue_views_view: bool | None = None,
    page_view: bool | None = None,
    intake_view: bool | None = None,
    guest_view_all_features: bool | None = None,
    archive_in: int | None = None,
    close_in: int | None = None,
    timezone: str = "",
    external_source: str = "",
    external_id: str = "",
    is_issue_type_enabled: bool | None = None,
    is_time_tracking_enabled: bool | None = None,
    default_state: str = "",
    estimate: str = "",
    modules: bool | None = None,
    cycles: bool | None = None,
    views: bool | None = None,
    pages: bool | None = None,
    intakes: bool | None = None,
    work_item_types: bool | None = None,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        params = ProjectLiteListQueryParams(
            cursor=opt(cursor),
            per_page=opt(per_page),
            order_by=opt(order_by),
            include_archived=False,
        )
        return client.projects.list_lite(workspace_slug=workspace_slug, params=params)

    if action == "create":
        if not name or not identifier:
            return missing(action, "name", "identifier")

        # Validate timezone against allowed literal values
        validated_timezone: TimezoneEnum | None = (
            timezone if timezone in get_args(TimezoneEnum) else None  # type: ignore[assignment]
        )

        return client.projects.create(
            workspace_slug=workspace_slug,
            data=CreateProject(
                name=name,
                identifier=identifier,
                description=opt(description),
                project_lead=opt(project_lead),
                default_assignee=opt(default_assignee),
                emoji=opt(emoji),
                cover_image=opt(cover_image),
                module_view=module_view,
                cycle_view=cycle_view,
                issue_views_view=issue_views_view,
                page_view=page_view,
                intake_view=intake_view,
                guest_view_all_features=guest_view_all_features,
                archive_in=archive_in,
                close_in=close_in,
                timezone=validated_timezone,
                external_source=opt(external_source),
                external_id=opt(external_id),
                is_issue_type_enabled=is_issue_type_enabled,
            ),
        )

    if not project_id:
        return missing(action, "project_id")

    if action == "retrieve":
        return client.projects.retrieve(workspace_slug=workspace_slug, project_id=project_id)

    if action == "update":
        if network is not None and network not in {0, 2}:
            return missing(action, "network must be 0 (secret) or 2 (public)")

        # Validate timezone against allowed literal values
        validated_tz: TimezoneEnum | None = (
            timezone if timezone in get_args(TimezoneEnum) else None  # type: ignore[assignment]
        )

        return client.projects.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=UpdateProject(
                name=opt(name),
                description=opt(description),
                project_lead=opt(project_lead),
                default_assignee=opt(default_assignee),
                identifier=opt(identifier),
                emoji=opt(emoji),
                cover_image=opt(cover_image),
                network=network,
                module_view=module_view,
                cycle_view=cycle_view,
                issue_views_view=issue_views_view,
                page_view=page_view,
                intake_view=intake_view,
                guest_view_all_features=guest_view_all_features,
                archive_in=archive_in,
                close_in=close_in,
                timezone=validated_tz,
                external_source=opt(external_source),
                external_id=opt(external_id),
                is_issue_type_enabled=is_issue_type_enabled,
                is_time_tracking_enabled=is_time_tracking_enabled,
                default_state=opt(default_state),
                estimate=opt(estimate),
            ),
        )

    if action == "delete":
        client.projects.delete(workspace_slug=workspace_slug, project_id=project_id)
        return None

    if action == "archive":
        client.projects.archive(workspace_slug=workspace_slug, project_id=project_id)
        return None

    if action == "unarchive":
        client.projects.unarchive(workspace_slug=workspace_slug, project_id=project_id)
        return None

    if action == "update_features":
        return client.projects.update_features(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=ProjectFeature(
                modules=modules,
                cycles=cycles,
                views=views,
                pages=pages,
                intakes=intakes,
                work_item_types=work_item_types,
            ),
        )

    return client.projects.get_worklog_summary(
        workspace_slug=workspace_slug, project_id=project_id
    )


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="project", description=DOC)
    def _project(
        action: str,
        project_id: str = "",
        cursor: str = "",
        per_page: int = 0,
        order_by: str = "",
        name: str = "",
        identifier: str = "",
        description: str = "",
        project_lead: str = "",
        default_assignee: str = "",
        emoji: str = "",
        cover_image: str = "",
        network: int | None = None,
        module_view: bool | None = None,
        cycle_view: bool | None = None,
        issue_views_view: bool | None = None,
        page_view: bool | None = None,
        intake_view: bool | None = None,
        guest_view_all_features: bool | None = None,
        archive_in: int | None = None,
        close_in: int | None = None,
        timezone: str = "",
        external_source: str = "",
        external_id: str = "",
        is_issue_type_enabled: bool | None = None,
        is_time_tracking_enabled: bool | None = None,
        default_state: str = "",
        estimate: str = "",
        modules: bool | None = None,
        cycles: bool | None = None,
        views: bool | None = None,
        pages: bool | None = None,
        intakes: bool | None = None,
        work_item_types: bool | None = None,
    ) -> (
        Project
        | PaginatedProjectLiteResponse
        | ProjectFeature
        | list[ProjectWorklogSummary]
        | str
        | None
    ):
        return _dispatch(
            action,
            project_id=project_id,
            cursor=cursor,
            per_page=per_page,
            order_by=order_by,
            name=name,
            identifier=identifier,
            description=description,
            project_lead=project_lead,
            default_assignee=default_assignee,
            emoji=emoji,
            cover_image=cover_image,
            network=network,
            module_view=module_view,
            cycle_view=cycle_view,
            issue_views_view=issue_views_view,
            page_view=page_view,
            intake_view=intake_view,
            guest_view_all_features=guest_view_all_features,
            archive_in=archive_in,
            close_in=close_in,
            timezone=timezone,
            external_source=external_source,
            external_id=external_id,
            is_issue_type_enabled=is_issue_type_enabled,
            is_time_tracking_enabled=is_time_tracking_enabled,
            default_state=default_state,
            estimate=estimate,
            modules=modules,
            cycles=cycles,
            views=views,
            pages=pages,
            intakes=intakes,
            work_item_types=work_item_types,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="project", description=DOC)
    def _project(
        action: str,
        project_id: str = "",
        cursor: str = "",
        per_page: int = 0,
        order_by: str = "",
        name: str = "",
        identifier: str = "",
        description: str = "",
        project_lead: str = "",
        default_assignee: str = "",
        emoji: str = "",
        cover_image: str = "",
        network: int | None = None,
        module_view: bool | None = None,
        cycle_view: bool | None = None,
        issue_views_view: bool | None = None,
        page_view: bool | None = None,
        intake_view: bool | None = None,
        guest_view_all_features: bool | None = None,
        archive_in: int | None = None,
        close_in: int | None = None,
        timezone: str = "",
        external_source: str = "",
        external_id: str = "",
        is_issue_type_enabled: bool | None = None,
        is_time_tracking_enabled: bool | None = None,
        default_state: str = "",
        estimate: str = "",
        modules: bool | None = None,
        cycles: bool | None = None,
        views: bool | None = None,
        pages: bool | None = None,
        intakes: bool | None = None,
        work_item_types: bool | None = None,
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action,
                    project_id=project_id,
                    cursor=cursor,
                    per_page=per_page,
                    order_by=order_by,
                    name=name,
                    identifier=identifier,
                    description=description,
                    project_lead=project_lead,
                    default_assignee=default_assignee,
                    emoji=emoji,
                    cover_image=cover_image,
                    network=network,
                    module_view=module_view,
                    cycle_view=cycle_view,
                    issue_views_view=issue_views_view,
                    page_view=page_view,
                    intake_view=intake_view,
                    guest_view_all_features=guest_view_all_features,
                    archive_in=archive_in,
                    close_in=close_in,
                    timezone=timezone,
                    external_source=external_source,
                    external_id=external_id,
                    is_issue_type_enabled=is_issue_type_enabled,
                    is_time_tracking_enabled=is_time_tracking_enabled,
                    default_state=default_state,
                    estimate=estimate,
                    modules=modules,
                    cycles=cycles,
                    views=views,
                    pages=pages,
                    intakes=intakes,
                    work_item_types=work_item_types,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
