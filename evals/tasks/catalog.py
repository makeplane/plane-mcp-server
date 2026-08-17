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
    "W11",
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


CATALOG_REVISION = 10
"""Bumped when a deliberate change to a fixture or verifier redefines what a task asks.

Revision 10 lets R6's provenance be proven by one count per project, not only by a single
count grouped by project. Both are honest routes to the winner, but only the grouped one
was registered, so every agent that counted each project separately answered correctly and
scored as unproven. R6 results are not comparable across this transition.
Revision 9 also binds R1/I2 provenance to the seeded state, not the work item alone. A work
item's `state` is an id, so resolving its name takes a second call, and the old rule needed the
target id and the answer in one response — unsatisfiable against a surface that does not expand
state. Both tasks failed every repetition while answering correctly.
Revision 8 binds L2's provenance to the activity count its verifier already checks, instead of
requiring the seeded comment phrase to appear in the activity readback. Plane's activity API
never emits comment text — it returns the creation row only — so the old requirement was
unsatisfiable and L2 failed in seeding on every repetition. L2 results are not comparable
across this transition, because before it there were none.
Revision 7 makes read tasks require response evidence bound to the target entity and gives
C2/R7 randomised, immutable seed-time oracles; pre-revision read results are not comparable.
Revision 6 stops W8 and W9 asking for unverifiable logged-date and batching properties;
it also tightens W3/W10 end-state contracts and paginates affected verifier reads. Revision
5 makes R1-R6, I2, L2, and L5 require observed successful Plane tool-call provenance and
randomises their hidden truth. Read results across either transition are not comparable.
Revision 4 rewrote R7 into an exact live state-and-group listing. Revision 3 removed
declared per-task tool sets and call floors and added fixture names. Fixture names being
covered means swapping a task's fixtures no longer needs a manual bump; changing a seeder's
behaviour under the same name still does.
"""


def task_fingerprint_payload(task: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical question payload shared by task and battery hashes."""
    return {
        "id": task.get("id"),
        "prompt": task.get("prompt"),
        "needs": sorted(task.get("needs") or []),
    }


def _short_fingerprint(document: Any) -> str:
    blob = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def task_fingerprint(task: dict[str, Any]) -> str:
    """Return a stable short hash of one task's own question payload."""
    return _short_fingerprint(task_fingerprint_payload(task))


def battery_fingerprint(tasks: list[dict[str, Any]] | None = None) -> str:
    """Stable short hash of the revision and each task's ID, prompt and fixture names.

    Everything hashed is a fact about what the agent was asked — never an expectation
    about how it should answer. Fixture *names* are covered, so swapping a task's
    fixtures is caught mechanically; seeder and verifier *bodies* are not, which is the
    hole CATALOG_REVISION exists to close by hand. A --tasks subset hashes differently
    from the full catalog.
    """
    src = list(TASKS if tasks is None else tasks)
    payload = [task_fingerprint_payload(task) for task in sorted(src, key=lambda item: str(item.get("id") or ""))]
    document = {"revision": CATALOG_REVISION, "tasks": payload}
    return _short_fingerprint(document)


__all__ = [
    "EXPECTED_TASK_IDS",
    "TASKS",
    "TASKS_BY_ID",
    "battery_fingerprint",
    "get_tasks",
    "task_author",
    "task_fingerprint",
    "task_fingerprint_payload",
]
