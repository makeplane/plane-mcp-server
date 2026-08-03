"""Consolidated `workspace` tool.

Collapses 2 tools from plane_mcp/tools/workspaces.py:
get_features, update_workspace_features.
"""

from __future__ import annotations

from fastmcp import FastMCP
from plane.models.projects import ProjectFeature
from plane.models.workspaces import WorkspaceFeature

from plane_mcp.client import get_plane_client_context
from spike.v2._common import bad_action, json_out

ACTIONS = ["get_features", "update_features"]

DOC = """Workspace-level settings. Actions:
get_features (no required params; pass project_id to get that project's features instead);
update_features (pass any of project_grouping, initiatives, teams, customers, wiki, pi).

get_features returns a ProjectFeature when project_id is given, otherwise the
workspace's WorkspaceFeature. update_features only touches the flags you pass;
omitted flags are left unchanged. To change a project's features use the
`project` tool with action=update_features."""


def _dispatch(
    action: str,
    project_id: str = "",
    project_grouping: bool | None = None,
    initiatives: bool | None = None,
    teams: bool | None = None,
    customers: bool | None = None,
    wiki: bool | None = None,
    pi: bool | None = None,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)

    client, workspace_slug = get_plane_client_context()

    if action == "get_features":
        if project_id:
            return client.projects.get_features(
                workspace_slug=workspace_slug, project_id=project_id
            )
        return client.workspaces.get_features(workspace_slug=workspace_slug)

    # Build data dict with only non-None values
    feature_data: dict[str, bool] = {}
    if project_grouping is not None:
        feature_data["project_grouping"] = project_grouping
    if initiatives is not None:
        feature_data["initiatives"] = initiatives
    if teams is not None:
        feature_data["teams"] = teams
    if customers is not None:
        feature_data["customers"] = customers
    if wiki is not None:
        feature_data["wiki"] = wiki
    if pi is not None:
        feature_data["pi"] = pi

    return client.workspaces.update_features(
        workspace_slug=workspace_slug, data=WorkspaceFeature(**feature_data)
    )


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="workspace", description=DOC)
    def _workspace(
        action: str,
        project_id: str = "",
        project_grouping: bool | None = None,
        initiatives: bool | None = None,
        teams: bool | None = None,
        customers: bool | None = None,
        wiki: bool | None = None,
        pi: bool | None = None,
    ) -> WorkspaceFeature | ProjectFeature | str | None:
        return _dispatch(
            action,
            project_id=project_id,
            project_grouping=project_grouping,
            initiatives=initiatives,
            teams=teams,
            customers=customers,
            wiki=wiki,
            pi=pi,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="workspace", description=DOC)
    def _workspace(
        action: str,
        project_id: str = "",
        project_grouping: bool | None = None,
        initiatives: bool | None = None,
        teams: bool | None = None,
        customers: bool | None = None,
        wiki: bool | None = None,
        pi: bool | None = None,
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action,
                    project_id=project_id,
                    project_grouping=project_grouping,
                    initiatives=initiatives,
                    teams=teams,
                    customers=customers,
                    wiki=wiki,
                    pi=pi,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
