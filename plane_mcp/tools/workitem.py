"""Work items: the core issue/task/epic record.

`state__group` is a valid `group_by` value for the count action but is NOT a
filterable PQL field. The two vocabularies are documented separately below;
merging them teaches the model to filter on a field the API rejects.
"""

from __future__ import annotations

import mimetypes
from html.parser import HTMLParser
from typing import Annotated, Any, Literal, get_args

import requests
from fastmcp import FastMCP
from fastmcp.utilities.logging import get_logger
from fastmcp.utilities.types import Image
from plane.errors.errors import HttpError
from plane.models.enums import PriorityEnum
from plane.models.query_params import (
    RetrieveQueryParams,
    WorkItemCountQueryParams,
    WorkItemQueryParams,
)
from plane.models.work_items import (
    CreateWorkItem,
    PaginatedWorkItemResponse,
    UpdateWorkItem,
    WorkItem,
    WorkItemDetail,
    WorkItemSearch,
)
from pydantic import Field

from plane_mcp.attachments import HTTP_TIMEOUT, IMAGE_READ_LIMIT, READABLE_IMAGE_TYPES
from plane_mcp.client import get_plane_client_context
from plane_mcp.pql_reference import PQL_FIELD_HINT
from plane_mcp.toolkit import (
    Action,
    build_annotations,
    build_description,
    coerce_list,
    envelope,
    ids_of,
    missing,
    one_of,
    opt,
    pql_failure,
    rich_text,
)

logger = get_logger(__name__)

NAME = "workitem"
TITLE = "Work items"

PRIORITIES = get_args(PriorityEnum)

WRITE_FIELDS = (
    "name",
    "assignees",
    "labels",
    "type_id",
    "point",
    "description_html",
    "description_stripped",
    "priority",
    "start_date",
    "target_date",
    "sort_order",
    "is_draft",
    "parent",
    "state",
    "estimate_point",
    "external_source",
    "external_id",
)
QUERY_FIELDS = ("order_by", "per_page", "cursor", "expand", "fields", "external_id", "external_source")

ACTIONS = (
    Action(
        "list",
        optional=("project_id", "pql", *QUERY_FIELDS),
        note="omit project_id to search the whole workspace",
        read=True,
    ),
    Action("list_archived", ("project_id",), ("pql", *QUERY_FIELDS), read=True),
    Action(
        "retrieve",
        ("project_id", "workitem_id"),
        ("expand", "fields", "external_id", "external_source", "order_by"),
        read=True,
    ),
    Action(
        "retrieve_by_identifier",
        ("workitem_identifier",),
        ("expand", "fields", "external_id", "external_source", "order_by"),
        note="identifier is PROJECT-N, e.g. ENG-42",
        read=True,
    ),
    Action("search", ("query",), ("expand", "fields", "external_id", "external_source", "order_by"), read=True),
    Action(
        "count",
        optional=("project_id", "pql", "group_by", "sub_group_by"),
        note="counts the whole workspace unless project_id narrows it",
        read=True,
    ),
    Action("create", ("project_id", "name"), WRITE_FIELDS[1:]),
    Action("update", ("project_id", "workitem_id"), WRITE_FIELDS, note="only the fields you pass are changed"),
    Action("delete", ("project_id", "workitem_id"), destructive=True),
    Action(
        "archive",
        ("project_id", "workitem_id"),
        ("archive",),
        note="archive defaults to true; pass archive=false to unarchive. Only completed or "
        "cancelled items can be archived",
    ),
    Action(
        "manage_assignee",
        ("project_id", "workitem_id"),
        ("add_user_id", "remove_user_id"),
        note="each takes one id or several; the list is merged, not replaced, and removals apply first",
    ),
    Action(
        "manage_label",
        ("project_id", "workitem_id"),
        ("add_label_id", "remove_label_id"),
        note="each takes one id or several; the list is merged, not replaced, and removals apply first",
    ),
    Action(
        "read_description_image",
        ("project_id", "workitem_id", "asset_id"),
        note="returns an image embedded in the description inline; asset_id is the src of an "
        "<image-component> tag in description_html",
        read=True,
    ),
)

