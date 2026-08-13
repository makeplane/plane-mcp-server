"""Comments on a work item."""

from __future__ import annotations

from typing import Literal

from fastmcp import FastMCP
from plane.models.work_items import CreateWorkItemComment, UpdateWorkItemComment, WorkItemComment

from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import Action, build_annotations, build_description, missing, needs, opt, page_params

NAME = "workitem_comment"
TITLE = "Work item comments"

ACTIONS = (
    Action("list", ("project_id", "workitem_id"), ("cursor", "per_page"), read=True),
    Action("retrieve", ("project_id", "workitem_id", "comment_id"), read=True),
    Action(
        "create",
        ("project_id", "workitem_id", "comment_html"),
        ("access", "external_source", "external_id"),
    ),
    Action(
        "update",
        ("project_id", "workitem_id", "comment_id"),
        ("comment_html", "access", "external_source", "external_id"),
    ),
    Action("delete", ("project_id", "workitem_id", "comment_id"), destructive=True),
)

FOOTER = "comment_html is HTML, e.g. '<p>Looks good.</p>'. access is INTERNAL or EXTERNAL."

LEGACY = {
    "list_work_item_comments": "list",
    "retrieve_work_item_comment": "retrieve",
    "create_work_item_comment": "create",
    "update_work_item_comment": "update",
    "delete_work_item_comment": "delete",
}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("Comments on a work item.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def workitem_comment(
        action: Literal["list", "retrieve", "create", "update", "delete"],
        project_id: str = "",
        workitem_id: str = "",
        comment_id: str = "",
        comment_html: str = "",
        access: str = "",
        external_source: str = "",
        external_id: str = "",
        cursor: str = "",
        per_page: int = 0,
    ) -> WorkItemComment | list[WorkItemComment] | str | None:
        client, workspace_slug = get_plane_client_context()

        if error := needs(action, project_id=project_id, workitem_id=workitem_id):
            return error

        if action == "list":
            return client.work_items.comments.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=workitem_id,
                params=page_params(cursor, per_page),
            )

        if action == "create":
            if not comment_html:
                return missing(action, "comment_html")
            return client.work_items.comments.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=workitem_id,
                data=CreateWorkItemComment(
                    comment_html=comment_html,
                    access=opt(access),
                    external_source=opt(external_source),
                    external_id=opt(external_id),
                ),
            )

        if not comment_id:
            return missing(action, "comment_id")

        if action == "retrieve":
            return client.work_items.comments.retrieve(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=workitem_id,
                comment_id=comment_id,
            )

        if action == "update":
            return client.work_items.comments.update(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=workitem_id,
                comment_id=comment_id,
                data=UpdateWorkItemComment(
                    comment_html=opt(comment_html),
                    access=opt(access),
                    external_source=opt(external_source),
                    external_id=opt(external_id),
                ),
            )

        client.work_items.comments.delete(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=workitem_id,
            comment_id=comment_id,
        )
        return None
