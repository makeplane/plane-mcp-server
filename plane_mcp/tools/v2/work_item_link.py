"""External links attached to a work item."""

from __future__ import annotations

from typing import Literal

from fastmcp import FastMCP
from plane.models.work_items import CreateWorkItemLink, UpdateWorkItemLink, WorkItemLink

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.v2._runtime import missing, page_params
from plane_mcp.tools.v2._spec import Action, build_annotations, build_description

NAME = "work_item_link"
TITLE = "Work item links"

ACTIONS = (
    Action("list", ("project_id", "work_item_id"), ("cursor", "per_page"), read=True),
    Action("retrieve", ("project_id", "work_item_id", "link_id"), read=True),
    Action("create", ("project_id", "work_item_id", "url")),
    Action("update", ("project_id", "work_item_id", "link_id", "url")),
    Action("delete", ("project_id", "work_item_id", "link_id"), destructive=True),
)

LEGACY = {
    "list_work_item_links": "list",
    "retrieve_work_item_link": "retrieve",
    "create_work_item_link": "create",
    "update_work_item_link": "update",
    "delete_work_item_link": "delete",
}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("External links attached to a work item.", ACTIONS),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def work_item_link(
        action: Literal["list", "retrieve", "create", "update", "delete"],
        project_id: str = "",
        work_item_id: str = "",
        link_id: str = "",
        url: str = "",
        cursor: str = "",
        per_page: int = 0,
    ) -> WorkItemLink | list[WorkItemLink] | str | None:
        client, workspace_slug = get_plane_client_context()

        if not project_id or not work_item_id:
            return missing(action, "project_id", "work_item_id")

        if action == "list":
            return client.work_items.links.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
                params=page_params(cursor, per_page),
            )

        if action == "create":
            if not url:
                return missing(action, "url")
            return client.work_items.links.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
                data=CreateWorkItemLink(url=url),
            )

        if not link_id:
            return missing(action, "link_id")

        if action == "retrieve":
            return client.work_items.links.retrieve(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
                link_id=link_id,
            )

        if action == "update":
            if not url:
                return missing(action, "url")
            return client.work_items.links.update(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
                link_id=link_id,
                data=UpdateWorkItemLink(url=url),
            )

        client.work_items.links.delete(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            link_id=link_id,
        )
        return None
