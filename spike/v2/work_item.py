"""Consolidated `work_item` tool.

Collapses the 12 tools in plane_mcp/tools/work_items.py into a single
action-dispatch tool. Behaviour (validation, SDK calls, PQL error handling)
is ported verbatim; only the tool surface changes.
"""

from __future__ import annotations

from html import escape
from typing import Any, get_args

from fastmcp import FastMCP
from fastmcp.utilities.logging import get_logger
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
    WorkItemGroupedCountResponse,
    WorkItemSearch,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools.pql_reference import PQL_FULL_REFERENCE
from spike.v2._common import bad_action, json_out, missing, opt

logger = get_logger(__name__)

ACTIONS = [
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
    "unarchive",
    "add_assignee",
    "remove_assignee",
    "add_label",
    "remove_label",
]

DOC = """Manage work items. Actions:
list (no required params; optional project_id to scope to one project, else workspace-wide; pql, order_by, per_page, cursor, expand, fields, external_id, external_source);
list_archived (project_id; optional pql, order_by, per_page, cursor, expand, fields);
retrieve (project_id, work_item_id; optional expand, fields, external_id, external_source, order_by);
retrieve_by_identifier (work_item_identifier in PROJECT-N format e.g. "INFRA-42"; optional expand, fields, external_id, external_source, order_by);
search (query; optional expand, fields, external_id, external_source, order_by);
count (no required params; optional pql, group_by, sub_group_by);
create (project_id, name; optional assignees, labels, type_id, point, description_html, description_stripped, priority, start_date, target_date, sort_order, is_draft, external_source, external_id, parent, state, estimate_point, type);
update (project_id, work_item_id; plus any of the create fields);
delete (project_id, work_item_id);
archive (project_id, work_item_id);
unarchive (project_id, work_item_id);
add_assignee (project_id, work_item_id, user_id);
remove_assignee (project_id, work_item_id, user_id);
add_label (project_id, work_item_id, label_id);
remove_label (project_id, work_item_id, label_id).

pql is a Plane Query Language filter, e.g. 'priority = "urgent" AND assignee = currentUser()'.
UUID fields (project, assignee, state, label, cycle, module, type, milestone, createdBy) need UUIDs -- call
the relevant list tool first if you only have a name. Call get_pql_reference for full PQL syntax.

priority: urgent, high, medium, low, none.
description_stripped is plain text -- it is wrapped into HTML and stored as description_html; description_html wins if both are given.
fields is a sparse fieldset: use `project` (not project_id) and `description_html` (there is no `description` field).
count group_by values: state_id, state__group, priority, project_id, type_id, labels__id, assignees__id,
issue_module__module_id, release_work_items__release_id, cycle_id, milestone_id, created_by, target_date, start_date.
add_assignee/remove_assignee and add_label/remove_label mutate a single entry without replacing the whole list.
Only work items in a completed or cancelled state can be archived."""


def _dump_results(items: Any, fields: str | None) -> list[Any]:
    """Serialize a page of work items, honoring `fields` as a sparse fieldset."""
    requested = {name.strip() for name in fields.split(",")} - {""} if fields else None

    def _dump(item: Any) -> Any:
        if not hasattr(item, "model_dump"):
            return item
        if requested:
            return item.model_dump(include=requested)
        return item.model_dump()

    return [_dump(item) for item in (items or [])]


def _ids(items: Any) -> list[str]:
    """Extract ids from assignees/labels that may be bare id strings or objects."""
    ids: list[str] = []
    for item in items or []:
        value = item if isinstance(item, str) else getattr(item, "id", None)
        if value:
            ids.append(value)
    return ids


def _resolve_description_html(description_html: str | None, description_stripped: str | None) -> str | None:
    """Resolve the description_html to persist.

    Plane recomputes description_stripped server-side from description_html on
    every save, so a stripped value sent on write is silently discarded. When the
    caller supplies only plain text, wrap it into minimal HTML so the description
    actually lands. description_html always wins when both are given.
    """
    if description_html is not None:
        return description_html
    if description_stripped is not None:
        return "<p>" + escape(description_stripped).replace("\n", "<br/>") + "</p>"
    return None


def _envelope(response: Any, fields: str | None) -> dict[str, Any]:
    return {
        "results": _dump_results(response.results, fields),
        "total_count": response.total_count,
        "count": response.count,
        "next_cursor": response.next_cursor,
        "prev_cursor": response.prev_cursor,
        "next_page_results": response.next_page_results,
        "prev_page_results": response.prev_page_results,
    }


def _pql_error(tool: str, pql: str, e: HttpError) -> dict[str, Any]:
    logger.warning("%s: invalid PQL %r → %s", tool, pql, e.response)
    return {
        "error": e.response["pql"],
        "failed_pql": pql,
        "pql_reference": PQL_FULL_REFERENCE,
        "hint": f"The PQL above failed. Fix it using the reference and retry {tool}.",
    }


