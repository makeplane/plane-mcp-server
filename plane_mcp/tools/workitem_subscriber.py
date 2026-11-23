"""The people subscribed to a work item: notified of its changes."""

from __future__ import annotations

from typing import Literal

from fastmcp import FastMCP
from plane.models.v2.work_items import UpdateWorkItemSubscribers, WorkItemSubscribers

from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import Action, build_annotations, build_description, coerce_list, missing, plan_gated

NAME = "workitem_subscriber"
TITLE = "Work item subscribers"

WORKITEM = ("workitem_identifier", "project_id", "workitem_id")

ACTIONS = (
    Action("list", optional=WORKITEM, read=True),
    Action(
        "manage",
        optional=(*WORKITEM, "add_user_id", "remove_user_id"),
        note="each takes one id or several, merged into the list; returns the new list",
    ),
)

FOOTER = (
    "Name the work item by workitem_identifier (ENG-42) or by project_id and workitem_id. Changing only "
    "yourself needs view access; naming anyone else needs edit access and a plan with subscriber management. "
    "New subscribers must be members of the project or a linked teamspace and able to see the work item; "
    "user ids come from `member list_project`."
)

# A new resource: nothing was ever advertised under another name.
LEGACY: dict[str, str] = {}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("Who is subscribed to a work item's updates.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    @plan_gated("Managing other people's subscriptions")
    def workitem_subscriber(
        action: Literal["list", "manage"],
        workitem_identifier: str = "",
        project_id: str = "",
        workitem_id: str = "",
        add_user_id: str = "",
        remove_user_id: str = "",
    ) -> WorkItemSubscribers | str:
        client, workspace_slug = get_plane_client_context()
        subscribers = client.v2.workspaces.projects.work_items.subscribers

        if workitem_identifier:
            target = (workspace_slug, workitem_identifier)
            retrieve, update = subscribers.retrieve_by_identifier, subscribers.update_by_identifier
        elif project_id and workitem_id:
            target = (workspace_slug, project_id, workitem_id)
            retrieve, update = subscribers.retrieve, subscribers.update
        else:
            return missing(action, "workitem_identifier, or project_id and workitem_id")

        if action == "list":
            return retrieve(*target)

        if not add_user_id and not remove_user_id:
            return missing(action, "add_user_id or remove_user_id")
        # The API merges the change itself, so a caller never resends the whole list.
        return update(
            *target,
            UpdateWorkItemSubscribers(add=coerce_list(add_user_id), remove=coerce_list(remove_user_id)),
        )
