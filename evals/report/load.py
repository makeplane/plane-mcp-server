"""JSONL row loading and error handling for evaluation reports."""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from evals.result_lifecycle import is_terminal_result
from evals.results import TaskResult

DedupeMode = Literal["latest", "none"]
ResultRow = TaskResult | dict[str, Any]


@dataclass(frozen=True, slots=True)
class RunExpectation:
    """Exact task/repetition universe declared by a result-file meta header."""

    task_ids: tuple[str, ...]
    reps: int
    label: str | None = None

    @property
    def expected_rows(self) -> int:
        return len(self.task_ids) * self.reps

    @property
    def keys(self) -> frozenset[tuple[str, int]]:
        return frozenset((task_id, rep) for task_id in self.task_ids for rep in range(self.reps))


@dataclass(frozen=True, slots=True)
class RunKeyValidation:
    """Raw-row comparison against one exact run expectation."""

    expectation: RunExpectation
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]

    @property
    def exact(self) -> bool:
        return not self.missing and not self.unexpected


def _first_meta_row(path: Path) -> dict[str, Any] | None:
    with path.open(encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or row.get("row_type") != "meta":
                return None
            return row
    return None


def load_run_expectation(path: Path) -> RunExpectation | None:
    """Reconstruct the exact expected ``(task_id, rep)`` set when declared."""
    row = _first_meta_row(path)
    if row is None:
        return None
    raw_task_ids = row.get("expected_task_ids")
    raw_reps = row.get("expected_reps")
    if raw_task_ids is None and raw_reps is None:
        return None
    if not isinstance(raw_task_ids, list) or raw_reps is None:
        raise ValueError(f"{path}: meta must declare expected_task_ids and expected_reps together")
    task_ids = tuple(str(task_id) for task_id in raw_task_ids)
    try:
        reps = int(raw_reps)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path}: expected_reps must be a positive integer") from exc
    if not task_ids or any(not task_id for task_id in task_ids):
        raise ValueError(f"{path}: expected_task_ids must be a non-empty list of non-empty ids")
    if len(set(task_ids)) != len(task_ids):
        raise ValueError(f"{path}: expected_task_ids contains duplicates")
    if reps < 1:
        raise ValueError(f"{path}: expected_reps must be a positive integer")
    expectation = RunExpectation(task_ids=task_ids, reps=reps, label=str(row["label"]) if row.get("label") else None)
    declared_rows = row.get("expected_rows")
    if declared_rows is not None and int(declared_rows) != expectation.expected_rows:
        raise ValueError(
            f"{path}: expected_rows={declared_rows} disagrees with exact expectation={expectation.expected_rows}"
        )
    return expectation


def _format_run_key(key: tuple[str, int], count: int) -> str:
    rendered = f"{key[0]}[rep={key[1]}]"
    return f"{rendered} x{count}" if count > 1 else rendered


def validate_run_keys(rows: list[ResultRow], expectation: RunExpectation) -> RunKeyValidation:
    """Validate exact keys while allowing append-only retry history.

    For an expected key, every occurrence except the last must be retryable. The final
    occurrence is authoritative. A prior terminal occurrence is a genuine duplicate and
    remains visible as an unexpected key.
    """
    expected = Counter({key: 1 for key in expectation.keys})
    histories: dict[tuple[str, int, str | None], list[TaskResult]] = defaultdict(list)
    for raw_row in rows:
        row = read_result(raw_row)
        if not is_meta_row(row):
            row_label = row.label if expectation.label is not None else None
            histories[(row.task_id, row.rep, row_label)].append(row)
    expected_history_keys = {(task_id, rep, expectation.label) for task_id, rep in expectation.keys}
    observed = Counter(
        {(task_id, rep): len(histories.get((task_id, rep, expectation.label), ())) for task_id, rep in expectation.keys}
    )
    missing_counts = expected - observed
    unexpected_counts: Counter[tuple[str, int]] = Counter()
    for history_key, history in histories.items():
        key = history_key[:2]
        if history_key not in expected_history_keys:
            unexpected_counts[key] += len(history)
            continue
        terminal_predecessors = sum(is_terminal_result(row) for row in history[:-1])
        if terminal_predecessors:
            unexpected_counts[key] += terminal_predecessors
    return RunKeyValidation(
        expectation=expectation,
        missing=tuple(_format_run_key(key, count) for key, count in sorted(missing_counts.items())),
        unexpected=tuple(_format_run_key(key, count) for key, count in sorted(unexpected_counts.items())),
    )


