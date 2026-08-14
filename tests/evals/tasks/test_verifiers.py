"""Offline eval tests for R, W, S, and C verifiers."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

from plane.errors.errors import HttpError

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
from evals.tasks.write import verify_w3, verify_w4, verify_w5, verify_w6, verify_w7, verify_w8


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


def test_f1_w7_behaviours():
    def test_f1_w7_blocked_by_only_does_not_pass():
        async def _go():
            """tgt id only in blocked_by (reverse) must FAIL — not pass via str(dump)."""
            plane = _W7Plane(
                {
                    "blocking": [],
                    "blocked_by": [{"id": "tgt-1"}],  # reverse direction only
                }
            )
            ctx = {"workspace_slug": "ws", "project_id": "p1"}
            ok, note = await verify_w7(plane, ctx, _run())
            assert ok is False, note
            assert "blocking" in note.lower() or "no blocking" in note.lower()
            assert "wrong direction" in note or "tgt-1" in note

        return asyncio.run(_go())

    def test_f1_w7_blocking_passes():
        async def _go():
            plane = _W7Plane({"blocking": [{"id": "tgt-1"}], "blocked_by": []})
            ctx = {"workspace_slug": "ws", "project_id": "p1"}
            ok, note = await verify_w7(plane, ctx, _run())
            assert ok is True, note

        # ---------------------------------------------------------------------------
        # F2 W6 — seeded past end_date alone must NOT pass as closed
        # ---------------------------------------------------------------------------

        return asyncio.run(_go())

    test_f1_w7_blocked_by_only_does_not_pass()
    test_f1_w7_blocking_passes()


class _W6Plane:
    def __init__(self, *, past_end: str, archived_at=None, snapshot=None, sprint13_names: list[str] | None = None):
        self._past_end = past_end
        self._archived_at = archived_at
        self._snapshot = snapshot
        self._s13 = sprint13_names or []
        self.cycles = SimpleNamespace(retrieve=self._retrieve, list_work_items=self._list_wi)

    def _retrieve(self, **kw):
        return SimpleNamespace(
            id="c12",
            end_date=self._past_end,
            archived_at=self._archived_at,
            progress_snapshot=self._snapshot,
        )

    def _list_wi(self, **kw):
        return _Page([_item(f"i{i}", n) for i, n in enumerate(self._s13)])


def test_f2_w6_closes_only_on_a_real_end_date_signal():
    """The API returns end_date as a timestamp, so whole-string comparison to today never
    matched — which silently left the transfer side effect as the only way to pass."""

    async def _go():
        today = date.today().isoformat()
        past = (date.today() - timedelta(days=14)).isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        titles = ["Inventory count goes negative under load"]
        two = [*titles, "Tooltip clipped inside modal dialog"]
        cases = [
            ("still at the seeded past end, never closed", past, past, titles, False, "not closed"),
            ("no-op agent: still ends tomorrow", f"{tomorrow}T00:00:00Z", tomorrow, titles, False, "not closed"),
            ("closed today, returned as a timestamp", f"{today}T00:00:00Z", tomorrow, titles, True, "end_date"),
            ("closed today, returned as a bare date", today, past, two, True, ""),
        ]
        for label, end_date, seed_end, names, want, expect in cases:
            plane = _W6Plane(past_end=end_date, sprint13_names=names)
            ctx = {
                "workspace_slug": "ws",
                "project_id": "p1",
                "cycle_past_id": "c12",
                "cycle_current_id": "c13",
                "cycle_past_seed_end_date": seed_end,
                "w6_unfinished_titles": names,
                "cycles": {CYCLE_PAST: "c12"},
            }
            ok, note = await verify_w6(plane, ctx, _run())
            assert ok is want, f"{label}: {note}"
            if expect:
                assert expect in note.lower() or expect in note, f"{label}: {note}"

    return asyncio.run(_go())


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


def test_f3_w5_accepts_archive_but_not_deletion():
    """A 404 alone is not evidence of archiving — deleting every item would also 404."""

    async def _go():
        ids = ["m1", "m2", "m3"]
        archived_at = SimpleNamespace(id="m1", archived_at="2026-01-01T00:00:00Z")
        cases = [
            ("all 404, nothing archived", _W5Plane(retrieve_map={}, archived_ids=[]), ids, False, "not archived"),
            ("404 but present in the archived list", _W5Plane(retrieve_map={}, archived_ids=ids), ids, True, ""),
            (
                "archived_at on retrieve",
                _W5Plane(retrieve_map={"m1": archived_at}, archived_ids=[]),
                ["m1"],
                True,
                "",
            ),
        ]
        for label, plane, module_ids, want, expect in cases:
            ctx = {"workspace_slug": "ws", "project_id": "p1", "module_completed_ids": module_ids}
            ok, note = await verify_w5(plane, ctx, _run())
            assert ok is want, f"{label}: {note}"
            if expect:
                assert expect in note, f"{label}: {note}"

    return asyncio.run(_go())


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


def test_f4_c1_requires_the_named_customer_linked_to_the_named_item():
    """Both ends are checked: a lookalike customer name and a link to any other item fail."""

    async def _go():
        request = SimpleNamespace(id="r1", name=CUSTOMER_REQUEST_NAME)
        cases = [
            (
                "customer name is a lookalike",
                [SimpleNamespace(id="c1", name="Acme Industries")],
                [SimpleNamespace(id="wi-r1")],
                [_item("wi-r1", R1_TITLE)],
                False,
                (CUSTOMER_NAME,),
            ),
            (
                "linked to a different item",
                [SimpleNamespace(id="c1", name=CUSTOMER_NAME)],
                [SimpleNamespace(id="wi-other")],
                [_item("wi-r1", R1_TITLE), _item("wi-other", "Other")],
                False,
                ("not linked", "wi-r1"),
            ),
            (
                "exact customer and item",
                [SimpleNamespace(id="c1", name=CUSTOMER_NAME)],
                [SimpleNamespace(id="wi-r1")],
                [_item("wi-r1", R1_TITLE)],
                True,
                (),
            ),
        ]
        for label, customers, linked, project_items, want, expect_any in cases:
            plane = _C1Plane(customers=customers, requests=[request], linked=linked, project_items=project_items)
            ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {R1_TITLE: "wi-r1"}}
            ok, note = await verify_c1(plane, ctx, _run())
            assert ok is want, f"{label}: {note}"
            if expect_any:
                assert any(s in note for s in expect_any), f"{label}: {note}"

    return asyncio.run(_go())


class _W3Plane:
    def __init__(self, comments: list[Any]):
        self.work_items = SimpleNamespace(
            list=lambda **kw: _Page([_item("w3", W3_TITLE)]),
            comments=SimpleNamespace(list=lambda **kw: _Page(comments)),
        )


def test_f5_w3_matches_the_phrase_in_either_comment_field():
    async def _go():
        html = "<p>Reviewed contrast tokens — needs design pass</p>"
        cases = [
            ("unrelated comment", "<p>lgtm</p>", "lgtm", False, "contrast tokens"),
            ("phrase present", html, "Reviewed contrast tokens — needs design pass", True, ""),
        ]
        for label, comment_html, stripped, want, expect in cases:
            plane = _W3Plane([SimpleNamespace(comment_html=comment_html, comment_stripped=stripped)])
            ok, note = await verify_w3(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
            assert ok is want, f"{label}: {note}"
            if expect:
                assert expect in note, f"{label}: {note}"

    return asyncio.run(_go())


class _W4Plane:
    def __init__(self, *, by_id: dict[str, Any], listed: list[Any]):
        self._by_id = by_id
        self.labels = SimpleNamespace(retrieve=self._retrieve, list=lambda **kw: _Page(listed))

    def _retrieve(self, **kw):
        lid = str(kw["label_id"])
        if lid not in self._by_id:
            raise _http404()
        return self._by_id[lid]


def test_f7_w4_follows_the_seeded_label_id_not_the_name():
    """A name scan would accept a different label renamed to the target."""

    async def _go():
        cases = [
            (
                "decoy label carries the new name",
                {"triage-id": SimpleNamespace(id="triage-id", name="triage")},
                [SimpleNamespace(id="triage-id", name="triage"), SimpleNamespace(id="other", name="needs-triage")],
                False,
                "triage-id",
            ),
            (
                "the seeded label itself was renamed",
                {"triage-id": SimpleNamespace(id="triage-id", name="needs-triage")},
                [SimpleNamespace(id="triage-id", name="needs-triage")],
                True,
                "",
            ),
        ]
        for label, by_id, listed, want, expect in cases:
            ctx = {"workspace_slug": "ws", "project_id": "p1", "labels": {"triage": "triage-id"}}
            ok, note = await verify_w4(_W4Plane(by_id=by_id, listed=listed), ctx, _run())
            assert ok is want, f"{label}: {note}"
            if expect:
                assert expect in note, f"{label}: {note}"

    return asyncio.run(_go())


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


def test_f6_s3_behaviours():
    def test_f6_s3_required_option_does_not_pass():
        async def _go():
            plane = _S3Plane(
                props=[
                    SimpleNamespace(
                        id="p1",
                        display_name="Severity",
                        property_type="OPTION",
                        is_required=True,
                    )
                ]
            )
            ok, note = await verify_s3(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
            assert ok is False, note
            assert "TEXT" in note

        return asyncio.run(_go())

    def test_f6_s3_required_text_passes():
        async def _go():
            plane = _S3Plane(
                props=[
                    SimpleNamespace(
                        id="p1",
                        display_name="Impact summary",
                        property_type="TEXT",
                        is_required=True,
                    )
                ]
            )
            ok, note = await verify_s3(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
            assert ok is True, note

        # ---------------------------------------------------------------------------
        # F9 S3 — workspace types found via get_features probe (not seed flag)
        # ---------------------------------------------------------------------------

        return asyncio.run(_go())

    test_f6_s3_required_option_does_not_pass()
    test_f6_s3_required_text_passes()


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

    # ---------------------------------------------------------------------------
    # F8 R3 due date stays in current week (seed helper)
    # ---------------------------------------------------------------------------

    return asyncio.run(_go())


def test_f8_behaviours():
    def test_f8_r3_due_date_clamped_to_iso_week():
        for weekday in range(7):  # 0=Mon … 6=Sun
            # Build a fixed "today" with that weekday relative to a known Monday.
            # 2026-08-10 is a Monday.
            monday = date(2026, 8, 10)
            today = monday + timedelta(days=weekday)
            days_to_week_end = 6 - today.weekday()
            due = min(today + timedelta(days=2), today + timedelta(days=days_to_week_end))
            # Sunday of that week
            week_end = today + timedelta(days=days_to_week_end)
            week_start = today - timedelta(days=today.weekday())
            assert week_start <= due <= week_end, f"weekday={weekday} due={due}"
            # Specifically: Sat/Sun must not go past Sunday
            if weekday >= 5:
                assert due <= week_end
                assert due == week_end or due == today  # Sun→today, Sat→Sun

    def test_f8_seed_r3_due_date_function_matches():
        sat = date(2026, 8, 15)  # known Saturday
        assert sat.weekday() == 5
        days_to_week_end = 6 - sat.weekday()
        due = min(sat + timedelta(days=2), sat + timedelta(days=days_to_week_end))
        assert due == date(2026, 8, 16)  # Sunday, not Monday 17
        # Sunday
        sun = date(2026, 8, 16)
        days_to_week_end = 6 - sun.weekday()
        due = min(sun + timedelta(days=2), sun + timedelta(days=days_to_week_end))
        assert due == sun

    test_f8_r3_due_date_clamped_to_iso_week()
    test_f8_seed_r3_due_date_function_matches()


class _W8Plane:
    def __init__(self, durations: list[int]):
        self.work_items = SimpleNamespace(
            list=lambda **kw: _Page([_item("w8", W8_TITLE)]),
            work_logs=SimpleNamespace(list=lambda **kw: [SimpleNamespace(duration=d) for d in durations]),
        )


def test_minor_behaviours():
    def test_minor_w8_behaviours():
        def test_minor_w8_480_minutes_fails():
            async def _go():
                plane = _W8Plane([480])
                ok, note = await verify_w8(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
                assert ok is False, note
                assert "120" in note

            return asyncio.run(_go())

        def test_minor_w8_exactly_120_passes():
            async def _go():
                plane = _W8Plane([120])
                ok, note = await verify_w8(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
                assert ok is True, note

            # ---------------------------------------------------------------------------
            # Minor R3 — all titles required; count alone insufficient
            # ---------------------------------------------------------------------------

            return asyncio.run(_go())

        test_minor_w8_480_minutes_fails()
        test_minor_w8_exactly_120_passes()

    def test_minor_r3_behaviours():
        def test_minor_r3_count_without_titles_fails():
            async def _go():
                titles = ["Webhook secret rotation docs missing", "Onboarding email template stale"]
                run = {"final_text": "There are 2 items due this week.", "calls": []}
                ok, note = await verify_r3(
                    object(),
                    {"r3_due_titles": titles, "r3_due_count": 2},
                    run,
                )
                assert ok is False, note
                assert "item contract" in note.lower()

            return asyncio.run(_go())

        def test_minor_r3_exact_item_contract_passes_in_any_order():
            async def _go():
                titles = ["Webhook secret rotation docs missing", "Onboarding email template stale"]
                run = {
                    "final_text": ("item: Onboarding email template stale\nitem: Webhook secret rotation docs missing"),
                    "calls": [],
                }
                ok, note = await verify_r3(
                    object(),
                    {"r3_due_titles": titles, "r3_due_count": 2},
                    run,
                )
                assert ok is True, note

            return asyncio.run(_go())

        test_minor_r3_count_without_titles_fails()
        test_minor_r3_exact_item_contract_passes_in_any_order()

    test_minor_w8_behaviours()
    test_minor_r3_behaviours()


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


def test_s5_requires_all_three_features_not_a_majority():
    """Every two-of-three combination must fail; the task asks for all three."""

    async def _go():
        cases = [
            ("cycles+customers, worklogs off", True, False, True, False, ("is_time_tracking_enabled",)),
            ("worklogs+customers, cycles off", False, True, True, False, ("cycle_view",)),
            ("customers only", False, False, True, False, ("cycle_view", "is_time_tracking_enabled")),
            ("project flags only, customers off", True, True, False, True, ("customers",)),
        ]
        for label, cycle_view, tracking, customers, features_cycles, expect in cases:
            plane = _S5Plane(
                cycle_view=cycle_view,
                time_tracking=tracking,
                customers=customers,
                features_cycles=features_cycles,
            )
            ok, note = await verify_s5(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
            assert ok is False, f"{label}: {note}"
            for s in expect:
                assert s in note, f"{label}: {note}"

        plane = _S5Plane(cycle_view=True, time_tracking=True, customers=True, features_cycles=True)
        ok, note = await verify_s5(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
        assert ok is True, note

    return asyncio.run(_go())
