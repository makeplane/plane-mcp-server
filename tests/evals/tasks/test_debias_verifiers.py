"""Offline eval tests for debias verifiers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from evals.seed import W2_TITLE
from evals.tasks.debias import (
    I1_TITLE,
    I3_TITLE,
    I4_TITLE,
    L1_TITLE,
    L2_TITLE,
    L3_TAG_VERSION,
    L4_PROP_DISPLAY,
    L4_PROP_VALUE,
    L5_TITLE,
    verify_i1,
    verify_i2,
    verify_i3,
    verify_i4,
    verify_i5,
    verify_l1,
    verify_l2,
    verify_l3,
    verify_l4,
    verify_l5,
)


class _Page:
    def __init__(self, results: list[Any] | None = None):
        self.results = results or []
        self.next_page_results = False
        self.next_cursor = None


def _run(text: str = "") -> dict[str, Any]:
    return {"final_text": text, "calls": []}


def _item(id: str, name: str, **kw: Any) -> SimpleNamespace:
    return SimpleNamespace(id=id, name=name, **kw)


class _WIRetrievePlane:
    """work_items.retrieve + list by name; optional labels expand."""

    def __init__(
        self,
        *,
        by_id: dict[str, Any],
        by_name: dict[str, str] | None = None,
        states: list[Any] | None = None,
    ):
        self._by_id = by_id
        self._by_name = by_name or {}
        self._states = states or []
        self.work_items = SimpleNamespace(
            list=self._list,
            retrieve=self._retrieve,
        )
        self.states = SimpleNamespace(list=lambda **kw: _Page(self._states))

    def _list(self, **kw):
        # Minimal name filter support used by _find_item_by_name.
        params = kw.get("params")
        name = None
        if params is not None:
            name = getattr(params, "name", None) or (params.get("name") if isinstance(params, dict) else None)
        if name and name in self._by_name:
            wid = self._by_name[name]
            row = self._by_id.get(wid) or _item(wid, name)
            return _Page([row])
        return _Page([])

    def _retrieve(self, **kw):
        wid = str(kw["work_item_id"])
        if wid not in self._by_id:
            raise LookupError(wid)
        return self._by_id[wid]


class _I3Plane:
    def __init__(self, cycle_item_ids: list[str]):
        self.cycles = SimpleNamespace(list_work_items=lambda **kw: _Page([_item(i, f"n-{i}") for i in cycle_item_ids]))


class _L1Plane:
    def __init__(self, durations: list[int], summary_ids: list[str] | None = None):
        self.work_items = SimpleNamespace(
            work_logs=SimpleNamespace(list=lambda **kw: [SimpleNamespace(duration=d) for d in durations]),
        )
        rows = [SimpleNamespace(work_item_id=i, duration=90) for i in (summary_ids or [])]
        self.projects = SimpleNamespace(get_worklog_summary=lambda **kw: rows)


class _L2Plane:
    def __init__(self, n_activities: int):
        acts = [SimpleNamespace(id=f"a{i}", verb="updated") for i in range(n_activities)]
        self.work_items = SimpleNamespace(activities=SimpleNamespace(list=lambda **kw: _Page(acts)))


class _L3Plane:
    def __init__(self, versions: list[str]):
        tags = [SimpleNamespace(id=f"t-{v}", version=v) for v in versions]
        self.releases = SimpleNamespace(tags=SimpleNamespace(list=lambda **kw: _Page(tags)))


class _L4Plane:
    def __init__(self, *, props: list[Any], values: dict[str, list[str]]):
        self.customers = SimpleNamespace(
            properties=SimpleNamespace(list=lambda **kw: _Page(props)),
            property_values=SimpleNamespace(list=lambda **kw: values),
        )


class _L5Plane:
    def __init__(self, n: int):
        rows = [SimpleNamespace(id=f"att-{i}") for i in range(n)]
        self.work_items = SimpleNamespace(attachments=SimpleNamespace(list=lambda **kw: _Page(rows)))


def test_i1_untouched_urgent_fails():
    async def _go():
        plane = _WIRetrievePlane(by_id={"wi-1": SimpleNamespace(id="wi-1", priority="urgent")})
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {I1_TITLE: "wi-1"}}
        ok, note = await verify_i1(plane, ctx, _run())
        assert ok is False, note

    return asyncio.run(_go())


def test_i1_wrong_item_high_target_still_urgent_fails():
    async def _go():
        # Right value on the wrong item; target remains urgent.
        plane = _WIRetrievePlane(
            by_id={
                "wi-1": SimpleNamespace(id="wi-1", priority="urgent"),
                "wi-other": SimpleNamespace(id="wi-other", priority="high"),
            }
        )
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {I1_TITLE: "wi-1"}}
        ok, note = await verify_i1(plane, ctx, _run())
        assert ok is False, note
        assert "urgent" in note or "high" in note

    return asyncio.run(_go())


def test_i2_untouched_empty_final_text_fails():
    async def _go():
        st = SimpleNamespace(id="st-backlog", name="Backlog", group="unstarted")
        plane = _WIRetrievePlane(
            by_id={"wi-2": SimpleNamespace(id="wi-2", state=st)},
            states=[st],
        )
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {W2_TITLE: "wi-2"}}
        ok, note = await verify_i2(plane, ctx, _run(""))
        assert ok is False, note

    return asyncio.run(_go())


def test_i2_wrong_state_name_in_text_fails():
    async def _go():
        st = SimpleNamespace(id="st-backlog", name="Backlog", group="unstarted")
        plane = _WIRetrievePlane(
            by_id={"wi-2": SimpleNamespace(id="wi-2", state=st)},
            states=[
                st,
                SimpleNamespace(id="st-done", name="Done", group="completed"),
            ],
        )
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {W2_TITLE: "wi-2"}}
        ok, note = await verify_i2(plane, ctx, _run("Done"))
        assert ok is False, note

    return asyncio.run(_go())


def test_i2_exact_state_contract_passes():
    async def _go():
        st = SimpleNamespace(id="st-backlog", name="Backlog", group="unstarted")
        plane = _WIRetrievePlane(
            by_id={"wi-2": SimpleNamespace(id="wi-2", state=st)},
            states=[st],
        )
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {W2_TITLE: "wi-2"}}
        ok, note = await verify_i2(plane, ctx, _run("state: Backlog"))
        assert ok is True, note

    return asyncio.run(_go())


def test_i3_untouched_not_on_cycle_fails():
    async def _go():
        plane = _I3Plane(["other-1", "other-2"])
        ctx = {
            "workspace_slug": "ws",
            "project_id": "p1",
            "items": {I3_TITLE: "footer-1"},
            "cycle_current_id": "cyc-1",
        }
        ok, note = await verify_i3(plane, ctx, _run())
        assert ok is False, note

    return asyncio.run(_go())


def test_i3_wrong_item_on_cycle_target_missing_fails():
    async def _go():
        plane = _I3Plane(["wrong-item"])
        ctx = {
            "workspace_slug": "ws",
            "project_id": "p1",
            "items": {I3_TITLE: "footer-1"},
            "cycle_current_id": "cyc-1",
        }
        ok, note = await verify_i3(plane, ctx, _run())
        assert ok is False, note

    return asyncio.run(_go())


def test_i4_untouched_no_label_fails():
    async def _go():
        plane = _WIRetrievePlane(by_id={"wi-4": SimpleNamespace(id="wi-4", labels=[])})
        ctx = {
            "workspace_slug": "ws",
            "project_id": "p1",
            "items": {I4_TITLE: "wi-4"},
            "labels": {"perf": "lab-perf"},
        }
        ok, note = await verify_i4(plane, ctx, _run())
        assert ok is False, note

    return asyncio.run(_go())


def test_i4_wrong_label_attached_fails():
    async def _go():
        plane = _WIRetrievePlane(by_id={"wi-4": SimpleNamespace(id="wi-4", labels=[SimpleNamespace(id="lab-auth")])})
        ctx = {
            "workspace_slug": "ws",
            "project_id": "p1",
            "items": {I4_TITLE: "wi-4"},
            "labels": {"perf": "lab-perf"},
        }
        ok, note = await verify_i4(plane, ctx, _run())
        assert ok is False, note

    return asyncio.run(_go())


def test_i5_untouched_none_priority_fails():
    async def _go():
        plane = _WIRetrievePlane(by_id={"wi-5": SimpleNamespace(id="wi-5", priority="none")})
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {I3_TITLE: "wi-5"}}
        ok, note = await verify_i5(plane, ctx, _run())
        assert ok is False, note

    return asyncio.run(_go())


def test_i5_wrong_value_high_fails():
    async def _go():
        plane = _WIRetrievePlane(by_id={"wi-5": SimpleNamespace(id="wi-5", priority="high")})
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {I3_TITLE: "wi-5"}}
        ok, note = await verify_i5(plane, ctx, _run())
        assert ok is False, note
        assert "high" in note

    return asyncio.run(_go())


def test_l1_untouched_no_worklog_fails():
    async def _go():
        plane = _L1Plane([])
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {L1_TITLE: "wi-l1"}}
        ok, note = await verify_l1(plane, ctx, _run())
        assert ok is False, note

    return asyncio.run(_go())


def test_l1_wrong_duration_120_fails():
    async def _go():
        plane = _L1Plane([120], summary_ids=["wi-l1"])
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {L1_TITLE: "wi-l1"}}
        ok, note = await verify_l1(plane, ctx, _run("Logged 120 minutes; summary ok."))
        assert ok is False, note
        assert "90" in note

    return asyncio.run(_go())


def test_l1_empty_summary_with_90m_log_fails():
    """Reviewer counterexample: 90m log present but final text empty → fail."""

    async def _go():
        plane = _L1Plane([90], summary_ids=["wi-l1"])
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {L1_TITLE: "wi-l1"}}
        ok, note = await verify_l1(plane, ctx, _run(""))
        assert ok is False, note
        assert "logged-minutes" in note.lower()

    return asyncio.run(_go())


def test_l1_one_hundred_ninety_minutes_fails():
    """Reviewer counterexample: English 'ninety' must not satisfy numeric duration."""

    async def _go():
        plane = _L1Plane([90], summary_ids=["wi-l1"])
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {L1_TITLE: "wi-l1"}}
        ok, note = await verify_l1(
            plane,
            ctx,
            _run("Logged one hundred ninety minutes. Project summary looks fine."),
        )
        assert ok is False, note
        assert "duration" in note.lower() or "90" in note or "1.5" in note

    return asyncio.run(_go())


def test_l1_prose_with_correct_facts_but_without_contract_fails():
    """Correct facts in prose do not satisfy the explicit output contract."""

    async def _go():
        plane = _L1Plane([90], summary_ids=["wi-l1"])
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {L1_TITLE: "wi-l1"}}
        ok, note = await verify_l1(plane, ctx, _run("Logged 1.5 hours total."))
        assert ok is False, note

    return asyncio.run(_go())


def test_l1_ninety_minutes_of_work_fails_by_design():
    """Calibration: prose without contract lines fails by design."""

    async def _go():
        plane = _L1Plane([90], summary_ids=["wi-l1"])
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {L1_TITLE: "wi-l1"}}
        ok, note = await verify_l1(plane, ctx, _run("90 minutes of work"))
        assert ok is False, note
        assert "logged-minutes" in note.lower()

    return asyncio.run(_go())


def test_l1_exact_duration_and_summary_contract_passes():
    async def _go():
        plane = _L1Plane([90], summary_ids=["wi-l1"])
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {L1_TITLE: "wi-l1"}}
        ok, note = await verify_l1(
            plane,
            ctx,
            _run("logged-minutes: 90\nsummary-work-item-id: wi-l1"),
        )
        assert ok is True, note

    return asyncio.run(_go())


def test_l2_untouched_empty_final_text_fails():
    async def _go():
        plane = _L2Plane(3)
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {L2_TITLE: "wi-l2"}}
        ok, note = await verify_l2(plane, ctx, _run(""))
        assert ok is False, note

    return asyncio.run(_go())


def test_l2_contract_count_three_passes():
    """Contract line 'count: 3' truth=3 passes."""

    async def _go():
        plane = _L2Plane(3)
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {L2_TITLE: "wi-l2"}}
        ok, note = await verify_l2(plane, ctx, _run("Saw some history.\ncount: 3"))
        assert ok is True, note

    return asyncio.run(_go())


def test_l2_contract_count_two_fails_truth_three():
    """Contract 'count: 2' truth=3 fails."""

    async def _go():
        plane = _L2Plane(3)
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {L2_TITLE: "wi-l2"}}
        ok, note = await verify_l2(plane, ctx, _run("count: 2"))
        assert ok is False, note

    return asyncio.run(_go())


def test_l2_negative_contract_and_bare_fail_truth_three():
    """'-3' and 'count: -3' fail truth=3 (signed equality)."""

    async def _go():
        plane = _L2Plane(3)
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {L2_TITLE: "wi-l2"}}
        ok1, _ = await verify_l2(plane, ctx, _run("-3"))
        ok2, _ = await verify_l2(plane, ctx, _run("count: -3"))
        assert ok1 is False
        assert ok2 is False

    return asyncio.run(_go())


def test_l2_prose_only_without_contract_fails_by_design():
    """By design: prose without 'count: N' (or bare int) fails — format is part of the task."""

    async def _go():
        plane = _L2Plane(3)
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {L2_TITLE: "wi-l2"}}
        ok, note = await verify_l2(plane, ctx, _run("There are 3 activities and some comment phrases."))
        assert ok is False, note

    return asyncio.run(_go())


def test_l3_untouched_no_tag_fails():
    async def _go():
        plane = _L3Plane([])
        ok, note = await verify_l3(plane, {"workspace_slug": "ws"}, _run())
        assert ok is False, note

    return asyncio.run(_go())


def test_l3_wrong_version_tag_fails():
    async def _go():
        plane = _L3Plane(["v0.0.1", "other-rc"])
        ok, note = await verify_l3(plane, {"workspace_slug": "ws"}, _run())
        assert ok is False, note
        assert L3_TAG_VERSION in note

    return asyncio.run(_go())


def test_l4_untouched_no_property_fails():
    async def _go():
        plane = _L4Plane(props=[], values={})
        ctx = {"workspace_slug": "ws", "customer": {"id": "cust-1", "name": "Acme Corp"}}
        ok, note = await verify_l4(plane, ctx, _run())
        assert ok is False, note

    return asyncio.run(_go())


def test_l4_right_property_wrong_value_fails():
    async def _go():
        prop = SimpleNamespace(id="prop-1", display_name=L4_PROP_DISPLAY, name="eval-industry", property_type="TEXT")
        plane = _L4Plane(props=[prop], values={"prop-1": ["Startup"]})
        ctx = {"workspace_slug": "ws", "customer": {"id": "cust-1"}}
        ok, note = await verify_l4(plane, ctx, _run())
        assert ok is False, note
        assert L4_PROP_VALUE in note or "Startup" in note or "lack" in note

    return asyncio.run(_go())


def test_l4_industry_url_type_with_enterprise_fails():
    """Reviewer counterexample: name contains Industry, URL type, value Enterprise → fail."""

    async def _go():
        prop = SimpleNamespace(
            id="prop-url",
            display_name="Industry",  # substring / wrong exact name
            name="industry",
            property_type="URL",
        )
        plane = _L4Plane(props=[prop], values={"prop-url": [L4_PROP_VALUE]})
        ctx = {"workspace_slug": "ws", "customer": {"id": "cust-1"}}
        ok, note = await verify_l4(plane, ctx, _run())
        assert ok is False, note

    return asyncio.run(_go())


def test_l4_exact_text_enterprise_passes():
    async def _go():
        prop = SimpleNamespace(id="prop-1", display_name=L4_PROP_DISPLAY, name="eval-industry", property_type="TEXT")
        plane = _L4Plane(props=[prop], values={"prop-1": [L4_PROP_VALUE]})
        ctx = {"workspace_slug": "ws", "customer": {"id": "cust-1"}}
        ok, note = await verify_l4(plane, ctx, _run())
        assert ok is True, note
        assert any(o.get("kind") == "customer_property" for o in ctx.get("workspace_objects") or [])

    return asyncio.run(_go())


def test_l5_untouched_empty_final_text_fails():
    async def _go():
        plane = _L5Plane(0)
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {L5_TITLE: "wi-l5"}}
        ok, note = await verify_l5(plane, ctx, _run(""))
        assert ok is False, note

    return asyncio.run(_go())


def test_l5_bare_zero_passes():
    """Fallback: whole-answer bare '0' still passes truth=0."""

    async def _go():
        plane = _L5Plane(0)
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {L5_TITLE: "wi-l5"}}
        ok, note = await verify_l5(plane, ctx, _run("0"))
        assert ok is True, note

    return asyncio.run(_go())


def test_l5_multiline_ending_count_zero_passes():
    """Multi-line answer ending with 'count: 0' passes truth=0."""

    async def _go():
        plane = _L5Plane(0)
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {L5_TITLE: "wi-l5"}}
        ok, note = await verify_l5(
            plane,
            ctx,
            _run("No files on this work item.\ncount: 0"),
        )
        assert ok is True, note

    return asyncio.run(_go())


def test_l5_prose_only_without_contract_fails_by_design():
    """By design: prose without contract line fails (format instruction is part of the task)."""

    async def _go():
        plane = _L5Plane(0)
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {L5_TITLE: "wi-l5"}}
        ok, note = await verify_l5(plane, ctx, _run("There are 0 attachments."))
        assert ok is False, note

    return asyncio.run(_go())


def test_l5_wrong_contract_count_fails():
    async def _go():
        plane = _L5Plane(0)
        ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {L5_TITLE: "wi-l5"}}
        ok, note = await verify_l5(plane, ctx, _run("count: 10"))
        assert ok is False, note

    return asyncio.run(_go())
