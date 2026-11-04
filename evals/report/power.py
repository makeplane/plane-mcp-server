"""Say plainly when the per-task numbers cannot carry a verdict.

A run's aggregate and its per-task rows have very different power, and the report
prints both in the same table. At 2 repetitions a task that passed once is
``1/2 UNSTABLE`` with a 95% interval of roughly [0.09, 0.91] -- compatible with
almost any true success rate -- while the paired aggregate across 35 tasks resolved
a call difference at p=0.0018. Reading a per-task row as a finding is therefore
wrong in exactly the runs where the aggregate is most convincing.

No new machinery: ``--tasks`` already allows a focused high-rep subset, and at
roughly $0.50 an arm that is affordable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, summary imports this module
    from .summary import Summary

#: Repetitions per task below which a per-task pass rate is not worth reading.
UNDERPOWERED_REPS = 5


def power_statement(summary: Summary) -> str | None:
    """Return the guardrail line, or None when at least one task is well powered.

    Scoped to the tasks that are actually shallow. An earlier version keyed off the
    best-covered task and fell silent as soon as any one task was deep, which left a
    mixed run's shallow tasks uncaveated -- the opposite of the intended failure.

    The threshold is a reporting heuristic, not a power calculation: 5/5 still carries a
    Wilson interval of roughly [0.57, 1.00], so the line points readers at the aggregate
    rather than promising that five repetitions settle anything.
    """
    counts = [task.n for task in summary.tasks.values() if task.n]
    if not counts:
        return None
    shallow = [count for count in counts if count < UNDERPOWERED_REPS]
    if not shallow:
        return None
    return (
        f"POWER: {len(shallow)} of {len(counts)} task(s) below {UNDERPOWERED_REPS} repetitions "
        f"(fewest {min(shallow)}) — their per-task pass rates and UNSTABLE flags are not verdicts "
        f"at that depth; read the aggregate and paired deltas for those. Raising --reps narrows "
        f"a per-task interval but no fixed count makes one conclusive."
    )


__all__ = ["UNDERPOWERED_REPS", "power_statement"]
