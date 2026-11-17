"""Saved views: a named work-item query, in a project or across the workspace."""

from __future__ import annotations

from typing import Any, Literal

from fastmcp import FastMCP
from plane.models.v2.views import CreateView, UpdateView, View

from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import Action, build_annotations, build_description, missing, one_of, opt

NAME = "view"
TITLE = "Views"

ACTIONS = (
    Action("list", optional=("project_id",), note="returns every view", read=True),
    Action("retrieve", ("view_id",), ("project_id",), read=True),
    Action("create", ("name",), ("project_id", "description", "pql", "access")),
    Action(
        "update",
        ("view_id",),
        ("project_id", "name", "description", "pql", "access"),
        note="only the fields you pass are changed",
    ),
    Action("delete", ("view_id",), ("project_id",), destructive=True),
)

FOOTER = (
    "Pass project_id for a project's views; omit it for workspace views. pql is the view's "
    "work-item query, in the syntax get_pql_reference describes. access is private or public."
)

LEGACY: dict[str, str] = {}

ACCESS = {"private": 0, "public": 1}

# A view also carries its layout settings; a listing leaves them out.
LIST_FIELDS = ("id", "name", "description", "access", "pql_filters", "owned_by_id")


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("Saved views of work items.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def view(
        action: Literal["list", "retrieve", "create", "update", "delete"],
        project_id: str = "",
        view_id: str = "",
        name: str = "",
        description: str = "",
        pql: str = "",
        access: str = "",
    ) -> View | list[dict[str, Any]] | str | None:
        client, workspace_slug = get_plane_client_context()

        if error := one_of("access", access, tuple(ACCESS)):
            return error

        workspace = client.v2.workspaces
        views = workspace.projects.views if project_id else workspace.views
        scope = (workspace_slug, project_id) if project_id else (workspace_slug,)
        pql_filters = {"stripped": pql} if pql else None

        if action == "list":
            # exclude_unset: the model would pad every field the listing left out with null.
            return [row.model_dump(exclude_unset=True) for row in views.iterate(*scope, fields=LIST_FIELDS)]

        if action == "create":
            if not name:
                return missing(action, "name")
            return views.create(
                *scope,
                CreateView(name=name, description=opt(description), pql_filters=pql_filters, access=ACCESS.get(access)),
            )

        if not view_id:
            return missing(action, "view_id")

        if action == "retrieve":
            return views.retrieve(*scope, view_id)

        if action == "update":
            return views.update(
                *scope,
                view_id,
                UpdateView(
                    name=opt(name), description=opt(description), pql_filters=pql_filters, access=ACCESS.get(access)
                ),
            )

        views.delete(*scope, view_id)
        return None