def _is_pql_error(pql: str, e: HttpError) -> bool:
    return bool(pql) and e.status_code == 400 and isinstance(e.response, dict) and "pql" in e.response


def _write_payload(
    name: str,
    assignees: list[str] | None,
    labels: list[str] | None,
    type_id: str,
    point: int,
    description_html: str,
    description_stripped: str,
    priority: str,
    start_date: str,
    target_date: str,
    sort_order: float,
    is_draft: bool,
    external_source: str,
    external_id: str,
    parent: str,
    state: str,
    estimate_point: str,
    type: str,  # noqa: A002 - mirrors the source tool's parameter name
) -> dict[str, Any]:
    validated_priority: PriorityEnum | None = (
        priority if priority in get_args(PriorityEnum) else None  # type: ignore[assignment]
    )
    return {
        "name": opt(name),
        "assignees": assignees,
        "labels": labels,
        "type_id": opt(type_id),
        "point": opt(point),
        "description_html": _resolve_description_html(opt(description_html), opt(description_stripped)),
        "priority": validated_priority,
        "start_date": opt(start_date),
        "target_date": opt(target_date),
        "sort_order": opt(sort_order),
        "is_draft": opt(is_draft),
        "external_source": opt(external_source),
        "external_id": opt(external_id),
        "parent": opt(parent),
        "state": opt(state),
        "estimate_point": opt(estimate_point),
        "type": opt(type),
    }


