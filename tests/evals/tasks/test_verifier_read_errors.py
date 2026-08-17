"""Verifier API-read failures are infrastructure; explicit fallbacks stay tolerant."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest
from plane.errors.errors import HttpError

from evals.changelog import normalize_changelog_text
from evals.fixtures import (
    CUSTOMER_NAME,
    CUSTOMER_REQUEST_NAME,
    CYCLE_CURRENT,
    INTAKE_BILLING_TITLE,
    INTAKE_SPAM_TITLE,
    R1_TITLE,
    W7_SOURCE_TITLE,
    W7_TARGET_TITLE,
)
from evals.tasks.cross import verify_c1, verify_c2
from evals.tasks.debias import L1_TITLE, L4_PROP_DISPLAY, verify_l1, verify_l3, verify_l4
from evals.tasks.schema import verify_s1, verify_s2, verify_s3, verify_s4, verify_s5
from evals.tasks.verification import VerifierReadError
from evals.tasks.write import (
    W10_PAGE_NAME,
    W11_TITLE,
    verify_w4,
    verify_w5,
    verify_w6,
    verify_w7,
    verify_w10,
    verify_w11,
)


class _Page:
    def __init__(self, results: list[Any] | None = None):
        self.results = results or []
        self.next_page_results = False
        self.next_cursor = None


def _http(status: int) -> HttpError:
    return HttpError("read unavailable" if status != 404 else "not found", status, {})


def _raise(exc: BaseException) -> Callable[..., Any]:
    def fail(**kwargs: Any) -> Any:
        raise exc

    return fail


def _run() -> dict[str, Any]:
    return {"final_text": "", "calls": []}


def _s1(site: str) -> tuple[Any, dict[str, Any]]:
    severity = SimpleNamespace(id="severity-1", display_name="Severity", property_type="OPTION", options=[])
    if site == "properties":
        properties = SimpleNamespace(list=_raise(_http(500)))
    else:
        properties = SimpleNamespace(list=lambda **kw: [severity], options=SimpleNamespace(list=_raise(_http(500))))
    return SimpleNamespace(work_item_properties=properties), {
        "workspace_slug": "ws",
        "project_id": "p1",
        "bug_type": {"id": "bug-1"},
    }


def _s3(site: str) -> tuple[Any, dict[str, Any]]:
    incident = SimpleNamespace(id="incident-1", name="Incident")
    project_types = [] if site in {"ownership", "workspace-types"} else [incident]
    workspace_features = (
        _raise(ConnectionError("features down"))
        if site == "ownership"
        else lambda **kw: SimpleNamespace(model_dump=lambda: {"is_work_item_types_enabled": True})
    )
    workspace_types = _raise(ConnectionError("types down")) if site == "workspace-types" else lambda **kw: []
    property_list = _raise(_http(500)) if site == "properties" else lambda **kw: []
    return SimpleNamespace(
        work_item_types=SimpleNamespace(list=lambda **kw: project_types),
        workspaces=SimpleNamespace(get_features=workspace_features),
        workspace_work_item_types=SimpleNamespace(list=workspace_types),
        work_item_properties=SimpleNamespace(list=property_list),
    ), {"workspace_slug": "ws", "project_id": "p1"}


def _s5(site: str) -> tuple[Any, dict[str, Any]]:
    project_features = (
        _raise(ConnectionError("project features down"))
        if site == "project"
        else lambda **kw: SimpleNamespace(model_dump=lambda: {"cycles": True})
    )
    workspace_features = _raise(ConnectionError("workspace features down"))
    return SimpleNamespace(
        projects=SimpleNamespace(
            retrieve=lambda **kw: SimpleNamespace(cycle_view=True, is_time_tracking_enabled=True),
            get_features=project_features,
        ),
        workspaces=SimpleNamespace(get_features=workspace_features),
    ), {"workspace_slug": "ws", "project_id": "p1"}


def _l4(site: str) -> tuple[Any, dict[str, Any]]:
    prop = SimpleNamespace(id="property-1", display_name=L4_PROP_DISPLAY, property_type="TEXT")
    properties = _raise(ConnectionError("properties down")) if site == "properties" else lambda **kw: _Page([prop])
    values = _raise(ConnectionError("values down"))
    return SimpleNamespace(
        customers=SimpleNamespace(
            properties=SimpleNamespace(list=properties),
            property_values=SimpleNamespace(list=values),
        )
    ), {"workspace_slug": "ws", "customer": {"id": "customer-1"}}


def _c1() -> tuple[Any, dict[str, Any]]:
    r1 = SimpleNamespace(id="item-r1", name=R1_TITLE, created_at="2026-01-01")
    customer = SimpleNamespace(id="customer-1", name=CUSTOMER_NAME)
    request = SimpleNamespace(id="request-1", name=CUSTOMER_REQUEST_NAME)
    return SimpleNamespace(
        work_items=SimpleNamespace(list=lambda **kw: _Page([r1])),
        customers=SimpleNamespace(
            list=lambda **kw: _Page([customer]),
            requests=SimpleNamespace(list=lambda **kw: _Page([request])),
            work_items=SimpleNamespace(list=_raise(ConnectionError("links down"))),
        ),
    ), {"workspace_slug": "ws", "project_id": "p1", "workspace_objects": []}


def _w5(site: str) -> tuple[Any, dict[str, Any]]:
    retrieve = _raise(_http(500) if site == "retrieve" else _http(404))
    return SimpleNamespace(
        work_items=SimpleNamespace(
            retrieve=retrieve,
            list_archived=_raise(ConnectionError("archive list down")),
        )
    ), {"workspace_slug": "ws", "project_id": "p1", "module_completed_ids": ["item-1"]}


def _w7(site: str) -> tuple[Any, dict[str, Any]]:
    rows = [
        SimpleNamespace(id="source-1", name=W7_SOURCE_TITLE, created_at="2026-01-02"),
        SimpleNamespace(id="target-1", name=W7_TARGET_TITLE, created_at="2026-01-01"),
    ]
    dependencies = (
        _raise(ConnectionError("dependencies down"))
        if site == "dependencies"
        else lambda **kw: {"blocking": [{"id": "target-1"}]}
    )
    links = _raise(ConnectionError("links down"))
    return SimpleNamespace(
        work_items=SimpleNamespace(
            list=lambda **kw: _Page(rows),
            dependencies=SimpleNamespace(list=dependencies),
            links=SimpleNamespace(list=links),
        )
    ), {"workspace_slug": "ws", "project_id": "p1"}


def _w10(site: str) -> tuple[Any, dict[str, Any]]:
    page = SimpleNamespace(id="page-1", name=W10_PAGE_NAME)
    listing = _raise(ConnectionError("pages down")) if site == "list" else lambda **kw: _Page([page])
    return SimpleNamespace(
        pages=SimpleNamespace(
            list_project_pages=listing,
            retrieve_project_page=_raise(TimeoutError("page read timed out")),
        )
    ), {"workspace_slug": "ws", "project_id": "p1"}


def _infra_cases() -> list[Any]:
    cases: list[Any] = []

    def add(case_id: str, task: str, reading: str, verifier: Any, plane: Any, ctx: dict[str, Any]) -> None:
        cases.append(pytest.param(task, reading, lambda: verifier(plane, ctx, _run()), id=case_id))

    plane, ctx = _s1("properties")
    add("S1-type-properties", "S1", "listing Bug type properties", verify_s1, plane, ctx)
    plane, ctx = _s1("options")
    add("S1-options", "S1", "listing Severity options", verify_s1, plane, ctx)
    add(
        "S2-estimate",
        "S2",
        "retrieving the project estimate",
        verify_s2,
        SimpleNamespace(estimates=SimpleNamespace(retrieve=_raise(_http(500)))),
        {"workspace_slug": "ws", "project_id": "p1"},
    )
    for site, reading in (
        ("ownership", "reading workspace work-item-type ownership"),
        ("workspace-types", "listing workspace work-item types"),
        ("properties", "listing Incident type properties"),
    ):
        plane, ctx = _s3(site)
        add(f"S3-{site}", "S3", reading, verify_s3, plane, ctx)
    add(
        "S4-intake-list-fallback",
        "S4",
        "listing intake while resolving",
        verify_s4,
        SimpleNamespace(
            intake=SimpleNamespace(
                retrieve=_raise(ConnectionError("retrieve down")),
                list=_raise(ConnectionError("list down")),
            )
        ),
        {
            "workspace_slug": "ws",
            "project_id": "p1",
            "intake": {"billing": {"issue_id": "billing-1"}, "spam": {"issue_id": "spam-1"}},
        },
    )
    for site, reading in (("project", "reading project feature flags"), ("workspace", "reading workspace customer")):
        plane, ctx = _s5(site)
        add(f"S5-{site}-features", "S5", reading, verify_s5, plane, ctx)
    add(
        "L1-worklog-summary",
        "L1",
        "reading the project worklog summary",
        verify_l1,
        SimpleNamespace(
            work_items=SimpleNamespace(work_logs=SimpleNamespace(list=lambda **kw: [SimpleNamespace(duration=90)])),
            projects=SimpleNamespace(get_worklog_summary=_raise(ConnectionError("summary down"))),
        ),
        {
            "workspace_slug": "ws",
            "project_id": "p1",
            "items": {L1_TITLE: "item-1"},
            "l1_expected_summary_ids": ["item-1", "item-seeded"],
        },
    )
    add(
        "L3-release-tags",
        "L3",
        "listing workspace release tags",
        verify_l3,
        SimpleNamespace(releases=SimpleNamespace(tags=SimpleNamespace(list=_raise(ConnectionError("tags down"))))),
        {"workspace_slug": "ws"},
    )
    for site, reading in (
        ("properties", "listing workspace customer properties"),
        ("values", "reading property values"),
    ):
        plane, ctx = _l4(site)
        add(f"L4-{site}", "L4", reading, verify_l4, plane, ctx)
    plane, ctx = _c1()
    add("C1-customer-work-items", "C1", "listing work items linked to customer", verify_c1, plane, ctx)
    baseline = "Changelog entry one: One. Changelog entry two: Two."
    add(
        "C2-release-changelog",
        "C2",
        "reading release release-1 and its changelog",
        verify_c2,
        SimpleNamespace(
            releases=SimpleNamespace(
                retrieve=_raise(_http(500)),
                changelog=SimpleNamespace(retrieve=lambda **kw: normalize_changelog_text(baseline)),
            )
        ),
        {
            "workspace_slug": "ws",
            "release": {"id": "release-1", "name": "1.2.0"},
            "release_changelog_text": baseline,
        },
    )
    add(
        "W4-triage-label",
        "W4",
        "retrieving seeded triage label",
        verify_w4,
        SimpleNamespace(labels=SimpleNamespace(retrieve=_raise(_http(500)))),
        {"workspace_slug": "ws", "project_id": "p1", "labels": {"triage": "label-1"}},
    )
    for site, reading in (("retrieve", "retrieving module work item"), ("archived", "listing archived items")):
        plane, ctx = _w5(site)
        add(f"W5-{site}", "W5", reading, verify_w5, plane, ctx)
    add(
        "W6-cycle-items",
        "W6",
        f"listing {CYCLE_CURRENT} work items",
        verify_w6,
        SimpleNamespace(
            cycles=SimpleNamespace(
                retrieve=lambda **kw: SimpleNamespace(
                    end_date=date.today().isoformat(), archived_at=None, progress_snapshot=None
                ),
                list_work_items=_raise(TimeoutError("cycle items read timed out")),
            )
        ),
        {
            "workspace_slug": "ws",
            "project_id": "p1",
            "cycle_past_id": "past-1",
            "cycle_current_id": "current-1",
            "w6_unfinished_titles": ["unfinished"],
        },
    )
    for site, reading in (("dependencies", "listing dependencies"), ("links", "listing links")):
        plane, ctx = _w7(site)
        add(f"W7-{site}", "W7", reading, verify_w7, plane, ctx)
    item = SimpleNamespace(id="item-1", name=W11_TITLE, created_at="2026-01-01")
    add(
        "W11-worklogs",
        "W11",
        "listing work logs for item",
        verify_w11,
        SimpleNamespace(
            work_items=SimpleNamespace(
                list=lambda **kw: _Page([item]),
                work_logs=SimpleNamespace(list=_raise(_http(500))),
            )
        ),
        {"workspace_slug": "ws", "project_id": "p1"},
    )
    for site, reading in (("list", "listing project pages"), ("retrieve", "retrieving project page")):
        plane, ctx = _w10(site)
        add(f"W10-{site}", "W10", reading, verify_w10, plane, ctx)
    return cases


@pytest.mark.parametrize(("task_id", "reading", "invoke"), _infra_cases())
def test_required_verifier_read_failures_are_infrastructure(task_id: str, reading: str, invoke: Callable[[], Any]):
    with pytest.raises(VerifierReadError) as caught:
        asyncio.run(invoke())

    message = str(caught.value)
    assert message.startswith(f"{task_id} verifier read failed while ")
    assert reading in message
    assert caught.value.__cause__ is not None


async def _negative_s2_estimate_404() -> tuple[bool, str]:
    plane = SimpleNamespace(estimates=SimpleNamespace(retrieve=_raise(_http(404))))
    return await verify_s2(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())


async def _negative_c2_oracle_404() -> tuple[bool, str]:
    baseline = "Changelog entry one: One. Changelog entry two: Two."
    plane = SimpleNamespace(
        releases=SimpleNamespace(
            retrieve=_raise(_http(404)),
            changelog=SimpleNamespace(retrieve=lambda **kw: normalize_changelog_text(baseline)),
        )
    )
    ctx = {
        "workspace_slug": "ws",
        "release": {"id": "release-1", "name": "1.2.0"},
        "release_changelog_text": baseline,
    }
    return await verify_c2(plane, ctx, _run())


async def _negative_w6_current_cycle_404() -> tuple[bool, str]:
    plane = SimpleNamespace(
        cycles=SimpleNamespace(
            retrieve=lambda **kw: SimpleNamespace(
                end_date=date.today().isoformat(), archived_at=None, progress_snapshot=None
            ),
            list_work_items=_raise(_http(404)),
        )
    )
    ctx = {
        "workspace_slug": "ws",
        "project_id": "p1",
        "cycle_past_id": "past-1",
        "cycle_current_id": "current-1",
        "w6_unfinished_titles": ["unfinished"],
    }
    return await verify_w6(plane, ctx, _run())


async def _negative_w10_page_retrieve_404() -> tuple[bool, str]:
    page = SimpleNamespace(id="page-1", name=W10_PAGE_NAME)
    plane = SimpleNamespace(
        pages=SimpleNamespace(
            list_project_pages=lambda **kw: _Page([page]),
            retrieve_project_page=_raise(_http(404)),
        )
    )
    return await verify_w10(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())


@pytest.mark.parametrize(
    ("invoke", "note_fragment"),
    [
        pytest.param(_negative_s2_estimate_404, "estimate not found", id="S2-missing-created-estimate"),
        pytest.param(_negative_c2_oracle_404, "no longer exists", id="C2-missing-seed-oracle"),
        pytest.param(_negative_w6_current_cycle_404, "not found", id="W6-missing-rollover-cycle"),
        pytest.param(_negative_w10_page_retrieve_404, "not found after listing", id="W10-missing-created-page"),
    ],
)
def test_authoritative_not_found_is_an_agent_failure(invoke: Callable[[], Any], note_fragment: str):
    success, note = asyncio.run(invoke())
    assert success is False
    assert note_fragment in note


async def _tolerant_s1_property_404() -> tuple[bool, str]:
    plane = SimpleNamespace(work_item_properties=SimpleNamespace(list=_raise(_http(404))))
    return await verify_s1(plane, {"workspace_slug": "ws", "project_id": "p1", "bug_type": {"id": "bug"}}, _run())


async def _tolerant_s1_options_404() -> tuple[bool, str]:
    severity = SimpleNamespace(id="severity", display_name="Severity", property_type="OPTION", options=[])
    plane = SimpleNamespace(
        work_item_properties=SimpleNamespace(
            list=lambda **kw: [severity],
            options=SimpleNamespace(list=_raise(_http(404))),
        )
    )
    return await verify_s1(plane, {"workspace_slug": "ws", "project_id": "p1", "bug_type": {"id": "bug"}}, _run())


async def _tolerant_s3_property_404() -> tuple[bool, str]:
    incident = SimpleNamespace(id="incident", name="Incident")
    plane = SimpleNamespace(
        work_item_types=SimpleNamespace(list=lambda **kw: [incident]),
        work_item_properties=SimpleNamespace(list=_raise(_http(404))),
    )
    return await verify_s3(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())


async def _tolerant_s4_retrieve_fallback() -> tuple[bool, str]:
    rows = [
        SimpleNamespace(issue_detail=SimpleNamespace(name=INTAKE_BILLING_TITLE), status=1),
        SimpleNamespace(issue_detail=SimpleNamespace(name=INTAKE_SPAM_TITLE), status=-1),
    ]
    plane = SimpleNamespace(
        intake=SimpleNamespace(retrieve=_raise(ConnectionError("retrieve down")), list=lambda **kw: rows)
    )
    ctx = {
        "workspace_slug": "ws",
        "project_id": "p1",
        "intake": {"billing": {"issue_id": "billing"}, "spam": {"issue_id": "spam"}},
    }
    return await verify_s4(plane, ctx, _run())


async def _tolerant_w4_not_found() -> tuple[bool, str]:
    plane = SimpleNamespace(labels=SimpleNamespace(retrieve=_raise(_http(404))))
    ctx = {"workspace_slug": "ws", "project_id": "p1", "labels": {"triage": "label-1"}}
    return await verify_w4(plane, ctx, _run())


async def _tolerant_w5_retrieve_fallback() -> tuple[bool, str]:
    archived = SimpleNamespace(id="item-1")
    plane = SimpleNamespace(
        work_items=SimpleNamespace(
            retrieve=_raise(_http(404)),
            list_archived=lambda **kw: _Page([archived]),
        )
    )
    ctx = {"workspace_slug": "ws", "project_id": "p1", "module_completed_ids": ["item-1"]}
    return await verify_w5(plane, ctx, _run())


async def _tolerant_w5_optional_archive_crosscheck() -> tuple[bool, str]:
    plane = SimpleNamespace(
        work_items=SimpleNamespace(
            retrieve=lambda **kw: SimpleNamespace(id="item-1", archived_at=None),
            list_archived=_raise(ConnectionError("optional list down")),
        )
    )
    ctx = {"workspace_slug": "ws", "project_id": "p1", "module_completed_ids": ["item-1"]}
    return await verify_w5(plane, ctx, _run())


async def _tolerant_w11_diagnostic_read() -> tuple[bool, str]:
    item = SimpleNamespace(id="item-1", name=W11_TITLE, created_at="2026-01-01")
    plane = SimpleNamespace(
        work_items=SimpleNamespace(list=lambda **kw: _Page([item]), work_logs=SimpleNamespace(list=lambda **kw: [])),
        projects=SimpleNamespace(retrieve=_raise(ConnectionError("diagnostic read down"))),
    )
    return await verify_w11(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())


async def _tolerant_w11_gate_404() -> tuple[bool, str]:
    item = SimpleNamespace(id="item-1", name=W11_TITLE, created_at="2026-01-01")
    plane = SimpleNamespace(
        work_items=SimpleNamespace(
            list=lambda **kw: _Page([item]),
            work_logs=SimpleNamespace(list=_raise(_http(404))),
        )
    )
    return await verify_w11(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())


@pytest.mark.parametrize(
    ("invoke", "want_success"),
    [
        pytest.param(_tolerant_s1_property_404, False, id="S1-property-404-is-absence"),
        pytest.param(_tolerant_s1_options_404, False, id="S1-options-404-is-absence"),
        pytest.param(_tolerant_s3_property_404, False, id="S3-property-404-is-absence"),
        pytest.param(_tolerant_s4_retrieve_fallback, True, id="S4-retrieve-has-list-fallback"),
        pytest.param(_tolerant_w4_not_found, False, id="W4-seed-id-404-is-deletion"),
        pytest.param(_tolerant_w5_retrieve_fallback, True, id="W5-retrieve-404-has-archive-fallback"),
        pytest.param(_tolerant_w5_optional_archive_crosscheck, False, id="W5-archive-list-crosscheck-is-optional"),
        pytest.param(_tolerant_w11_diagnostic_read, False, id="W11-feature-read-is-diagnostic-only"),
        pytest.param(_tolerant_w11_gate_404, False, id="W11-worklog-404-is-disabled-gate"),
    ],
)
def test_deliberately_tolerant_verifier_reads_are_pinned(invoke: Callable[[], Any], want_success: bool):
    success, note = asyncio.run(invoke())
    assert success is want_success, note
