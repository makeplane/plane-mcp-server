"""JSONL row loading and classification for evaluation reports."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Literal

from evals.results import TaskResult

DedupeMode = Literal["latest", "none"]
ResultRow = TaskResult | dict[str, Any]


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
    """Load JSONL data rows (skip meta / missing task_id).

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
                    f"warning: {path}:{line_number}: skipping invalid JSON ({exc})",
                    file=sys.stderr,
                )
                continue
            if not isinstance(row, dict):
                continue
            result = TaskResult.from_row(row)
            if is_meta_row(result):
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
                f"(--no-dedupe keeps all rows; bare --out reuse double-counts)",
                file=sys.stderr,
            )
        else:
            seen_keys.add(key)
    return rows
