"""Workspace and project members, and the role definitions they can hold."""

from __future__ import annotations

from typing import Literal

from fastmcp import FastMCP
from plane.models.query_params import MemberListQueryParams, MemberQueryParams
from plane.models.v2.members import CreateProjectMember, UpdateProjectMember

from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import Action, build_annotations, build_description, missing, needs, one_of, opt

NAME = "member"
TITLE = "Members and roles"

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
    Action("list_project", ("project_id",), read=True),
    Action("list_roles", optional=("namespace", "cursor", "per_page"), read=True),
    Action("retrieve_role", ("role_id",), read=True),
    Action("add_to_project", ("project_id", "user_id"), ("role_slug",)),
    Action("change_project_role", ("project_id", "user_id", "role_slug")),
    Action("remove_from_project", ("project_id", "user_id"), destructive=True),
)

PROJECT_ROLES = ("admin", "contributor", "guest")

FOOTER = (
    "namespace is 'workspace' (Owner/Admin/Member/Guest) or 'project' "
    "(Admin/Contributor/Commenter/Guest); omit for both. A role slug is stable but not "
    "globally unique -- key on (namespace, slug). user_id is a person's id from list_workspace. "
    "A project role is admin, contributor or guest; a workspace guest can only be a project guest."
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
            "list_roles",
            "retrieve_role",
            "add_to_project",
            "change_project_role",
            "remove_from_project",
        ],
        project_id: str = "",
        user_id: str = "",
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
            # Removed members keep an inactive row; the roster is who is on the project now.
            return client.projects.get_members(
                workspace_slug=workspace_slug, project_id=project_id, params=MemberQueryParams(is_active=True)
            )

        if action in ("add_to_project", "change_project_role", "remove_from_project"):
            if error := needs(action, project_id=project_id, user_id=user_id):
                return error
            if action == "change_project_role" and not role_slug:
                return missing(action, "role_slug")
            if error := one_of("role_slug", role_slug, PROJECT_ROLES):
                return error
            members = client.v2.workspaces.projects.members
            if action == "add_to_project":
                return members.create(
                    workspace_slug, project_id, CreateProjectMember(member_id=user_id, role=opt(role_slug))
                )
            # Plane addresses a membership by its own id; the caller knows the person's.
            membership = next(iter(members.list(workspace_slug, project_id, member_id=user_id).data), None)
            if membership is None:
                return "Error: that person is not a member of this project."
            if action == "change_project_role":
                return members.update(workspace_slug, project_id, membership.id, UpdateProjectMember(role=role_slug))
            members.delete(workspace_slug, project_id, membership.id)
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
