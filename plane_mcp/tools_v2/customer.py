"""Consolidated `customer` tool.

Collapses customers/base.py (5 tools) and customers/work_items.py (2 tools)
into one action-dispatch tool.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from plane.models.customers import (
    CreateCustomer,
    Customer,
    CustomerWorkItem,
    LinkCustomerWorkItems,
    PaginatedCustomerResponse,
    UpdateCustomer,
)

from plane_mcp.client import get_plane_client_context
from plane_mcp.tools_v2._common import bad_action, json_out, missing, opt

ACTIONS = [
    "list",
    "retrieve",
    "create",
    "update",
    "delete",
    "list_work_items",
    "manage_work_items",
]

DOC = """Manage customers and the work items linked to them. Actions:
list (no required params; optional query, cursor, per_page);
retrieve (customer_id);
create (name; optional description_html, email, website_url, domain, employees,
    stage, contract_status, revenue, external_source, external_id);
update (customer_id; optional name, description_html, email, website_url, domain,
    employees, stage, contract_status, revenue, logo_props, external_source, external_id);
delete (customer_id, OR both external_source and external_id);
list_work_items (customer_id; optional customer_request_id, search);
manage_work_items (customer_id, operation, work_item_ids; optional customer_request_id).

create is an upsert: when external_source and external_id are both given the matching
customer is updated, otherwise the customer with the same name is updated. A repeated
call never duplicates.

domain is the customer's industry -- shown as "Industry" in Plane. Free text, e.g.
"Retail", "Fintech". It is NOT a web domain; the site belongs in website_url.
stage is stored free-form but Plane only renders:
    lead | sales_qualified_lead | contract_negotiation | closed_won | closed_lost
contract_status is stored free-form but Plane only renders:
    active | pre_contract | signed | inactive
revenue is annual revenue as a string, e.g. "5000000".

manage_work_items: operation is "link" or "unlink". On unlink, omitting
customer_request_id drops every link to the work item, whichever request made it.
It returns the customer's linked work items after the operation.
list returns a paginated envelope (results + total_count, next_cursor,
next_page_results); page again while next_page_results is true. list_work_items is
unpaginated and returns every linked work item."""


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
    name: str,
    description_html: str,
    email: str,
    website_url: str,
    domain: str,
    employees: int,
    stage: str,
    contract_status: str,
    revenue: str,
    logo_props: dict | None,
    external_source: str,
    external_id: str,
    query: str,
    cursor: str,
    per_page: int,
    operation: str,
    work_item_ids: list[str] | None,
    customer_request_id: str,
    search: str,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        return client.customers.list(
            workspace_slug=workspace_slug, params=_page_params(cursor, per_page, query)
        )

    if action == "create":
        if not name:
            return missing(action, "name")
        return client.customers.create(
            workspace_slug=workspace_slug,
            data=CreateCustomer(
                name=name,
                description_html=opt(description_html),
                email=opt(email),
                website_url=opt(website_url),
                domain=opt(domain),
                employees=opt(employees),
                stage=opt(stage),
                contract_status=opt(contract_status),
                revenue=opt(revenue),
                external_source=opt(external_source),
                external_id=opt(external_id),
            ),
        )

    if action == "delete":
        if not customer_id and not (external_source and external_id):
            return missing(action, "customer_id, or both external_source and external_id")
        client.customers.delete(
            workspace_slug=workspace_slug,
            customer_id=opt(customer_id),
            external_source=opt(external_source),
            external_id=opt(external_id),
        )
        return None

    if not customer_id:
        return missing(action, "customer_id")

    if action == "retrieve":
        return client.customers.retrieve(workspace_slug=workspace_slug, customer_id=customer_id)

    if action == "update":
        return client.customers.update(
            workspace_slug=workspace_slug,
            customer_id=customer_id,
            data=UpdateCustomer(
                name=opt(name),
                description_html=opt(description_html),
                email=opt(email),
                website_url=opt(website_url),
                domain=opt(domain),
                employees=opt(employees),
                stage=opt(stage),
                contract_status=opt(contract_status),
                revenue=opt(revenue),
                logo_props=logo_props,
                external_source=opt(external_source),
                external_id=opt(external_id),
            ),
        )

    work_items = client.customers.work_items

    if action == "list_work_items":
        return work_items.list(
            workspace_slug=workspace_slug,
            customer_id=customer_id,
            customer_request_id=opt(customer_request_id),
            search=opt(search),
        )

    if operation not in ("link", "unlink"):
        return missing(action, 'operation ("link" or "unlink")')
    if not work_item_ids:
        return missing(action, "work_item_ids")

    if operation == "link":
        work_items.create(
            workspace_slug=workspace_slug,
            customer_id=customer_id,
            data=LinkCustomerWorkItems(work_item_ids=work_item_ids),
            customer_request_id=opt(customer_request_id),
        )
    else:
        for work_item_id in work_item_ids:
            work_items.delete(
                workspace_slug=workspace_slug,
                customer_id=customer_id,
                work_item_id=work_item_id,
                customer_request_id=opt(customer_request_id),
            )

    return work_items.list(workspace_slug=workspace_slug, customer_id=customer_id)


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="customer", description=DOC)
    def _customer(
        action: str,
        customer_id: str = "",
        name: str = "",
        description_html: str = "",
        email: str = "",
        website_url: str = "",
        domain: str = "",
        employees: int = 0,
        stage: str = "",
        contract_status: str = "",
        revenue: str = "",
        logo_props: dict | None = None,
        external_source: str = "",
        external_id: str = "",
        query: str = "",
        cursor: str = "",
        per_page: int = 0,
        operation: str = "",
        work_item_ids: list[str] | None = None,
        customer_request_id: str = "",
        search: str = "",
    ) -> Customer | PaginatedCustomerResponse | list[CustomerWorkItem] | str | None:
        return _dispatch(
            action, customer_id, name, description_html, email, website_url, domain,
            employees, stage, contract_status, revenue, logo_props, external_source,
            external_id, query, cursor, per_page, operation, work_item_ids,
            customer_request_id, search,
        )


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="customer", description=DOC)
    def _customer(
        action: str,
        customer_id: str = "",
        name: str = "",
        description_html: str = "",
        email: str = "",
        website_url: str = "",
        domain: str = "",
        employees: int = 0,
        stage: str = "",
        contract_status: str = "",
        revenue: str = "",
        logo_props: dict | None = None,
        external_source: str = "",
        external_id: str = "",
        query: str = "",
        cursor: str = "",
        per_page: int = 0,
        operation: str = "",
        work_item_ids: list[str] | None = None,
        customer_request_id: str = "",
        search: str = "",
    ) -> str:
        try:
            return json_out(
                _dispatch(
                    action, customer_id, name, description_html, email, website_url, domain,
                    employees, stage, contract_status, revenue, logo_props, external_source,
                    external_id, query, cursor, per_page, operation, work_item_ids,
                    customer_request_id, search,
                )
            )
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
