"""Read-task definitions and their verifiers."""

from __future__ import annotations

from typing import Any

from evals.seed import CYCLE_CURRENT, R1_TITLE, R5_COMMENT_PHRASES, R5_TITLE
from evals.tasks.common import (
    count_open_urgent,
    find_item_by_name,
    get_final_text,
    state_name,
    word_boundary,
)


async def verify_r1(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R1: final text must name the target item's state and no other seeded state.

    Matching rule: word-boundary, case-insensitive regex on the exact state name
    resolved from the API at verify time (never hardcoded). Additionally fail if
    any *other* project state name also matches (blocks guessing/list_states echo).
    """
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
    if not word_boundary(expected).search(final_text):
        return False, f"final text missing state name {expected!r}"

    other_states = [n for n in (ctx.get("state_names") or []) if n and n.casefold() != expected.casefold()]
    collisions = [n for n in other_states if word_boundary(n).search(final_text)]
    if collisions:
        return (
            False,
            f"final text names other state(s) {collisions!r} besides expected {expected!r}",
        )
    return True, f"final text names only state {expected!r}"


R1_TASK: dict[str, Any] = {
    "id": "R1",
    "tags": {"read", "tier1"},
    "prompt": (
        "In project {project}, what is the current state of the work item titled "
        f"'{R1_TITLE}'? Answer with the state name."
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
    "surface_tools": {
        "v2": {
            "optimal_calls": 1,
            "optimal_tools": {"find_work_items"},
            "alternate_tools": {
                "get_work_item",
                "search_projects",
                "list_states",
                "get_workspace_context",
                "get_pql_reference",
            },
        },
    },
    "needs": {"items"},
    "verify": verify_r1,
}


async def verify_r2(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R2: final text must contain the exact urgent-open count (word-boundary)."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    expected = count_open_urgent(plane, workspace_slug, project_id)
    final_text = get_final_text(run)
    # Word-boundary on the decimal form of the count (blocks "4" matching "24").
    if not word_boundary(str(expected)).search(final_text):
        return False, f"final text missing urgent-open count {expected}"
    return True, f"final text names count {expected}"


R2_TASK: dict[str, Any] = {
    "id": "R2",
    "tags": {"read", "tier1"},
    "prompt": ("In project {project}, how many urgent open work items are there? Answer with the integer count only."),
    "optimal_calls": 1,
    "optimal_tools": {"count_work_items"},
    "alternate_tools": {
        "list_work_items",
        "search_work_items",
        "list_projects",
        "list_states",
        "get_pql_reference",
    },
    "surface_tools": {
        "v2": {
            # No count tool on v2 — find_work_items with priority/state filters is optimal.
            "optimal_calls": 1,
            "optimal_tools": {"find_work_items"},
            "alternate_tools": {
                "get_work_item",
                "search_projects",
                "list_states",
                "get_workspace_context",
                "get_pql_reference",
            },
        },
    },
    "needs": {"items"},
    "verify": verify_r2,
}


async def verify_r3(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R3: final text must include each seeded assigned-to-me / due-this-week title."""
    titles = list(ctx.get("r3_due_titles") or [])
    if not titles:
        return False, "no R3 due titles in seed ctx"
    final_text = get_final_text(run)
    missing = [t for t in titles if not word_boundary(t).search(final_text)]
    if missing:
        return False, f"final text missing title(s) {missing!r}"
    return True, f"final text names {len(titles)} due-this-week assigned items"


R3_TASK: dict[str, Any] = {
    "id": "R3",
    "tags": {"read", "tier1"},
    "prompt": (
        "In project {project}, list work items assigned to me that are due this week. Answer with their titles."
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
    "surface_tools": {
        "v2": {
            "optimal_calls": 1,
            "optimal_tools": {"find_work_items"},
            "alternate_tools": {
                "get_workspace_context",
                "get_work_item",
                "search_projects",
                "get_pql_reference",
            },
        },
    },
    "needs": {"items"},
    "verify": verify_r3,
}


async def verify_r4(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R4: final text must mention the active cycle name and the overdue item title."""
    final_text = get_final_text(run)
    notes: list[str] = []
    ok = True
    if not word_boundary(CYCLE_CURRENT).search(final_text):
        ok = False
        notes.append(f"missing active cycle {CYCLE_CURRENT!r}")
    else:
        notes.append(f"names {CYCLE_CURRENT}")
    overdue = ctx.get("r4_overdue_title")
    if overdue:
        if not word_boundary(overdue).search(final_text):
            # Soft: also accept "overdue" keyword + any active item title.
            if "overdue" not in final_text.casefold():
                ok = False
                notes.append(f"missing overdue title {overdue!r}")
            else:
                notes.append("mentions overdue (title not exact)")
        else:
            notes.append(f"names overdue {overdue!r}")
    return ok, "; ".join(notes)


R4_TASK: dict[str, Any] = {
    "id": "R4",
    "tags": {"read", "tier1"},
    "prompt": (
        "In project {project}, what is in the active cycle, and is anything overdue? "
        f"Name the cycle (expect '{CYCLE_CURRENT}') and any overdue item titles."
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
    "surface_tools": {
        "v2": {
            "optimal_calls": 1,
            "optimal_tools": {"find_work_items"},
            "alternate_tools": {
                "list_cycles",
                "get_work_item",
                "get_pql_reference",
                "search_projects",
                "get_workspace_context",
            },
        },
    },
    "needs": {"items", "cycles"},
    "verify": verify_r4,
}


async def verify_r5(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R5: final text must include seeded comment phrases (word-boundary)."""
    phrases = list(ctx.get("r5_comment_phrases") or R5_COMMENT_PHRASES)
    final_text = get_final_text(run)
    missing = [p for p in phrases if not word_boundary(p).search(final_text)]
    if missing:
        return False, f"final text missing comment phrase(s) {missing!r}"
    return True, f"final text names {len(phrases)} discussion phrases"


R5_TASK: dict[str, Any] = {
    "id": "R5",
    "tags": {"read", "tier1"},
    "prompt": (
        f"In project {{project}}, summarize the discussion on the work item titled '{R5_TITLE}'. "
        "Include the key phrases from its comments."
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
    "surface_tools": {
        "v2": {
            # include= depth: single get_work_item with include=comments after resolve,
            # or find + get with include.
            "optimal_calls": 2,
            "optimal_tools": {"find_work_items", "get_work_item"},
            "alternate_tools": {
                "search_projects",
                "get_workspace_context",
                "create_comment",
            },
        },
    },
    "needs": {"items"},
    "verify": verify_r5,
}


async def verify_r6(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R6: final text must name the project that has more open bugs (resolved at verify)."""
    expected = ctx.get("r6_more_bugs_project") or ctx.get("second_project_name")
    if not expected:
        return False, "second project name missing from seed ctx"
    final_text = get_final_text(run)
    # Match the full project name or the distinctive " B" suffix run8 form.
    if word_boundary(expected).search(final_text):
        return True, f"final text names project with more bugs {expected!r}"
    # Allow matching just the identifier-ish trailing token (e.g. run8 + B).
    run8 = ctx.get("run8") or ""
    alt = f"EVAL {run8} B"
    if word_boundary(alt).search(final_text) or (run8 and run8 in final_text and " B" in final_text):
        return True, f"final text names second project ({alt})"
    return False, f"final text missing project with more bugs {expected!r}"


R6_TASK: dict[str, Any] = {
    "id": "R6",
    "tags": {"read", "tier1"},
    "prompt": (
        "Across the eval projects created for this run (main project {project} and its "
        "sibling 'B' project), which project has more open Bug-typed work items? "
        "Answer with the project name."
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
    "surface_tools": {
        "v2": {
            "optimal_calls": 3,
            "optimal_tools": {"search_projects", "find_work_items", "get_workspace_context"},
            "alternate_tools": {
                "get_work_item",
                "get_pql_reference",
                "list_states",
            },
        },
        # Type id resolution cleaner on v2-schema
        "v2-schema": {
            "optimal_calls": 3,
            "optimal_tools": {"search_projects", "find_work_items", "resolve_work_item_type"},
            "alternate_tools": {
                "list_work_item_types",
                "get_workspace_context",
                "get_work_item",
                "get_pql_reference",
            },
        },
    },
    "needs": {"items", "bug_type", "second_project"},
    "verify": verify_r6,
}


async def verify_r7(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R7 (extra): final text names at least one legal next state for the R1 item.

    Resolves available completed/started/unstarted states at verify time and
    requires a word-boundary hit on one of them (or explicit 'unrestricted').
    """
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    page = plane.states.list(workspace_slug=workspace_slug, project_id=project_id)
    names = [(s.name or "").strip() for s in (page.results or []) if (s.name or "").strip()]
    final_text = get_final_text(run)
    if "unrestricted" in final_text.casefold() or "any state" in final_text.casefold():
        return True, "agent reported unrestricted transitions"
    hits = [n for n in names if word_boundary(n).search(final_text)]
    if not hits:
        return False, f"final text names none of project states {names}"
    return True, f"final text names state(s) {hits}"


R7_TASK: dict[str, Any] = {
    "id": "R7",
    "tags": {"read", "tier1", "extra"},
    "prompt": (
        f"In project {{project}}, what states can the work item '{R1_TITLE}' "
        "legally transition to under workflow rules? List the state names "
        "(or say unrestricted if none)."
    ),
    # Extra: exercises list_available_transitions.
    "optimal_calls": 2,
    "optimal_tools": {"list_work_items", "list_states"},
    "alternate_tools": {
        "retrieve_work_item",
        "search_work_items",
        "list_projects",
    },
    "surface_tools": {
        "v2": {
            "optimal_calls": 1,
            "optimal_tools": {"list_available_transitions"},
            "alternate_tools": {
                "find_work_items",
                "get_work_item",
                "list_states",
                "search_projects",
            },
        },
    },
    "needs": {"items"},
    "verify": verify_r7,
}


READ_TASKS: list[dict[str, Any]] = [R1_TASK, R2_TASK, R3_TASK, R4_TASK, R5_TASK, R6_TASK, R7_TASK]


__all__ = ["READ_TASKS", "verify_r1", "verify_r2", "verify_r3", "verify_r4", "verify_r5", "verify_r6", "verify_r7"]
