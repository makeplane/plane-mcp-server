"""Consolidated `release_tag` tool.

Collapses the 5 tools in plane_mcp/tools/releases/tags.py into one
action-dispatch tool.
"""

from __future__ import annotations

from fastmcp import FastMCP
from plane.models.releases import (
    CreateReleaseTag,
    PaginatedReleaseTagResponse,
    ReleaseTag,
    UpdateReleaseTag,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools_v2._common import bad_action, json_out, missing, opt, page_params

ACTIONS = ["list", "retrieve", "create", "update", "delete"]

DOC = """Manage release tags in the workspace. Actions:
list (no required params; optional cursor, per_page);
retrieve (tag_id);
create (version; optional description, commit_hash, git_tag);
update (tag_id; only the fields you pass are changed: version, description, commit_hash, git_tag);
delete (tag_id).

A release tag is a version marker (e.g. "v1.2.0") plus optional git metadata, not a
label. Attach one to a release via the release tool's tag_id. Version must be unique
in the workspace."""


def _dispatch(
    action: str,
    tag_id: str,
    version: str,
    description: str,
    commit_hash: str,
    git_tag: str,
    cursor: str,
    per_page: int,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        response: PaginatedReleaseTagResponse = client.releases.tags.list(
            workspace_slug=workspace_slug, params=page_params(cursor, per_page) or {}
        )
        return response.results

    if action == "create":
        if not version:
            return missing(action, "version")
        return client.releases.tags.create(
            workspace_slug=workspace_slug,
            data=CreateReleaseTag(
                version=version,
                description=opt(description),
                commit_hash=opt(commit_hash),
                git_tag=opt(git_tag),
            ),
        )

    if not tag_id:
        return missing(action, "tag_id")

    if action == "retrieve":
        return client.releases.tags.retrieve(workspace_slug=workspace_slug, tag_id=tag_id)

    if action == "update":
        return client.releases.tags.update(
            workspace_slug=workspace_slug,
            tag_id=tag_id,
            data=UpdateReleaseTag(
                version=opt(version),
                description=opt(description),
                commit_hash=opt(commit_hash),
                git_tag=opt(git_tag),
            ),
        )

    client.releases.tags.delete(workspace_slug=workspace_slug, tag_id=tag_id)
    return None


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="release_tag", description=DOC)
    def _release_tag(
        action: str,
        tag_id: str = "",
        version: str = "",
        description: str = "",
        commit_hash: str = "",
        git_tag: str = "",
        cursor: str = "",
        per_page: int = 0,
    ) -> ReleaseTag | list[ReleaseTag] | str | None:
        return _dispatch(action, tag_id, version, description, commit_hash, git_tag, cursor, per_page)


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="release_tag", description=DOC)
    def _release_tag(
        action: str,
        tag_id: str = "",
        version: str = "",
        description: str = "",
        commit_hash: str = "",
        git_tag: str = "",
        cursor: str = "",
        per_page: int = 0,
    ) -> str:
        try:
            return json_out(
                _dispatch(action, tag_id, version, description, commit_hash, git_tag, cursor, per_page)
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
