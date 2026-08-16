"""Success-conditioned MCP tool-error measurements for eval reports."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from evals.results import TaskResult

from .load import ResultRow, is_infra_error_row, is_meta_row, read_result
from .statistics import median

SCHEMA_FRICTION_LIMITATION = (
    "limitation: is_error is the MCP-level error flag, so this counts tool-reported failures; "
    "an error that is the correct task outcome still contributes, while calling the wrong tool "
    "successfully does not"
)


@dataclass(frozen=True, slots=True)
class TaskSchemaFriction:
    """Absolute and attempt-normalized errors for one task's eligible rows."""

    task_id: str
    repetitions: int
    errored_calls: int
    total_calls: int
    median_errored_calls: float
    errored_call_rate: float | None

    @property
    def address(self) -> str:
        rate = f"{self.errored_call_rate:.1%}" if self.errored_call_rate is not None else "n/a; zero attempts"
        return f"{self.task_id}={self.errored_calls}/{self.total_calls} ({rate})"


@dataclass(frozen=True, slots=True)
class SchemaFrictionMeasurement:
    """Task-cluster aggregate over successful, trace-intact result rows."""

    tasks: dict[str, TaskSchemaFriction]
    task_mean_errored_calls: float | None
    task_mean_errored_call_rate: float | None
    rate_task_count: int

    @property
    def task_count(self) -> int:
        return len(self.tasks)

    @property
    def errored_task_ids(self) -> tuple[str, ...]:
        return tuple(task_id for task_id, task in self.tasks.items() if task.errored_calls)


def successful_trace_rows(rows: list[ResultRow]) -> list[TaskResult]:
    """Return the exact row population used for successful call deltas."""
    eligible: list[TaskResult] = []
    for raw_row in rows:
        row = read_result(raw_row)
        if is_meta_row(row) or is_infra_error_row(row) or row.error or row.skipped or not row.trace_integrity:
            continue
        if row.success:
            eligible.append(row)
    return eligible


def measure_schema_friction(rows: list[ResultRow]) -> SchemaFrictionMeasurement:
    """Measure absolute errors and error rate with tasks as sampling units."""
    by_task: dict[str, list[TaskResult]] = defaultdict(list)
    for row in successful_trace_rows(rows):
        by_task[row.task_id].append(row)

    tasks: dict[str, TaskSchemaFriction] = {}
    for task_id in sorted(by_task):
        task_rows = by_task[task_id]
        errored_calls = sum(row.errored_calls for row in task_rows)
        total_calls = sum(row.num_calls for row in task_rows)
        tasks[task_id] = TaskSchemaFriction(
            task_id=task_id,
            repetitions=len(task_rows),
            errored_calls=errored_calls,
            total_calls=total_calls,
            median_errored_calls=float(median([float(row.errored_calls) for row in task_rows]) or 0.0),
            errored_call_rate=(errored_calls / total_calls if total_calls else None),
        )

    absolute_values = [task.median_errored_calls for task in tasks.values()]
    rate_values = [task.errored_call_rate for task in tasks.values() if task.errored_call_rate is not None]
    return SchemaFrictionMeasurement(
        tasks=tasks,
        task_mean_errored_calls=(sum(absolute_values) / len(absolute_values) if absolute_values else None),
        task_mean_errored_call_rate=(sum(rate_values) / len(rate_values) if rate_values else None),
        rate_task_count=len(rate_values),
    )


def schema_friction_statement(measurement: SchemaFrictionMeasurement) -> str:
    """Render explicit zeros, task addresses, and the measurement boundary."""
    absolute = measurement.task_mean_errored_calls
    absolute_text = f"{absolute:.1f}" if absolute is not None else "n/a"
    rate = measurement.task_mean_errored_call_rate
    rate_text = f"{rate:.1%}" if rate is not None else "n/a"
    flagged = [task.address for task in measurement.tasks.values() if task.errored_calls]
    flagged_text = f" [{', '.join(flagged)}]" if flagged else " []"
    return "\n".join(
        (
            "schema friction (same successful, trace-intact rows as call deltas): "
            f"task-mean median errored calls={absolute_text} across {measurement.task_count} tasks; "
            f"task-mean errored-call rate={rate_text} across {measurement.rate_task_count} tasks with calls",
            f"  errored-call tasks: {len(flagged)}/{measurement.task_count}{flagged_text}",
            f"  {SCHEMA_FRICTION_LIMITATION}",
        )
    )
