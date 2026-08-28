"""Pages, at workspace or project scope, their hierarchy, and their links to work items.

NOTE ON PLANE CE 1.4.2
----------------------
In Plane Community Edition 1.4.2 the Pages REST API is served ONLY under the
legacy ``/api/`` prefix (``/api/workspaces/<slug>/projects/<uuid>/pages/``). The
``plane`` SDK hard-codes ``/api/v1`` in ``plane.config.Configuration``, and the
Pages routes are not registered under ``/api/v1/`` at all, so every SDK call to
Pages 404s.

This module talks to the legacy ``/api/`` endpoint directly so it can reuse the
SAME ``PLANE_API_KEY`` as the rest of the connector (the ``X-Api-Key`` header),
instead of a browser session cookie.

IMPORTANT — server-side prerequisite
-------------------------------------
Out of the box, the legacy ``/api/`` routes only use ``SessionAuthentication``,
so an ``X-Api-Key`` request returns 401. To let the API key reach Pages, add
``APIKeyAuthentication`` to DRF's ``DEFAULT_AUTHENTICATION_CLASSES`` in
``apps/api/plane/settings/common.py`` on the Plane server (one-line change, then
restart the API). With that in place this module works using only
``PLANE_API_KEY`` — no session cookie required.
"""

from __future__ import annotations

import os
from typing import Any, Literal

import requests
from fastmcp import FastMCP
from plane.models.collections import AddCollectionPages, UpdateCollectionPage
from plane.models.pages import CreatePage, Page, UpdatePage
from plane.models.query_params import PaginatedQueryParams
from plane.models.work_item_pages import CreateWorkItemPage, WorkItemPage

from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import Action, as_params, build_annotations, build_description, envelope, missing, needs, opt

NAME = "page"
TITLE = "Pages"

# ---------------------------------------------------------------------------
# Legacy /api/ client for Pages (Plane CE 1.4.2) — authenticated with PLANE_API_KEY
# ---------------------------------------------------------------------------
_PLANE_BASE_URL = os.getenv("PLANE_BASE_URL", "").rstrip("/")
_PLANE_WORKSPACE_SLUG = os.getenv("PLANE_WORKSPACE_SLUG", "")
_PLANE_API_KEY = os.getenv("PLANE_API_KEY", "")


def _require_api_key() -> None:
    """Fail fast with a clear message if the API key is missing."""
    from plane.errors.errors import HttpError

    if not _PLANE_API_KEY:
        raise HttpError(
            "Pages require PLANE_API_KEY (X-Api-Key). The legacy /api/ Pages route must "
            "also accept the API key on the server side — see the module docstring.",
            401,
            "missing PLANE_API_KEY",
        )


def _page_url(project_id: str, page_id: str = "", extra: str = "") -> str:
    """Build a legacy Pages URL. Workspace scope when project_id is empty.

    A trailing slash is always appended: Django's URLs are slash-terminated and
    a missing slash triggers a 301 that drops the auth header on redirect.
    """
    if project_id:
        url = f"{_PLANE_BASE_URL}/api/workspaces/{_PLANE_WORKSPACE_SLUG}/projects/{project_id}/pages"
    else:
        url = f"{_PLANE_BASE_URL}/api/workspaces/{_PLANE_WORKSPACE_SLUG}/pages"
    if page_id:
        url = f"{url}/{page_id}"
    if extra:
        url = f"{url}/{extra}"
    return url + "/"


def _legacy_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Api-Key": _PLANE_API_KEY,
    }


def _legacy_request(method: str, url: str, json: dict[str, Any] | None = None) -> Any:
    from plane.errors.errors import HttpError

    resp = requests.request(method, url, headers=_legacy_headers(), json=json, timeout=30)
    if resp.status_code == 204:
        return None
    if 200 <= resp.status_code < 300:
        if not resp.content:
            return None
        try:
            return resp.json()
        except Exception:
            return resp.text
    try:
        payload = resp.json()
    except Exception:
        payload = resp.text
    raise HttpError(f"HTTP {resp.status_code}: {resp.reason}", resp.status_code, payload)


