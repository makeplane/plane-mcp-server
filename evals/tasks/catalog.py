"""Task catalog assembly, lookup, authorship, and fingerprinting."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from evals.tasks.cross import CROSS_TASKS
from evals.tasks.debias import DEBIAS_TASKS
from evals.tasks.read import READ_TASKS
from evals.tasks.schema import SCHEMA_TASKS
from evals.tasks.write import WRITE_TASKS

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


CATALOG_REVISION = 2
"""Bumped when a deliberate change to a fixture or verifier redefines what a task asks.

The hash below covers prompts and tool sets, not ``needs`` or verifier bodies, because
prompt drift is the signal it was built to catch. That leaves a hole: correcting a seeder
changes the question a task puts to the agent while the fingerprint keeps asserting the
results are comparable. Bumping this closes the hole by making the redefinition visible.

Revision 1 covers batteries 6-8. Revision 2 is the workspace/project feature-exclusion
correction: excluding a feature now writes it ``False`` instead of omitting the write, so
S5 is graded on three conditions the agent must actually satisfy rather than two plus one
the workspace already happened to be in.
"""


def battery_fingerprint(tasks: list[dict[str, Any]] | None = None) -> str:
    """Stable short hash of the task battery used for a run.

    SHA-256 (first 12 hex chars) over a canonical serialization of ``CATALOG_REVISION``
    and every task sorted by id: id, prompt, sorted optimal/alternate tools, and
    optimal_calls.

    Ceilings (intentionally *not* covered per-task by the hash):
    - Verifier functions and ``needs`` fixtures do not alter the fingerprint on their
      own — prompt/tool-set drift is the stability signal. Bump ``CATALOG_REVISION``
      when they change in a way that redefines the question.
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
            }
        )
    document = {"revision": CATALOG_REVISION, "tasks": payload}
    blob = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


__all__ = [
    "EXPECTED_TASK_IDS",
    "TASKS",
    "TASKS_BY_ID",
    "battery_fingerprint",
    "get_tasks",
    "task_author",
]
