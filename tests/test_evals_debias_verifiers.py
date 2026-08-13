"""Adversarial offline verifier tests for WS3 de-bias tasks + sample existing.

For each covered verifier: (a) untouched seed end-state must FAIL, and
(b) a plausibly-wrong end state (right field wrong value / right value wrong
item) must FAIL. Fake plane clients only — no network.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from evals.seed import R1_TITLE, W2_TITLE, W8_TITLE
from evals.tasks import (
    I1_TITLE,
    I3_TITLE,
    I4_TITLE,
    L1_TITLE,
    L2_TITLE,
    L3_TAG_VERSION,
    L4_PROP_DISPLAY,
    L4_PROP_VALUE,
    L5_TITLE,
    verify_c2,
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
    verify_r1,
    verify_w2,
    verify_w4,
    verify_w8,
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


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.delenv("REDIS_PORT", raising=False)


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# I1 — priority high by UUID
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# I2 — state by identifier in final text
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# I3 — cycle membership by UUIDs
# ---------------------------------------------------------------------------


class _I3Plane:
    def __init__(self, cycle_item_ids: list[str]):
        self.cycles = SimpleNamespace(list_work_items=lambda **kw: _Page([_item(i, f"n-{i}") for i in cycle_item_ids]))


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


# ---------------------------------------------------------------------------
# I4 — label attach by UUIDs
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# I5 — priority low by UUID
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# L1 — 90m worklog + summary
# ---------------------------------------------------------------------------


class _L1Plane:
    def __init__(self, durations: list[int], summary_ids: list[str] | None = None):
        self.work_items = SimpleNamespace(
            work_logs=SimpleNamespace(list=lambda **kw: [SimpleNamespace(duration=d) for d in durations]),
        )
        rows = [SimpleNamespace(work_item_id=i, duration=90) for i in (summary_ids or [])]
        self.projects = SimpleNamespace(get_worklog_summary=lambda **kw: rows)


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


# ---------------------------------------------------------------------------
# L2 — activities
# ---------------------------------------------------------------------------


class _L2Plane:
    def __init__(self, n_activities: int):
        acts = [SimpleNamespace(id=f"a{i}", verb="updated") for i in range(n_activities)]
        self.work_items = SimpleNamespace(activities=SimpleNamespace(list=lambda **kw: _Page(acts)))


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


# ---------------------------------------------------------------------------
# L3 — release tag
# ---------------------------------------------------------------------------


class _L3Plane:
    def __init__(self, versions: list[str]):
        tags = [SimpleNamespace(id=f"t-{v}", version=v) for v in versions]
        self.releases = SimpleNamespace(tags=SimpleNamespace(list=lambda **kw: _Page(tags)))


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


# ---------------------------------------------------------------------------
# L4 — customer property values
# ---------------------------------------------------------------------------


class _L4Plane:
    def __init__(self, *, props: list[Any], values: dict[str, list[str]]):
        self.customers = SimpleNamespace(
            properties=SimpleNamespace(list=lambda **kw: _Page(props)),
            property_values=SimpleNamespace(list=lambda **kw: values),
        )


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


# ---------------------------------------------------------------------------
# L5 — attachment count
# ---------------------------------------------------------------------------


class _L5Plane:
    def __init__(self, n: int):
        rows = [SimpleNamespace(id=f"att-{i}") for i in range(n)]
        self.work_items = SimpleNamespace(attachments=SimpleNamespace(list=lambda **kw: _Page(rows)))


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


def test_reports_contract_int_unit():
    """Direct unit cases for the contract helper."""
    from evals.tasks import reports_contract_int

    assert reports_contract_int("count: 3", 3) is True
    assert reports_contract_int("count: 2", 3) is False
    assert reports_contract_int("-3", 3) is False
    assert reports_contract_int("count: -3", 3) is False
    assert reports_contract_int("0", 0) is True
    assert reports_contract_int("Some prose only", 0) is False
    assert reports_contract_int("preamble\ncount: 0\n", 0) is True
    # Last contract line wins
    assert reports_contract_int("count: 9\ncount: 3", 3) is True
    assert reports_contract_int("count: 9\ncount: 3", 9) is False


def test_exact_line_contract_helpers_unit():
    from evals.tasks import contract_values, reports_contract_value, reports_contract_values

    text = "prose mentions state Done\nSTATE: In Progress\nitem: B\nitem: A"
    assert contract_values(text, "state") == ["In Progress"]
    assert reports_contract_value(text, "state", "In Progress") is True
    assert reports_contract_value("- state: In Progress", "state", "In Progress") is False
    assert reports_contract_values(text, "item", ["A", "B"]) is True
    assert reports_contract_values("item: A\nitem: A", "item", ["A"]) is False


# ---------------------------------------------------------------------------
# Sample of 6 existing verifiers (untouched + wrong-value)
# ---------------------------------------------------------------------------


class _R1Plane:
    def __init__(self, state_name: str):
        st = SimpleNamespace(id="st-1", name=state_name, group="started")
        self.work_items = SimpleNamespace(
            list=lambda **kw: _Page([_item("r1", R1_TITLE, state=st)]),
            retrieve=lambda **kw: SimpleNamespace(id="r1", name=R1_TITLE, state=st),
        )
        self.states = SimpleNamespace(
            list=lambda **kw: _Page(
                [
                    st,
                    SimpleNamespace(id="st-2", name="Done", group="completed"),
                    SimpleNamespace(id="st-3", name="Backlog", group="unstarted"),
                ]
            )
        )


def test_existing_r1_untouched_empty_text_fails():
    async def _go():
        plane = _R1Plane("In Progress")
        ctx = {
            "workspace_slug": "ws",
            "project_id": "p1",
            "r1_state_name": "In Progress",
            "state_names": ["In Progress", "Done", "Backlog"],
        }
        ok, note = await verify_r1(plane, ctx, _run(""))
        assert ok is False, note

    return asyncio.run(_go())


def test_existing_r1_wrong_state_in_text_fails():
    async def _go():
        plane = _R1Plane("In Progress")
        ctx = {
            "workspace_slug": "ws",
            "project_id": "p1",
            "r1_state_name": "In Progress",
            "state_names": ["In Progress", "Done", "Backlog"],
        }
        ok, note = await verify_r1(plane, ctx, _run("Done"))
        assert ok is False, note

    return asyncio.run(_go())


def test_existing_r1_exact_state_contract_passes():
    async def _go():
        plane = _R1Plane("In Progress")
        ctx = {
            "workspace_slug": "ws",
            "project_id": "p1",
            "r1_state_name": "In Progress",
            "state_names": ["In Progress", "Done", "Backlog"],
        }
        ok, note = await verify_r1(plane, ctx, _run("state: In Progress"))
        assert ok is True, note

    return asyncio.run(_go())


class _R2Plane:
    def __init__(self, count: int):
        self._count = count
        self.work_items = SimpleNamespace(
            list=lambda **kw: _Page([_item(f"u{i}", f"U{i}", priority="urgent") for i in range(count)]),
            count=lambda **kw: (
                SimpleNamespace(total_count=count) if False else None
            ),  # unused; verify_r2 uses list path
        )


def test_existing_r2_wrong_count_in_text_fails():
    async def _go():
        # verify_r2 counts open urgent via SDK; text must match that count.
        from evals.tasks import verify_r2 as _vr2

        class Plane:
            def __init__(self):
                self.work_items = SimpleNamespace(
                    list=lambda **kw: _Page(
                        [
                            _item("1", "a", priority="urgent", state=SimpleNamespace(group="started")),
                            _item("2", "b", priority="urgent", state=SimpleNamespace(group="started")),
                            _item("3", "c", priority="urgent", state=SimpleNamespace(group="started")),
                            _item("4", "d", priority="urgent", state=SimpleNamespace(group="started")),
                        ]
                    )
                )
                self.states = SimpleNamespace(
                    list=lambda **kw: _Page([SimpleNamespace(id="s", name="S", group="started", default=False)])
                )

        # If verifier only checks text against live count, empty/wrong text fails.
        ok, note = await _vr2(Plane(), {"workspace_slug": "ws", "project_id": "p1"}, _run("0"))
        assert ok is False, note

    return asyncio.run(_go())


class _W2Plane:
    def __init__(self, group: str, name: str):
        st = SimpleNamespace(id="st", name=name, group=group)
        self.work_items = SimpleNamespace(
            list=lambda **kw: _Page([_item("w2", W2_TITLE, state=st)]),
            retrieve=lambda **kw: SimpleNamespace(id="w2", state=st),
        )
        self.states = SimpleNamespace(list=lambda **kw: _Page([st]))


def test_existing_w2_untouched_not_done_fails():
    async def _go():
        plane = _W2Plane("started", "In Progress")
        ok, note = await verify_w2(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
        assert ok is False, note

    return asyncio.run(_go())


def test_existing_w2_wrong_cancelled_group_fails():
    async def _go():
        plane = _W2Plane("cancelled", "Cancelled")
        ok, note = await verify_w2(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
        assert ok is False, note

    return asyncio.run(_go())


class _W4Plane:
    def __init__(self, name: str):
        self.labels = SimpleNamespace(
            retrieve=lambda **kw: SimpleNamespace(id=kw["label_id"], name=name),
            list=lambda **kw: _Page([SimpleNamespace(id="triage-id", name=name)]),
        )


def test_existing_w4_untouched_still_triage_fails():
    async def _go():
        plane = _W4Plane("triage")
        ctx = {"workspace_slug": "ws", "project_id": "p1", "labels": {"triage": "triage-id"}}
        ok, note = await verify_w4(plane, ctx, _run())
        assert ok is False, note

    return asyncio.run(_go())


def test_existing_w4_wrong_name_needs_review_fails():
    async def _go():
        plane = _W4Plane("needs-review")
        ctx = {"workspace_slug": "ws", "project_id": "p1", "labels": {"triage": "triage-id"}}
        ok, note = await verify_w4(plane, ctx, _run())
        assert ok is False, note

    return asyncio.run(_go())


class _W8Plane:
    def __init__(self, durations: list[int]):
        self.work_items = SimpleNamespace(
            list=lambda **kw: _Page([_item("w8", W8_TITLE)]),
            work_logs=SimpleNamespace(list=lambda **kw: [SimpleNamespace(duration=d) for d in durations]),
        )


def test_existing_w8_untouched_no_log_fails():
    async def _go():
        plane = _W8Plane([])
        ok, note = await verify_w8(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
        assert ok is False, note

    return asyncio.run(_go())


def test_existing_w8_wrong_duration_fails():
    async def _go():
        plane = _W8Plane([60])
        ok, note = await verify_w8(plane, {"workspace_slug": "ws", "project_id": "p1"}, _run())
        assert ok is False, note

    return asyncio.run(_go())


def test_existing_c2_untouched_empty_text_fails():
    async def _go():
        ctx = {"release_changelog_text": "Changelog entry one: OAuth login hardening."}
        ok, note = await verify_c2(object(), ctx, _run(""))
        assert ok is False, note

    return asyncio.run(_go())


def test_existing_c2_wrong_release_name_fails():
    async def _go():
        ok, note = await verify_c2(
            object(),
            {"release_changelog_text": "Changelog entry one: OAuth login hardening."},
            _run("Release 9.9.9 shipped nothing useful."),
        )
        assert ok is False, note

    return asyncio.run(_go())


def test_existing_c2_exact_release_and_shipped_contract_passes():
    async def _go():
        ok, note = await verify_c2(
            object(),
            {
                "release_changelog_text": (
                    "Changelog entry one: OAuth login hardening. Changelog entry two: webhook retry backoff."
                )
            },
            _run("release: 1.2.0\nshipped: OAuth login hardening\nshipped: webhook retry backoff"),
        )
        assert ok is True, note

    return asyncio.run(_go())


# ---------------------------------------------------------------------------
# Prompt binding hard-fail + dry-run markers
# ---------------------------------------------------------------------------


def test_prompt_bind_strict_empty_raises():
    from evals.tasks import TASKS_BY_ID, PromptBindError, format_task_prompt

    t = TASKS_BY_ID["I1"]
    with pytest.raises(PromptBindError):
        format_task_prompt(t, {"project_name": "P", "items": {}}, strict=True)


def test_prompt_bind_strict_exception_raises():
    from evals.tasks import PromptBindError, format_task_prompt

    def boom(_ctx):
        raise RuntimeError("seed broken")

    task = {
        "id": "X",
        "prompt": "do {work_item_id}",
        "prompt_bind": boom,
    }
    with pytest.raises(PromptBindError, match="prompt_bind failed"):
        format_task_prompt(task, {"project_name": "P"}, strict=True)


def test_prompt_bind_dry_run_markers():
    from evals.tasks import TASKS_BY_ID, format_task_prompt

    t = TASKS_BY_ID["I1"]
    text = format_task_prompt(t, {"project_name": "EVAL x"}, strict=False)
    assert "<work_item_id>" in text
    assert "EVAL x" in text


def test_prompt_bind_strict_success():
    from evals.tasks import TASKS_BY_ID, format_task_prompt

    t = TASKS_BY_ID["I1"]
    text = format_task_prompt(
        t,
        {"project_name": "P", "items": {I1_TITLE: "uuid-abc"}},
        strict=True,
    )
    assert "uuid-abc" in text
    assert "<" not in text


# ---------------------------------------------------------------------------
# Teardown deletes release_tag + customer_property
# ---------------------------------------------------------------------------


class _TeardownPlane:
    def __init__(self):
        self.deleted: list[tuple[str, str]] = []
        self.releases = SimpleNamespace(
            tags=SimpleNamespace(
                list=lambda **kw: _Page([SimpleNamespace(id="tag-1", version=L3_TAG_VERSION)]),
                delete=lambda **kw: self.deleted.append(("release_tag", kw["tag_id"])),
            ),
            delete=lambda **kw: self.deleted.append(("release", kw.get("release_id"))),
        )
        self.customers = SimpleNamespace(
            properties=SimpleNamespace(
                list=lambda **kw: _Page(
                    [
                        SimpleNamespace(
                            id="prop-1",
                            display_name=L4_PROP_DISPLAY,
                            name="eval-industry",
                        )
                    ]
                ),
                delete=lambda **kw: self.deleted.append(("customer_property", kw["property_id"])),
            ),
            list=lambda **kw: _Page([]),
            delete=lambda **kw: None,
        )
        self.projects = SimpleNamespace(delete=lambda **kw: None)
        self.workspace_work_item_types = SimpleNamespace(delete=lambda **kw: None)
        self.workspace_work_item_properties = SimpleNamespace(delete=lambda **kw: None)


def test_teardown_deletes_release_tag_and_customer_property():
    from evals.seed import teardown

    plane = _TeardownPlane()
    ctx = {
        "workspace_slug": "ws",
        "project_id": "p1",
        "project_name": "EVAL x",
        "workspace_objects": [
            {"kind": "release_tag", "id": "tag-tracked"},
            {"kind": "customer_property", "id": "prop-tracked"},
        ],
    }
    teardown(plane, ctx)
    kinds = {k for k, _ in plane.deleted}
    assert "release_tag" in kinds
    assert "customer_property" in kinds
    # Tracked ids deleted
    assert ("release_tag", "tag-tracked") in plane.deleted
    assert ("customer_property", "prop-tracked") in plane.deleted


def test_preclean_removes_stale_tag_and_property():
    from evals.seed import _preclean_ws3_workspace_artifacts

    deleted: list[tuple[str, str]] = []

    class Plane:
        releases = SimpleNamespace(
            tags=SimpleNamespace(
                list=lambda **kw: _Page([SimpleNamespace(id="t-old", version=L3_TAG_VERSION)]),
                delete=lambda **kw: deleted.append(("tag", kw["tag_id"])),
            )
        )
        customers = SimpleNamespace(
            properties=SimpleNamespace(
                list=lambda **kw: _Page([SimpleNamespace(id="p-old", display_name=L4_PROP_DISPLAY, name="x")]),
                delete=lambda **kw: deleted.append(("prop", kw["property_id"])),
            )
        )

    _preclean_ws3_workspace_artifacts(Plane(), "ws")
    assert ("tag", "t-old") in deleted
    assert ("prop", "p-old") in deleted


def test_preclean_delete_failure_raises_for_infra_seed():
    """Found artifact that cannot be deleted must raise (harness → infra_seed)."""
    from evals.seed import _preclean_ws3_workspace_artifacts

    class Plane:
        releases = SimpleNamespace(
            tags=SimpleNamespace(
                list=lambda **kw: _Page([SimpleNamespace(id="t-stuck", version=L3_TAG_VERSION)]),
                delete=lambda **kw: (_ for _ in ()).throw(RuntimeError("403 forbidden")),
            )
        )
        customers = SimpleNamespace(
            properties=SimpleNamespace(
                list=lambda **kw: _Page([]),
                delete=lambda **kw: None,
            )
        )

    with pytest.raises(RuntimeError, match="preclean|failed to delete|eval-rc1|release tag"):
        _preclean_ws3_workspace_artifacts(Plane(), "ws")


def test_preclean_empty_list_is_silent():
    from evals.seed import _preclean_ws3_workspace_artifacts

    class Plane:
        releases = SimpleNamespace(tags=SimpleNamespace(list=lambda **kw: _Page([]), delete=lambda **kw: None))
        customers = SimpleNamespace(properties=SimpleNamespace(list=lambda **kw: _Page([]), delete=lambda **kw: None))

    _preclean_ws3_workspace_artifacts(Plane(), "ws")  # no raise


# ---------------------------------------------------------------------------
# L2 activity-worker seed gate
# ---------------------------------------------------------------------------


def test_l2_activity_gate_raises_when_empty():
    """Empty activities list after comments → TaskSkipped env:no-activity-worker."""
    from types import SimpleNamespace

    from evals.seed import R5_TITLE, _gate_activity_worker
    from evals.tasks import TaskSkipped

    class Plane:
        work_items = SimpleNamespace(activities=SimpleNamespace(list=lambda **kw: SimpleNamespace(results=[])))

    ctx = {"project_id": "p1", "items": {R5_TITLE: "wi-r5"}}
    with pytest.raises(TaskSkipped, match="env:no-activity-worker"):
        _gate_activity_worker(Plane(), "ws", ctx)


def test_l2_activity_gate_proceeds_when_nonempty():
    from types import SimpleNamespace

    from evals.seed import R5_TITLE, _gate_activity_worker

    class Plane:
        work_items = SimpleNamespace(
            activities=SimpleNamespace(list=lambda **kw: SimpleNamespace(results=[SimpleNamespace(id="a1")]))
        )

    ctx = {"project_id": "p1", "items": {R5_TITLE: "wi-r5"}}
    _gate_activity_worker(Plane(), "ws", ctx)  # no raise
