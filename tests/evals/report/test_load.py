"""Offline eval tests for load."""

from __future__ import annotations

import json
from pathlib import Path

from evals.report import (
    dedupe_rows_latest,
    is_infra_error_row,
    load_rows,
    summarize,
)


def test_is_infra_error_row_covers_infrastructure_prefix():
    assert is_infra_error_row({"error_class": "infra_cli"}) is True
    assert is_infra_error_row({"error_class": "task"}) is False


def test_load_behaviours(tmp_path, capsys):
    def test_load_rows_dedupe_latest_wins(tmp_path):
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

    def test_load_rows_no_dedupe_warns_on_duplicate_keys(tmp_path, capsys):
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

    def test_load_rows_skips_meta_and_missing_task_id(tmp_path):
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
        assert len(rows) == 1
        assert rows[0].task_id == "R1"

    _d0 = tmp_path / "test_load_rows_dedupe_latest_wins"
    _d0.mkdir()
    test_load_rows_dedupe_latest_wins(_d0)
    _d1 = tmp_path / "test_load_rows_no_dedupe_warns_on_duplicate_keys"
    _d1.mkdir()
    test_load_rows_no_dedupe_warns_on_duplicate_keys(_d1, capsys)
    _d2 = tmp_path / "test_load_rows_skips_meta_and_missing_task_id"
    _d2.mkdir()
    test_load_rows_skips_meta_and_missing_task_id(_d2)


def test_real_historical_rows_parse_and_report_with_backward_defaults():
    fixture = Path(__file__).parents[2] / "fixtures" / "evals_historical_rows.jsonl"
    rows = load_rows(fixture)

    assert [row.schema_version for row in rows] == [0, 0]
    by_task = {row.task_id: row for row in rows}
    battery4 = by_task["L3"]
    assert battery4.final_text == ""
    assert battery4.result_tokens_estimated is None
    assert battery4.alternate_calls is None
    assert battery4.calls[0].result_tokens is None
    assert battery4.calls[0].action == "create"

    battery5 = by_task["R2"]
    assert battery5.final_text.endswith("\n4")
    assert battery5.result_tokens_estimated is True
    assert [call.result_tokens for call in battery5.calls] == [315, 64]

    summary = summarize(rows)
    assert summary.tasks["L3"].success == "1/1"
    assert summary.tasks["L3"].med_calls == 1
    assert summary.tasks["L3"].result_tokens_mode == "unavailable"
    assert summary.tasks["R2"].success == "1/1"
    assert summary.tasks["R2"].med_calls == 2
    assert summary.tasks["R2"].result_tokens_mode == "estimated"


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
