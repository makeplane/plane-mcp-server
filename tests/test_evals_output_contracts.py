"""Focused offline tests for the read-task line contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from evals.seed import CYCLE_CURRENT, R1_TITLE, R5_COMMENT_PHRASES
from evals.tasks.cross import verify_c2
from evals.tasks.read import verify_r2, verify_r4, verify_r5, verify_r6, verify_r7


class _Page:
    def __init__(self, results: list[Any]):
        self.results = results
        self.next_page_results = False
        self.next_cursor = None


def _run(text: str) -> dict[str, Any]:
    return {"final_text": text, "calls": []}


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
