"""Trace-signature indicators for possible work outside the measured MCP surface."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from evals.core.evidence import TARGET_ENTITY_EVIDENCE
from evals.core.results import TRACE_INTEGRITY_SCHEMA_VERSION, CallRecord, TaskResult
from evals.core.task_metadata import task_metadata_from_rows

from .load import ResultRow, is_infra_error_row, is_meta_row, read_result
from .statistics import percentile

ZERO_CALL_SUCCESS = "zero_call_success"
WRITE_WITHOUT_WRITE_CALL = "write_without_write_call"
ANSWER_WITHOUT_PROVENANCE = "answer_without_provenance"
IMPLAUSIBLY_FEW_CALLS = "implausibly_few_calls"

INDICATOR_ORDER = (
    ZERO_CALL_SUCCESS,
    WRITE_WITHOUT_WRITE_CALL,
    ANSWER_WITHOUT_PROVENANCE,
    IMPLAUSIBLY_FEW_CALLS,
)
INDICATOR_LABELS = {
    ZERO_CALL_SUCCESS: "zero-call success",
    WRITE_WITHOUT_WRITE_CALL: "write without a write call",
    ANSWER_WITHOUT_PROVENANCE: "answer without provenance",
    IMPLAUSIBLY_FEW_CALLS: "implausibly few calls",
}

LOW_CALL_MIN_REPETITIONS = 5
LOW_CALL_IQR_MULTIPLIER = 3.0
LOW_CALL_RULE = (
    "among at least 5 successful trace-usable repetitions for the same task, "
    "calls < Q1 - 3×IQR and calls ≤ half the task median"
)
OFF_SURFACE_LIMITATION = (
    "detects off-surface work only when it leaves a trace signature; it cannot detect "
    "an agent that performs the work off-surface and also makes convincing surface calls"
)

_MUTATING_TASK_TAGS = frozenset({"setup", "write"})
_MUTATING_VERBS = frozenset(
    {
        "accept",
        "add",
        "approve",
        "archive",
        "assign",
        "attach",
        "cancel",
        "complete",
        "create",
        "decline",
        "delete",
        "detach",
        "disable",
        "duplicate",
        "enable",
        "link",
        "manage",
        "move",
        "publish",
        "reject",
        "remove",
        "restore",
        "set",
        "start",
        "submit",
        "transfer",
        "unarchive",
        "unlink",
        "update",
        "upload",
    }
)


@dataclass(frozen=True, slots=True)
class OffSurfaceRow:
    """The indicator set attached to one persisted result row."""

    task_id: str
    rep: int
    indicators: frozenset[str]

    @property
    def address(self) -> str:
        return f"{self.task_id}[rep={self.rep}]"


@dataclass(frozen=True, slots=True)
class OffSurfaceMeasurement:
    """Per-row findings and their run-level aggregate views."""

    rows: tuple[OffSurfaceRow, ...] = ()
    mutation_intent_available: bool = True
    """False when no run declared its task tags, so write-intent cannot be judged."""

    @property
    def flagged_rows(self) -> int:
        return len(self.rows)

    @property
    def indicator_hits(self) -> int:
        return sum(len(row.indicators) for row in self.rows)

    def addresses(self, indicator: str) -> tuple[str, ...]:
        return tuple(row.address for row in self.rows if indicator in row.indicators)


def _trace_usable(row: TaskResult) -> bool:
    """Accept authoritative traces and legacy rows predating typed trace integrity."""
    return row.trace_integrity is True or (
        row.trace_integrity is None and row.schema_version < TRACE_INTEGRITY_SCHEMA_VERSION
    )


def _successful_row(row: TaskResult) -> bool:
    return bool(
        row.success and not row.error and not row.skipped and not is_infra_error_row(row) and _trace_usable(row)
    )


def task_requires_mutation(task: Mapping[str, Any] | None) -> bool:
    """Derive mutation intent from persisted tags instead of a task-id allowlist."""
    tags = task.get("tags") if task is not None else ()
    return bool(_MUTATING_TASK_TAGS.intersection(str(tag) for tag in (tags or ())))


def call_plausibly_writes(call: CallRecord) -> bool:
    """Conservatively recognize successful calls with a mutating verb or action."""
    if call.is_error:
        return False
    candidates = (call.action, call.tool)
    for candidate in candidates:
        normalized = str(candidate or "").strip().casefold().replace("-", "_")
        verb = normalized.split("_", 1)[0]
        if verb in _MUTATING_VERBS:
            return True
    return False


def _has_target_provenance(row: TaskResult) -> bool:
    return any(not call.is_error and TARGET_ENTITY_EVIDENCE in (call.observed_sentinels or ()) for call in row.calls)


def _answer_was_correct(row: TaskResult) -> bool:
    # Provenance-enforced read rows already have success=False when their answer was
    # correct but evidence was missing. ``answer_with_provenance`` persists the two
    # facts separately in this stable verifier note.
    return row.success or "answer_correct=true" in row.verify_note.casefold()


def measure_off_surface(
    rows: list[ResultRow],
    *,
    task_catalog: Mapping[str, Mapping[str, Any]] | None = None,
) -> OffSurfaceMeasurement:
    """Compute suspicion indicators without changing row success or completeness.

    ``task_catalog`` carries the run's own task facts. When absent it falls back to the
    metadata persisted in the rows' meta header; a file written before that header existed
    yields no mutation intent, which suppresses one indicator rather than inventing it from
    a catalog that may have changed since.
    """
    if task_catalog is None:
        task_catalog = task_metadata_from_rows(rows)
    mutation_intent_available = bool(task_catalog)
    results: list[TaskResult] = []
    for raw_row in rows:
        if is_meta_row(raw_row):
            continue
        results.append(read_result(raw_row))

    flags_by_index: dict[int, set[str]] = defaultdict(set)
    successful_by_task: dict[str, list[tuple[int, TaskResult]]] = defaultdict(list)
    for index, row in enumerate(results):
        successful = _successful_row(row)
        if successful:
            successful_by_task[row.task_id].append((index, row))
            if row.num_calls == 0 and not row.calls:
                flags_by_index[index].add(ZERO_CALL_SUCCESS)
            if task_requires_mutation(task_catalog.get(row.task_id)) and not any(
                call_plausibly_writes(call) for call in row.calls
            ):
                flags_by_index[index].add(WRITE_WITHOUT_WRITE_CALL)

        if (
            _trace_usable(row)
            and not row.error
            and not row.skipped
            and row.evidence_trace_available
            and _answer_was_correct(row)
            and not _has_target_provenance(row)
        ):
            flags_by_index[index].add(ANSWER_WITHOUT_PROVENANCE)

    for task_rows in successful_by_task.values():
        if len(task_rows) < LOW_CALL_MIN_REPETITIONS:
            continue
        call_counts = [float(row.num_calls) for _, row in task_rows]
        first_quartile = percentile(call_counts, 0.25)
        task_median = percentile(call_counts, 0.5)
        third_quartile = percentile(call_counts, 0.75)
        assert first_quartile is not None and task_median is not None and third_quartile is not None
        lower_outer_fence = first_quartile - LOW_CALL_IQR_MULTIPLIER * (third_quartile - first_quartile)
        for index, row in task_rows:
            if row.num_calls < lower_outer_fence and row.num_calls <= task_median / 2.0:
                flags_by_index[index].add(IMPLAUSIBLY_FEW_CALLS)

    findings = tuple(
        OffSurfaceRow(
            task_id=row.task_id,
            rep=row.rep,
            indicators=frozenset(flags_by_index[index]),
        )
        for index, row in enumerate(results)
        if flags_by_index[index]
    )
    return OffSurfaceMeasurement(rows=findings, mutation_intent_available=mutation_intent_available)


def off_surface_statement(measurement: OffSurfaceMeasurement) -> str:
    """Render an investigation-ready aggregate, including explicit zero results."""
    if measurement.flagged_rows:
        headline = (
            f"off-surface indicators: {measurement.flagged_rows} flagged rows "
            f"({measurement.indicator_hits} indicator hits)"
        )
    else:
        headline = "off-surface indicators: 0"
    lines = [headline]
    for indicator in INDICATOR_ORDER:
        # An indicator that could not be evaluated says so in its own position. A bare zero
        # here would read as "checked and clean", which is the opposite of unknown.
        if indicator == WRITE_WITHOUT_WRITE_CALL and not measurement.mutation_intent_available:
            lines.append(
                f"  {INDICATOR_LABELS[indicator]}: not evaluated — this file declares no task tags, "
                "so mutation intent is unknown"
            )
            continue
        addresses = measurement.addresses(indicator)
        suffix = f" [{', '.join(addresses)}]" if addresses else ""
        rule = f"; rule: {LOW_CALL_RULE}" if indicator == IMPLAUSIBLY_FEW_CALLS else ""
        lines.append(f"  {INDICATOR_LABELS[indicator]}: {len(addresses)}{suffix}{rule}")
    lines.append(f"  limitation: {OFF_SURFACE_LIMITATION}")
    return "\n".join(lines)


__all__ = [
    "ANSWER_WITHOUT_PROVENANCE",
    "IMPLAUSIBLY_FEW_CALLS",
    "INDICATOR_LABELS",
    "INDICATOR_ORDER",
    "LOW_CALL_RULE",
    "OFF_SURFACE_LIMITATION",
    "OffSurfaceMeasurement",
    "OffSurfaceRow",
    "WRITE_WITHOUT_WRITE_CALL",
    "ZERO_CALL_SUCCESS",
    "call_plausibly_writes",
    "measure_off_surface",
    "off_surface_statement",
    "task_requires_mutation",
]
