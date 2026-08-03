"""Consolidated `release_label` tool.

Collapses the 5 tools in plane_mcp/tools/releases/labels.py into one
action-dispatch tool. Two layers: the workspace's palette of release labels
(create/update/delete) and which of those labels are attached to a given
release (manage).
"""

from __future__ import annotations

from fastmcp import FastMCP
from plane.models.releases import (
    AddReleaseItemLabel,
    CreateReleaseLabel,
    PaginatedReleaseLabelResponse,
    ReleaseLabel,
    RemoveReleaseItemLabel,
    UpdateReleaseLabel,
)

from plane_mcp.client import get_plane_client_context
from spike.v2._common import bad_action, json_out, missing, opt, page_params

ACTIONS = ["list", "create", "update", "delete", "manage"]

DOC = """Manage release labels. Actions:
list (no required params; with release_id lists the labels attached to that release, without it lists the workspace palette; optional cursor, per_page);
create (name; optional color e.g. "#4E5355", sort_order);
update (label_id; only the fields you pass are changed: name, color, sort_order);
delete (label_id);
manage (release_id, label_ids, operation="attach"|"detach").

create/update/delete act on the workspace palette; delete removes the label everywhere.
To only remove a label from one release use manage with operation="detach".
manage attaches/detaches labels that already exist in the palette and returns the
release's attached labels afterwards. Palette label names must be unique."""


def _dispatch(
    action: str,
    label_id: str,
    release_id: str,
    name: str,
    color: str,
    sort_order: int | None,
    label_ids: list[str] | None,
    operation: str,
    cursor: str,
    per_page: int,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        params = page_params(cursor, per_page) or {}
        response: PaginatedReleaseLabelResponse
        if release_id:
            response = client.releases.item_labels.list(
                workspace_slug=workspace_slug, release_id=release_id, params=params
            )
        else:
            response = client.releases.labels.list(workspace_slug=workspace_slug, params=params)
        return response.results

    if action == "create":
        if not name:
            return missing(action, "name")
        return client.releases.labels.create(
            workspace_slug=workspace_slug,
            data=CreateReleaseLabel(name=name, color=opt(color), sort_order=sort_order),
        )

    if action == "update":
        if not label_id:
            return missing(action, "label_id")
        return client.releases.labels.update(
            workspace_slug=workspace_slug,
            label_id=label_id,
            data=UpdateReleaseLabel(name=opt(name), color=opt(color), sort_order=sort_order),
        )

    if action == "delete":
        if not label_id:
            return missing(action, "label_id")
        client.releases.labels.delete(workspace_slug=workspace_slug, label_id=label_id)
        return None

    # manage
    if not release_id:
        return missing(action, "release_id")
    if operation not in ("attach", "detach"):
        return missing(action, 'operation ("attach" or "detach")')
    if not label_ids:
        return missing(action, "label_ids")

    item_labels = client.releases.item_labels
    if operation == "attach":
        item_labels.create(
            workspace_slug=workspace_slug,
            release_id=release_id,
            data=AddReleaseItemLabel(label_ids=label_ids),
        )
    else:
        item_labels.delete(
            workspace_slug=workspace_slug,
            release_id=release_id,
            data=RemoveReleaseItemLabel(label_ids=label_ids),
        )
    return item_labels.list(workspace_slug=workspace_slug, release_id=release_id).results


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="release_label", description=DOC)
    def _release_label(
        action: str,
        label_id: str = "",
        release_id: str = "",
        name: str = "",
        color: str = "",
        sort_order: int | None = None,
        label_ids: list[str] | None = None,
        operation: str = "",
        cursor: str = "",
        per_page: int = 0,
    ) -> ReleaseLabel | list[ReleaseLabel] | str | None:
        return _dispatch(
            action, label_id, release_id, name, color, sort_order,
            label_ids, operation, cursor, per_page,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="release_label", description=DOC)
    def _release_label(
        action: str,
        label_id: str = "",
        release_id: str = "",
        name: str = "",
        color: str = "",
        sort_order: int | None = None,
        label_ids: list[str] | None = None,
        operation: str = "",
        cursor: str = "",
        per_page: int = 0,
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, label_id, release_id, name, color, sort_order,
                    label_ids, operation, cursor, per_page,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
