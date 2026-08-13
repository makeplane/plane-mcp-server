"""Offline eval tests for structural output contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from evals.seed import CYCLE_CURRENT, R1_TITLE, R5_COMMENT_PHRASES, W2_TITLE, W8_TITLE
from evals.tasks.cross import verify_c2
from evals.tasks.read import verify_r1, verify_r2, verify_r4, verify_r5, verify_r6, verify_r7
from evals.tasks.write import verify_w2, verify_w4, verify_w8


class _Page:
    def __init__(self, results: list[Any] | None = None):
        self.results = results or []
        self.next_page_results = False
        self.next_cursor = None


def _run(text: str = "") -> dict[str, Any]:
    return {"final_text": text, "calls": []}


def _item(id: str, name: str, **kw: Any) -> SimpleNamespace:
    return SimpleNamespace(id=id, name=name, **kw)


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


class _W2Plane:
    def __init__(self, group: str, name: str):
        st = SimpleNamespace(id="st", name=name, group=group)
        self.work_items = SimpleNamespace(
            list=lambda **kw: _Page([_item("w2", W2_TITLE, state=st)]),
            retrieve=lambda **kw: SimpleNamespace(id="w2", state=st),
        )
        self.states = SimpleNamespace(list=lambda **kw: _Page([st]))


class _W4Plane:
    def __init__(self, name: str):
        self.labels = SimpleNamespace(
            retrieve=lambda **kw: SimpleNamespace(id=kw["label_id"], name=name),
            list=lambda **kw: _Page([SimpleNamespace(id="triage-id", name=name)]),
        )


class _W8Plane:
    def __init__(self, durations: list[int]):
        self.work_items = SimpleNamespace(
            list=lambda **kw: _Page([_item("w8", W8_TITLE)]),
            work_logs=SimpleNamespace(list=lambda **kw: [SimpleNamespace(duration=d) for d in durations]),
        )


def test_r2_written_number_prose_fails_and_count_contract_passes():
    async def _go():
        state = SimpleNamespace(id="started", name="Started", group="started")
        items = [SimpleNamespace(id=str(index), priority="urgent", state=state) for index in range(4)]
        plane = SimpleNamespace(
            states=SimpleNamespace(list=lambda **kwargs: _Page([state])),
            work_items=SimpleNamespace(list=lambda **kwargs: _Page(items)),
        )
        ctx = {"workspace_slug": "ws", "project_id": "project"}

        prose_ok, _ = await verify_r2(plane, ctx, _run("There are four urgent open work items."))
        contract_ok, note = await verify_r2(plane, ctx, _run("count: 4"))

        assert prose_ok is False
        assert contract_ok is True, note

    return asyncio.run(_go())


def test_r4_contract_requires_cycle_items_and_exact_overdue_title():
    async def _go():
        overdue = "Session cookie not rotated after login"
        ctx = {
            "items": {R1_TITLE: "item-1", overdue: "item-2"},
            "r4_active_item_ids": ["item-1", "item-2"],
            "r4_overdue_title": overdue,
        }
        text = f"cycle: {CYCLE_CURRENT}\nitem: {R1_TITLE}\nitem: {overdue}\noverdue: {overdue}"

        ok, note = await verify_r4(object(), ctx, _run(text))
        keyword_only_ok, _ = await verify_r4(object(), ctx, _run(f"cycle: {CYCLE_CURRENT}\noverdue"))

        assert ok is True, note
        assert keyword_only_ok is False

    return asyncio.run(_go())


def test_r5_exact_comment_lines_pass_but_free_prose_does_not():
    async def _go():
        ctx = {"r5_comment_phrases": list(R5_COMMENT_PHRASES)}
        contract = "\n".join(f"comment: {phrase}" for phrase in reversed(R5_COMMENT_PHRASES))
        prose = f"The discussion covered {R5_COMMENT_PHRASES[0]} and {R5_COMMENT_PHRASES[1]}."

        contract_ok, note = await verify_r5(object(), ctx, _run(contract))
        prose_ok, _ = await verify_r5(object(), ctx, _run(prose))

        assert contract_ok is True, note
        assert prose_ok is False

    return asyncio.run(_go())


def test_r6_exact_project_contract_passes_and_shorthand_fails():
    async def _go():
        expected = "EVAL deadbeef B"
        ctx = {"r6_more_bugs_project": expected}

        exact_ok, note = await verify_r6(object(), ctx, _run(f"project: {expected}"))
        shorthand_ok, _ = await verify_r6(object(), ctx, _run("The B project has more bugs."))

        assert exact_ok is True, note
        assert shorthand_ok is False

    return asyncio.run(_go())


def test_r7_transition_contract_is_structural():
    async def _go():
        states = [
            SimpleNamespace(name="Backlog"),
            SimpleNamespace(name="In Progress"),
            SimpleNamespace(name="Done"),
        ]
        plane = SimpleNamespace(states=SimpleNamespace(list=lambda **kwargs: _Page(states)))
        ctx = {"workspace_slug": "ws", "project_id": "project"}

        exact_ok, note = await verify_r7(plane, ctx, _run("transition: Done"))
        prose_ok, _ = await verify_r7(plane, ctx, _run("It can move to Done."))

        assert exact_ok is True, note
        assert prose_ok is False

    return asyncio.run(_go())


def test_c2_correct_changelog_prose_without_contract_fails():
    async def _go():
        changelog = "Changelog entry one: OAuth login hardening. Changelog entry two: webhook retry backoff."
        prose = "Release 1.2.0 shipped OAuth login hardening and webhook retry backoff."

        ok, _ = await verify_c2(object(), {"release_changelog_text": changelog}, _run(prose))

        assert ok is False

    return asyncio.run(_go())


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


def test_existing_r2_wrong_count_in_text_fails():
    async def _go():
        # verify_r2 counts open urgent via SDK; text must match that count.
        from evals.tasks.read import verify_r2 as _vr2

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
