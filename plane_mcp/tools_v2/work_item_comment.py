"""Consolidated `work_item_comment` tool.

Collapses the 5 tools in plane_mcp/tools/work_item_comments.py into a single
action-dispatch tool. SDK calls and validation are ported verbatim.
"""

from __future__ import annotations

from typing import Any, get_args

from fastmcp import FastMCP
from plane.models.enums import AccessEnum
from plane.models.work_items import (
    CreateWorkItemComment,
    PaginatedWorkItemCommentResponse,
    UpdateWorkItemComment,
    WorkItemComment,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools_v2._common import bad_action, json_out, missing, opt

ACTIONS = ["list", "retrieve", "create", "update", "delete"]

DOC = """Manage comments on a work item. Actions:
list (project_id, work_item_id; optional params dict of query parameters);
retrieve (project_id, work_item_id, comment_id);
create (project_id, work_item_id; plus comment_html and/or comment_json, optional access, external_source, external_id);
update (project_id, work_item_id, comment_id; plus comment_html, comment_json, access, external_source, external_id);
delete (project_id, work_item_id, comment_id).

comment_html is HTML -- use <p>, <br>, <ul><li>, <strong>, <code>; never literal newline escapes.
access must be INTERNAL or EXTERNAL; any other value is ignored (sent as unset)."""


def _dispatch(
    action: str,
    project_id: str,
    work_item_id: str,
    comment_id: str,
    comment_html: str,
    comment_json: dict[str, Any] | None,
    access: str,
    external_source: str,
    external_id: str,
    params: dict[str, Any] | None,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)
    if not project_id:
        return missing(action, "project_id")
    if not work_item_id:
        return missing(action, "work_item_id")

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        response: PaginatedWorkItemCommentResponse = client.work_items.comments.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            params=params,
        )
        return response.results

    # Validate access against allowed literal values
    validated_access: AccessEnum | None = (
        access if access in get_args(AccessEnum) else None  # type: ignore[assignment]
    )

    if action == "create":
        return client.work_items.comments.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=CreateWorkItemComment(
                comment_html=opt(comment_html),
                comment_json=comment_json,
                access=validated_access,
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
            work_item_id=work_item_id,
            comment_id=comment_id,
        )

    if action == "update":
        return client.work_items.comments.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            comment_id=comment_id,
            data=UpdateWorkItemComment(
                comment_html=opt(comment_html),
                comment_json=comment_json,
                access=validated_access,
                external_source=opt(external_source),
                external_id=opt(external_id),
            ),
        )

    client.work_items.comments.delete(
        workspace_slug=workspace_slug,
        project_id=project_id,
        work_item_id=work_item_id,
        comment_id=comment_id,
    )
    return None


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="work_item_comment", description=DOC)
    def _work_item_comment(
        action: str,
        project_id: str = "",
        work_item_id: str = "",
        comment_id: str = "",
        comment_html: str = "",
        comment_json: dict[str, Any] | None = None,
        access: str = "",
        external_source: str = "",
        external_id: str = "",
        params: dict[str, Any] | None = None,
    ) -> WorkItemComment | list[WorkItemComment] | str | None:
        return _dispatch(
            action, project_id, work_item_id, comment_id, comment_html,
            comment_json, access, external_source, external_id, params,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="work_item_comment", description=DOC)
    def _work_item_comment(
        action: str,
        project_id: str = "",
        work_item_id: str = "",
        comment_id: str = "",
        comment_html: str = "",
        comment_json: dict[str, Any] | None = None,
        access: str = "",
        external_source: str = "",
        external_id: str = "",
        params: dict[str, Any] | None = None,
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, project_id, work_item_id, comment_id, comment_html,
                    comment_json, access, external_source, external_id, params,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