GROUP_BY_VALUES = (
    "state_id",
    "state__group",
    "priority",
    "project_id",
    "type_id",
    "labels__id",
    "assignees__id",
    "issue_module__module_id",
    "release_work_items__release_id",
    "cycle_id",
    "milestone_id",
    "created_by",
    "target_date",
    "start_date",
)

FOOTER = (
    f"priority: {', '.join(PRIORITIES)}.\n"
    "UUID fields (assignees, labels, state, parent, type_id) need UUIDs -- list the relevant "
    "resource first if you only have a name.\n"
    "description_stripped is plain text and is wrapped into HTML on save; description_html wins "
    "if both are given.\n"
    "To clear a field on update: assignees=[] or labels=[], and start_date=null or "
    "target_date=null. A field you leave out is not changed.\n"
    "fields is a sparse fieldset: use `project`, not project_id, and `description_html`, not "
    "description.\n"
    f"count group_by and sub_group_by accept: {', '.join(GROUP_BY_VALUES)}. These are grouping "
    "keys only -- they are not PQL filter fields, and filtering on state__group is rejected.\n"
    f"read_description_image supports PNG/JPEG/GIF/WEBP up to {IMAGE_READ_LIMIT // 1024 // 1024} MB. "
    "These images are not work item attachments, so workitem_attachment cannot list or read them."
)

LEGACY = {
    "list_work_items": "list",
    "list_archived_work_items": "list_archived",
    "retrieve_work_item": "retrieve",
    "retrieve_work_item_by_identifier": "retrieve_by_identifier",
    "search_work_items": "search",
    "count_work_items": "count",
    "create_work_item": "create",
    "update_work_item": "update",
    "delete_work_item": "delete",
    "manage_work_item_archive": "archive",
    "manage_work_item_assignee": "manage_assignee",
    "manage_work_item_label": "manage_label",
}


def _scoped_pql(pql: str, project_id: str) -> str:
    """Narrow a PQL filter to one project, since the count endpoint is workspace-wide."""
    if not project_id:
        return pql
    scope = f'project = "{project_id}"'
    return f"({pql}) AND {scope}" if pql else scope


