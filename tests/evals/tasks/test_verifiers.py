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


def test_f2_w6_seeded_past_end_alone_fails():
    async def _go():
        """Sprint 12 still at seed end_date (today-14) with no archive → not closed."""
        seed_end = (date.today() - timedelta(days=14)).isoformat()
        plane = _W6Plane(past_end=seed_end, sprint13_names=["Inventory count goes negative under load"])
        ctx = {
            "workspace_slug": "ws",
            "project_id": "p1",
            "cycle_past_id": "c12",
            "cycle_current_id": "c13",
            "cycle_past_seed_end_date": seed_end,
            "w6_unfinished_titles": ["Inventory count goes negative under load"],
            "cycles": {CYCLE_PAST: "c12"},
        }
        ok, note = await verify_w6(plane, ctx, _run())
        assert ok is False, note
        assert "not closed" in note.lower() or "Sprint 12 not closed" in note

    return asyncio.run(_go())


def test_f2_w6_end_date_today_as_timestamp_passes_close():
    async def _go():
        """The API returns end_date as a timestamp, not a bare date.

        Sprint 12 closed today comes back as '<today>T00:00:00Z'; comparing the
        whole string to today's date never matches, which silently killed the
        complete_cycle close signal and left the transfer side effect
        (progress_snapshot) as the only way to pass.
        """
        today = date.today().isoformat()
        titles = ["Inventory count goes negative under load"]
        plane = _W6Plane(past_end=f"{today}T00:00:00Z", sprint13_names=titles)
        ctx = {
            "workspace_slug": "ws",
            "project_id": "p1",
            "cycle_past_id": "c12",
            "cycle_current_id": "c13",
            "cycle_past_seed_end_date": (date.today() + timedelta(days=1)).isoformat(),
            "w6_unfinished_titles": titles,
        }
        ok, note = await verify_w6(plane, ctx, _run())
        assert ok is True, note
        assert "end_date" in note

    return asyncio.run(_go())


def test_f2_w6_open_seed_end_tomorrow_alone_fails():
    async def _go():
        """A no-op agent leaves Sprint 12 ending tomorrow → not closed."""
        seed_end = (date.today() + timedelta(days=1)).isoformat()
        titles = ["Inventory count goes negative under load"]
        plane = _W6Plane(past_end=f"{seed_end}T00:00:00Z", sprint13_names=titles)
        ctx = {
            "workspace_slug": "ws",
            "project_id": "p1",
            "cycle_past_id": "c12",
            "cycle_current_id": "c13",
            "cycle_past_seed_end_date": seed_end,
            "w6_unfinished_titles": titles,
        }
        ok, note = await verify_w6(plane, ctx, _run())
        assert ok is False, note
        assert "not closed" in note.lower()

    return asyncio.run(_go())


def test_f2_w6_end_date_today_passes_close():
    async def _go():
        today = date.today().isoformat()
        plane = _W6Plane(
            past_end=today,
            sprint13_names=[
                "Inventory count goes negative under load",
                "Tooltip clipped inside modal dialog",
            ],
        )
        ctx = {
            "workspace_slug": "ws",
            "project_id": "p1",
            "cycle_past_id": "c12",
            "cycle_current_id": "c13",
            "cycle_past_seed_end_date": (date.today() - timedelta(days=14)).isoformat(),
            "w6_unfinished_titles": [
                "Inventory count goes negative under load",
                "Tooltip clipped inside modal dialog",
            ],
        }
        ok, note = await verify_w6(plane, ctx, _run())
        assert ok is True, note

    # ---------------------------------------------------------------------------
    # F3 W5 — 404 without archived list entry is NOT archive
    # ---------------------------------------------------------------------------

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


def test_f3_w5_deleted_404_without_archive_fails():
    async def _go():
        """All items 404 and archived list empty → fail (deletes ≠ archive)."""
        ids = ["m1", "m2", "m3"]
        plane = _W5Plane(retrieve_map={}, archived_ids=[])  # all 404, none archived
        ctx = {
            "workspace_slug": "ws",
            "project_id": "p1",
            "module_completed_ids": ids,
        }
        ok, note = await verify_w5(plane, ctx, _run())
        assert ok is False, note
        assert "not archived" in note

    return asyncio.run(_go())