def _dispatch(  # noqa: PLR0911, PLR0912, PLR0913, PLR0915
    action: str,
    project_id: str,
    work_item_id: str,
    work_item_identifier: str,
    query: str,
    pql: str,
    order_by: str,
    per_page: int,
    cursor: str,
    expand: str,
    fields: str,
    external_id: str,
    external_source: str,
    group_by: str,
    sub_group_by: str,
    name: str,
    assignees: list[str] | None,
    labels: list[str] | None,
    type_id: str,
    point: int,
    description_html: str,
    description_stripped: str,
    priority: str,
    start_date: str,
    target_date: str,
    sort_order: float,
    is_draft: bool,
    parent: str,
    state: str,
    estimate_point: str,
    type: str,  # noqa: A002 - mirrors the source tool's parameter name
    user_id: str,
    label_id: str,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)

    client, workspace_slug = get_plane_client_context()

    if action == "list":
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
            if project_id:
                response: PaginatedWorkItemResponse = client.work_items.list(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    params=params,
                )
            else:
                response = client.work_items.list_workspace(
                    workspace_slug=workspace_slug,
                    params=params,
                )
        except HttpError as e:
            if _is_pql_error(pql, e):
                return _pql_error("list_work_items", pql, e)
            raise
        return _envelope(response, opt(fields))

    if action == "list_archived":
        if not project_id:
            return missing(action, "project_id")
        params = WorkItemQueryParams(
            pql=opt(pql),
            order_by=opt(order_by),
            per_page=opt(per_page),
            cursor=opt(cursor),
            expand=opt(expand),
            fields=opt(fields),
        )
        try:
            response = client.work_items.list_archived(
                workspace_slug=workspace_slug,
                project_id=project_id,
                params=params,
            )
        except HttpError as e:
            if _is_pql_error(pql, e):
                return _pql_error("list_archived_work_items", pql, e)
            raise
        return _envelope(response, opt(fields))

    if action == "count":
        count_params = WorkItemCountQueryParams(
            pql=opt(pql), group_by=opt(group_by), sub_group_by=opt(sub_group_by)
        )
        try:
            count_response: WorkItemGroupedCountResponse = client.work_items.count_workspace(
                workspace_slug=workspace_slug,
                params=count_params,
            )
        except HttpError as e:
            if _is_pql_error(pql, e):
                return _pql_error("count_work_items", pql, e)
            raise
        return count_response.model_dump()

    if action == "search":
        if not query:
            return missing(action, "query")
        return client.work_items.search(
            workspace_slug=workspace_slug,
            query=query,
            params=RetrieveQueryParams(
                expand=opt(expand),
                fields=opt(fields),
                external_id=opt(external_id),
                external_source=opt(external_source),
                order_by=opt(order_by),
            ),
        )

    if action == "retrieve_by_identifier":
        if not work_item_identifier:
            return missing(action, "work_item_identifier")
        parts = work_item_identifier.rsplit("-", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            return (
                f"Error: invalid work item identifier {work_item_identifier!r}. "
                "Expected PROJECT-N format where N is the sequence number."
            )
        project_identifier, sequence_str = parts
        return client.work_items.retrieve_by_identifier(
            workspace_slug=workspace_slug,
            project_identifier=project_identifier,
            issue_identifier=int(sequence_str),
            params=RetrieveQueryParams(
                expand=opt(expand),
                fields=opt(fields),
                external_id=opt(external_id),
                external_source=opt(external_source),
                order_by=opt(order_by),
            ),
        )

    if not project_id:
        return missing(action, "project_id")

    if action == "create":
        if not name:
            return missing(action, "name")
        return client.work_items.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=CreateWorkItem(
                **_write_payload(
                    name, assignees, labels, type_id, point, description_html,
                    description_stripped, priority, start_date, target_date, sort_order,
                    is_draft, external_source, external_id, parent, state, estimate_point, type,
                )
            ),
        )

    if not work_item_id:
        return missing(action, "work_item_id")

    if action == "retrieve":
        return client.work_items.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            params=RetrieveQueryParams(
                expand=opt(expand),
                fields=opt(fields),
                external_id=opt(external_id),
                external_source=opt(external_source),
                order_by=opt(order_by),
            ),
        )

    if action == "update":
        return client.work_items.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=UpdateWorkItem(
                **_write_payload(
                    name, assignees, labels, type_id, point, description_html,
                    description_stripped, priority, start_date, target_date, sort_order,
                    is_draft, external_source, external_id, parent, state, estimate_point, type,
                )
            ),
        )

    if action == "delete":
        client.work_items.delete(
            workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id
        )
        return None

    if action == "archive":
        client.work_items.archive(
            workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id
        )
        return None

    if action == "unarchive":
        client.work_items.unarchive(
            workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id
        )
        return None

    if action in ("add_assignee", "remove_assignee"):
        if not user_id:
            return missing(action, "user_id")
        current = client.work_items.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id
        )
        ids = _ids(current.assignees)
        if action == "remove_assignee":
            ids = [uid for uid in ids if uid != user_id]
        elif user_id not in ids:
            ids.append(user_id)
        return client.work_items.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=UpdateWorkItem(assignees=ids),
        )

    # add_label / remove_label
    if not label_id:
        return missing(action, "label_id")
    current = client.work_items.retrieve(
        workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id
    )
    ids = _ids(current.labels)
    if action == "remove_label":
        ids = [lid for lid in ids if lid != label_id]
    elif label_id not in ids:
        ids.append(label_id)
    return client.work_items.update(
        workspace_slug=workspace_slug,
        project_id=project_id,
        work_item_id=work_item_id,
        data=UpdateWorkItem(labels=ids),
    )


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="work_item", description=DOC)
    def _work_item(
        action: str,
        project_id: str = "",
        work_item_id: str = "",
        work_item_identifier: str = "",
        query: str = "",
        pql: str = "",
        order_by: str = "",
        per_page: int = 0,
        cursor: str = "",
        expand: str = "",
        fields: str = "",
        external_id: str = "",
        external_source: str = "",
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
        start_date: str = "",
        target_date: str = "",
        sort_order: float = 0,
        is_draft: bool = False,
        parent: str = "",
        state: str = "",
        estimate_point: str = "",
        type: str = "",  # noqa: A002 - mirrors the source tool's parameter name
        user_id: str = "",
        label_id: str = "",
    ) -> WorkItem | WorkItemDetail | WorkItemSearch | dict[str, Any] | str | None:
        return _dispatch(
            action, project_id, work_item_id, work_item_identifier, query, pql,
            order_by, per_page, cursor, expand, fields, external_id, external_source,
            group_by, sub_group_by, name, assignees, labels, type_id, point,
            description_html, description_stripped, priority, start_date, target_date,
            sort_order, is_draft, parent, state, estimate_point, type, user_id, label_id,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="work_item", description=DOC)
    def _work_item(
        action: str,
        project_id: str = "",
        work_item_id: str = "",
        work_item_identifier: str = "",
        query: str = "",
        pql: str = "",
        order_by: str = "",
        per_page: int = 0,
        cursor: str = "",
        expand: str = "",
        fields: str = "",
        external_id: str = "",
        external_source: str = "",
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
        start_date: str = "",
        target_date: str = "",
        sort_order: float = 0,
        is_draft: bool = False,
        parent: str = "",
        state: str = "",
        estimate_point: str = "",
        type: str = "",  # noqa: A002 - mirrors the source tool's parameter name
        user_id: str = "",
        label_id: str = "",
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, project_id, work_item_id, work_item_identifier, query, pql,
                    order_by, per_page, cursor, expand, fields, external_id, external_source,
                    group_by, sub_group_by, name, assignees, labels, type_id, point,
                    description_html, description_stripped, priority, start_date, target_date,
                    sort_order, is_draft, parent, state, estimate_point, type, user_id, label_id,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
