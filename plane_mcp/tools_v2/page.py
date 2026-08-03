"""Consolidated `page` tool -- collapses the 6 verb-per-resource page tools.

Mirrors plane_mcp/tools_v2/intake.py. Source of truth: plane_mcp/tools/pages.py.

Note: the source module has no update or delete tool for pages, so neither exists here.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from plane.models.pages import CreatePage, Page
from plane.models.work_item_pages import CreateWorkItemPage, WorkItemPage

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools_v2._common import bad_action, json_out, missing, opt

ACTIONS = [
    "list",
    "retrieve",
    "create",
    "list_work_item_pages",
    "attach_to_work_item",
    "detach_from_work_item",
]

DOC = """Manage pages (workspace-level or project-level) and their work item links. Actions:
list (no required params; optional project_id, params e.g. per_page, cursor);
retrieve (page_id; optional project_id);
create (name, description_html; optional project_id, plus optional page fields*);
list_work_item_pages (project_id, work_item_id);
attach_to_work_item (project_id, work_item_id, page_id);
detach_from_work_item (project_id, work_item_id, work_item_page_id).

*optional page fields: access, color, is_locked, archived_at, view_props, logo_props,
external_id, external_source.
For list/retrieve/create, pass project_id for a project page; omit it for a workspace-level page.
description_html is HTML content. archived_at is ISO 8601. access is an integer access level.
detach_from_work_item takes work_item_page_id (the link's id), not the page id."""


def _dispatch(
    action: str,
    project_id: str,
    page_id: str,
    work_item_id: str,
    work_item_page_id: str,
    name: str,
    description_html: str,
    color: str,
    is_locked: bool,
    archived_at: str,
    external_id: str,
    external_source: str,
    access: int | None,
    view_props: dict[str, Any] | None,
    logo_props: dict[str, Any] | None,
    params: dict[str, Any] | None,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        if project_id:
            response = client.pages.list_project_pages(
                workspace_slug=workspace_slug, project_id=project_id, params=params
            )
        else:
            response = client.pages.list_workspace_pages(
                workspace_slug=workspace_slug, params=params
            )
        return response.results

    if action == "retrieve":
        if not page_id:
            return missing(action, "page_id")
        if project_id:
            return client.pages.retrieve_project_page(
                workspace_slug=workspace_slug,
                project_id=project_id,
                page_id=page_id,
            )
        return client.pages.retrieve_workspace_page(
            workspace_slug=workspace_slug,
            page_id=page_id,
        )

    if action == "create":
        if not name or not description_html:
            return missing(action, "name", "description_html")
        data = CreatePage(
            name=name,
            description_html=description_html,
            access=access,
            color=opt(color),
            is_locked=opt(is_locked),
            archived_at=opt(archived_at),
            view_props=view_props,
            logo_props=logo_props,
            external_id=opt(external_id),
            external_source=opt(external_source),
        )
        if project_id:
            return client.pages.create_project_page(
                workspace_slug=workspace_slug,
                project_id=project_id,
                data=data,
            )
        return client.pages.create_workspace_page(
            workspace_slug=workspace_slug,
            data=data,
        )

    if not project_id or not work_item_id:
        return missing(action, "project_id", "work_item_id")

    if action == "list_work_item_pages":
        linked = client.work_items.pages.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
        )
        return linked.results

    if action == "attach_to_work_item":
        if not page_id:
            return missing(action, "page_id")
        return client.work_items.pages.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=CreateWorkItemPage(page_id=page_id),
        )

    # action == "detach_from_work_item"
    if not work_item_page_id:
        return missing(action, "work_item_page_id")
    client.work_items.pages.delete(
        workspace_slug=workspace_slug,
        project_id=project_id,
        work_item_id=work_item_id,
        work_item_page_id=work_item_page_id,
    )
    return None


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="page", description=DOC)
    def _page(
        action: str,
        project_id: str = "",
        page_id: str = "",
        work_item_id: str = "",
        work_item_page_id: str = "",
        name: str = "",
        description_html: str = "",
        color: str = "",
        is_locked: bool = False,
        archived_at: str = "",
        external_id: str = "",
        external_source: str = "",
        access: int | None = None,
        view_props: dict[str, Any] | None = None,
        logo_props: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Page | list[Page] | WorkItemPage | list[WorkItemPage] | str | None:
        return _dispatch(
            action, project_id, page_id, work_item_id, work_item_page_id, name,
            description_html, color, is_locked, archived_at, external_id, external_source,
            access, view_props, logo_props, params,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="page", description=DOC)
    def _page(
        action: str,
        project_id: str = "",
        page_id: str = "",
        work_item_id: str = "",
        work_item_page_id: str = "",
        name: str = "",
        description_html: str = "",
        color: str = "",
        is_locked: bool = False,
        archived_at: str = "",
        external_id: str = "",
        external_source: str = "",
        access: int | None = None,
        view_props: dict[str, Any] | None = None,
        logo_props: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, project_id, page_id, work_item_id, work_item_page_id, name,
                    description_html, color, is_locked, archived_at, external_id, external_source,
                    access, view_props, logo_props, params,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