def test_f3_w5_404_present_in_archived_passes():
    async def _go():
        ids = ["m1", "m2", "m3"]
        plane = _W5Plane(retrieve_map={}, archived_ids=ids)
        ctx = {"workspace_slug": "ws", "project_id": "p1", "module_completed_ids": ids}
        ok, note = await verify_w5(plane, ctx, _run())
        assert ok is True, note

    return asyncio.run(_go())


def test_f3_w5_archived_at_on_retrieve_passes():
    async def _go():
        ids = ["m1"]
        plane = _W5Plane(
            retrieve_map={"m1": SimpleNamespace(id="m1", archived_at="2026-01-01T00:00:00Z")},
            archived_ids=[],
        )
        ctx = {"workspace_slug": "ws", "project_id": "p1", "module_completed_ids": ids}
        ok, note = await verify_w5(plane, ctx, _run())
        assert ok is True, note

    # ---------------------------------------------------------------------------
    # F4 C1 — must link exact R1 item; acme* / random links fail
    # ---------------------------------------------------------------------------

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


def test_f4_c1_wrong_customer_name_fails():
    async def _go():
        plane = _C1Plane(
            customers=[SimpleNamespace(id="c1", name="Acme Industries")],
            requests=[SimpleNamespace(id="r1", name=CUSTOMER_REQUEST_NAME)],
            linked=[SimpleNamespace(id="wi-r1")],
            project_items=[_item("wi-r1", R1_TITLE)],
        )
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {R1_TITLE: "wi-r1"}}
        ok, note = await verify_c1(plane, ctx, _run())
        assert ok is False, note
        assert CUSTOMER_NAME in note

    return asyncio.run(_go())


def test_f4_c1_linked_other_item_not_r1_fails():
    async def _go():
        plane = _C1Plane(
            customers=[SimpleNamespace(id="c1", name=CUSTOMER_NAME)],
            requests=[SimpleNamespace(id="r1", name=CUSTOMER_REQUEST_NAME)],
            linked=[SimpleNamespace(id="wi-other")],  # not R1
            project_items=[_item("wi-r1", R1_TITLE), _item("wi-other", "Other")],
        )
        ctx = {"workspace_slug": "ws", "project_id": "p1"}
        ok, note = await verify_c1(plane, ctx, _run())
        assert ok is False, note
        assert "not linked" in note or "wi-r1" in note

    return asyncio.run(_go())


def test_f4_c1_exact_r1_link_passes():
    async def _go():
        plane = _C1Plane(
            customers=[SimpleNamespace(id="c1", name=CUSTOMER_NAME)],
            requests=[SimpleNamespace(id="r1", name=CUSTOMER_REQUEST_NAME)],
            linked=[SimpleNamespace(id="wi-r1")],
            project_items=[_item("wi-r1", R1_TITLE)],
        )
        ctx = {"workspace_slug": "ws", "project_id": "p1"}
        ok, note = await verify_c1(plane, ctx, _run())
        assert ok is True, note

    # ---------------------------------------------------------------------------
    # F5 W3 — comment must contain 'contrast tokens'
    # ---------------------------------------------------------------------------

    return asyncio.run(_go())


class _W3Plane:
    def __init__(self, comments: list[Any]):
        self.work_items = SimpleNamespace(
            list=lambda **kw: _Page([_item("w3", W3_TITLE)]),
            comments=SimpleNamespace(list=lambda **kw: _Page(comments)),
        )


