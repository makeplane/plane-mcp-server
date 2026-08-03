"""Consolidated `customer_request` tool.

Collapses customers/requests.py (5 tools) into one action-dispatch tool.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from plane.models.customers import (
    CreateCustomerRequest,
    CustomerRequest,
    PaginatedCustomerRequestResponse,
    UpdateCustomerRequest,
)

from plane_mcp.client import get_plane_client_context
from spike.v2._common import bad_action, json_out, missing, opt

ACTIONS = ["list", "retrieve", "create", "update", "delete"]

DOC = """Manage a customer's requests. Actions:
list (customer_id; optional query, cursor, per_page);
retrieve (customer_id, request_id);
create (customer_id, name; optional description_html, link, work_item_ids);
update (customer_id, request_id; optional name, description_html, link);
delete (customer_id, request_id).

A customer request records something the customer asked for. The work items
addressing it are read with the customer tool (action=list_work_items), not here.

work_item_ids is settable only on create: it links those work items to the customer
as the request is created, and is never echoed back on the created request. Change
links afterwards with the customer tool (action=manage_work_items) passing this
request_id as customer_request_id -- update cannot change them.
delete also unlinks the work items linked through the request.
query filters to requests whose name contains that text.
list returns a paginated envelope (results + total_count, next_cursor,
next_page_results); page again while next_page_results is true."""


def _page_params(cursor: str, per_page: int, query: str = "") -> dict[str, Any]:
    """Build query params for a paginated customer endpoint, dropping unset ones."""
    params: dict[str, Any] = {}
    if cursor:
        params["cursor"] = cursor
    if per_page:
        params["per_page"] = per_page
    if query:
        params["query"] = query
    return params


def _dispatch(
    action: str,
    customer_id: str,
    request_id: str,
    name: str,
    description_html: str,
    link: str,
    work_item_ids: list[str] | None,
    query: str,
    cursor: str,
    per_page: int,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)
    if not customer_id:
        return missing(action, "customer_id")

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        return client.customers.requests.list(
            workspace_slug=workspace_slug,
            customer_id=customer_id,
            params=_page_params(cursor, per_page, query),
        )

    if action == "create":
        if not name:
            return missing(action, "name")
        return client.customers.requests.create(
            workspace_slug=workspace_slug,
            customer_id=customer_id,
            data=CreateCustomerRequest(
                name=name,
                description_html=opt(description_html),
                link=opt(link),
                work_item_ids=work_item_ids,
            ),
        )

    if not request_id:
        return missing(action, "request_id")

    if action == "retrieve":
        return client.customers.requests.retrieve(
            workspace_slug=workspace_slug, customer_id=customer_id, request_id=request_id
        )

    if action == "delete":
        client.customers.requests.delete(
            workspace_slug=workspace_slug, customer_id=customer_id, request_id=request_id
        )
        return None

    return client.customers.requests.update(
        workspace_slug=workspace_slug,
        customer_id=customer_id,
        request_id=request_id,
        data=UpdateCustomerRequest(
            name=opt(name), description_html=opt(description_html), link=opt(link)
        ),
    )


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="customer_request", description=DOC)
    def _customer_request(
        action: str,
        customer_id: str = "",
        request_id: str = "",
        name: str = "",
        description_html: str = "",
        link: str = "",
        work_item_ids: list[str] | None = None,
        query: str = "",
        cursor: str = "",
        per_page: int = 0,
    ) -> CustomerRequest | PaginatedCustomerRequestResponse | str | None:
        return _dispatch(
            action, customer_id, request_id, name, description_html, link,
            work_item_ids, query, cursor, per_page,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="customer_request", description=DOC)
    def _customer_request(
        action: str,
        customer_id: str = "",
        request_id: str = "",
        name: str = "",
        description_html: str = "",
        link: str = "",
        work_item_ids: list[str] | None = None,
        query: str = "",
        cursor: str = "",
        per_page: int = 0,
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, customer_id, request_id, name, description_html, link,
                    work_item_ids, query, cursor, per_page,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
