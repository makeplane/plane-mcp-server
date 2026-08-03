"""Consolidated `release` tool.

Collapses 9 tools from three modules into one action-dispatch tool:

* plane_mcp/tools/releases/base.py       -- list/retrieve/create/update/delete
* plane_mcp/tools/releases/changelog.py  -- get_changelog/update_changelog
* plane_mcp/tools/releases/work_items.py -- list_work_items/manage_work_items
"""

from __future__ import annotations

from html import escape
from typing import Any

from fastmcp import FastMCP
from plane.models.releases import (
    AddReleaseWorkItems,
    CreateRelease,
    PaginatedReleaseResponse,
    PaginatedReleaseWorkItemResponse,
    Release,
    ReleaseChangelog,
    ReleaseWorkItem,
    RemoveReleaseWorkItems,
    UpdateRelease,
    UpdateReleaseChangelog,
)

from plane_mcp.client import get_plane_client_context
from spike.v2._common import bad_action, json_out, missing, opt, page_params

ACTIONS = [
    "list",
    "retrieve",
    "create",
    "update",
    "delete",
    "get_changelog",
    "update_changelog",
    "list_work_items",
    "manage_work_items",
]

DOC = """Manage releases, their changelog, and their linked work items. Actions:
list (no required params; optional cursor, per_page);
retrieve (release_id);
create (name; optional description_html, status, release_date, target_date, tag_id, lead_id, is_prerelease, external_source, external_id);
update (release_id; only the fields you pass are changed: name, description_html, status, release_date, target_date, tag_id, lead_id, is_prerelease);
delete (release_id);
get_changelog (release_id);
update_changelog (release_id; plus description_html and/or description_stripped and/or description_json);
list_work_items (release_id; optional cursor, per_page);
manage_work_items (release_id, work_item_ids, operation="add"|"remove").

status: unreleased (default) | released | cancelled.
release_date is the date shown as "Target date" in the Plane UI (YYYY-MM-DD); use it for
what a user calls the release's target/release date. target_date is a separate stored
field not shown in the release UI -- prefer release_date unless you specifically need it.
tag_id is a release tag (version marker such as "v1.2.0"), not a label.

Changelog: each release has exactly one, created empty on first access. The changelog UI
renders description_json, so plain text passed as description_stripped is also converted
into a matching ProseMirror doc; explicit description_html / description_json win over it."""


def _resolve_description_html(description_html: str | None, description_stripped: str | None) -> str | None:
    """Resolve the description_html to persist (ported from releases/_helpers.py)."""
    if description_html is not None:
        return description_html
    if description_stripped is not None:
        return "<p>" + escape(description_stripped).replace("\n", "<br/>") + "</p>"
    return None


def _plain_text_to_prosemirror(text: str) -> dict[str, Any]:
    """Build a minimal ProseMirror doc from plain text (one paragraph per line)."""
    paragraphs: list[dict[str, Any]] = []
    for line in text.split("\n"):
        paragraph: dict[str, Any] = {"type": "paragraph"}
        if line:
            paragraph["content"] = [{"type": "text", "text": line}]
        paragraphs.append(paragraph)
    return {"type": "doc", "content": paragraphs}


