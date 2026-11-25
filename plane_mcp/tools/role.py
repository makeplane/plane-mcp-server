"""Custom roles and the permission schemes they are built from (api v2)."""

from __future__ import annotations

from typing import Literal

from fastmcp import FastMCP
from plane.models.v2 import (
    CreatePermissionScheme,
    CreateRole,
    UpdatePermissionScheme,
    UpdateRole,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import Action, build_annotations, build_description, dump_results, missing, needs, one_of, opt

NAME = "role"
TITLE = "Roles and permission schemes"

ACTIONS = (
    Action("list", optional=("namespace",), note="summary rows; retrieve for permissions", read=True),
    Action("retrieve", ("role_id",), read=True),
    Action("create", ("name", "namespace"), ("permission_scheme_ids", "based_on_slug", "description")),
    Action("update", ("role_id",), ("name", "description", "permission_scheme_ids", "status", "reassign_to")),
    Action("delete", ("role_id",), ("reassign_to",), destructive=True),
    Action("list_schemes", note="summary rows; retrieve_scheme for permissions", read=True),
    Action("retrieve_scheme", ("scheme_id",), read=True),
    Action("create_scheme", ("name", "namespace", "permissions"), ("description",)),
    Action("update_scheme", ("scheme_id",), ("name", "description", "permissions")),
    Action("delete_scheme", ("scheme_id",), destructive=True),
    Action("get_scheme_impact", ("scheme_id",), note="roles, users, projects it reaches", read=True),
    Action("list_permissions", ("namespace",), note="the keys a scheme may grant", read=True),
)

# What a listing carries; `retrieve`/`retrieve_scheme` return the full row, permissions included.
ROLE_LIST_FIELDS = ("id", "name", "slug", "namespace", "is_system", "status")
SCHEME_LIST_FIELDS = ("id", "name", "slug", "namespace", "is_system")

NAMESPACES = ("workspace", "project")
STATUSES = ("active", "inactive")

FOOTER = (
    "Only custom roles and schemes change; system ones are read-only. A role grants the union of its "
    "schemes. namespace is workspace or project. Take scheme keys (workitem:edit, workitem:edit+creator) "
    "from list_permissions for that namespace; the server adds the keys each requires, so a scheme reads "
    "back with more. Run get_scheme_impact before update_scheme or delete_scheme: an edit changes every "
    "role using it, and delete fails while one does. Deleting a role with members, or setting "
    "status=inactive, needs reassign_to: another active role id in its namespace."
)

LEGACY: dict[str, str] = {}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description(
            "Custom roles and the permission schemes they grant. Writes need an admin and a plan with custom roles.",
            ACTIONS,
            FOOTER,
        ),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def role(
        action: Literal[
            "list",
            "retrieve",
            "create",
            "update",
            "delete",
            "list_schemes",
            "retrieve_scheme",
            "create_scheme",
            "update_scheme",
            "delete_scheme",
            "get_scheme_impact",
            "list_permissions",
        ],
        role_id: str = "",
        scheme_id: str = "",
        name: str = "",
        description: str = "",
        namespace: str = "",
        # None leaves the list unchanged on update; [] clears it.
        permission_scheme_ids: list[str] | None = None,
        permissions: list[str] | None = None,
        based_on_slug: str = "",
        status: str = "",
        reassign_to: str = "",
    ):
        client, workspace_slug = get_plane_client_context()
        workspace = client.v2.workspaces
        roles, schemes = workspace.roles, workspace.permission_schemes

        if error := one_of("namespace", namespace, NAMESPACES) or one_of("status", status, STATUSES):
            return error

        if action == "list":
            rows = roles.iterate(workspace_slug, namespace=opt(namespace), fields=ROLE_LIST_FIELDS)
            return dump_results(rows, ",".join(ROLE_LIST_FIELDS))

        if action == "list_schemes":
            rows = schemes.iterate(workspace_slug, fields=SCHEME_LIST_FIELDS)
            return dump_results(rows, ",".join(SCHEME_LIST_FIELDS))

        if action == "list_permissions":
            if not namespace:
                return missing(action, "namespace")
            return workspace.permissions.resources(workspace_slug, namespace=namespace)

        if action == "create":
            if error := needs(action, name=name, namespace=namespace):
                return error
            return roles.create(
                workspace_slug,
                CreateRole(
                    name=name,
                    namespace=namespace,
                    permission_scheme_ids=permission_scheme_ids,
                    based_on_slug=opt(based_on_slug),
                    description=opt(description),
                ),
            )

        if action == "create_scheme":
            if error := needs(action, name=name, namespace=namespace, permissions=permissions):
                return error
            return schemes.create(
                workspace_slug,
                CreatePermissionScheme(
                    name=name, namespace=namespace, permissions=permissions, description=opt(description)
                ),
            )

        if action in ("retrieve", "update", "delete"):
            if not role_id:
                return missing(action, "role_id")
            if action == "retrieve":
                return roles.retrieve(workspace_slug, role_id)
            if action == "update":
                return roles.update(
                    workspace_slug,
                    role_id,
                    UpdateRole(
                        name=opt(name),
                        description=opt(description),
                        permission_scheme_ids=permission_scheme_ids,
                        status=opt(status),
                        reassign_to=opt(reassign_to),
                    ),
                )
            roles.delete(workspace_slug, role_id, reassign_to=opt(reassign_to))
            return None

        if not scheme_id:
            return missing(action, "scheme_id")
        if action == "retrieve_scheme":
            return schemes.retrieve(workspace_slug, scheme_id)
        if action == "get_scheme_impact":
            return schemes.impact(workspace_slug, scheme_id)
        if action == "update_scheme":
            return schemes.update(
                workspace_slug,
                scheme_id,
                UpdatePermissionScheme(name=opt(name), description=opt(description), permissions=permissions),
            )
        schemes.delete(workspace_slug, scheme_id)
        return None
