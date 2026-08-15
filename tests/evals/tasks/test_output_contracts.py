"""Offline eval tests for structural output contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from evals.evidence import TARGET_ENTITY_EVIDENCE
from evals.seed import CYCLE_CURRENT, R1_TITLE, R5_COMMENT_PHRASES, W2_TITLE, W8_TITLE
from evals.tasks.cross import verify_c2
from evals.tasks.debias import verify_i2, verify_l2, verify_l5
from evals.tasks.read import verify_r1, verify_r2, verify_r3, verify_r4, verify_r5, verify_r6, verify_r7
from evals.tasks.write import verify_w2, verify_w4, verify_w8
from tests.evals.conftest import case_params


class _Page:
    def __init__(self, results: list[Any] | None = None):
        self.results = results or []
        self.next_page_results = False
        self.next_cursor = None


def _run(text: str = "", *, calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "final_text": text,
        "calls": (
            [
                {
                    "tool": "plane_call",
                    "is_error": False,
                    "observed_sentinels": [TARGET_ENTITY_EVIDENCE],
                }
            ]
            if calls is None
            else calls
        ),
        "call_source": "test",
        "evidence_trace_available": True,
    }


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


class _C2Plane:
    def __init__(self, changelog_html: str = "", error: Exception | None = None, release_name: str = "1.2.0"):
        def retrieve(**kwargs):
            if error is not None:
                raise error
            return SimpleNamespace(description_html=changelog_html)

        self.releases = SimpleNamespace(
            retrieve=lambda **kwargs: SimpleNamespace(name=release_name),
            changelog=SimpleNamespace(retrieve=retrieve),
        )


def _r2_written_number_prose_fails_and_count_contract_passes():
    async def _go():
        state = SimpleNamespace(id="started", name="Started", group="started")
        items = [SimpleNamespace(id=str(index), priority="urgent", state=state) for index in range(4)]
        plane = SimpleNamespace(
            states=SimpleNamespace(list=lambda **kwargs: _Page([state])),
            work_items=SimpleNamespace(list=lambda **kwargs: _Page(items)),
        )
        ctx = {"workspace_slug": "ws", "project_id": "project", "r2_urgent_open_count": 4}

        prose_ok, _ = await verify_r2(plane, ctx, _run("There are four urgent open work items."))
        contract_ok, note = await verify_r2(plane, ctx, _run("count: 4"))

        assert prose_ok is False
        assert contract_ok is True, note

    return asyncio.run(_go())


def _r2_rejects_a_count_that_disagrees_with_the_api():
    async def _go():
        from evals.tasks.read import verify_r2 as _vr2

        urgent = [_item(str(i), "x", priority="urgent", state=SimpleNamespace(group="started")) for i in range(4)]

        class Plane:
            work_items = SimpleNamespace(list=lambda **kw: _Page(urgent))
            states = SimpleNamespace(
                list=lambda **kw: _Page([SimpleNamespace(id="s", name="S", group="started", default=False)])
            )

        ctx = {"workspace_slug": "ws", "project_id": "p1", "r2_urgent_open_count": 4}
        ok, note = await _vr2(Plane(), ctx, _run("0"))
        assert ok is False, note

    return asyncio.run(_go())


@pytest.mark.parametrize(
    "case",
    case_params(
        _r2_written_number_prose_fails_and_count_contract_passes,
        _r2_rejects_a_count_that_disagrees_with_the_api,
    ),
)
def test_r2_behaviours(case):
    case()


def test_r4_contract_requires_cycle_items_and_exact_overdue_title():
    async def _go():
        overdue = "Session cookie not rotated after login"
        ctx = {
            "r4_cycle_name": CYCLE_CURRENT,
            "r4_active_titles": [R1_TITLE, overdue],
            "r4_overdue_titles": [overdue],
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


def test_read_provenance_matrix_and_canary_coverage():
    async def _go():
        ctx = {"r2_urgent_open_count": 4}
        no_call_ok, no_call_note = await verify_r2(object(), ctx, _run("count: 4", calls=[]))
        assert no_call_ok is False
        assert "answer_correct=true" in no_call_note
        assert "provenance=missing" in no_call_note

        successful_ok, successful_note = await verify_r2(object(), ctx, _run("count: 4"))
        assert successful_ok is True, successful_note
        assert "answer_correct=true" in successful_note
        assert "provenance=observed" in successful_note

        unrelated_ok, unrelated_note = await verify_r2(
            object(),
            ctx,
            _run(
                "count: 4",
                calls=[{"tool": "plane_call", "is_error": False, "observed_sentinels": []}],
            ),
        )
        assert unrelated_ok is False
        assert "answer_correct=true" in unrelated_note
        assert "0 evidence-bearing of 1 successful" in unrelated_note

        failed_call_ok, failed_call_note = await verify_r2(
            object(),
            ctx,
            _run("count: 4", calls=[{"tool": "plane_call", "is_error": True}]),
        )
        assert failed_call_ok is False
        assert "answer_correct=true" in failed_call_note
        assert "0 evidence-bearing of 0 successful" in failed_call_note

        wrong_ok, wrong_note = await verify_r2(object(), ctx, _run("count: 3"))
        assert wrong_ok is False
        assert "answer_correct=false" in wrong_note
        assert "provenance=observed" in wrong_note

        unavailable_ok, unavailable_note = await verify_r2(
            object(),
            ctx,
            {"final_text": "count: 4"},
        )
        assert unavailable_ok is False
        assert "provenance=unavailable" in unavailable_note

        incomplete_ok, incomplete_note = await verify_r2(
            object(),
            ctx,
            {
                **_run("count: 4"),
                "driver_notes": ["proxy_sidecar_incomplete:skipped_rows=1"],
                "trace_integrity": False,
                "trace_integrity_reason": "recorder_loss",
            },
        )
        assert incomplete_ok is False
        assert "answer_correct=true" in incomplete_note
        assert "provenance=trace incomplete" in incomplete_note
        assert "sentinel" not in incomplete_note

        # The real canary supplies this exact empty trace. Every affected read verifier
        # must reject even when its text happens to be correct.
        empty_run = {
            "final_text": "",
            "calls": [],
            "call_source": "canary",
            "evidence_trace_available": False,
        }
        cases = [
            ("R1", verify_r1, {"r1_state_name": "Investigating 4821"}, "state: Investigating 4821"),
            ("R2", verify_r2, {"r2_urgent_open_count": 6}, "count: 6"),
            ("R3", verify_r3, {"r3_due_titles": ["Due case 4821"]}, "item: Due case 4821"),
            (
                "R4",
                verify_r4,
                {
                    "r4_cycle_name": "Sprint 47",
                    "r4_active_titles": ["Active case 4821"],
                    "r4_overdue_titles": ["Active case 4821"],
                },
                "cycle: Sprint 47\nitem: Active case 4821\noverdue: Active case 4821",
            ),
            ("R5", verify_r5, {"r5_comment_phrases": ["comment ref-4821"]}, "comment: comment ref-4821"),
            ("R6", verify_r6, {"r6_more_bugs_project": "EVAL deadbeef"}, "project: EVAL deadbeef"),
            ("I2", verify_i2, {"i2_state_name": "Investigating 4821"}, "state: Investigating 4821"),
            ("L2", verify_l2, {"l2_activity_count": 3}, "count: 3"),
            ("L5", verify_l5, {"l5_attachment_count": 2}, "count: 2"),
        ]
        for task_id, verifier, task_ctx, correct_text in cases:
            ok, note = await verifier(object(), task_ctx, {**empty_run, "final_text": correct_text})
            assert ok is False, f"{task_id}: {note}"
            assert "answer_correct=true" in note, f"{task_id}: {note}"
            assert "provenance=unavailable" in note, f"{task_id}: {note}"

    return asyncio.run(_go())


def test_r7_state_group_contract_matches_live_api_exactly():
    async def _go():
        states = [
            SimpleNamespace(name="Backlog", group="backlog"),
            SimpleNamespace(name="In Progress", group="started"),
            SimpleNamespace(name="Done", group="completed"),
        ]
        plane = SimpleNamespace(states=SimpleNamespace(list=lambda **kwargs: _Page(states)))
        ctx = {
            "workspace_slug": "ws",
            "project_id": "project",
            "r7_state_pairs": [
                "Backlog | group: backlog",
                "In Progress | group: started",
                "Done | group: completed",
            ],
        }

        exact = "\n".join(
            [
                "state: Done | group: completed",
                "state: Backlog | group: backlog",
                "state: In Progress | group: started",
            ]
        )
        exact_ok, note = await verify_r7(plane, ctx, _run(exact))
        unrestricted_ok, _ = await verify_r7(plane, ctx, _run("state: unrestricted"))
        wrong_group_ok, _ = await verify_r7(
            plane,
            ctx,
            _run(exact.replace("Done | group: completed", "Done | group: started")),
        )
        names_only_ok, _ = await verify_r7(plane, ctx, _run("state: Backlog\nstate: In Progress\nstate: Done"))
        prose_ok, _ = await verify_r7(plane, ctx, _run("It can move to Done."))
        empty_ok, _ = await verify_r7(plane, ctx, _run())

        assert exact_ok is True, note
        assert unrestricted_ok is False
        assert wrong_group_ok is False
        assert names_only_ok is False
        assert prose_ok is False
        assert empty_ok is False

    return asyncio.run(_go())


def test_r7_rejects_oracle_mutation_and_zero_call_default_state_cans():
    async def _go():
        baseline = [
            "Backlog | group: backlog",
            "In Progress | group: started",
            "Done | group: completed",
            "Review 7b0a1f9c | group: started",
        ]
        ctx = {"workspace_slug": "ws", "project_id": "project", "r7_state_pairs": baseline}
        mutated_states = [
            SimpleNamespace(name="Backlog", group="backlog"),
            SimpleNamespace(name="In Progress", group="started"),
            SimpleNamespace(name="Done", group="completed"),
            SimpleNamespace(name="Agent Rewrite", group="completed"),
        ]
        mutated_answer = "\n".join(
            f"state: {value}"
            for value in [
                "Backlog | group: backlog",
                "In Progress | group: started",
                "Done | group: completed",
                "Agent Rewrite | group: completed",
            ]
        )
        mutated_ok, mutated_note = await verify_r7(
            SimpleNamespace(states=SimpleNamespace(list=lambda **kwargs: _Page(mutated_states))),
            ctx,
            _run(mutated_answer),
        )
        assert mutated_ok is False
        assert "oracle was mutated after seeding" in mutated_note

        live_baseline = [
            SimpleNamespace(name=value.split(" | group: ")[0], group=value.split(" | group: ")[1]) for value in baseline
        ]
        canned = "state: Backlog | group: backlog\nstate: In Progress | group: started\nstate: Done | group: completed"
        canned_ok, canned_note = await verify_r7(
            SimpleNamespace(states=SimpleNamespace(list=lambda **kwargs: _Page(live_baseline))),
            ctx,
            _run(canned, calls=[]),
        )
        assert canned_ok is False
        assert "answer_correct=false" in canned_note
        assert "provenance=missing" in canned_note

    return asyncio.run(_go())


CHANGELOG = "Changelog entry one: OAuth login hardening. Changelog entry two: webhook retry backoff."
R1_CTX = {
    "workspace_slug": "ws",
    "project_id": "p1",
    "r1_state_name": "In Progress",
    "state_names": ["In Progress", "Done", "Backlog"],
}


def test_r1_accepts_only_the_exact_state_contract():
    async def _go():
        cases = [
            ("untouched: empty answer", "", False),
            ("names a different state", "Done", False),
            ("exact contract line", "state: In Progress", True),
        ]
        for label, text, want in cases:
            ok, note = await verify_r1(_R1Plane("In Progress"), dict(R1_CTX), _run(text))
            assert ok is want, f"{label}: {note}"

    return asyncio.run(_go())


def test_w2_requires_the_exact_done_state():
    """Other terminal and completed-group states are not the requested Done state."""

    async def _go():
        cases = [
            ("untouched: still in progress", "started", "In Progress"),
            ("cancelled, not done", "cancelled", "Cancelled"),
            ("different completed-group state", "completed", "Closed"),
        ]
        for label, group, name in cases:
            ok, note = await verify_w2(_W2Plane(group, name), {"workspace_slug": "ws", "project_id": "p1"}, _run())
            assert ok is False, f"{label}: {note}"

    return asyncio.run(_go())


def test_w4_requires_the_label_renamed_to_the_exact_target():
    async def _go():
        ctx = {"workspace_slug": "ws", "project_id": "p1", "labels": {"triage": "triage-id"}}
        for label, name in [
            ("untouched: still triage", "triage"),
            ("renamed to something else", "needs-review"),
            ("space is not the requested hyphen", "needs triage"),
        ]:
            ok, note = await verify_w4(_W4Plane(name), dict(ctx), _run())
            assert ok is False, f"{label}: {note}"

        fallback_ctx = {"workspace_slug": "ws", "project_id": "p1"}
        ok, note = await verify_w4(_W4Plane("needs triage"), fallback_ctx, _run())
        assert ok is False, f"name-scan fallback accepted a space-separated label: {note}"

    return asyncio.run(_go())


def test_w8_requires_a_log_of_exactly_the_asked_duration():
    async def _go():
        for label, durations in [("untouched: no log", []), ("wrong duration", [60])]:
            ok, note = await verify_w8(_W8Plane(durations), {"workspace_slug": "ws", "project_id": "p1"}, _run())
            assert ok is False, f"{label}: {note}"

    return asyncio.run(_go())


def test_c2_grades_the_release_contract_not_correct_prose():
    """Prose naming the right release and entries still fails; the format is the task."""

    async def _go():
        cases = [
            ("untouched: empty answer", "", False),
            ("wrong release name", "Release 9.9.9 shipped nothing useful.", False),
            ("correct facts as prose", "Release 1.2.0 shipped OAuth login hardening and webhook retry backoff.", False),
            (
                "exact contract",
                "release: 1.2.0\nshipped: OAuth login hardening\nshipped: webhook retry backoff",
                True,
            ),
        ]
        ctx = {
            "workspace_slug": "ws",
            "release": {"id": "release-1", "name": "1.2.0"},
            "release_changelog_text": CHANGELOG,
        }
        plane = _C2Plane(f"<p>{CHANGELOG}</p>")
        for label, text, want in cases:
            ok, note = await verify_c2(plane, ctx, _run(text))
            assert ok is want, f"{label}: {note}"

    return asyncio.run(_go())


def test_c2_live_changelog_behaviours():
    mutated = "Changelog entry one: Live API fact. Changelog entry two: Different live item."
    ctx = {
        "workspace_slug": "ws",
        "release": {"id": "release-1", "name": "1.2.0"},
        "release_changelog_text": CHANGELOG,
    }
    baseline_answer = "release: 1.2.0\nshipped: OAuth login hardening\nshipped: webhook retry backoff"

    async def _go():
        identical_ok, identical_note = await verify_c2(_C2Plane(f"<p>{CHANGELOG}</p>"), ctx, _run(baseline_answer))
        assert identical_ok is True, identical_note

        mutated_ok, mutated_note = await verify_c2(_C2Plane(f"<p>{mutated}</p>"), ctx, _run(baseline_answer))
        assert mutated_ok is False
        assert "changelog was mutated after seeding" in mutated_note

        live_answer = "release: 1.2.0\nshipped: Live API fact\nshipped: Different live item"
        exploit_ok, exploit_note = await verify_c2(_C2Plane(f"<p>{mutated}</p>"), ctx, _run(live_answer))
        assert exploit_ok is False, exploit_note

        renamed_ok, renamed_note = await verify_c2(
            _C2Plane(f"<p>{CHANGELOG}</p>", release_name="9.9.9-agent"),
            ctx,
            _run(baseline_answer),
        )
        assert renamed_ok is False
        assert "release name was mutated after seeding" in renamed_note

        empty_live_ok, empty_live_note = await verify_c2(_C2Plane("<p></p>"), ctx, _run(baseline_answer))
        assert empty_live_ok is False
        assert "mutated after seeding" in empty_live_note
        assert "live changelog is empty" in empty_live_note

        empty_seed_ctx = {**ctx, "release_changelog_text": ""}
        empty_seed_ok, empty_seed_note = await verify_c2(_C2Plane("<p></p>"), empty_seed_ctx, _run())
        assert empty_seed_ok is False
        assert "fixture missing" in empty_seed_note
        assert "seeded changelog baseline is empty" in empty_seed_note

        with pytest.raises(RuntimeError, match="C2 verifier read failed while reading release"):
            await verify_c2(_C2Plane(error=RuntimeError("503 unavailable")), ctx, _run())

    return asyncio.run(_go())


def test_c2_repository_constant_answer_without_reading_fails():
    randomized = (
        "Changelog entry one: OAuth login hardening ticket EVAL-a91c7e20. "
        "Changelog entry two: webhook retry backoff window 7-a91c7e20."
    )
    ctx = {
        "workspace_slug": "ws",
        "release": {"id": "release-random", "name": "1.8.14-eval.a91c7e20"},
        "release_changelog_text": randomized,
    }
    repository_constant_answer = "release: 1.2.0\nshipped: OAuth login hardening\nshipped: webhook retry backoff"

    ok, note = asyncio.run(
        verify_c2(
            _C2Plane(f"<p>{randomized}</p>", release_name="1.8.14-eval.a91c7e20"),
            ctx,
            _run(repository_constant_answer, calls=[]),
        )
    )

    assert ok is False
    assert "answer_correct=false" in note
    assert "provenance=missing" in note
