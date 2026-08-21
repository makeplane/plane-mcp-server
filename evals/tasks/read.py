"""Read-task definitions and their verifiers."""

from __future__ import annotations

from collections import Counter
from typing import Any

from evals.core.fixtures import R1_TITLE, R5_TITLE
from evals.core.state_oracle import state_name_group_pairs
from evals.tasks.answers import (
    answer_with_provenance,
    contract_values,
    get_final_text,
    reports_contract_int,
    reports_contract_value,
    reports_contract_values,
)


async def verify_r1(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R1: final text reports the API-confirmed seed state with call provenance."""
    expected = str(ctx.get("r1_state_name") or "")
    if not expected:
        return answer_with_provenance(False, "API-confirmed seed state missing", run)

    final_text = get_final_text(run)
    answer_correct = reports_contract_value(final_text, "state", expected)
    answer_note = (
        f"final text reports state {expected!r} via contract"
        if answer_correct
        else f"state values={contract_values(final_text, 'state')!r}; want [{expected!r}]"
    )
    return answer_with_provenance(answer_correct, answer_note, run)


R1_TASK: dict[str, Any] = {
    "id": "R1",
    "tags": {"read"},
    "prompt": (
        "In project {project}, what is the current state of the work item titled "
        f"'{R1_TITLE}'? Return exactly one line: 'state: <exact state name>'."
    ),
    "needs": {"items"},
    "verify": verify_r1,
}


async def verify_r2(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R2: report the seed count with item-value or target-scoped total-count evidence."""
    expected = ctx.get("r2_urgent_open_count")
    if not isinstance(expected, int):
        return answer_with_provenance(False, "API-confirmed urgent-open seed count missing", run)
    final_text = get_final_text(run)
    answer_correct = reports_contract_int(final_text, expected)
    answer_note = (
        f"final text reports urgent-open count {expected} via contract"
        if answer_correct
        else f"final text missing contract count: {expected} (need 'count: {expected}')"
    )
    return answer_with_provenance(answer_correct, answer_note, run)


R2_TASK: dict[str, Any] = {
    "id": "R2",
    "tags": {"read"},
    "prompt": (
        "In project {project}, how many urgent open work items are there? "
        "Return exactly one line of the form 'count: N', where N is the integer count."
    ),
    "needs": {"items"},
    "verify": verify_r2,
}


async def verify_r3(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R3: ``item: TITLE`` lines exactly match the seeded due-title set."""
    titles = list(ctx.get("r3_due_titles") or [])
    if not titles:
        return answer_with_provenance(False, "no API-confirmed R3 due titles in seed ctx", run)
    final_text = get_final_text(run)
    answer_correct = reports_contract_values(final_text, "item", titles)
    answer_note = (
        f"final text reports exactly {len(titles)} due-this-week assigned items"
        if answer_correct
        else f"item contract values={contract_values(final_text, 'item')!r}; want {titles!r}"
    )
    return answer_with_provenance(answer_correct, answer_note, run)


R3_TASK: dict[str, Any] = {
    "id": "R3",
    "tags": {"read"},
    "prompt": (
        "In project {project}, list work items assigned to me that are due this week. "
        "Return one line per result as 'item: <exact work item title>' and no other 'item:' lines."
    ),
    "needs": {"items"},
    "verify": verify_r3,
}


async def verify_r4(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R4: contract reports the active cycle, all its items, and overdue items."""
    final_text = get_final_text(run)
    notes: list[str] = []
    ok = True
    cycle_name = str(ctx.get("r4_cycle_name") or "")
    if not cycle_name:
        ok = False
        notes.append("API-confirmed active-cycle name missing from seed ctx")
    elif not reports_contract_value(final_text, "cycle", cycle_name):
        ok = False
        notes.append(f"cycle values={contract_values(final_text, 'cycle')!r}; want [{cycle_name!r}]")
    else:
        notes.append(f"cycle={cycle_name!r}")

    active_titles = [str(value) for value in (ctx.get("r4_active_titles") or [])]
    if not active_titles:
        ok = False
        notes.append("no active-cycle titles in seed ctx")
    elif not reports_contract_values(final_text, "item", active_titles):
        ok = False
        notes.append(f"item values={contract_values(final_text, 'item')!r}; want {active_titles!r}")
    else:
        notes.append(f"{len(active_titles)} active-cycle items")

    overdue_titles = [str(value) for value in (ctx.get("r4_overdue_titles") or [])]
    expected_overdue = overdue_titles or ["none"]
    if not reports_contract_values(final_text, "overdue", expected_overdue):
        ok = False
        notes.append(f"overdue values={contract_values(final_text, 'overdue')!r}; want {expected_overdue!r}")
    else:
        notes.append(f"overdue={expected_overdue!r}")
    return answer_with_provenance(ok, "; ".join(notes), run)


R4_TASK: dict[str, Any] = {
    "id": "R4",
    "tags": {"read"},
    "prompt": (
        "In project {project}, what is in the active cycle, and is anything overdue? "
        "Use these exact contract lines: one 'cycle: <exact active cycle name>' line, one "
        "'item: <exact work item title>' line for every item in that cycle, and one "
        "'overdue: <exact work item title>' line for every overdue item. If none "
        "are overdue, use 'overdue: none'."
    ),
    "needs": {"items", "cycles"},
    "verify": verify_r4,
}


async def verify_r5(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R5: ``comment: TEXT`` lines exactly match the seeded comments."""
    phrases = list(ctx.get("r5_comment_phrases") or [])
    if not phrases:
        return answer_with_provenance(False, "no API-confirmed R5 comments in seed ctx", run)
    final_text = get_final_text(run)
    answer_correct = reports_contract_values(final_text, "comment", phrases)
    answer_note = (
        f"final text reports exactly {len(phrases)} seeded comments"
        if answer_correct
        else f"comment values={contract_values(final_text, 'comment')!r}; want {phrases!r}"
    )
    return answer_with_provenance(answer_correct, answer_note, run)


R5_TASK: dict[str, Any] = {
    "id": "R5",
    "tags": {"read"},
    "prompt": (
        f"In project {{project}}, summarize the discussion on the work item titled '{R5_TITLE}'. "
        "You may summarize in prose, but end with one contract line per comment: "
        "'comment: <exact comment text>'. Copy the comment text exactly and include "
        "no other 'comment:' lines."
    ),
    "needs": {"items"},
    "verify": verify_r5,
}


async def verify_r6(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R6: report the winner with item-value or exact project-grouped-count evidence."""
    expected = str(ctx.get("r6_more_bugs_project") or "")
    if not expected:
        return answer_with_provenance(False, "API-confirmed R6 winner missing from seed ctx", run)
    final_text = get_final_text(run)
    answer_correct = reports_contract_value(final_text, "project", expected)
    answer_note = (
        f"final text reports project with more bugs {expected!r}"
        if answer_correct
        else f"project values={contract_values(final_text, 'project')!r}; want [{expected!r}]"
    )
    return answer_with_provenance(answer_correct, answer_note, run)


R6_TASK: dict[str, Any] = {
    "id": "R6",
    "tags": {"read"},
    "prompt": (
        "Across the eval projects created for this run (main project {project} and its "
        "sibling 'B' project), which project has more open Bug-typed work items? "
        "Return exactly one line: 'project: <exact project name>'."
    ),
    "needs": {"items", "bug_type", "second_project"},
    "verify": verify_r6,
}


async def verify_r7(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R7: report the immutable state/group baseline with target-response evidence."""
    baseline = [str(value) for value in (ctx.get("r7_state_pairs") or [])]
    if not baseline:
        return answer_with_provenance(False, "R7 fixture error: seeded state baseline is empty", run)
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    page = plane.states.list(workspace_slug=workspace_slug, project_id=project_id)
    states = list(page.results or [])
    live = state_name_group_pairs(states)
    if Counter(live) != Counter(baseline):
        return answer_with_provenance(
            False,
            f"state oracle was mutated after seeding: live={live!r}; baseline={baseline!r}",
            run,
        )

    final_text = get_final_text(run)
    if not reports_contract_values(final_text, "state", baseline):
        reported = contract_values(final_text, "state")
        return answer_with_provenance(
            False,
            f"state values={reported!r}; want seeded state/group pairs {baseline!r}",
            run,
        )
    return answer_with_provenance(True, f"final text reports all {len(baseline)} seeded state/group pairs", run)


R7_TASK: dict[str, Any] = {
    "id": "R7",
    "tags": {"read", "extra"},
    "prompt": (
        "List every workflow state in project {project} and its group. Return exactly "
        "one line per state as 'state: <exact state name> | group: <exact state group>'."
    ),
    # Extra: exercises project state listing.
    "needs": set(),
    "verify": verify_r7,
}


READ_TASKS: list[dict[str, Any]] = [R1_TASK, R2_TASK, R3_TASK, R4_TASK, R5_TASK, R6_TASK, R7_TASK]


__all__ = ["READ_TASKS", "verify_r1", "verify_r2", "verify_r3", "verify_r4", "verify_r5", "verify_r6", "verify_r7"]
