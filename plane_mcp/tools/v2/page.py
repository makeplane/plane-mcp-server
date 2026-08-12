"""Pages, at workspace or project scope, and their links to work items.

Every page action is scoped by whether project_id is supplied: with it the page
is a project page, without it a workspace page. The SDK has a separate endpoint
pair for each, so the branch is explicit rather than a default.
"""

from __future__ import annotations

from typing import Any, Literal

from fastmcp import FastMCP
from plane.models.pages import CreatePage, Page
from plane.models.query_params import PaginatedQueryParams
from plane.models.work_item_pages import CreateWorkItemPage, WorkItemPage

from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import Action, as_params, build_annotations, build_description, missing, opt

NAME = "page"
TITLE = "Pages"

ACTIONS = (
    Action(
        "list", (), ("project_id", "cursor", "per_page"), note="workspace pages unless project_id is given", read=True
    ),
    Action("retrieve", ("page_id",), ("project_id",), read=True),
    Action(
        "create",
        ("name", "description_html"),
        ("project_id", "access", "color", "is_locked", "external_source", "external_id"),
    ),
    Action("list_work_item_pages", ("project_id", "work_item_id"), read=True),
    Action("attach_to_work_item", ("project_id", "work_item_id", "page_id")),
    Action(
        "detach_from_work_item",
        ("project_id", "work_item_id", "work_item_page_id"),
        note="work_item_page_id is the link id from list_work_item_pages, not the page id",
        destructive=True,
    ),
)

FOOTER = (
    "description_html is the page body as HTML. access is the page access level. "
    "Omit project_id to work with workspace-level pages."
)

LEGACY = {
    "list_pages": "list",
    "retrieve_page": "retrieve",
    "create_page": "create",
    "list_work_item_pages": "list_work_item_pages",
    "attach_page_to_work_item": "attach_to_work_item",
    "detach_page_from_work_item": "detach_from_work_item",
}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("Pages at workspace or project scope.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def page(
        action: Literal[
            "list",
            "retrieve",
            "create",
            "list_work_item_pages",
            "attach_to_work_item",
            "detach_from_work_item",
        ],
        project_id: str = "",
        page_id: str = "",
        work_item_id: str = "",
        work_item_page_id: str = "",
        name: str = "",
        description_html: str = "",
        # Left unset rather than defaulted: 0 is a real access level.
        access: int | None = None,
        color: str = "",
        is_locked: bool | None = None,
        external_source: str = "",
        external_id: str = "",
        cursor: str = "",
        per_page: int = 0,
    ) -> Page | list[Page] | WorkItemPage | list[WorkItemPage] | dict[str, Any] | str | None:
        client, workspace_slug = get_plane_client_context()

        if action == "list":
            params = as_params(PaginatedQueryParams, cursor=cursor, per_page=per_page)
            if project_id:
                response = client.pages.list_project_pages(
                    workspace_slug=workspace_slug, project_id=project_id, params=params
                )
            else:
                response = client.pages.list_workspace_pages(workspace_slug=workspace_slug, params=params)
            return response.results

        if action == "retrieve":
            if not page_id:
                return missing(action, "page_id")
            if project_id:
                return client.pages.retrieve_project_page(
                    workspace_slug=workspace_slug, project_id=project_id, page_id=page_id
                )
            return client.pages.retrieve_workspace_page(workspace_slug=workspace_slug, page_id=page_id)

        if action == "create":
            if not name or not description_html:
                return missing(action, "name", "description_html")
            data = CreatePage(
                name=name,
                description_html=description_html,
                access=access,
                color=opt(color),
                is_locked=is_locked,
                external_id=opt(external_id),
                external_source=opt(external_source),
            )
            if project_id:
                return client.pages.create_project_page(workspace_slug=workspace_slug, project_id=project_id, data=data)
            return client.pages.create_workspace_page(workspace_slug=workspace_slug, data=data)

        if not project_id or not work_item_id:
            return missing(action, "project_id", "work_item_id")

        if action == "list_work_item_pages":
            response = client.work_items.pages.list(
                workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id
            )
            return response.results

        if action == "attach_to_work_item":
            if not page_id:
                return missing(action, "page_id")
            return client.work_items.pages.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
                data=CreateWorkItemPage(page_id=page_id),
            )

        if not work_item_page_id:
            return missing(action, "work_item_page_id")
        client.work_items.pages.delete(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            work_item_page_id=work_item_page_id,
        )
        return None