class _ImageAssetIds(HTMLParser):
    """Collects the `src` of every `<image-component>` in a description."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "image-component":
            src = dict(attrs).get("src")
            if src:
                self.ids.append(src)


def _description_image_ids(description_html: str | None) -> list[str]:
    parser = _ImageAssetIds()
    parser.feed(description_html or "")
    return parser.ids


def _download(url: str, name: str) -> bytes:
    """Stream a Plane-issued storage URL, stopping once it passes the image limit.

    The URL comes from Plane's own API, not from the caller, so this does not
    use the SSRF-guarded fetch that rejects private addresses -- self-hosted
    storage (MinIO) is normally reachable only on one.
    """
    chunks: list[bytes] = []
    size = 0
    try:
        with requests.get(url, timeout=HTTP_TIMEOUT, stream=True) as response:
            response.raise_for_status()
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > IMAGE_READ_LIMIT:
                    raise ValueError(f"{name!r} exceeds the {IMAGE_READ_LIMIT // 1024 // 1024} MB image limit.")
                chunks.append(chunk)
    except requests.RequestException as exc:
        raise ValueError(f"Failed to fetch image {name!r}: {exc}") from exc
    return b"".join(chunks)


def _read_description_image(client, workspace_slug: str, project_id: str, workitem_id: str, asset_id: str) -> Image:
    """Fetch an editor-embedded image.

    The asset endpoint authorises on the workspace, not the work item, so the
    caller's access to the work item is proven first and the asset must be one
    its description actually embeds.
    """
    try:
        workitem = client.work_items.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, work_item_id=workitem_id
        )
    except HttpError as exc:
        raise ValueError(f"Failed to fetch work item {workitem_id!r}: HTTP {exc.status_code} - {exc.response}") from exc

    embedded = _description_image_ids(workitem.description_html)
    if asset_id not in embedded:
        found = ", ".join(dict.fromkeys(embedded)) or "none"
        raise ValueError(
            f"Asset {asset_id!r} is not an image in the description of work item {workitem_id!r}. "
            f"Images in the description: {found}."
        )

    try:
        asset = client.workspaces.get_asset(workspace_slug=workspace_slug, asset_id=asset_id)
    except HttpError as exc:
        raise ValueError(f"Failed to get image {asset_id!r}: HTTP {exc.status_code} - {exc.response}") from exc

    name = asset.asset_name or asset_id
    content_type = (asset.asset_type or mimetypes.guess_type(name)[0] or "").split(";")[0].strip().lower()
    if content_type not in READABLE_IMAGE_TYPES:
        raise ValueError(
            f"Unsupported content type {content_type or 'unknown'!r} for image {name!r}. Supported: PNG/JPEG/GIF/WEBP."
        )

    if not asset.asset_url:
        raise ValueError(f"Plane returned no download URL for image {name!r}; the file may be missing from storage.")

    payload = _download(asset.asset_url, name)

    # FastMCP Image(format=X) sets the MIME type to "image/X", so strip the prefix.
    return Image(data=payload, format=content_type.removeprefix("image/"))


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("Work items -- issues, tasks and epics.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def workitem(  # noqa: PLR0911, PLR0912 - one branch per action is the point
        action: Literal[
            "list",
            "list_archived",
            "retrieve",
            "retrieve_by_identifier",
            "search",
            "count",
            "create",
            "update",
            "delete",
            "archive",
            "manage_assignee",
            "manage_label",
            "read_description_image",
        ],
        project_id: str = "",
        workitem_id: str = "",
        workitem_identifier: str = "",
        query: str = "",
        pql: Annotated[str, Field(description=PQL_FIELD_HINT)] = "",
        group_by: str = "",
        sub_group_by: str = "",
        name: str = "",
        assignees: list[str] | None = None,
        labels: list[str] | None = None,
        type_id: str = "",
        point: int = 0,
        description_html: str = "",
        description_stripped: str = "",
        priority: str = "",
        start_date: str | None = "",
        target_date: str | None = "",
        sort_order: float = 0,
        parent: str = "",
        state: str = "",
        estimate_point: str = "",
        add_user_id: str = "",
        remove_user_id: str = "",
        add_label_id: str = "",
        remove_label_id: str = "",
        external_source: str = "",
        external_id: str = "",
        order_by: str = "",
        expand: str = "",
        fields: str = "",
        cursor: str = "",
        per_page: int = 0,
        # Tri-state: False publishes a draft, unset leaves the flag alone.
        is_draft: bool | None = None,
        archive: bool | None = None,
        asset_id: str = "",
    ) -> WorkItem | WorkItemDetail | WorkItemSearch | Image | dict[str, Any] | list[Any] | str | None:
        client, workspace_slug = get_plane_client_context()

        if error := one_of("priority", priority, PRIORITIES):
            return error
        if error := one_of("group_by", group_by, GROUP_BY_VALUES):
            return error
        if error := one_of("sub_group_by", sub_group_by, GROUP_BY_VALUES):
            return error

        def retrieve_params() -> RetrieveQueryParams:
            return RetrieveQueryParams(
                expand=opt(expand),
                fields=opt(fields),
                external_id=opt(external_id),
                external_source=opt(external_source),
                order_by=opt(order_by),
            )

        def write_payload() -> dict[str, Any]:
            """The fields the caller supplied, and no others."""
            payload = {
                "name": opt(name),
                "assignees": coerce_list(assignees),
                "labels": coerce_list(labels),
                "type_id": opt(type_id),
                "point": opt(point),
                "description_html": rich_text(description_html, description_stripped),
                "priority": opt(priority),
                "start_date": opt(start_date),
                "target_date": opt(target_date),
                "sort_order": opt(sort_order),
                "is_draft": is_draft,
                "external_source": opt(external_source),
                "external_id": opt(external_id),
                "parent": opt(parent),
                "state": opt(state),
                "estimate_point": opt(estimate_point),
            }
            supplied = {field: value for field, value in payload.items() if value is not None}
            dates = {"start_date": start_date, "target_date": target_date}
            return supplied | {field: None for field, value in dates.items() if value is None}

        if action in ("list", "list_archived"):
            if action == "list_archived" and not project_id:
                return missing(action, "project_id")
            params = WorkItemQueryParams(
                pql=opt(pql),
                order_by=opt(order_by),
                per_page=opt(per_page),
                cursor=opt(cursor),
                expand=opt(expand),
                fields=opt(fields),
                external_id=opt(external_id),
                external_source=opt(external_source),
            )
            try:
                if action == "list_archived":
                    response = client.work_items.list_archived(
                        workspace_slug=workspace_slug, project_id=project_id, params=params
                    )
                elif project_id:
                    response: PaginatedWorkItemResponse = client.work_items.list(
                        workspace_slug=workspace_slug, project_id=project_id, params=params
                    )
                else:
                    response = client.work_items.list_workspace(workspace_slug=workspace_slug, params=params)
            except HttpError as exc:
                failure = pql_failure("workitem", action, pql, exc)
                if failure:
                    return failure
                raise
            return envelope(response, opt(fields))

        if action == "count":
            scoped = _scoped_pql(pql, project_id)
            try:
                response = client.work_items.count_workspace(
                    workspace_slug=workspace_slug,
                    params=WorkItemCountQueryParams(
                        pql=opt(scoped), group_by=opt(group_by), sub_group_by=opt(sub_group_by)
                    ),
                )
            except HttpError as exc:
                failure = pql_failure("workitem", action, scoped, exc)
                if failure:
                    return failure
                raise
            return response.model_dump()

        if action == "search":
            if not query:
                return missing(action, "query")
            return client.work_items.search(workspace_slug=workspace_slug, query=query, params=retrieve_params())

        if action == "retrieve_by_identifier":
            if not workitem_identifier:
                return missing(action, "workitem_identifier")
            head, _, sequence = workitem_identifier.rpartition("-")
            if not head or not sequence.isdigit():
                return (
                    f"Error: invalid work item identifier {workitem_identifier!r}. "
                    "Expected PROJECT-N, for example ENG-42."
                )
            return client.work_items.retrieve_by_identifier(
                workspace_slug=workspace_slug,
                project_identifier=head,
                issue_identifier=int(sequence),
                params=retrieve_params(),
            )

        if not project_id:
            return missing(action, "project_id")

        if action == "create":
            if not name:
                return missing(action, "name")
            return client.work_items.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                data=CreateWorkItem(**write_payload()),
            )

        if not workitem_id:
            return missing(action, "workitem_id")

        if action == "read_description_image":
            if not asset_id:
                return missing(action, "asset_id")
            return _read_description_image(client, workspace_slug, project_id, workitem_id, asset_id)

        if action == "retrieve":
            return client.work_items.retrieve(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=workitem_id,
                params=retrieve_params(),
            )

        if action == "update":
            return client.work_items.update(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=workitem_id,
                data=UpdateWorkItem(**write_payload()),
            )

        if action == "delete":
            client.work_items.delete(workspace_slug=workspace_slug, project_id=project_id, work_item_id=workitem_id)
            return None

        if action == "archive":
            archiving = archive is not False
            operation = client.work_items.archive if archiving else client.work_items.unarchive
            operation(workspace_slug=workspace_slug, project_id=project_id, work_item_id=workitem_id)
            return {"workitem_id": workitem_id, "archived": archiving}

        # manage_assignee / manage_label: read the current set, mutate it, write it back.
        add, remove, field = (
            (add_user_id, remove_user_id, "assignees")
            if action == "manage_assignee"
            else (add_label_id, remove_label_id, "labels")
        )
        if not add and not remove:
            return missing(action, f"add_{field[:-1]}_id or remove_{field[:-1]}_id")
        # Either side takes one id or several, so adding three assignees is one call.
        adding, removing = coerce_list(add) or [], coerce_list(remove) or []
        current = client.work_items.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, work_item_id=workitem_id
        )
        ids = [value for value in ids_of(getattr(current, field)) if value not in removing]
        ids += [value for value in adding if value not in ids]
        return client.work_items.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=workitem_id,
            data=UpdateWorkItem(**{field: ids}),
        )
