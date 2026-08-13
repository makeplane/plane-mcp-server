"""Read-task definitions and their verifiers."""

from __future__ import annotations

from typing import Any

from evals.seed import CYCLE_CURRENT, R1_TITLE, R5_COMMENT_PHRASES, R5_TITLE
from evals.tasks.answers import (
    contract_values,
    get_final_text,
    reports_contract_int,
    reports_contract_value,
    reports_contract_values,
)
from evals.tasks.lookups import count_open_urgent, find_item_by_name, state_name


async def verify_r1(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R1: final text must report the API-resolved state via ``state: NAME``."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    title = R1_TITLE
    item = find_item_by_name(plane, workspace_slug, project_id, title)
    if item is None:
        return False, f"seeded item {title!r} not found"
    detail = plane.work_items.retrieve(workspace_slug=workspace_slug, project_id=project_id, work_item_id=item.id)
    expected = state_name(plane, workspace_slug, project_id, detail.state)
    if not expected:
        # Prefer the seeded name when API is sparse.
        expected = ctx.get("r1_state_name")
    if not expected:
        return False, "could not resolve expected state name from API"

    final_text = get_final_text(run)
    if not reports_contract_value(final_text, "state", expected):
        return False, f"final text must contain exactly 'state: {expected}'"
    return True, f"final text reports state {expected!r} via contract"


R1_TASK: dict[str, Any] = {
    "id": "R1",
    "tags": {"read", "tier1"},
    "prompt": (
        "In project {project}, what is the current state of the work item titled "
        f"'{R1_TITLE}'? Return exactly one line: 'state: <exact state name>'."
    ),
    "optimal_calls": 1,
    "optimal_tools": {"list_work_items"},
    "alternate_tools": {
        "search_work_items",
        "list_archived_work_items",
        "count_work_items",
        "retrieve_work_item",
        "retrieve_work_item_by_identifier",
        "list_projects",
        "list_states",
    },
    "needs": {"items"},
    "verify": verify_r1,
}


async def verify_r2(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R2: final text reports the urgent-open count via ``count: N``."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    expected = count_open_urgent(plane, workspace_slug, project_id)
    final_text = get_final_text(run)
    if not reports_contract_int(final_text, expected):
        return False, f"final text missing contract count: {expected} (need 'count: {expected}')"
    return True, f"final text reports urgent-open count {expected} via contract"


R2_TASK: dict[str, Any] = {
    "id": "R2",
    "tags": {"read", "tier1"},
    "prompt": (
        "In project {project}, how many urgent open work items are there? "
        "Return exactly one line of the form 'count: N', where N is the integer count."
    ),
    "optimal_calls": 1,
    "optimal_tools": {"count_work_items"},
    "alternate_tools": {
        "list_work_items",
        "search_work_items",
        "list_projects",
        "list_states",
        "get_pql_reference",
    },
    "needs": {"items"},
    "verify": verify_r2,
}


async def verify_r3(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R3: ``item: TITLE`` lines exactly match the seeded due-title set."""
    titles = list(ctx.get("r3_due_titles") or [])
    if not titles:
        return False, "no R3 due titles in seed ctx"
    final_text = get_final_text(run)
    if not reports_contract_values(final_text, "item", titles):
        return False, f"item contract values={contract_values(final_text, 'item')!r}; want {titles!r}"
    return True, f"final text reports exactly {len(titles)} due-this-week assigned items"


R3_TASK: dict[str, Any] = {
    "id": "R3",
    "tags": {"read", "tier1"},
    "prompt": (
        "In project {project}, list work items assigned to me that are due this week. "
        "Return one line per result as 'item: <exact work item title>' and no other 'item:' lines."
    ),
    "optimal_calls": 2,
    "optimal_tools": {"get_me", "list_work_items"},
    "alternate_tools": {
        "search_work_items",
        "count_work_items",
        "list_projects",
        "get_workspace_members",
        "get_pql_reference",
        "retrieve_work_item",
    },
    "needs": {"items"},
    "verify": verify_r3,
}


async def verify_r4(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R4: contract reports the active cycle, all its items, and overdue items."""
    final_text = get_final_text(run)
    notes: list[str] = []
    ok = True
    if not reports_contract_value(final_text, "cycle", CYCLE_CURRENT):
        ok = False
        notes.append(f"cycle values={contract_values(final_text, 'cycle')!r}; want [{CYCLE_CURRENT!r}]")
    else:
        notes.append(f"cycle={CYCLE_CURRENT!r}")

    active_ids = {str(value) for value in (ctx.get("r4_active_item_ids") or [])}
    active_titles = [str(title) for title, item_id in (ctx.get("items") or {}).items() if str(item_id) in active_ids]
    if not active_titles:
        ok = False
        notes.append("no active-cycle titles in seed ctx")
    elif not reports_contract_values(final_text, "item", active_titles):
        ok = False
        notes.append(f"item values={contract_values(final_text, 'item')!r}; want {active_titles!r}")
    else:
        notes.append(f"{len(active_titles)} active-cycle items")

    overdue = str(ctx.get("r4_overdue_title") or "")
    expected_overdue = [overdue] if overdue else ["none"]
    if not reports_contract_values(final_text, "overdue", expected_overdue):
        ok = False
        notes.append(f"overdue values={contract_values(final_text, 'overdue')!r}; want {expected_overdue!r}")
    else:
        notes.append(f"overdue={expected_overdue!r}")
    return ok, "; ".join(notes)


R4_TASK: dict[str, Any] = {
    "id": "R4",
    "tags": {"read", "tier1"},
    "prompt": (
        "In project {project}, what is in the active cycle, and is anything overdue? "
        f"Use these exact contract lines: one 'cycle: {CYCLE_CURRENT}' line, one "
        "'item: <exact work item title>' line for every item in that cycle, and one "
        "'overdue: <exact work item title>' line for every overdue item. If none "
        "are overdue, use 'overdue: none'."
    ),
    "optimal_calls": 2,
    "optimal_tools": {"list_cycles", "list_work_items"},
    "alternate_tools": {
        "list_cycle_work_items",
        "retrieve_cycle",
        "search_work_items",
        "list_projects",
        "get_pql_reference",
    },
    "needs": {"items", "cycles"},
    "verify": verify_r4,
}


async def verify_r5(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R5: ``comment: TEXT`` lines exactly match the seeded comments."""
    phrases = list(ctx.get("r5_comment_phrases") or R5_COMMENT_PHRASES)
    final_text = get_final_text(run)
    if not reports_contract_values(final_text, "comment", phrases):
        return False, f"comment values={contract_values(final_text, 'comment')!r}; want {phrases!r}"
    return True, f"final text reports exactly {len(phrases)} seeded comments"


R5_TASK: dict[str, Any] = {
    "id": "R5",
    "tags": {"read", "tier1"},
    "prompt": (
        f"In project {{project}}, summarize the discussion on the work item titled '{R5_TITLE}'. "
        "You may summarize in prose, but end with one contract line per comment: "
        "'comment: <exact comment text>'. Copy the comment text exactly and include "
        "no other 'comment:' lines."
    ),
    "optimal_calls": 2,
    "optimal_tools": {"list_work_items", "list_work_item_comments"},
    "alternate_tools": {
        "search_work_items",
        "retrieve_work_item",
        "retrieve_work_item_by_identifier",
        "list_work_item_activities",
        "list_projects",
    },
    "needs": {"items"},
    "verify": verify_r5,
}


async def verify_r6(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R6: final text reports the winning project via ``project: NAME``."""
    expected = ctx.get("r6_more_bugs_project") or ctx.get("second_project_name")
    if not expected:
        return False, "second project name missing from seed ctx"
    final_text = get_final_text(run)
    if not reports_contract_value(final_text, "project", expected):
        return False, f"project values={contract_values(final_text, 'project')!r}; want [{expected!r}]"
    return True, f"final text reports project with more bugs {expected!r}"


R6_TASK: dict[str, Any] = {
    "id": "R6",
    "tags": {"read", "tier1"},
    "prompt": (
        "Across the eval projects created for this run (main project {project} and its "
        "sibling 'B' project), which project has more open Bug-typed work items? "
        "Return exactly one line: 'project: <exact project name>'."
    ),
    "optimal_calls": 3,
    "optimal_tools": {"list_projects", "list_work_items", "resolve_work_item_type"},
    "alternate_tools": {
        "count_work_items",
        "search_work_items",
        "list_work_item_types",
        "retrieve_project",
        "get_pql_reference",
    },
    "needs": {"items", "bug_type", "second_project"},
    "verify": verify_r6,
}


async def verify_r7(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R7 (extra): contract names project states or explicitly says unrestricted.

    This preserves the verifier's existing semantic ceiling: the full surface
    exposes project states, not authoritative workflow transition evaluation.
    The output match itself is structural and exact.
    """
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    page = plane.states.list(workspace_slug=workspace_slug, project_id=project_id)
    names = [(s.name or "").strip() for s in (page.results or []) if (s.name or "").strip()]
    final_text = get_final_text(run)
    reported = contract_values(final_text, "transition")
    if reported == ["unrestricted"]:
        return True, "agent reported unrestricted transitions"
    if not reported:
        return False, "final text has no 'transition: <state>' contract lines"
    unknown = [value for value in reported if value not in names]
    if unknown:
        return False, f"transition values {unknown!r} are not exact project state names; have {names!r}"
    return True, f"final text reports project state(s) {reported!r} via contract"


R7_TASK: dict[str, Any] = {
    "id": "R7",
    "tags": {"read", "tier1", "extra"},
    "prompt": (
        f"In project {{project}}, what states can the work item '{R1_TITLE}' "
        "legally transition to under workflow rules? Return one line per state as "
        "'transition: <exact state name>'. If transitions are unrestricted, return "
        "exactly 'transition: unrestricted'."
    ),
    # Extra: exercises list_available_transitions.
    "optimal_calls": 2,
    "optimal_tools": {"list_work_items", "list_states"},
    "alternate_tools": {
        "retrieve_work_item",
        "search_work_items",
        "list_projects",
    },
    "needs": {"items"},
    "verify": verify_r7,
}


READ_TASKS: list[dict[str, Any]] = [R1_TASK, R2_TASK, R3_TASK, R4_TASK, R5_TASK, R6_TASK, R7_TASK]


__all__ = ["READ_TASKS", "verify_r1", "verify_r2", "verify_r3", "verify_r4", "verify_r5", "verify_r6", "verify_r7"]
