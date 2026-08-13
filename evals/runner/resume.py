"""Resume decisions for evaluation result files."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from evals.results import TaskResult

from .meta import is_meta_or_non_task_row


def should_skip_resume_row(row: TaskResult | dict[str, Any]) -> bool:
    """Return True if a prior row is a completed result that resume should skip.

    Re-run when ``error_class`` starts with ``infra_`` or when ``error`` is non-null.
    Rows with ``skipped`` set are treated as complete and are not retried (intentional:
    surface/plan skips are stable outcomes, not infra failures).
    Pure function — unit-tested without the live battery.
    """
    result = row if isinstance(row, TaskResult) else TaskResult.from_row(row)
    error_class = result.error_class
    if isinstance(error_class, str) and error_class.startswith("infra_"):
        return False
    if result.error is not None:
        return False
    return True


def _resume_field_mismatch(
    row: dict[str, Any],
    *,
    field: str,
    expected: str | None,
) -> str | None:
    """Return an error message if row[field] is present and disagrees with expected."""
    if expected is None:
        return None
    raw = row.get(field)
    if raw is None or raw == "":
        return None  # back-compat: older rows without the key pass
    # Surface/driver/provider compare case-insensitively; battery/model are exact.
    if field in ("surface", "driver", "provider"):
        received, wanted = str(raw).strip().lower(), expected.strip().lower()
    else:
        received, wanted = str(raw).strip(), expected.strip()
    if received != wanted:
        return f"error: --resume file {field} {raw!r} does not match current {field} {expected!r}"
    return None


def load_resume_skip_keys(
    path: Path,
    *,
    surface: str,
    battery: str | None = None,
    model: str | None = None,
    driver: str | None = None,
    provider: str | None = None,
) -> tuple[set[tuple[str, int]], int, int]:
    """Load existing JSONL rows and decide which (task_id, rep) pairs to skip.

    Returns ``(skip_keys, n_skip, n_retry)`` where ``n_retry = len(seen - skip_keys)``
    (keys that still need a re-run). Raises ``SystemExit`` when a row's surface /
    battery / model / driver / provider disagrees with the current run (missing keys pass for
    back-compat). Meta lines (``row_type=meta`` or no task_id) are mismatch-checked
    but not counted as task rows. Truncated/invalid JSON lines are warned and skipped.
    """
    if not path.is_file():
        return set(), 0, 0
    skip_keys: set[tuple[str, int]] = set()
    seen: set[tuple[str, int]] = set()
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                print(
                    f"warning: --resume {path}:{line_number}: skipping invalid JSON ({exc})",
                    file=sys.stderr,
                )
                continue
            if not isinstance(row, dict):
                continue
            for field, expected in (
                ("surface", surface),
                ("battery", battery),
                ("driver", driver),
                ("provider", provider),
            ):
                message = _resume_field_mismatch(row, field=field, expected=expected)
                if message:
                    raise SystemExit(message)
            # New tier-aware rows identify the resolved model explicitly. Older
            # API rows use requested_model for the resolved ID, while oldest rows
            # only have model (which may be provider-reported).
            model_row = dict(row)
            if model_row.get("resolved_model"):
                model_row["model"] = model_row["resolved_model"]
            elif model_row.get("requested_model"):
                model_row["model"] = model_row["requested_model"]
            message = _resume_field_mismatch(model_row, field="model", expected=model)
            if message:
                raise SystemExit(message)
            # Meta / header rows: checked above, not part of resume key set.
            if is_meta_or_non_task_row(row):
                continue
            result = TaskResult.from_row(row)
            if not result.task_id:
                continue
            key = (result.task_id, result.rep)
            seen.add(key)
            if should_skip_resume_row(result):
                skip_keys.add(key)
            else:
                # Prior infra/error row: do not skip (will re-run). Drop any earlier skip.
                skip_keys.discard(key)
    retry_count = len(seen - skip_keys)
    return skip_keys, len(skip_keys), retry_count