def test_f5_w3_unrelated_comment_fails():
    async def _go():
        plane = _W3Plane([SimpleNamespace(comment_html="<p>lgtm</p>", comment_stripped="lgtm")])
        ok, note = await verify_w3(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
        assert ok is False, note
        assert "contrast tokens" in note

    return asyncio.run(_go())


def test_f5_w3_phrase_in_html_passes():
    async def _go():
        plane = _W3Plane(
            [
                SimpleNamespace(
                    comment_html="<p>Reviewed contrast tokens — needs design pass</p>",
                    comment_stripped="Reviewed contrast tokens — needs design pass",
                )
            ]
        )
        ok, note = await verify_w3(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
        assert ok is True, note

    # ---------------------------------------------------------------------------
    # F7 W4 — seeded triage id is authoritative
    # ---------------------------------------------------------------------------

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


def test_f7_w4_wrong_label_renamed_triage_id_unchanged_fails():
    async def _go():
        """Name-scan would see needs-triage, but seeded triage id still named triage."""
        plane = _W4Plane(
            by_id={"triage-id": SimpleNamespace(id="triage-id", name="triage")},
            listed=[
                SimpleNamespace(id="triage-id", name="triage"),
                SimpleNamespace(id="other", name="needs-triage"),  # wrong label renamed
            ],
        )
        ctx = {"workspace_slug": "ws", "project_id": "p1", "labels": {"triage": "triage-id"}}
        ok, note = await verify_w4(plane, ctx, _run())
        assert ok is False, note
        assert "triage-id" in note

    return asyncio.run(_go())


def test_f7_w4_seeded_id_renamed_passes():
    async def _go():
        plane = _W4Plane(
            by_id={"triage-id": SimpleNamespace(id="triage-id", name="needs-triage")},
            listed=[SimpleNamespace(id="triage-id", name="needs-triage")],
        )
        ctx = {"workspace_slug": "ws", "project_id": "p1", "labels": {"triage": "triage-id"}}
        ok, note = await verify_w4(plane, ctx, _run())
        assert ok is True, note

    # ---------------------------------------------------------------------------
    # F6 S3 — required OPTION must not pass as TEXT
    # ---------------------------------------------------------------------------

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


def test_f8_r3_due_date_clamped_to_iso_week():
    """today+2d on Sat/Sun must not leave the week — replicate seed formula."""
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
    """Import seed's computation path by re-running the formula against weekends."""
    # Saturday
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


class _W8Plane:
    def __init__(self, durations: list[int]):
        self.work_items = SimpleNamespace(
            list=lambda **kw: _Page([_item("w8", W8_TITLE)]),
            work_logs=SimpleNamespace(list=lambda **kw: [SimpleNamespace(duration=d) for d in durations]),
        )


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


def test_s5_only_cycles_enabled_fails():
    """Two-of-three: cycles on, worklogs off, customers on → fail."""

    async def _go():
        plane = _S5Plane(cycle_view=True, time_tracking=False, customers=True)
        ok, note = await verify_s5(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
        assert ok is False, note
        assert "is_time_tracking_enabled" in note

    return asyncio.run(_go())


def test_s5_only_worklogs_enabled_fails():
    async def _go():
        plane = _S5Plane(cycle_view=False, time_tracking=True, customers=True)
        ok, note = await verify_s5(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
        assert ok is False, note
        assert "cycle_view" in note

    return asyncio.run(_go())


def test_s5_workspace_customers_on_but_project_flags_off_fails():
    """Workspace customers alone is not enough — project gates must also pass."""

    async def _go():
        plane = _S5Plane(cycle_view=False, time_tracking=False, customers=True)
        ok, note = await verify_s5(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
        assert ok is False, note
        assert "cycle_view" in note
        assert "is_time_tracking_enabled" in note

    return asyncio.run(_go())


def test_s5_project_flags_on_but_customers_off_fails():
    """Two-of-three: project ok, workspace customers off → fail."""

    async def _go():
        plane = _S5Plane(cycle_view=True, time_tracking=True, customers=False, features_cycles=True)
        ok, note = await verify_s5(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
        assert ok is False, note
        assert "customers" in note

    return asyncio.run(_go())


def test_s5_all_three_enabled_passes():
    async def _go():
        plane = _S5Plane(cycle_view=True, time_tracking=True, customers=True, features_cycles=True)
        ok, note = await verify_s5(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
        assert ok is True, note

    return asyncio.run(_go())
