"""Public task catalog assembled from task-class modules."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from evals.tasks.common import (
    PromptBindError,
    TaskSkipped,
    as_id,
    contract_values,
    count_open_urgent,
    find_item_by_name,
    find_items_by_name,
    format_task_prompt,
    get_final_text,
    ids,
    is_not_found,
    reports_contract_int,
    reports_contract_value,
    reports_contract_values,
    reports_exact_int,
    state_group,
    state_name,
    whole_answer_int,
    word_boundary,
)
from evals.tasks.cross import CROSS_TASKS, verify_c1, verify_c2
from evals.tasks.debias import (
    DEBIAS_TASKS,
    I1_TITLE,
    I2_TITLE,
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
from evals.tasks.read import (
    READ_TASKS,
    verify_r1,
    verify_r2,
    verify_r3,
    verify_r4,
    verify_r5,
    verify_r6,
    verify_r7,
)
from evals.tasks.schema import SCHEMA_TASKS, verify_s1, verify_s2, verify_s3, verify_s4, verify_s5
from evals.tasks.write import (
    WRITE_TASKS,
    verify_w1,
    verify_w2,
    verify_w3,
    verify_w4,
    verify_w5,
    verify_w6,
    verify_w7,
    verify_w8,
    verify_w9,
    verify_w10,
)

EXPECTED_TASK_IDS = (
    "R1",
    "R2",
    "R3",
    "R4",
    "R5",
    "R6",
    "W1",
    "W2",
    "W3",
    "W4",
    "W5",
    "W6",
    "W7",
    "W8",
    "W9",
    "W10",
    "S1",
    "S2",
    "S3",
    "S4",
    "S5",
    "C1",
    "C2",
    "R7",
    "I1",
    "I2",
    "I3",
    "I4",
    "I5",
    "L1",
    "L2",
    "L3",
    "L4",
    "L5",
)

# Preserve the historical catalog order exactly: R7 was added after C1/C2.
TASKS: list[dict[str, Any]] = [
    *READ_TASKS[:6],
    *WRITE_TASKS,
    *SCHEMA_TASKS,
    *CROSS_TASKS,
    READ_TASKS[6],
    *DEBIAS_TASKS,
]
if tuple(task["id"] for task in TASKS) != EXPECTED_TASK_IDS:
    raise RuntimeError("assembled task order changed; battery/result compatibility would break")

TASKS_BY_ID: dict[str, dict[str, Any]] = {task["id"]: task for task in TASKS}


def resolve_surface_tool_sets(
    task: dict[str, Any],
    surface: str,
) -> dict[str, Any]:
    """Resolve optimal/alternate tool sets for a surface.

    Returns a dict with:
      - skip (str | None): if set, the runner should SKIP the task on this surface
      - optimal_tools / alternate_tools: classification sets
      - optimal_calls: optional override
      - classification: ``exact`` when an overlay or full/legacy sets apply;
        ``approximate`` when falling back to flat legacy-named sets on a non-full
        surface that has no overlay
    """
    surface = (surface or "full").strip().lower()
    overlays = task.get("surface_tools") or {}

    if surface in ("full", "legacy", ""):
        return {
            "skip": None,
            "optimal_tools": set(task["optimal_tools"]),
            "alternate_tools": set(task["alternate_tools"]),
            "optimal_calls": task.get("optimal_calls"),
            "classification": "exact",
        }

    ov = overlays.get(surface)
    # v2-schema is a superset of v2 for *supported* tools, but schema adds none of
    # the long-tail APIs (worklog summary, activities, release tags, customer
    # property values). Inherit the full v2 overlay — including expected_skip /
    # unsupported — when no schema-specific entry exists.
    if ov is None and surface == "v2-schema":
        ov = overlays.get("v2")

    if ov is None:
        return {
            "skip": None,
            "optimal_tools": set(task["optimal_tools"]),
            "alternate_tools": set(task["alternate_tools"]),
            "optimal_calls": task.get("optimal_calls"),
            "classification": "approximate",
        }

    if ov.get("unsupported") or ov.get("expected_skip"):
        return {
            "skip": ov.get("reason") or f"task {task.get('id')} unsupported on surface {surface}",
            "optimal_tools": set(),
            "alternate_tools": set(),
            "optimal_calls": None,
            "classification": "exact",
        }

    optimal = set(ov["optimal_tools"])
    alternate = set(ov["alternate_tools"])
    if not optimal.isdisjoint(alternate):
        raise ValueError(f"{task.get('id')}/{surface}: optimal/alternate overlap")
    return {
        "skip": None,
        "optimal_tools": optimal,
        "alternate_tools": alternate,
        "optimal_calls": ov.get("optimal_calls", task.get("optimal_calls")),
        "classification": "exact",
    }


def get_tasks(ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Return tasks filtered by id list (None = all)."""
    if ids is None:
        return list(TASKS)
    missing = [i for i in ids if i not in TASKS_BY_ID]
    if missing:
        raise SystemExit(f"Unknown task id(s): {', '.join(missing)}. Known: {', '.join(TASKS_BY_ID)}")
    return [TASKS_BY_ID[i] for i in ids]