def _dispatch(
    action: str,
    release_id: str,
    name: str,
    description_html: str,
    description_stripped: str,
    description_json: dict[str, Any] | None,
    status: str,
    target_date: str,
    release_date: str,
    tag_id: str,
    lead_id: str,
    is_prerelease: bool | None,
    external_source: str,
    external_id: str,
    work_item_ids: list[str] | None,
    operation: str,
    cursor: str,
    per_page: int,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        response: PaginatedReleaseResponse = client.releases.list(
            workspace_slug=workspace_slug, params=page_params(cursor, per_page) or {}
        )
        return response.results

    if action == "create":
        if not name:
            return missing(action, "name")
        return client.releases.create(
            workspace_slug=workspace_slug,
            data=CreateRelease(
                name=name,
                description_html=opt(description_html),
                status=opt(status),
                target_date=opt(target_date),
                release_date=opt(release_date),
                tag=opt(tag_id),
                lead=opt(lead_id),
                is_prerelease=is_prerelease,
                external_source=opt(external_source),
                external_id=opt(external_id),
            ),
        )

    if not release_id:
        return missing(action, "release_id")

    if action == "retrieve":
        return client.releases.retrieve(workspace_slug=workspace_slug, release_id=release_id)

    if action == "update":
        return client.releases.update(
            workspace_slug=workspace_slug,
            release_id=release_id,
            data=UpdateRelease(
                name=opt(name),
                description_html=opt(description_html),
                status=opt(status),
                target_date=opt(target_date),
                release_date=opt(release_date),
                tag=opt(tag_id),
                lead=opt(lead_id),
                is_prerelease=is_prerelease,
            ),
        )

    if action == "delete":
        client.releases.delete(workspace_slug=workspace_slug, release_id=release_id)
        return None

    if action == "get_changelog":
        return client.releases.changelog.retrieve(workspace_slug=workspace_slug, release_id=release_id)

    if action == "update_changelog":
        html_in = opt(description_html)
        stripped_in = opt(description_stripped)
        # The changelog editor is document-based (renders description_json), so a
        # bare plain-text write must also emit a matching doc. Explicit html/json win.
        if description_json is None and html_in is None and stripped_in is not None:
            description_json = _plain_text_to_prosemirror(stripped_in)
        return client.releases.changelog.update(
            workspace_slug=workspace_slug,
            release_id=release_id,
            data=UpdateReleaseChangelog(
                description_html=_resolve_description_html(html_in, stripped_in),
                description_json=description_json,
            ),
        )

    if action == "list_work_items":
        wi_response: PaginatedReleaseWorkItemResponse = client.releases.work_items.list(
            workspace_slug=workspace_slug,
            release_id=release_id,
            params=page_params(cursor, per_page) or {},
        )
        return wi_response.results

    # manage_work_items
    if operation not in ("add", "remove"):
        return missing(action, 'operation ("add" or "remove")')
    if not work_item_ids:
        return missing(action, "work_item_ids")

    work_items = client.releases.work_items
    if operation == "add":
        work_items.create(
            workspace_slug=workspace_slug,
            release_id=release_id,
            data=AddReleaseWorkItems(work_item_ids=work_item_ids),
        )
    else:
        work_items.delete(
            workspace_slug=workspace_slug,
            release_id=release_id,
            data=RemoveReleaseWorkItems(work_item_ids=work_item_ids),
        )
    return work_items.list(workspace_slug=workspace_slug, release_id=release_id).results


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="release", description=DOC)
    def _release(
        action: str,
        release_id: str = "",
        name: str = "",
        description_html: str = "",
        description_stripped: str = "",
        description_json: dict[str, Any] | None = None,
        status: str = "",
        target_date: str = "",
        release_date: str = "",
        tag_id: str = "",
        lead_id: str = "",
        is_prerelease: bool | None = None,
        external_source: str = "",
        external_id: str = "",
        work_item_ids: list[str] | None = None,
        operation: str = "",
        cursor: str = "",
        per_page: int = 0,
    ) -> Release | list[Release] | ReleaseChangelog | list[ReleaseWorkItem] | str | None:
        return _dispatch(
            action, release_id, name, description_html, description_stripped, description_json,
            status, target_date, release_date, tag_id, lead_id, is_prerelease,
            external_source, external_id, work_item_ids, operation, cursor, per_page,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="release", description=DOC)
    def _release(
        action: str,
        release_id: str = "",
        name: str = "",
        description_html: str = "",
        description_stripped: str = "",
        description_json: dict[str, Any] | None = None,
        status: str = "",
        target_date: str = "",
        release_date: str = "",
        tag_id: str = "",
        lead_id: str = "",
        is_prerelease: bool | None = None,
        external_source: str = "",
        external_id: str = "",
        work_item_ids: list[str] | None = None,
        operation: str = "",
        cursor: str = "",
        per_page: int = 0,
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, release_id, name, description_html, description_stripped, description_json,
                    status, target_date, release_date, tag_id, lead_id, is_prerelease,
                    external_source, external_id, work_item_ids, operation, cursor, per_page,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
