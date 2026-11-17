"""Regression tests for verifier reads whose target can land after page one."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from plane.errors.errors import HttpError

from evals.seed import CUSTOMER_NAME, CUSTOMER_REQUEST_NAME, R1_TITLE
from evals.tasks.cross import verify_c1
from evals.tasks.debias import L3_TAG_VERSION, L4_PROP_DISPLAY, L4_PROP_VALUE, verify_l3, verify_l4
from evals.tasks.write import W10_PAGE_BODY, W10_PAGE_NAME, verify_w5, verify_w10


class _Page:
    def __init__(self, results: list[Any], *, more: bool, cursor: str = ""):
        self.results = results
        self.next_page_results = more
        self.next_cursor = cursor


def _cursor(params: Any) -> str | None:
    if isinstance(params, dict):
        return params.get("cursor")
    return getattr(params, "cursor", None)


class _TwoPageList:
    def __init__(self, target: Any):
        self.target = target
        self.cursors: list[str | None] = []

    def list(self, *, params=None, **kwargs):
        cursor = _cursor(params)
        self.cursors.append(cursor)
        if cursor is None:
            return _Page([], more=True, cursor="cursor-2")
        assert cursor == "cursor-2"
        return _Page([self.target], more=False)


def test_c1_paginates_customers_requests_and_customer_work_items():
    customer = SimpleNamespace(id="customer-1", name=CUSTOMER_NAME)
    request = SimpleNamespace(id="request-1", name=CUSTOMER_REQUEST_NAME)
    linked = SimpleNamespace(id="wi-r1")
    customers = _TwoPageList(customer)
    requests = _TwoPageList(request)
    customer_work_items = _TwoPageList(linked)
    project_work_items = SimpleNamespace(
        list=lambda **kwargs: _Page(
            [SimpleNamespace(id="wi-r1", name=R1_TITLE, created_at="2026-01-01")],
            more=False,
        )
    )
    plane = SimpleNamespace(
        work_items=project_work_items,
        customers=SimpleNamespace(
            list=customers.list,
            requests=SimpleNamespace(list=requests.list),
            work_items=SimpleNamespace(list=customer_work_items.list),
        ),
    )
    ctx = {"workspace_slug": "ws", "project_id": "p1", "workspace_objects": []}

    ok, note = asyncio.run(verify_c1(plane, ctx, {"final_text": "", "calls": []}))
    assert ok is True, note
    assert customers.cursors == [None, "cursor-2"]
    assert requests.cursors == [None, "cursor-2"]
    assert customer_work_items.cursors == [None, "cursor-2"]


def test_l3_paginates_release_tags():
    tags = _TwoPageList(SimpleNamespace(id="tag-1", version=L3_TAG_VERSION))
    plane = SimpleNamespace(releases=SimpleNamespace(tags=SimpleNamespace(list=tags.list)))
    ctx = {"workspace_slug": "ws", "workspace_objects": []}

    ok, note = asyncio.run(verify_l3(plane, ctx, {"final_text": "", "calls": []}))
    assert ok is True, note
    assert tags.cursors == [None, "cursor-2"]


def test_l4_paginates_customer_properties():
    prop = SimpleNamespace(id="prop-1", display_name=L4_PROP_DISPLAY, property_type="TEXT")
    props = _TwoPageList(prop)
    plane = SimpleNamespace(
        customers=SimpleNamespace(
            properties=SimpleNamespace(list=props.list),
            property_values=SimpleNamespace(list=lambda **kwargs: {"prop-1": [L4_PROP_VALUE]}),
        )
    )
    ctx = {
        "workspace_slug": "ws",
        "customer": {"id": "customer-1"},
        "workspace_objects": [],
    }

    ok, note = asyncio.run(verify_l4(plane, ctx, {"final_text": "", "calls": []}))
    assert ok is True, note
    assert props.cursors == [None, "cursor-2"]


def test_w5_paginates_archived_work_items():
    archived = _TwoPageList(SimpleNamespace(id="wi-archived"))

    def missing(**kwargs):
        raise HttpError("not found", status_code=404, response={})

    plane = SimpleNamespace(
        work_items=SimpleNamespace(
            retrieve=missing,
            list_archived=archived.list,
        )
    )
    ctx = {
        "workspace_slug": "ws",
        "project_id": "p1",
        "module_completed_ids": ["wi-archived"],
    }

    ok, note = asyncio.run(verify_w5(plane, ctx, {"final_text": "", "calls": []}))
    assert ok is True, note
    assert archived.cursors == [None, "cursor-2"]


def test_w10_paginates_pages_then_retrieves_the_page_two_body():
    pages = _TwoPageList(SimpleNamespace(id="page-1", name=W10_PAGE_NAME))
    plane = SimpleNamespace(
        pages=SimpleNamespace(
            list_project_pages=pages.list,
            retrieve_project_page=lambda **kwargs: SimpleNamespace(
                id="page-1",
                description_html=f"<p>{W10_PAGE_BODY}</p>",
            ),
        )
    )
    ctx = {"workspace_slug": "ws", "project_id": "p1"}

    ok, note = asyncio.run(verify_w10(plane, ctx, {"final_text": "", "calls": []}))
    assert ok is True, note
    assert pages.cursors == [None, "cursor-2"]
