"""Consolidated `member` tool.

Collapses 5 tools drawn from four source modules:
- plane_mcp/tools/users.py:      get_me
- plane_mcp/tools/workspaces.py: get_workspace_members
- plane_mcp/tools/projects.py:   get_project_members
- plane_mcp/tools/roles.py:      list_roles, retrieve_role
"""

from __future__ import annotations

from fastmcp import FastMCP
from plane.models.projects import PaginatedProjectMemberResponse
from plane.models.query_params import MemberListQueryParams
from plane.models.roles import PaginatedRoleResponse, Role
from plane.models.users import UserLite
from plane.models.workspaces import PaginatedWorkspaceMemberResponse

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools_v2._common import bad_action, json_out, missing, opt

ACTIONS = ["me", "list_workspace", "list_project", "list_roles", "retrieve_role"]

NAMESPACES = ["workspace", "project"]

DOC = """Look up members, the current user, and role definitions. Actions:
me (no params) -- the current authenticated user;
list_workspace (no required params; optional filters first_name, last_name, email, display_name, role_slug, is_active, is_bot, plus cursor, per_page 1-1000 default 100, order_by);
list_project (project_id; same optional filters as list_workspace);
list_roles (no required params; optional namespace "workspace" or "project", per_page, cursor);
retrieve_role (role_id).

first_name/last_name/email/display_name are case-insensitive "contains" matches;
role_slug is exact; all filters combine with AND.
Member lists return a paginated envelope (results incl. role, role_slug, is_active,
is_bot, plus total_count, next_cursor, next_page_results) -- page again while
next_page_results is true.
list_roles: namespace="workspace" gives Owner/Admin/Member/Guest; "project" gives the
project-role definitions shared across all projects (Admin/Contributor/Commenter/Guest);
omit for both. slug is stable but NOT globally unique (admin/guest exist in both
namespaces) -- key by (namespace, slug)."""


def _dispatch(
    action: str,
    project_id: str = "",
    role_id: str = "",
    namespace: str = "",
    first_name: str = "",
    last_name: str = "",
    email: str = "",
    display_name: str = "",
    role_slug: str = "",
    is_active: bool | None = None,
    is_bot: bool | None = None,
    cursor: str = "",
    per_page: int = 0,
    order_by: str = "",
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)

    client, workspace_slug = get_plane_client_context()

    if action == "me":
        return client.users.get_me()

    if action == "list_roles":
        if namespace and namespace not in NAMESPACES:
            return missing(action, f"namespace must be one of: {', '.join(NAMESPACES)}")
        return client.roles.list(
            workspace_slug=workspace_slug,
            namespace=opt(namespace),  # type: ignore[arg-type]
            per_page=opt(per_page),
            cursor=opt(cursor),
        )

    if action == "retrieve_role":
        if not role_id:
            return missing(action, "role_id")
        return client.roles.retrieve(workspace_slug=workspace_slug, role_id=role_id)

    params = MemberListQueryParams(
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
    )

    if action == "list_workspace":
        return client.workspaces.get_members_lite(workspace_slug=workspace_slug, params=params)

    if not project_id:
        return missing(action, "project_id")
    return client.projects.get_members_lite(
        workspace_slug=workspace_slug, project_id=project_id, params=params
    )


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="member", description=DOC)
    def _member(
        action: str,
        project_id: str = "",
        role_id: str = "",
        namespace: str = "",
        first_name: str = "",
        last_name: str = "",
        email: str = "",
        display_name: str = "",
        role_slug: str = "",
        is_active: bool | None = None,
        is_bot: bool | None = None,
        cursor: str = "",
        per_page: int = 0,
        order_by: str = "",
    ) -> (
        UserLite
        | PaginatedWorkspaceMemberResponse
        | PaginatedProjectMemberResponse
        | PaginatedRoleResponse
        | Role
        | str
        | None
    ):
        return _dispatch(
            action,
            project_id=project_id,
            role_id=role_id,
            namespace=namespace,
            first_name=first_name,
            last_name=last_name,
            email=email,
            display_name=display_name,
            role_slug=role_slug,
            is_active=is_active,
            is_bot=is_bot,
            cursor=cursor,
            per_page=per_page,
            order_by=order_by,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="member", description=DOC)
    def _member(
        action: str,
        project_id: str = "",
        role_id: str = "",
        namespace: str = "",
        first_name: str = "",
        last_name: str = "",
        email: str = "",
        display_name: str = "",
        role_slug: str = "",
        is_active: bool | None = None,
        is_bot: bool | None = None,
        cursor: str = "",
        per_page: int = 0,
        order_by: str = "",
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action,
                    project_id=project_id,
                    role_id=role_id,
                    namespace=namespace,
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    display_name=display_name,
                    role_slug=role_slug,
                    is_active=is_active,
                    is_bot=is_bot,
                    cursor=cursor,
                    per_page=per_page,
                    order_by=order_by,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
