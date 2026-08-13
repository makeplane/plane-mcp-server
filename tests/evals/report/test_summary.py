"""Offline eval tests for summary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals import report as report_mod
from evals.report import (
    is_infra_error_row,
    load_rows,
    summarize,
    wilson_interval,
)


def test_summarize_excludes_infra_errors_from_success():
    rows = [
        {"task_id": "R1", "success": True, "num_calls": 2, "calls": [], "error": None},
        {
            "task_id": "R1",
            "success": False,
            "num_calls": 0,
            "calls": [],
            "error": "HttpError: 409",
            "error_class": "infra_seed",
        },
        {
            "task_id": "R1",
            "success": False,
            "num_calls": 0,
            "calls": [],
            "error": "timeout after 120s",
            "error_class": "infra_cli",
        },
        {"task_id": "R1", "success": False, "num_calls": 3, "calls": [], "error": None},
    ]
    summary = summarize(rows)
    assert summary.infra_errors == 2
    assert summary.tasks["R1"].n == 2  # only non-infra, non-error rows
    assert summary.tasks["R1"].k == 1
    assert summary.tasks["R1"].success == "1/2"
    assert summary.tasks["R1"].infra_err == 2
    assert is_infra_error_row(rows[1]) is True
    assert is_infra_error_row(rows[0]) is False


def test_wilson_interval_bounds():
    lo, hi = wilson_interval(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-4)
    assert hi == pytest.approx(0.7634, abs=1e-4)
    lo0, hi0 = wilson_interval(0, 10)
    assert lo0 == 0.0
    assert hi0 == pytest.approx(0.27754, abs=1e-4)
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_summarize_aggregate_wilson_and_call_variance():
    rows = [
        {"task_id": "R1", "rep": 0, "success": True, "num_calls": 2, "calls": []},
        {"task_id": "R1", "rep": 1, "success": True, "num_calls": 4, "calls": []},
        {"task_id": "R1", "rep": 2, "success": False, "num_calls": 6, "calls": []},
        {"task_id": "R2", "rep": 0, "success": True, "num_calls": 1, "calls": []},
    ]
    s = summarize(rows)
    assert s.tasks["R1"].n == 3
    assert s.tasks["R1"].k == 2
    assert s.tasks["R1"].calls_min == 2.0
    assert s.tasks["R1"].calls_max == 6.0
    assert s.tasks["R1"].med_calls == 4.0
    assert s.tasks["R1"].unstable is True
    assert s.tasks["R2"].unstable is False
    assert s.aggregate_k == 3
    assert s.aggregate_n == 4
    assert s.multi_rep is True
    assert s.unstable_task_ids == ["R1"]
    assert s.unstable_tasks == 1
    assert 0.0 <= s.aggregate_wilson_lo <= s.aggregate_wilson_hi <= 1.0


def test_multi_rep_synthetic_file_reports_wilson_unstable_and_noise_floor(tmp_path: Path, capsys):
    path = tmp_path / "multi.jsonl"
    outcomes = {
        "R1": [True, True, True],
        "R2": [True, False, True],
        "R3": [False, False, False],
    }
    rows = [
        {
            "task_id": task_id,
            "rep": rep,
            "label": "local",
            "success": success,
            "num_calls": rep + 1,
            "calls": [],
        }
        for task_id, task_outcomes in outcomes.items()
        for rep, success in enumerate(task_outcomes)
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    loaded = load_rows(path)
    summary = summarize(loaded)

    assert len(loaded) == 9  # distinct rep keys are not deduped away
    assert summary.tasks["R1"].success == "3/3"
    assert summary.tasks["R1"].unstable is False
    assert summary.tasks["R2"].success == "2/3"
    assert summary.tasks["R2"].wilson_lo == pytest.approx(0.2077, abs=1e-4)
    assert summary.tasks["R2"].wilson_hi == pytest.approx(0.9385, abs=1e-4)
    assert summary.tasks["R2"].unstable is True
    assert summary.tasks["R3"].success == "0/3"
    assert summary.tasks["R3"].unstable is False
    assert summary.unstable_task_ids == ["R2"]

    report_mod.print_table(summary, "Summary: multi.jsonl")
    output = capsys.readouterr().out
    assert "unstable" in output
    r2_line = next(line for line in output.splitlines() if line.startswith("R2"))
    assert "2/3" in r2_line
    assert "[0.21,0.94]" in r2_line
    assert "YES" in r2_line
    assert "measured noise floor: 1 task flipped at least once" in output
    assert "minimum meaningful difference: 2 tasks" in output


def test_single_rep_summary_rendering_is_unchanged(capsys):
    rows = [{"task_id": "R1", "rep": 0, "label": "local", "success": True, "num_calls": 2, "calls": []}]

    report_mod.print_table(summarize(rows), "Summary: sample.jsonl")

    assert capsys.readouterr().out == (
        "Summary: sample.jsonl\n"
        "aggregate success: 1/1 (100.0%) Wilson95 [0.21,1.00]\n"
        "task     n  success         wilson95 med_calls  opt         IQR  mispick  err capped h_err i_err  "
        "med_rtok  p95_rtok med_cum_in\n"
        "-------------------------------------------------------------------------------------------------------------------------------\n"
        "R1       1      1/1      [0.21,1.00]       2.0    1     2.0-2.0    0.0%    0      0     0     0         "
        "-         -          0\n"
    )
