"""Success-conditioned MCP tool-error measurements for eval reports."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from evals.core.error_class import NOT_FOUND, REFUSED, REJECTED, UNCLASSIFIED
from evals.core.results import CallRecord, TaskResult

from .load import ResultRow, is_infra_error_row, is_meta_row, read_result
from .statistics import median

SCHEMA_FRICTION_LIMITATION = (
    "limitation: is_error is the MCP-level error flag, so this counts tool-reported failures; "
    "an error that is the correct task outcome still contributes, while calling the wrong tool "
    "successfully does not"
)

FRICTION_SPLIT_LIMITATION = (
    "limitation: a first not_found is read as the answer to an existence question, since asking "
    "has no cheaper form; only a repeat on the same tool and action is counted as friction. A "
    "surface that misleads an agent into one wrong lookup is therefore not charged for it"
)


def split_errors(calls: list[CallRecord]) -> dict[str, int]:
    """Count a row's errored calls by what kind of "no" they received.

    ``not_found`` is split in two. The first absent read of a given tool and action
    is the answer to an existence question -- there is no cheaper way to ask -- and
    lands in ``answered``. A second identical one means the first was not understood,
    so it joins ``surface``.
    """
    counts = dict.fromkeys(("navigation", "surface", "answered", "other", "unclassified", "unflagged"), 0)
    seen_absent: set[tuple[str, str]] = set()
    for call in calls:
        if not call.is_error and call.error_class is None:
            continue
        if not call.is_error:
            # A refusal the server reported as a successful result. It is counted here
            # and named separately, because it is absent from `errored_calls` -- the
            # protocol-flag total the rest of the report and every earlier run use.
            counts["unflagged"] += 1
        kind = call.error_class or UNCLASSIFIED
        if kind == REFUSED:
            counts["navigation"] += 1
        elif kind == REJECTED:
            counts["surface"] += 1
        elif kind == NOT_FOUND:
            key = (call.tool, call.action or "")
            if key in seen_absent:
                counts["surface"] += 1
            else:
                seen_absent.add(key)
                counts["answered"] += 1
        elif kind == UNCLASSIFIED:
            # Kept apart from `other`, which holds errors we did classify and chose
            # not to charge to tool design. A row written before this field existed
            # lands here in full, and a split reading zero surface friction because
            # nothing was classified must not read as a surface with no friction.
            counts["unclassified"] += 1
        else:
            # denied/failed: real, but not attributable to tool design.
            counts["other"] += 1
    return counts


@dataclass(frozen=True, slots=True)
class TaskSchemaFriction:
    """Absolute and attempt-normalized errors for one task's eligible rows."""

    task_id: str
    repetitions: int
    errored_calls: int
    total_calls: int
    median_errored_calls: float
    errored_call_rate: float | None
    navigation_calls: int = 0
    surface_calls: int = 0
    answered_calls: int = 0
    other_calls: int = 0
    unclassified_calls: int = 0
    unflagged_refusals: int = 0

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
    navigation_calls: int = 0
    surface_calls: int = 0
    answered_calls: int = 0
    other_calls: int = 0
    unclassified_calls: int = 0
    unflagged_refusals: int = 0
    total_calls: int = 0

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
        split = {key: 0 for key in ("navigation", "surface", "answered", "other", "unclassified", "unflagged")}
        for row in task_rows:
            for key, value in split_errors(row.calls).items():
                split[key] += value
        tasks[task_id] = TaskSchemaFriction(
            task_id=task_id,
            repetitions=len(task_rows),
            errored_calls=errored_calls,
            total_calls=total_calls,
            median_errored_calls=float(median([float(row.errored_calls) for row in task_rows]) or 0.0),
            errored_call_rate=(errored_calls / total_calls if total_calls else None),
            navigation_calls=split["navigation"],
            surface_calls=split["surface"],
            answered_calls=split["answered"],
            other_calls=split["other"],
            unclassified_calls=split["unclassified"],
            unflagged_refusals=split["unflagged"],
        )

    absolute_values = [task.median_errored_calls for task in tasks.values()]
    rate_values = [task.errored_call_rate for task in tasks.values() if task.errored_call_rate is not None]
    return SchemaFrictionMeasurement(
        tasks=tasks,
        task_mean_errored_calls=(sum(absolute_values) / len(absolute_values) if absolute_values else None),
        task_mean_errored_call_rate=(sum(rate_values) / len(rate_values) if rate_values else None),
        rate_task_count=len(rate_values),
        navigation_calls=sum(task.navigation_calls for task in tasks.values()),
        surface_calls=sum(task.surface_calls for task in tasks.values()),
        answered_calls=sum(task.answered_calls for task in tasks.values()),
        other_calls=sum(task.other_calls for task in tasks.values()),
        unclassified_calls=sum(task.unclassified_calls for task in tasks.values()),
        unflagged_refusals=sum(task.unflagged_refusals for task in tasks.values()),
        total_calls=sum(task.total_calls for task in tasks.values()),
    )


def _split_lines(measurement: SchemaFrictionMeasurement) -> tuple[str, ...]:
    """The three numbers the single rate used to conflate."""
    total = measurement.total_calls

    def share(count: int) -> str:
        return f"{count}" + (f" ({count / total:.1%})" if total else "")

    surface = measurement.surface_calls
    unclassified = measurement.unclassified_calls
    lines = [
        f"  by kind, of {total} calls: "
        f"surface friction={share(surface)}, "
        f"navigation={share(measurement.navigation_calls)}, "
        f"answered existence questions={share(measurement.answered_calls)}, "
        f"other={share(measurement.other_calls)}, "
        f"unclassified={share(unclassified)}",
    ]
    if measurement.unflagged_refusals:
        lines.append(
            f"  {measurement.unflagged_refusals} refusal(s) arrived flagged as successful results, so they "
            "are counted above but not in the errored-call total"
        )
    if unclassified:
        # Never let "no surface friction" stand in for "nothing was classified".
        lines.append(
            f"  split incomplete: {unclassified} errored call(s) carry no class — a run recorded "
            "before error classes existed, or payloads the classifier does not recognise"
        )
    else:
        lines.append(
            "  surface friction is the number to act on: a well-formed call the API refused on meaning"
            + ("" if surface else " — none in this run")
        )
    return tuple(lines)


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
            *_split_lines(measurement),
            f"  {SCHEMA_FRICTION_LIMITATION}",
            f"  {FRICTION_SPLIT_LIMITATION}",
        )
    )
