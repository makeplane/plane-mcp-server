"""Shared fixtures and helpers for eval harness tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def case_params(*cases):
    """Build readable pytest cases from consolidated case helpers."""
    return [pytest.param(case, id=case.__name__.removeprefix("_").replace("_", "-")) for case in cases]


@pytest.fixture(autouse=True)
def _eval_creds(monkeypatch):
    monkeypatch.setenv("EVAL_PLANE_API_KEY", "test-key")
    monkeypatch.setenv("EVAL_PLANE_WORKSPACE_SLUG", "test-ws")
    monkeypatch.delenv("EVAL_PLANE_BASE_URL", raising=False)
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.delenv("REDIS_PORT", raising=False)


def _data_rows(path: Path) -> list[dict]:
    """Parse JSONL skipping meta / non-task lines."""
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("row_type") == "meta" or row.get("task_id") is None:
            continue
        out.append(row)
    return out