ACTIONS = (
    Action(
        "list", (), ("project_id", "cursor", "per_page"), note="workspace pages unless project_id is given", read=True
    ),
    Action("retrieve", ("page_id",), ("project_id",), read=True),
    Action(
        "create",
        ("name", "description_html"),
        (
            "project_id",
            "parent_id",
            "collection_id",
            "access",
            "color",
            "is_locked",
            "external_source",
            "external_id",
        ),
        note="parent_id nests the new page under an existing one; collection_id files it. "
        "Pass one or the other, never both",
    ),
    Action(
        "update",
        ("page_id",),
        ("project_id", "name", "description_html"),
        note="pass name, description_html, or both; description_html replaces the whole body, "
        "so retrieve the page first when editing part of it; a locked or archived page is refused",
    ),
    Action(
        "archive",
        ("page_id",),
        ("project_id", "archive"),
        note="archive defaults to true; pass archive=false to restore",
    ),
    Action(
        "delete",
        ("page_id",),
        ("project_id",),
        note="requires the page to be archived first",
        destructive=True,
    ),
    Action(
        "set_collection",
        ("page_id", "collection_id"),
        note="files a page into a collection, or moves it out of the one it is in; workspace pages only, "
        "and collection_id comes from the collection tool",
    ),
    Action("list_workitem_pages", ("project_id", "workitem_id"), read=True),
    Action("attach_to_workitem", ("project_id", "workitem_id", "page_id")),
    Action(
        "detach_from_workitem",
        ("project_id", "workitem_id", "workitem_page_id"),
        note="workitem_page_id is the link id from list_workitem_pages, not the page id",
        destructive=True,
    ),
)

FOOTER = (
    "description_html is the page body as HTML. access is the page access level. "
    "update changes only the fields you pass. A page must be archived before it can be deleted. "
    "Omit project_id to work with workspace-level pages. "
    "A page's parent is fixed at creation -- pass parent_id to create to build a hierarchy, since "
    "nothing can reparent it afterwards. list and retrieve both report a page's parent_id and the "
    "collection_id it is filed in, so neither needs looking up. "
    "Collections themselves live in the collection tool; here, create files a new page into one and "
    "set_collection files or moves an existing page."
)

