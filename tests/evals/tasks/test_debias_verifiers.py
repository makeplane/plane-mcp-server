"""Offline eval tests for debias verifiers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from evals.evidence import TARGET_ENTITY_EVIDENCE
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
    return {
        "final_text": text,
        "calls": [
            {
                "tool": "plane_call",
                "is_error": False,
                "observed_sentinels": [TARGET_ENTITY_EVIDENCE],
            }
        ],
        "call_source": "test",
        "evidence_trace_available": True,
    }


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


BACKLOG = SimpleNamespace(id="st-backlog", name="Backlog", group="unstarted")
DONE = SimpleNamespace(id="st-done", name="Done", group="completed")


def _i1_ctx():
    return {"workspace_slug": "ws", "project_id": "p1", "items": {I1_TITLE: "wi-1"}}


def test_i1_passes_only_when_the_target_item_itself_changed():
    """Priority must land on the item the task names, not merely somewhere."""

    async def _go():
        cases = [
            (
                "untouched: target still urgent",
                _WIRetrievePlane(by_id={"wi-1": SimpleNamespace(id="wi-1", priority="urgent")}),
                (),
            ),
            (
                "right value on the wrong item",
                _WIRetrievePlane(
                    by_id={
                        "wi-1": SimpleNamespace(id="wi-1", priority="urgent"),
                        "wi-other": SimpleNamespace(id="wi-other", priority="high"),
                    }
                ),
                ("urgent", "high"),
            ),
        ]
        for label, plane, expect_any in cases:
            ok, note = await verify_i1(plane, _i1_ctx(), _run())
            assert ok is False, f"{label}: {note}"
            if expect_any:
                assert any(s in note for s in expect_any), f"{label}: {note}"

    return asyncio.run(_go())


def test_i2_requires_the_state_contract_to_name_the_real_state():
    async def _go():
        def plane_for(states):
            return _WIRetrievePlane(by_id={"wi-2": SimpleNamespace(id="wi-2", state=BACKLOG)}, states=states)

        ctx = {
            "workspace_slug": "ws",
            "project_id": "p1",
            "items": {W2_TITLE: "wi-2"},
            "i2_state_name": "Backlog",
        }
        cases = [
            ("untouched: empty answer", [BACKLOG], "", False),
            ("names a different state", [BACKLOG, DONE], "Done", False),
            ("exact contract line", [BACKLOG], "state: Backlog", True),
        ]
        for label, states, text, want in cases:
            ok, note = await verify_i2(plane_for(states), dict(ctx), _run(text))
            assert ok is want, f"{label}: {note}"

    return asyncio.run(_go())


def test_i3_fails_unless_the_target_item_is_on_the_cycle():
    async def _go():
        cases = [
            ("untouched: target never added", ["other-1", "other-2"]),
            ("added the wrong item", ["wrong-item"]),
        ]
        for label, on_cycle in cases:
            ctx = {
                "workspace_slug": "ws",
                "project_id": "p1",
                "items": {I3_TITLE: "footer-1"},
                "cycle_current_id": "cyc-1",
            }
            ok, note = await verify_i3(_I3Plane(on_cycle), ctx, _run())
            assert ok is False, f"{label}: {note}"

    return asyncio.run(_go())


def test_i4_requires_the_named_label_on_the_target():
    async def _go():
        cases = [
            ("untouched: no labels", []),
            ("a different label attached", [SimpleNamespace(id="lab-auth")]),
        ]
        for label, labels in cases:
            plane = _WIRetrievePlane(by_id={"wi-4": SimpleNamespace(id="wi-4", labels=labels)})
            ctx = {
                "workspace_slug": "ws",
                "project_id": "p1",
                "items": {I4_TITLE: "wi-4"},
                "labels": {"perf": "lab-perf"},
            }
            ok, note = await verify_i4(plane, ctx, _run())
            assert ok is False, f"{label}: {note}"

    return asyncio.run(_go())


def test_i5_rejects_both_untouched_and_wrong_priority():
    async def _go():
        cases = [
            ("untouched: priority none", "none", ()),
            ("wrong value: high", "high", ("high",)),
        ]
        for label, priority, expect in cases:
            plane = _WIRetrievePlane(by_id={"wi-5": SimpleNamespace(id="wi-5", priority=priority)})
            ctx = {"workspace_slug": "ws", "project_id": "p1", "items": {I3_TITLE: "wi-5"}}
            ok, note = await verify_i5(plane, ctx, _run())
            assert ok is False, f"{label}: {note}"
            for s in expect:
                assert s in note, f"{label}: {note}"

    return asyncio.run(_go())


def test_l1_grades_the_duration_contract_not_the_prose():
    """Prose stating the right facts still fails: the format is part of the task.

    Covers the counterexamples — English "ninety" is not a number, and a correct log with an empty answer.
    """

    async def _go():
        ctx = {
            "workspace_slug": "ws",
            "project_id": "p1",
            "items": {L1_TITLE: "wi-l1"},
            "l1_expected_summary_ids": ["wi-l1"],
        }
        cases = [
            ("untouched: no worklog", [], None, "", False, ()),
            ("wrong duration 120", [120], ["wi-l1"], "Logged 120 minutes; summary ok.", False, ("90",)),
            ("90m logged but empty answer", [90], ["wi-l1"], "", False, ("logged-minutes",)),
            (
                "English 'ninety' is not a number",
                [90],
                ["wi-l1"],
                "Logged one hundred ninety minutes. Project summary looks fine.",
                False,
                (),
            ),
            ("correct facts, no contract", [90], ["wi-l1"], "Logged 1.5 hours total.", False, ()),
            ("bare prose, no contract", [90], ["wi-l1"], "90 minutes of work", False, ("logged-minutes",)),
            (
                "exact contract",
                [90],
                ["wi-l1"],
                "logged-minutes: 90\nsummary-work-item-id: wi-l1",
                True,
                (),
            ),
        ]
        for label, durations, summary_ids, text, want, expect in cases:
            plane = _L1Plane(durations, summary_ids=summary_ids)
            ok, note = await verify_l1(plane, dict(ctx), _run(text))
            assert ok is want, f"{label}: {note}"
            for s in expect:
                assert s in note.lower(), f"{label}: {note}"

        # The 'ninety' case must name the duration it objected to.
        plane = _L1Plane([90], summary_ids=["wi-l1"])
        _, note = await verify_l1(
            plane, dict(ctx), _run("Logged one hundred ninety minutes. Project summary looks fine.")
        )
        assert "duration" in note.lower() or "90" in note or "1.5" in note, note

        mutated_ok, mutated_note = await verify_l1(
            _L1Plane([90], summary_ids=["wi-l1", "agent-added"]),
            dict(ctx),
            _run("logged-minutes: 90\nsummary-work-item-id: wi-l1\nsummary-work-item-id: agent-added"),
        )
        assert mutated_ok is False
        assert "mutated beyond the seeded oracle" in mutated_note

    return asyncio.run(_go())


def test_l2_counts_activities_through_the_contract_only():
    async def _go():
        ctx = {
            "workspace_slug": "ws",
            "project_id": "p1",
            "items": {L2_TITLE: "wi-l2"},
            "l2_activity_count": 3,
        }
        cases = [
            ("untouched: empty answer", "", False),
            ("contract matches truth 3", "Saw some history.\ncount: 3", True),
            ("contract says 2, truth 3", "count: 2", False),
            ("bare negative", "-3", False),
            ("negative contract", "count: -3", False),
            ("prose without contract", "There are 3 activities and some comment phrases.", False),
        ]
        for label, text, want in cases:
            ok, note = await verify_l2(_L2Plane(3), dict(ctx), _run(text))
            assert ok is want, f"{label}: {note}"

    return asyncio.run(_go())


def test_l3_requires_the_exact_release_tag_version():
    async def _go():
        cases = [("untouched: no tags", [], ()), ("wrong version", ["v0.0.1", "other-rc"], (L3_TAG_VERSION,))]
        for label, versions, expect in cases:
            ok, note = await verify_l3(_L3Plane(versions), {"workspace_slug": "ws"}, _run())
            assert ok is False, f"{label}: {note}"
            for s in expect:
                assert s in note, f"{label}: {note}"

    return asyncio.run(_go())


def test_l4_matches_the_property_on_name_type_and_value():
    """A URL-typed property whose name merely contains "Industry" must not satisfy it."""

    async def _go():
        exact = SimpleNamespace(id="prop-1", display_name=L4_PROP_DISPLAY, name="eval-industry", property_type="TEXT")
        loose = SimpleNamespace(id="prop-url", display_name="Industry", name="industry", property_type="URL")
        cases = [
            ("untouched: no property", [], {}, False, ()),
            (
                "right property, wrong value",
                [exact],
                {"prop-1": ["Startup"]},
                False,
                (L4_PROP_VALUE, "Startup", "lack"),
            ),
            ("wrong name and type, right value", [loose], {"prop-url": [L4_PROP_VALUE]}, False, ()),
            ("exact text property", [exact], {"prop-1": [L4_PROP_VALUE]}, True, ()),
        ]
        for label, props, values, want, expect_any in cases:
            ctx = {"workspace_slug": "ws", "customer": {"id": "cust-1", "name": "Acme Corp"}}
            ok, note = await verify_l4(_L4Plane(props=props, values=values), ctx, _run())
            assert ok is want, f"{label}: {note}"
            if expect_any:
                assert any(s in note for s in expect_any), f"{label}: {note}"
            if want:
                assert any(o.get("kind") == "customer_property" for o in ctx.get("workspace_objects") or [])

    return asyncio.run(_go())


def test_l5_accepts_the_api_confirmed_count_only_through_the_contract():
    async def _go():
        ctx = {
            "workspace_slug": "ws",
            "project_id": "p1",
            "items": {L5_TITLE: "wi-l5"},
            "l5_attachment_count": 2,
        }
        cases = [
            ("untouched: empty answer", "", False),
            ("bare count as the whole answer", "2", True),
            ("multiline ending in the contract", "Two files on this work item.\ncount: 2", True),
            ("prose without contract", "There are 2 attachments.", False),
            ("contract with the wrong count", "count: 10", False),
        ]
        for label, text, want in cases:
            ok, note = await verify_l5(_L5Plane(2), dict(ctx), _run(text))
            assert ok is want, f"{label}: {note}"

    return asyncio.run(_go())
