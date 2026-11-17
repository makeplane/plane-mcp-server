"""Offline eval tests for load."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.report import (
    dedupe_rows_latest,
    is_infra_error_row,
    load_rows,
    summarize,
)
from tests.evals.conftest import case_params


def test_is_infra_error_row_covers_infrastructure_prefix():
    assert is_infra_error_row({"error_class": "infra_cli"}) is True
    assert is_infra_error_row({"error_class": "task"}) is False


def _load_rows_dedupe_latest_wins(tmp_path, _capsys):
    p = tmp_path / "dup.jsonl"
    rows = [
        {"task_id": "R1", "rep": 0, "label": "local", "success": True, "num_calls": 1},
        {"task_id": "R1", "rep": 0, "label": "local", "success": False, "num_calls": 9},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    loaded = load_rows(p)  # default dedupe=latest
    assert len(loaded) == 1
    assert loaded[0].num_calls == 9
    assert loaded[0].success is False


def _load_rows_no_dedupe_warns_on_duplicate_keys(tmp_path, capsys):
    p = tmp_path / "dup.jsonl"
    rows = [
        {"task_id": "R1", "rep": 0, "label": "local", "success": True},
        {"task_id": "R1", "rep": 0, "label": "local", "success": False},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    loaded = load_rows(p, dedupe="none")
    assert len(loaded) == 2
    err = capsys.readouterr().err
    assert "duplicate" in err
    assert "R1" in err


def _load_rows_skips_meta_and_surfaces_missing_task_id(tmp_path, capsys):
    p = tmp_path / "r.jsonl"
    lines = [
        json.dumps(
            {
                "row_type": "meta",
                "run_id": "abc",
                "label": "candidate",
                "battery": "deadbeef0001",
                "model": "sonnet",
                "driver": "claude-cli",
                "git_sha": "x",
                "ts": "t",
            }
        ),
        json.dumps({"label": "candidate", "rep": 0, "success": True}),  # no task_id
        json.dumps({"task_id": "R1", "rep": 0, "label": "candidate", "success": True, "num_calls": 2}),
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rows = load_rows(p)
    assert len(rows) == 2
    assert rows[0].error_class == "harness_report_load"
    assert rows[1].task_id == "R1"
    assert "recording result without task_id as a harness error" in capsys.readouterr().err


@pytest.mark.parametrize(
    "case",
    case_params(
        _load_rows_dedupe_latest_wins,
        _load_rows_no_dedupe_warns_on_duplicate_keys,
        _load_rows_skips_meta_and_surfaces_missing_task_id,
    ),
)
def test_load_behaviours(case, tmp_path, capsys):
    case(tmp_path, capsys)


def test_schema_v0_rows_parse_with_unknown_trace_integrity():
    """Synthetic schema-0 rows keep data but do not claim verified traces."""
    fixture = Path(__file__).parents[2] / "fixtures" / "evals_schema_v0_rows.jsonl"
    rows = load_rows(fixture)

    assert [row.schema_version for row in rows] == [0, 0]
    by_task = {row.task_id: row for row in rows}
    release_row = by_task["L3"]
    assert release_row.final_text == ""
    assert release_row.result_tokens_estimated is None
    assert release_row.calls[0].result_tokens is None
    assert release_row.calls[0].action == "create"

    count_row = by_task["R2"]
    assert count_row.final_text.endswith("\n4")
    assert count_row.result_tokens_estimated is True
    assert [call.result_tokens for call in count_row.calls] == [315, 64]
    assert release_row.trace_integrity is None
    assert count_row.trace_integrity is None

    summary = summarize(rows)
    assert summary.tasks["L3"].success == "1/1"
    assert summary.tasks["L3"].med_calls is None
    assert summary.tasks["L3"].result_tokens_mode == "unavailable"
    assert summary.tasks["R2"].success == "1/1"
    assert summary.tasks["R2"].med_calls is None
    assert summary.tasks["R2"].result_tokens_mode == "unavailable"


def test_dedupe_rows_latest_pure():
    rows = [
        {"task_id": "R1", "rep": 0, "label": "local", "num_calls": 1},
        {"task_id": "R1", "rep": 0, "label": "local", "num_calls": 5},
        {"task_id": "R2", "rep": 0, "label": "local", "num_calls": 3},
    ]
    out = dedupe_rows_latest(rows)
    assert len(out) == 2
    by_id = {r.task_id: r for r in out}
    assert by_id["R1"].num_calls == 5
    assert by_id["R2"].num_calls == 3


def test_malformed_rows_surface_as_completeness_errors(tmp_path, capsys):
    path = tmp_path / "malformed.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"task_id": "R1", "success": True, "calls": []}),
                "{not-json",
                json.dumps(["not", "an", "object"]),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rows = load_rows(path)
    summary = summarize(rows, expected_rows=3)

    assert len(rows) == 3
    assert summary.aggregate_n == 1
    assert summary.harness_errors == 2
    assert summary.complete is False
    assert {row.error_class for row in rows if row.error} == {"harness_report_load"}
    warnings = capsys.readouterr().err
    assert "recording invalid JSON as a harness error" in warnings
    assert "recording non-object JSON as a harness error" in warnings
