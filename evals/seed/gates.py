"""Plan-gate classification, shared by every seeder that can meet a paid feature.

This is policy, not a resource. It lived in ``projects`` because the first gate encountered
was a project one, and every other seeder then imported the project module to reach it —
which made ``projects`` a hub and produced the package's only import cycle, since
``item_types`` needs the classifier while ``projects`` needs the item-type seeder.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

from plane.errors.errors import HttpError

from evals.core.errors import TaskSkipped

# Wording a refusal uses when the workspace's plan is what stands in the way. A feature
# switched off for a project says "not enabled for this project" instead, which is a
# configuration state the harness can change and so is not a gate.
PLAN_GATE_PROSE = ("upgrade your plan", "payment required", "subscription", "not available on your")


def is_plan_gate(exc: BaseException) -> bool:
    """True only for genuine plan gates — not generic API failures.

    402 is unambiguous. 403 and 400 are not: Plane uses 403 for ordinary permission denial
    and for the initiative/teamspace plan gates in the same shape, so a bare 403 counted as
    a gate turned real permission bugs into environment skips. Those two now need the
    refusal to name a plan limit.
    """
    if not isinstance(exc, HttpError):
        return False
    if exc.status_code == 402:
        return True
    if exc.status_code not in (400, 403):
        return False
    blob = f"{exc} {exc.response!s}".lower()
    return any(phrase in blob for phrase in PLAN_GATE_PROSE)


@contextlib.contextmanager
def plan_gate_skips(feature: str) -> Iterator[None]:
    """Turn a plan refusal raised inside the block into a task skip.

    An uncaught seed exception becomes infra_seed and kills the task-rep; a capability the
    plan excludes is an environment fact, recorded like L2's missing activity worker.
    ``TaskSkipped`` lives in a neutral module, so seed and task packages can import in
    either order without a cycle.
    """
    try:
        yield
    except Exception as exc:
        if is_plan_gate(exc):
            status = getattr(exc, "status_code", None)
            detail = f"HTTP {status}: {exc}" if status else str(exc)
            raise TaskSkipped(f"env:plan-gated:{feature}", detail=detail[:300]) from exc
        raise


__all__ = ["PLAN_GATE_PROSE", "is_plan_gate", "plan_gate_skips"]
