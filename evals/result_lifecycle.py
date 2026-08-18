"""Shared terminal/retryable classification for persisted evaluation results."""

from __future__ import annotations

from typing import Any

from evals.core.results import TaskResult
from evals.skip_taxonomy import is_expected_environment_capability_skip


def is_terminal_result(row: TaskResult | dict[str, Any]) -> bool:
    """Return whether a result is authoritative rather than eligible for retry."""
    result = row if isinstance(row, TaskResult) else TaskResult.from_row(row)
    error_class = result.error_class
    if isinstance(error_class, str) and error_class.startswith("infra_"):
        return False
    if result.error is not None or result.cleanup_error is not None:
        return False
    if result.skipped is not None:
        return is_expected_environment_capability_skip(result.skipped, task_id=result.task_id)
    return True


__all__ = ["is_terminal_result"]
