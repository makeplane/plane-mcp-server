"""Group a run's failures by what kind of wrong they were."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from evals.core.failure_kind import FAILURE_KINDS, NON_DEFECT_KINDS, UNCLASSIFIED, classify_failure
from evals.core.results import TaskResult

from .load import ResultRow, is_infra_error_row, is_meta_row, read_result

FAILURE_KIND_LIMITATION = (
    "limitation: kinds are read from verifier note text, so they describe what the verifier "
    "could see. A write that landed on the wrong entity reports as a missing or partial write, "
    "because the right entity is empty either way -- distinguishing those needs call arguments"
)


@dataclass(frozen=True, slots=True)
class FailureKindMeasurement:
    """Counts per kind, plus the task ids behind each."""

    counts: dict[str, int] = field(default_factory=dict)
    task_ids: dict[str, tuple[str, ...]] = field(default_factory=dict)
    total: int = 0

    @property
    def defects(self) -> int:
        """Failures attributable to the agent rather than the run or environment."""
        return sum(count for kind, count in self.counts.items() if kind not in NON_DEFECT_KINDS)

    @property
    def non_defects(self) -> int:
        return sum(self.counts.get(kind, 0) for kind in NON_DEFECT_KINDS)


def measure_failure_kinds(rows: list[ResultRow]) -> FailureKindMeasurement:
    """Classify every failed row that carries a verifier note."""
    counts: dict[str, int] = dict.fromkeys(FAILURE_KINDS, 0)
    tasks: dict[str, set[str]] = defaultdict(set)
    total = 0
    for raw_row in rows:
        row: TaskResult = read_result(raw_row)
        if is_meta_row(row) or is_infra_error_row(row) or row.error or row.skipped:
            continue
        if row.success:
            continue
        kind = classify_failure(
            row.verify_note,
            stop_reason=row.stop_reason,
            hit_max_iterations=row.hit_max_iterations,
        )
        counts[kind] += 1
        tasks[kind].add(row.task_id)
        total += 1
    return FailureKindMeasurement(
        counts=counts,
        task_ids={kind: tuple(sorted(ids)) for kind, ids in sorted(tasks.items())},
        total=total,
    )


def failure_kind_statement(measurement: FailureKindMeasurement) -> str:
    """Name every kind that occurred, and say plainly which are not agent defects."""
    if not measurement.total:
        return "failure kinds: no failed rows"
    parts = [
        f"{kind}={measurement.counts[kind]}"
        + (f" [{', '.join(measurement.task_ids[kind])}]" if kind in measurement.task_ids else "")
        for kind in FAILURE_KINDS
        if measurement.counts.get(kind)
    ]
    lines = [f"failure kinds ({measurement.total} failed rows): " + "; ".join(parts)]
    if measurement.non_defects:
        lines.append(
            f"  {measurement.non_defects} of {measurement.total} are not agent defects: a correct answer "
            "the run could not evidence, an environment gap, or a capped run"
        )
    unclassified = measurement.counts.get(UNCLASSIFIED, 0)
    if unclassified:
        # Never let a zero in some kind stand in for "the classifier did not recognise it".
        lines.append(f"  {unclassified} note(s) matched no pattern, so the split above is incomplete by that much")
    lines.append(f"  {FAILURE_KIND_LIMITATION}")
    return "\n".join(lines)


__all__ = [
    "FAILURE_KIND_LIMITATION",
    "FailureKindMeasurement",
    "failure_kind_statement",
    "measure_failure_kinds",
]
