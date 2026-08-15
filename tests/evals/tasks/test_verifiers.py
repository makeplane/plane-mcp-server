"""Offline eval tests for R, W, S, and C verifiers."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from plane.errors.errors import HttpError

from evals.evidence import TARGET_ENTITY_EVIDENCE
from evals.seed import (
    CUSTOMER_NAME,
    CUSTOMER_REQUEST_NAME,
    CYCLE_PAST,
    R1_TITLE,
    W3_TITLE,
    W7_SOURCE_TITLE,
    W7_TARGET_TITLE,
    W7_URL,
    W8_TITLE,
)
from evals.tasks.cross import verify_c1
from evals.tasks.read import verify_r3
from evals.tasks.schema import verify_s3, verify_s5
from evals.tasks.write import (
    W10_PAGE_BODY,
    W10_PAGE_NAME,
    verify_w3,
    verify_w4,
    verify_w5,
    verify_w6,
    verify_w7,
    verify_w8,
    verify_w10,
)


class _Page:
    def __init__(self, results: list[Any] | None = None, next_page_results: bool = False):
        self.results = results or []
        self.next_page_results = next_page_results
        self.next_cursor = None


def _http404() -> HttpError:
    return HttpError("not found", status_code=404, response={})


def _item(id: str, name: str, **kw: Any) -> SimpleNamespace:
    return SimpleNamespace(id=id, name=name, **kw)


def _run() -> dict[str, Any]:
    return {"final_text": "", "calls": []}


class _DepsDump:
    def __init__(self, data: dict):
        self._data = data

    def model_dump(self) -> dict:
        return self._data


class _W7Plane:
    """Fake where dependencies.list returns dump with tgt only in blocked_by."""

    def __init__(self, deps_dump: dict, urls: list[str] | None = None):
        self._deps_dump = deps_dump
        self._urls = urls if urls is not None else [W7_URL]
        self.work_items = SimpleNamespace(
            list=lambda **kw: _Page([_item("src-1", W7_SOURCE_TITLE), _item("tgt-1", W7_TARGET_TITLE)]),
            dependencies=SimpleNamespace(list=self._deps_list),
            links=SimpleNamespace(list=self._links_list),
        )

    def _deps_list(self, **kw):
        return _DepsDump(self._deps_dump)

    def _links_list(self, **kw):
        return _Page([SimpleNamespace(url=u) for u in self._urls])


@pytest.mark.parametrize(
    ("dependencies", "want", "expect_wrong_direction"),
    [
        pytest.param(
            {"blocking": [], "blocked_by": [{"id": "tgt-1"}]},
            False,
            True,
            id="blocked-by-is-wrong-direction",
        ),
        pytest.param(
            {"blocking": [{"id": "tgt-1"}], "blocked_by": []},
            True,
            False,
            id="blocking-passes",
        ),
    ],
)
def test_f1_w7_behaviours(dependencies, want, expect_wrong_direction):
    ok, note = asyncio.run(
        verify_w7(
            _W7Plane(dependencies),
            {"workspace_slug": "ws", "project_id": "p1"},
            _run(),
        )
    )
    assert ok is want, note
    if expect_wrong_direction:
        assert "blocking" in note.lower() or "no blocking" in note.lower()
        assert "wrong direction" in note or "tgt-1" in note


class _W6Plane:
    def __init__(
        self,
        *,
        past_end: str,
        archived_at=None,
        snapshot=None,
        sprint13_names: list[str] | None = None,
        list_error: Exception | None = None,
    ):
        self._past_end = past_end
        self._archived_at = archived_at
        self._snapshot = snapshot
        self._s13 = sprint13_names or []
        self._list_error = list_error
        self.cycles = SimpleNamespace(retrieve=self._retrieve, list_work_items=self._list_wi)

    def _retrieve(self, **kw):
        return SimpleNamespace(
            id="c12",
            end_date=self._past_end,
            archived_at=self._archived_at,
            progress_snapshot=self._snapshot,
        )

    def _list_wi(self, **kw):
        if self._list_error is not None:
            raise self._list_error
        return _Page([_item(f"i{i}", n) for i, n in enumerate(self._s13)])


@pytest.mark.parametrize(
    ("end_offset", "timestamp", "seed_offset", "names", "want", "expect"),
    [
        pytest.param(
            -14, False, -14, ["Inventory count goes negative under load"], False, "not closed", id="seeded-past-end"
        ),
        pytest.param(1, True, 1, ["Inventory count goes negative under load"], False, "not closed", id="noop-tomorrow"),
        pytest.param(
            0, True, 1, ["Inventory count goes negative under load"], True, "end_date", id="closed-today-timestamp"
        ),
        pytest.param(
            0,
            False,
            -14,
            ["Inventory count goes negative under load", "Tooltip clipped inside modal dialog"],
            True,
            "",
            id="closed-today-date",
        ),
    ],
)
def test_f2_w6_closes_only_on_a_real_end_date_signal(end_offset, timestamp, seed_offset, names, want, expect):
    """A timestamp and a bare date must provide the same real closure signal."""
    end_date = (date.today() + timedelta(days=end_offset)).isoformat()
    if timestamp:
        end_date = f"{end_date}T00:00:00Z"
    ctx = {
        "workspace_slug": "ws",
        "project_id": "p1",
        "cycle_past_id": "c12",
        "cycle_current_id": "c13",
        "cycle_past_seed_end_date": (date.today() + timedelta(days=seed_offset)).isoformat(),
        "w6_unfinished_titles": names,
        "cycles": {CYCLE_PAST: "c12"},
    }
    ok, note = asyncio.run(verify_w6(_W6Plane(past_end=end_date, sprint13_names=names), ctx, _run()))
    assert ok is want, note
    if expect:
        assert expect in note.lower() or expect in note, note


@pytest.mark.parametrize(
    ("sprint13_names", "list_error", "ctx_override", "want", "expect_note", "raises"),
    [
        pytest.param(None, None, {}, True, "", None, id="healthy"),
        pytest.param(
            ["Inventory count goes negative under load"],
            None,
            {},
            False,
            "unfinished not on Sprint 13",
            None,
            id="missing-rollover-item",
        ),
        pytest.param(None, RuntimeError("503 unavailable"), {}, None, "", "W6.*Sprint 13", id="listing-error"),
        pytest.param(
            None,
            None,
            {"cycle_current_id": None},
            None,
            "",
            "W6 fixture error.*Sprint 13 id missing",
            id="missing-current-cycle",
        ),
        pytest.param(
            None,
            None,
            {"cycle_past_id": None},
            None,
            "",
            "W6 fixture error.*Sprint 12 id missing",
            id="missing-past-cycle",
        ),
        pytest.param(
            None,
            None,
            {"w6_unfinished_titles": []},
            None,
            "",
            "W6 fixture error.*unfinished items.*empty",
            id="empty-unfinished-fixture",
        ),
    ],
)
def test_w6_rollover_verification_is_fail_closed(sprint13_names, list_error, ctx_override, want, expect_note, raises):
    expected = ["Inventory count goes negative under load", "Tooltip clipped inside modal dialog"]
    ctx = {
        "workspace_slug": "ws",
        "project_id": "p1",
        "cycle_past_id": "c12",
        "cycle_current_id": "c13",
        "cycle_past_seed_end_date": (date.today() + timedelta(days=1)).isoformat(),
        "w6_unfinished_titles": expected,
        **ctx_override,
    }
    plane = _W6Plane(
        past_end=date.today().isoformat(),
        sprint13_names=expected if sprint13_names is None else sprint13_names,
        list_error=list_error,
    )
    if raises:
        with pytest.raises(RuntimeError, match=raises):
            asyncio.run(verify_w6(plane, ctx, _run()))
        return

    ok, note = asyncio.run(verify_w6(plane, ctx, _run()))
    assert ok is want, note
    if expect_note:
        assert expect_note in note


class _W5Plane:
    def __init__(self, *, retrieve_map: dict[str, Any], archived_ids: list[str]):
        self._retrieve_map = retrieve_map
        self._archived_ids = archived_ids
        self.work_items = SimpleNamespace(
            retrieve=self._retrieve,
            list_archived=self._list_archived,
            list=lambda **kw: _Page([]),
        )

    def _retrieve(self, **kw):
        wid = str(kw["work_item_id"])
        if wid not in self._retrieve_map:
            raise _http404()
        val = self._retrieve_map[wid]
        if val is None:
            raise _http404()
        return val

    def _list_archived(self, **kw):
        return _Page([_item(i, f"n-{i}") for i in self._archived_ids])


@pytest.mark.parametrize(
    ("plane", "module_ids", "want", "expect"),
    [
        pytest.param(
            _W5Plane(retrieve_map={}, archived_ids=[]),
            ["m1", "m2", "m3"],
            False,
            "not archived",
            id="deleted-not-archived",
        ),
        pytest.param(
            _W5Plane(retrieve_map={}, archived_ids=["m1", "m2", "m3"]), ["m1", "m2", "m3"], True, "", id="archived-list"
        ),
        pytest.param(
            _W5Plane(
                retrieve_map={"m1": SimpleNamespace(id="m1", archived_at="2026-01-01T00:00:00Z")},
                archived_ids=[],
            ),
            ["m1"],
            True,
            "",
            id="archived-at",
        ),
    ],
)
def test_f3_w5_accepts_archive_but_not_deletion(plane, module_ids, want, expect):
    """A 404 alone is not evidence of archiving — deleting every item would also 404."""
    ctx = {"workspace_slug": "ws", "project_id": "p1", "module_completed_ids": module_ids}
    ok, note = asyncio.run(verify_w5(plane, ctx, _run()))
    assert ok is want, note
    if expect:
        assert expect in note, note


class _C1Plane:
    def __init__(
        self,
        *,
        customers: list[Any],
        requests: list[Any],
        linked: list[Any],
        project_items: list[Any],
    ):
        self.customers = SimpleNamespace(
            list=lambda **kw: _Page(customers),
            requests=SimpleNamespace(list=lambda **kw: _Page(requests)),
            work_items=SimpleNamespace(list=lambda **kw: _Page(linked)),
        )
        self.work_items = SimpleNamespace(list=lambda **kw: _Page(project_items))


@pytest.mark.parametrize(
    ("customers", "linked", "project_items", "want", "expect_any"),
    [
        pytest.param(
            [SimpleNamespace(id="c1", name="Acme Industries")],
            [SimpleNamespace(id="wi-r1")],
            [_item("wi-r1", R1_TITLE)],
            False,
            (CUSTOMER_NAME,),
            id="lookalike-customer",
        ),
        pytest.param(
            [SimpleNamespace(id="c1", name=CUSTOMER_NAME)],
            [SimpleNamespace(id="wi-other")],
            [_item("wi-r1", R1_TITLE), _item("wi-other", "Other")],
            False,
            ("not linked", "wi-r1"),
            id="wrong-linked-item",
        ),
        pytest.param(
            [SimpleNamespace(id="c1", name=CUSTOMER_NAME)],
            [SimpleNamespace(id="wi-r1")],
            [_item("wi-r1", R1_TITLE)],
            True,
            (),
            id="exact-customer-and-item",
        ),
    ],
)
def test_f4_c1_requires_the_named_customer_linked_to_the_named_item(customers, linked, project_items, want, expect_any):
    """Both ends are checked: a lookalike customer name and a link to any other item fail."""
    plane = _C1Plane(
        customers=customers,
        requests=[SimpleNamespace(id="r1", name=CUSTOMER_REQUEST_NAME)],
        linked=linked,
        project_items=project_items,
    )
    ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {R1_TITLE: "wi-r1"}}
    ok, note = asyncio.run(verify_c1(plane, ctx, _run()))
    assert ok is want, note
    if expect_any:
        assert any(s in note for s in expect_any), note


class _W3Plane:
    def __init__(self, comments: list[Any]):
        self.work_items = SimpleNamespace(
            list=lambda **kw: _Page([_item("w3", W3_TITLE)]),
            comments=SimpleNamespace(list=lambda **kw: _Page(comments)),
        )


@pytest.mark.parametrize(
    ("comment_html", "stripped", "want", "expect"),
    [
        pytest.param("<p>lgtm</p>", "lgtm", False, "contrast tokens", id="unrelated-comment"),
        pytest.param(
            "<p>Reviewed contrast tokens — needs design pass</p>",
            "Reviewed contrast tokens — needs design pass",
            True,
            "",
            id="exact-stripped-text",
        ),
        pytest.param(
            "<p>Reviewed contrast tokens — needs&nbsp;design pass</p>",
            None,
            True,
            "",
            id="normalized-html",
        ),
        pytest.param(
            "<p>Reviewed contrast tokens — needs design pass and accessibility review</p>",
            "Reviewed contrast tokens — needs design pass and accessibility review",
            False,
            "exact normalized comment",
            id="substring-only",
        ),
    ],
)
def test_f5_w3_requires_exact_normalized_comment_text(comment_html, stripped, want, expect):
    plane = _W3Plane([SimpleNamespace(comment_html=comment_html, comment_stripped=stripped)])
    ok, note = asyncio.run(verify_w3(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run()))
    assert ok is want, note
    if expect:
        assert expect in note, note


class _Pages:
    def __init__(self, body: str):
        self.body = body

    def list_project_pages(self, **kwargs):
        return _Page([SimpleNamespace(id="page-1", name=W10_PAGE_NAME)])

    def retrieve_project_page(self, **kwargs):
        return SimpleNamespace(id="page-1", name=W10_PAGE_NAME, description_html=self.body)


@pytest.mark.parametrize(
    ("body", "want", "expect"),
    [
        pytest.param("<p>Unrelated runbook body</p>", False, "body mismatch", id="wrong-body"),
        pytest.param(f"<p>  {W10_PAGE_BODY} </p>", True, "", id="normalized-exact-body"),
    ],
)
def test_w10_requires_the_exact_normalized_page_body(body, want, expect):
    ok, note = asyncio.run(
        verify_w10(
            SimpleNamespace(pages=_Pages(body)),
            {"workspace_slug": "ws", "project_id": "p1"},
            _run(),
        )
    )
    assert ok is want, note
    if expect:
        assert expect in note


class _W4Plane:
    def __init__(self, *, by_id: dict[str, Any], listed: list[Any]):
        self._by_id = by_id
        self.labels = SimpleNamespace(retrieve=self._retrieve, list=lambda **kw: _Page(listed))

    def _retrieve(self, **kw):
        lid = str(kw["label_id"])
        if lid not in self._by_id:
            raise _http404()
        return self._by_id[lid]


@pytest.mark.parametrize(
    ("by_id", "listed", "want", "expect"),
    [
        pytest.param(
            {"triage-id": SimpleNamespace(id="triage-id", name="triage")},
            [SimpleNamespace(id="triage-id", name="triage"), SimpleNamespace(id="other", name="needs-triage")],
            False,
            "triage-id",
            id="decoy-label",
        ),
        pytest.param(
            {"triage-id": SimpleNamespace(id="triage-id", name="needs-triage")},
            [SimpleNamespace(id="triage-id", name="needs-triage")],
            True,
            "",
            id="seeded-label-renamed",
        ),
    ],
)
def test_f7_w4_follows_the_seeded_label_id_not_the_name(by_id, listed, want, expect):
    """A name scan would accept a different label renamed to the target."""
    ctx = {"workspace_slug": "ws", "project_id": "p1", "labels": {"triage": "triage-id"}}
    ok, note = asyncio.run(verify_w4(_W4Plane(by_id=by_id, listed=listed), ctx, _run()))
    assert ok is want, note
    if expect:
        assert expect in note, note


class _S3Plane:
    def __init__(self, *, props: list[Any], types: list[Any] | None = None, workspace_owns: bool = False):
        self._props = props
        self._types = types if types is not None else [SimpleNamespace(id="t-inc", name="Incident")]
        self._ws_owns = workspace_owns
        self.work_item_types = SimpleNamespace(list=lambda **kw: self._types)
        self.work_item_properties = SimpleNamespace(list=lambda **kw: self._props)
        self.workspaces = SimpleNamespace(
            get_features=lambda **kw: SimpleNamespace(model_dump=lambda: {"is_work_item_types_enabled": self._ws_owns})
        )
        self.workspace_work_item_types = SimpleNamespace(list=lambda **kw: [])


@pytest.mark.parametrize(
    ("display_name", "property_type", "want", "expect"),
    [
        pytest.param("Severity", "OPTION", False, "TEXT", id="required-option-fails"),
        pytest.param("Impact summary", "TEXT", True, "", id="required-text-passes"),
    ],
)
def test_f6_s3_behaviours(display_name, property_type, want, expect):
    plane = _S3Plane(
        props=[
            SimpleNamespace(
                id="p1",
                display_name=display_name,
                property_type=property_type,
                is_required=True,
            )
        ]
    )
    ok, note = asyncio.run(verify_s3(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run()))
    assert ok is want, note
    if expect:
        assert expect in note


def test_f9_s3_workspace_type_via_features_probe():
    async def _go():
        """Incident only on workspace types; empty project list; needs empty (no seed flag)."""
        plane = _S3Plane(props=[], types=[], workspace_owns=True)
        # Override workspace type list to include Incident
        plane.workspace_work_item_types = SimpleNamespace(
            list=lambda **kw: [SimpleNamespace(id="ws-inc", name="Incident")]
        )
        plane.work_item_properties = SimpleNamespace(
            list=lambda **kw: [
                SimpleNamespace(
                    id="p1",
                    display_name="Impact summary",
                    property_type="TEXT",
                    is_required=True,
                )
            ]
        )
        # No bug_type_workspace_level in ctx — old code would miss Incident.
        ok, note = await verify_s3(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
        assert ok is True, note

    return asyncio.run(_go())


@pytest.mark.parametrize(
    "weekday",
    range(7),
    ids=["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
)
def test_f8_r3_due_date_clamped_to_iso_week(weekday):
    monday = date(2026, 8, 10)
    today = monday + timedelta(days=weekday)
    days_to_week_end = 6 - today.weekday()
    due = min(today + timedelta(days=2), today + timedelta(days=days_to_week_end))
    week_end = today + timedelta(days=days_to_week_end)
    week_start = today - timedelta(days=today.weekday())
    assert week_start <= due <= week_end, f"weekday={weekday} due={due}"
    if weekday >= 5:
        assert due <= week_end
        assert due == week_end or due == today


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        pytest.param(date(2026, 8, 15), date(2026, 8, 16), id="saturday-clamps-to-sunday"),
        pytest.param(date(2026, 8, 16), date(2026, 8, 16), id="sunday-stays-sunday"),
    ],
)
def test_f8_seed_r3_due_date_function_matches(today, expected):
    days_to_week_end = 6 - today.weekday()
    due = min(today + timedelta(days=2), today + timedelta(days=days_to_week_end))
    assert due == expected


class _W8Plane:
    def __init__(self, durations: list[int]):
        self.work_items = SimpleNamespace(
            list=lambda **kw: _Page([_item("w8", W8_TITLE)]),
            work_logs=SimpleNamespace(list=lambda **kw: [SimpleNamespace(duration=d) for d in durations]),
        )


@pytest.mark.parametrize(
    ("duration", "want", "expect"),
    [
        pytest.param(480, False, "120", id="480-minutes-fails"),
        pytest.param(120, True, "", id="exactly-120-passes"),
    ],
)
def test_minor_w8_behaviours(duration, want, expect):
    ok, note = asyncio.run(
        verify_w8(
            _W8Plane([duration]),
            {"workspace_slug": "ws", "project_id": "p1"},
            _run(),
        )
    )
    assert ok is want, note
    if expect:
        assert expect in note


@pytest.mark.parametrize(
    ("run", "want", "expect"),
    [
        pytest.param(
            {"final_text": "There are 2 items due this week.", "calls": []},
            False,
            "item contract",
            id="count-without-titles-fails",
        ),
        pytest.param(
            {
                "final_text": "item: Onboarding email template stale\nitem: Webhook secret rotation docs missing",
                "calls": [
                    {
                        "tool": "plane_call",
                        "is_error": False,
                        "observed_sentinels": [TARGET_ENTITY_EVIDENCE],
                    }
                ],
                "call_source": "test",
                "evidence_trace_available": True,
            },
            True,
            "",
            id="exact-items-any-order",
        ),
    ],
)
def test_minor_r3_behaviours(run, want, expect):
    titles = ["Webhook secret rotation docs missing", "Onboarding email template stale"]
    ok, note = asyncio.run(verify_r3(object(), {"r3_due_titles": titles, "r3_due_count": 2}, run))
    assert ok is want, note
    if expect:
        assert expect in note.lower()


class _S5Plane:
    def __init__(
        self,
        *,
        cycle_view: bool,
        time_tracking: bool,
        customers: bool = True,
        features_cycles: bool | None = None,
    ):
        self.projects = SimpleNamespace(
            retrieve=lambda **kw: SimpleNamespace(
                id=kw["project_id"],
                cycle_view=cycle_view,
                is_time_tracking_enabled=time_tracking,
            ),
            get_features=lambda **kw: SimpleNamespace(
                model_dump=lambda: {
                    "cycles": features_cycles if features_cycles is not None else cycle_view,
                    "modules": False,
                }
            ),
        )
        self.workspaces = SimpleNamespace(
            get_features=lambda **kw: SimpleNamespace(model_dump=lambda: {"customers": customers})
        )


@pytest.mark.parametrize(
    ("cycle_view", "tracking", "customers", "features_cycles", "want", "expect"),
    [
        pytest.param(True, False, True, False, False, ("is_time_tracking_enabled",), id="worklogs-off"),
        pytest.param(False, True, True, False, False, ("cycle_view",), id="cycles-off"),
        pytest.param(False, False, True, False, False, ("cycle_view", "is_time_tracking_enabled"), id="customers-only"),
        pytest.param(True, True, False, True, False, ("customers",), id="customers-off"),
        pytest.param(True, True, True, True, True, (), id="all-three-enabled"),
    ],
)
def test_s5_requires_all_three_features_not_a_majority(cycle_view, tracking, customers, features_cycles, want, expect):
    plane = _S5Plane(
        cycle_view=cycle_view,
        time_tracking=tracking,
        customers=customers,
        features_cycles=features_cycles,
    )
    ok, note = asyncio.run(verify_s5(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run()))
    assert ok is want, note
    for text in expect:
        assert text in note, note
