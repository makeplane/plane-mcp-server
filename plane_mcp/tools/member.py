"""Workspace and project members, and the role definitions they can hold."""

from __future__ import annotations

from typing import Literal

from fastmcp import FastMCP
from plane.models.query_params import MemberListQueryParams
from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import Action, build_annotations, build_description, missing, needs, opt

NAME = "member"
TITLE = "Members and roles"

_PROJECT_ROLE_VALUES = {
    "guest": 5,
    "member": 15,
    "contributor": 15,
    "admin": 20,
}


def _project_role_value(role: str) -> int | None:
    normalized = role.strip().lower()
    if normalized in _PROJECT_ROLE_VALUES:
        return _PROJECT_ROLE_VALUES[normalized]
    if normalized.isdigit():
        value = int(normalized)
        return value if value in {5, 15, 20} else None
    return None

ACTIONS = (
    Action("me", note="the authenticated user", read=True),
    Action(
        "list_workspace",
        optional=(
            "first_name",
            "last_name",
            "email",
            "display_name",
            "role_slug",
            "is_active",
            "is_bot",
            "cursor",
            "per_page",
            "order_by",
        ),
        note="name filters match case-insensitively and combine with AND",
        read=True,
    ),
    Action(
        "list_project",
        ("project_id",),
        note="returns project member users; id is the workspace user UUID (the v1 list API does not expose membership row ids)",
        read=True,
    ),
    Action(
        "add_project",
        ("project_id", "member_id", "role"),
        note="adds an existing workspace user through Plane API v1; returns the created membership row including its id",
    ),
    Action(
        "update_project",
        ("project_id", "membership_id", "role"),
        note="changes the project role through Plane API v1; requires a known membership row id",
    ),
    Action(
        "remove_project",
        ("project_id", "membership_id"),
        note="removes project access through Plane API v1; requires a known membership row id",
        destructive=True,
    ),
    Action("list_roles", optional=("namespace", "cursor", "per_page"), read=True),
    Action("retrieve_role", ("role_id",), read=True),
)

FOOTER = (
    "namespace is 'workspace' (Owner/Admin/Member/Guest) or 'project' "
    "(Admin/Contributor/Commenter/Guest); omit for both. A role slug is stable but not "
    "globally unique -- key on (namespace, slug). For project membership writes, member_id "
    "is the workspace user UUID from list_workspace; membership_id is the project-membership "
    "row id returned when a membership is created (Plane API v1 does not expose row ids in its "
    "project-member list). For writes, role accepts guest (5), member/contributor (15), admin (20), "
    "or the numeric value as a string."
)

LEGACY = {
    "get_me": "me",
    "get_workspace_members": "list_workspace",
    "get_project_members": "list_project",
    "list_roles": "list_roles",
    "retrieve_role": "retrieve_role",
}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("Workspace and project members, and role definitions.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def member(
        action: Literal[
            "me",
            "list_workspace",
            "list_project",
            "add_project",
            "update_project",
            "remove_project",
            "list_roles",
            "retrieve_role",
        ],
        project_id: str = "",
        member_id: str = "",
        membership_id: str = "",
        role: str = "",
        role_id: str = "",
        namespace: str = "",
        first_name: str = "",
        last_name: str = "",
        email: str = "",
        display_name: str = "",
        role_slug: str = "",
        # Tri-state: False filters for inactive/non-bot members, unset filters neither.
        is_active: bool | None = None,
        is_bot: bool | None = None,
        order_by: str = "",
        cursor: str = "",
        per_page: int = 0,
    ):
        client, workspace_slug = get_plane_client_context()

        if action == "me":
            return client.users.get_me()

        if action == "list_workspace":
            return client.workspaces.get_members_lite(
                workspace_slug=workspace_slug,
                params=MemberListQueryParams(
                    first_name=opt(first_name),
                    last_name=opt(last_name),
                    email=opt(email),
                    display_name=opt(display_name),
                    role_slug=opt(role_slug),
                    is_active=is_active,
                    is_bot=is_bot,
                    cursor=opt(cursor),
                    per_page=per_page or 100,
                    order_by=opt(order_by),
                ),
            )

        if action == "list_project":
            if not project_id:
                return missing(action, "project_id")
            return client.projects.get_members(workspace_slug=workspace_slug, project_id=project_id)

        if action == "add_project":
            if error := needs(action, project_id=project_id, member_id=member_id, role=role):
                return error
            role_value = _project_role_value(role)
            if role_value is None:
                return "Error: role must be guest, member, contributor, admin, 5, 15, or 20."
            return client.projects._post(
                f"{workspace_slug}/projects/{project_id}/members",
                {"member": member_id, "role": role_value},
            )

        if action == "update_project":
            if error := needs(action, project_id=project_id, membership_id=membership_id, role=role):
                return error
            role_value = _project_role_value(role)
            if role_value is None:
                return "Error: role must be guest, member, contributor, admin, 5, 15, or 20."
            return client.projects._patch(
                f"{workspace_slug}/projects/{project_id}/members/{membership_id}",
                {"role": role_value},
            )

        if action == "remove_project":
            if error := needs(action, project_id=project_id, membership_id=membership_id):
                return error
            client.projects._delete(
                f"{workspace_slug}/projects/{project_id}/members/{membership_id}"
            )
            return None

        if action == "list_roles":
            return client.roles.list(
                workspace_slug=workspace_slug,
                namespace=opt(namespace),
                per_page=opt(per_page),
                cursor=opt(cursor),
            )

        if not role_id:
            return missing(action, "role_id")
        return client.roles.retrieve(workspace_slug=workspace_slug, role_id=role_id)
