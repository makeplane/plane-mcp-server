"""Run metadata and repository provenance for evaluation results."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.results import RESULT_SCHEMA_VERSION


def read_git_revision() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL,
                cwd=Path(__file__).resolve().parent.parent.parent,
            )
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def is_meta_or_non_task_row(row: dict[str, Any]) -> bool:
    """True for run-header meta lines or any row without a task_id."""
    if row.get("row_type") == "meta":
        return True
    return row.get("task_id") is None


def make_run_meta_row(
    *,
    run_id: str,
    label: str,
    server: str,
    battery: str,
    model: str | None,
    driver: str,
    git_sha: str,
    provider: str | None = None,
    requested_model: str | None = None,
    requested_tier: str | None = None,
    resolved_model: str | None = None,
    expected_rows: int | None = None,
    ts: str | None = None,
) -> dict[str, Any]:
    """Build the single first-line meta record for a new output JSONL."""
    row = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "row_type": "meta",
        "run_id": run_id,
        "label": label,
        "server": server,
        "battery": battery,
        "model": model,
        "requested_model": requested_model if requested_model is not None else model,
        "requested_tier": requested_tier,
        "resolved_model": resolved_model if resolved_model is not None else model,
        "driver": driver,
        "provider": provider,
        "git_sha": git_sha,
        "ts": ts or datetime.now(timezone.utc).isoformat(),
    }
    if expected_rows is not None:
        row["expected_rows"] = expected_rows
    return row


def maybe_write_run_meta(path: Path, meta: dict[str, Any]) -> bool:
    """Write meta as the first line when the file is missing or empty. Returns True if written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size > 0:
        return False
    with path.open("w", encoding="utf-8") as file:
        file.write(json.dumps(meta, default=str) + "\n")
    return True