LEGACY = {
    "list_pages": "list",
    "retrieve_page": "retrieve",
    "create_page": "create",
    "list_work_item_pages": "list_workitem_pages",
    "attach_page_to_work_item": "attach_to_workitem",
    "detach_page_from_work_item": "detach_from_workitem",
}


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("Pages at workspace or project scope.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def page(
        action: Literal[
            "list",
            "retrieve",
            "create",
            "update",
            "archive",
            "delete",
            "set_collection",
            "list_workitem_pages",
            "attach_to_workitem",
            "detach_from_workitem",
        ],
        project_id: str = "",
        page_id: str = "",
        parent_id: str = "",
        collection_id: str = "",
        workitem_id: str = "",
        workitem_page_id: str = "",
        name: str = "",
        description_html: str = "",
        # Left unset rather than defaulted: 0 is a real access level.
        access: int | None = None,
        color: str = "",
        is_locked: bool | None = None,
        archive: bool = True,
        external_source: str = "",
        external_id: str = "",
        cursor: str = "",
        per_page: int = 0,
    ) -> Page | WorkItemPage | list[WorkItemPage] | dict[str, Any] | str | None:
        client, workspace_slug = get_plane_client_context()

        # ----- Core Page CRUD: legacy /api/ + PLANE_API_KEY (X-Api-Key) -----
        _require_api_key()
        if action == "list":
            if project_id:
                return _legacy_request("GET", _page_url(project_id))
            return _legacy_request("GET", _page_url(""))

        if action == "retrieve":
            if not page_id:
                return missing(action, "page_id")
            if project_id:
                return _legacy_request("GET", _page_url(project_id, page_id))
            return _legacy_request("GET", _page_url("", page_id))

        if action == "create":
            if error := needs(action, name=name, description_html=description_html):
                return error
            if parent_id and collection_id:
                return "Error: pass parent_id or collection_id, not both. A nested page takes its parent's collection."
            if collection_id and project_id:
                return "Error: collections hold workspace pages only. Omit project_id, or omit collection_id."
            data: dict[str, Any] = {"name": name, "description_html": description_html}
            if access is not None:
                data["access"] = access
            if color:
                data["color"] = color
            if is_locked is not None:
                data["is_locked"] = is_locked
            if parent_id:
                data["parent_id"] = parent_id
            if external_id:
                data["external_id"] = external_id
            if external_source:
                data["external_source"] = external_source
            if collection_id:
                data["collection_id"] = collection_id
            if project_id:
                return _legacy_request("POST", _page_url(project_id), json=data)
            return _legacy_request("POST", _page_url(""), json=data)

        if action == "update":
            if not page_id:
                return missing(action, "page_id")
            if not (name or description_html):
                return missing(action, "name or description_html")
            data = {}
            if name:
                data["name"] = name
            if description_html:
                data["description_html"] = description_html
            if project_id:
                return _legacy_request("PUT", _page_url(project_id, page_id), json=data)
            return _legacy_request("PUT", _page_url("", page_id), json=data)

        if action == "archive":
            if not page_id:
                return missing(action, "page_id")
            if project_id:
                url = _page_url(project_id, page_id, "archive")
            else:
                url = _page_url("", page_id, "archive")
            if archive:
                _legacy_request("POST", url)
            else:
                _legacy_request("DELETE", url)
            return {"page_id": page_id, "archived": archive}

        if action == "delete":
            if not page_id:
                return missing(action, "page_id")
            if project_id:
                _legacy_request("DELETE", _page_url(project_id, page_id))
            else:
                _legacy_request("DELETE", _page_url("", page_id))
            return None

        # ----- Set collection / work-item links: still via SDK (/api/v1/) -----
        if action == "set_collection":
            if error := needs(action, page_id=page_id, collection_id=collection_id):
                return error

            filed = client.pages.retrieve_workspace_page(workspace_slug=workspace_slug, page_id=page_id)

            if not filed.collection_id:
                added = client.collections.pages.add(
                    workspace_slug=workspace_slug,
                    collection_id=collection_id,
                    data=AddCollectionPages(page_ids=[page_id]),
                )
                if not added:
                    return None
                membership_id = added[0].id
            elif str(filed.collection_id) == collection_id:
                membership_id = filed.page_collection_id
            else:
                membership_id = client.collections.pages.update(
                    workspace_slug=workspace_slug,
                    collection_id=str(filed.collection_id),
                    page_collection_id=str(filed.page_collection_id),
                    data=UpdateCollectionPage(collection=collection_id),
                ).id

            return {
                "page_id": page_id,
                "collection_id": collection_id,
                "page_collection_id": str(membership_id),
            }

        if error := needs(action, project_id=project_id, workitem_id=workitem_id):
            return error

        if action == "list_workitem_pages":
            response = client.work_items.pages.list(
                workspace_slug=workspace_slug, project_id=project_id, work_item_id=workitem_id
            )
            return response.results

        if action == "attach_to_workitem":
            if not page_id:
                return missing(action, "page_id")
            return client.work_items.pages.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=workitem_id,
                data=CreateWorkItemPage(page_id=page_id),
            )

        if not workitem_page_id:
            return missing(action, "workitem_page_id")
        client.work_items.pages.delete(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=workitem_id,
            work_item_page_id=workitem_page_id,
        )
        return None