def _invalid_result_row(path: Path, line_number: int, reason: str) -> TaskResult:
    """Represent an unreadable persisted row as a completeness-visible harness error."""
    return TaskResult(
        task_id=f"<invalid-result-line-{line_number}>",
        rep=line_number,
        label=path.stem,
        success=False,
        error=f"{path}:{line_number}: {reason}",
        error_class="harness_report_load",
    )


def load_run_expected_rows(path: Path) -> int | None:
    """Read the declared run size from the JSONL meta header, when available."""
    if expectation := load_run_expectation(path):
        return expectation.expected_rows
    row = _first_meta_row(path)
    if row is None:
        return None
    value = row.get("expected_rows")
    return int(value) if value is not None else None


def read_result(row: ResultRow) -> TaskResult:
    """Return one row as the declared persisted result type."""
    return row if isinstance(row, TaskResult) else TaskResult.from_row(row)


def is_meta_row(row: ResultRow) -> bool:
    """True for run-header meta lines (or any row without a task_id)."""
    result = read_result(row)
    return result.row_type == "meta" or not result.task_id


def is_infra_error_row(row: ResultRow) -> bool:
    """True when a row failed for infrastructure reasons, not task verification.

    Any ``error_class`` starting with ``infra_`` (``infra_seed``, ``infra_cli``,
    ``infra_api``, …) is excluded from success-rate denominators.
    """
    error_class = read_result(row).error_class
    return isinstance(error_class, str) and error_class.startswith("infra_")


def is_unlaunched_row(row: ResultRow) -> bool:
    """True when no agent ran for this row, so it observed no tool manifest.

    A seed failure and a seed-time skip both end before the agent starts. Neither can
    carry a manifest fingerprint, so neither can be held to one — demanding it refused
    perfectly sound comparisons: one plan-gated task made every report command exit.
    """
    result = read_result(row)
    return is_infra_error_row(row) or bool(result.skipped)


def dedupe_rows_latest(rows: list[ResultRow]) -> list[TaskResult]:
    """Keep only the last row per (task_id, rep, label); preserve key insertion order."""
    latest: dict[tuple[str, int, str], TaskResult] = {}
    order: list[tuple[str, int, str]] = []
    for raw_row in rows:
        row = read_result(raw_row)
        key = (row.task_id, row.rep, row.label)
        if key not in latest:
            order.append(key)
        latest[key] = row
    return [latest[key] for key in order]


def load_rows(path: Path, *, dedupe: DedupeMode = "latest") -> list[TaskResult]:
    """Load JSONL data rows, representing malformed data as harness errors.

    Default ``dedupe="latest"`` keeps the last row per (task_id, rep, label)
    so resume appends do not double-count. Pass ``dedupe="none"`` for forensics.
    """
    rows: list[TaskResult] = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                print(
                    f"warning: {path}:{line_number}: recording invalid JSON as a harness error ({exc})",
                    file=sys.stderr,
                )
                rows.append(_invalid_result_row(path, line_number, f"invalid JSON ({exc})"))
                continue
            if not isinstance(row, dict):
                print(
                    f"warning: {path}:{line_number}: recording non-object JSON as a harness error",
                    file=sys.stderr,
                )
                rows.append(_invalid_result_row(path, line_number, "result row is not a JSON object"))
                continue
            if row.get("row_type") == "meta":
                continue
            try:
                result = TaskResult.from_row(row)
            except Exception as exc:
                print(
                    f"warning: {path}:{line_number}: recording invalid result object as a harness error ({exc})",
                    file=sys.stderr,
                )
                rows.append(_invalid_result_row(path, line_number, f"invalid result object ({exc})"))
                continue
            if not result.task_id:
                print(
                    f"warning: {path}:{line_number}: recording result without task_id as a harness error",
                    file=sys.stderr,
                )
                rows.append(_invalid_result_row(path, line_number, "result row has no task_id"))
                continue
            rows.append(result)
    if dedupe == "latest":
        return dedupe_rows_latest(rows)
    # Forensics: warn on duplicates but keep all.
    seen_keys: set[tuple[str, int, str]] = set()
    for row in rows:
        key = (row.task_id, row.rep, row.label)
        if key in seen_keys:
            print(
                f"warning: {path}: duplicate (task_id, rep, label)={key} "
                "(--no-dedupe keeps append-only history; validator distinguishes retries from duplicates)",
                file=sys.stderr,
            )
        else:
            seen_keys.add(key)
    return rows