def task_author(task: dict[str, Any]) -> str:
    """Return the task author; default ``claude`` when the key is absent."""
    return str(task.get("author") or "claude")


def _serialize_surface_tools(surface_tools: dict[str, Any] | None) -> dict[str, Any]:
    """Stable JSON-friendly form of a task's surface_tools overlay."""
    if not surface_tools:
        return {}
    out: dict[str, Any] = {}
    for surface in sorted(surface_tools):
        ov = surface_tools[surface] or {}
        if not isinstance(ov, dict):
            out[surface] = ov
            continue
        entry: dict[str, Any] = {}
        for key in sorted(ov):
            val = ov[key]
            if isinstance(val, set | frozenset):
                entry[key] = sorted(val)
            elif isinstance(val, list | tuple):
                entry[key] = list(val)
            else:
                entry[key] = val
        out[surface] = entry
    return out


def battery_fingerprint(tasks: list[dict[str, Any]] | None = None) -> str:
    """Stable short hash of the task battery used for a run.

    SHA-256 (first 12 hex chars) over a canonical serialization of every task
    sorted by id: id, prompt, sorted optimal/alternate tools, optimal_calls,
    and the surface_tools overlay (sets sorted, keys sorted).

    Ceilings (intentionally *not* covered by the hash):
    - Verifier functions and ``needs`` fixtures do not alter the fingerprint —
      prompt/tool-set drift is the stability signal, not seed/verify logic.
    - The hash covers the *selected* task list: ``--tasks`` subsets produce
      different fingerprints than a full-catalog run.
    """
    src = list(TASKS if tasks is None else tasks)
    payload: list[dict[str, Any]] = []
    for t in sorted(src, key=lambda x: str(x.get("id") or "")):
        payload.append(
            {
                "id": t.get("id"),
                "prompt": t.get("prompt"),
                "optimal_tools": sorted(t.get("optimal_tools") or []),
                "alternate_tools": sorted(t.get("alternate_tools") or []),
                "optimal_calls": t.get("optimal_calls"),
                "surface_tools": _serialize_surface_tools(t.get("surface_tools")),
            }
        )
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


__all__ = [
    "EXPECTED_TASK_IDS",
    "PromptBindError",
    "TASKS",
    "TASKS_BY_ID",
    "TaskSkipped",
    "as_id",
    "battery_fingerprint",
    "contract_values",
    "count_open_urgent",
    "find_item_by_name",
    "find_items_by_name",
    "format_task_prompt",
    "get_final_text",
    "get_tasks",
    "ids",
    "is_not_found",
    "reports_contract_int",
    "reports_contract_value",
    "reports_contract_values",
    "reports_exact_int",
    "resolve_surface_tool_sets",
    "state_group",
    "state_name",
    "task_author",
    "whole_answer_int",
    "word_boundary",
    "I1_TITLE",
    "I2_TITLE",
    "I3_TITLE",
    "I4_TITLE",
    "L1_TITLE",
    "L2_TITLE",
    "L3_TAG_VERSION",
    "L4_PROP_DISPLAY",
    "L4_PROP_VALUE",
    "L5_TITLE",
    "verify_r1",
    "verify_r2",
    "verify_r3",
    "verify_r4",
    "verify_r5",
    "verify_r6",
    "verify_r7",
    "verify_w1",
    "verify_w2",
    "verify_w3",
    "verify_w4",
    "verify_w5",
    "verify_w6",
    "verify_w7",
    "verify_w8",
    "verify_w9",
    "verify_w10",
    "verify_s1",
    "verify_s2",
    "verify_s3",
    "verify_s4",
    "verify_s5",
    "verify_c1",
    "verify_c2",
    "verify_i1",
    "verify_i2",
    "verify_i3",
    "verify_i4",
    "verify_i5",
    "verify_l1",
    "verify_l2",
    "verify_l3",
    "verify_l4",
    "verify_l5",
]
