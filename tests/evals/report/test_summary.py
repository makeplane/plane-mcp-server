"""Offline eval tests for summary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals import report as report_mod
from evals.report import (
    completeness_statement,
    execution_coverage_statement,
    format_tool_distribution,
    format_tool_variability,
    is_infra_error_row,
    load_rows,
    summarize,
    wilson_interval,
)
from tests.evals.conftest import case_params


def test_completeness_is_independent_from_success_rate():
    complete = summarize(
        [
            {"task_id": "R1", "success": True, "calls": []},
            {"task_id": "R2", "success": False, "calls": []},
            {"task_id": "L4", "skipped": "env:plan-gated:customers", "calls": []},
        ],
        expected_rows=3,
    )
    assert complete.aggregate_k == 1
    assert complete.aggregate_n == 2
    assert complete.completed_rows == 3
    assert complete.expected_skips == 1
    assert complete.complete is True
    assert completeness_statement(complete).startswith("RUN COMPLETE:")
    assert execution_coverage_statement(complete) == (
        "EXECUTION COVERAGE: 2/3 rows evaluated (66.7%); skipped tasks=[L4 (env:plan-gated:customers)]"
    )

    missing_worker = summarize(
        [{"task_id": "L2", "skipped": "env:no-activity-worker", "calls": []}],
        expected_rows=1,
    )
    assert missing_worker.aggregate_n == 0
    assert missing_worker.completed_rows == 1
    assert missing_worker.expected_skips == 1
    assert missing_worker.expected_skip_reasons == {"no-activity-worker": 1}
    assert missing_worker.complete is True
    assert completeness_statement(missing_worker).startswith("RUN COMPLETE:")

    verifier_crash = summarize(
        [
            {"task_id": "R1", "success": True, "calls": []},
            {"task_id": "W6", "error": "RuntimeError: verifier broke", "error_class": "task", "calls": []},
        ],
        expected_rows=2,
    )
    assert verifier_crash.aggregate_k == 1
    assert verifier_crash.aggregate_n == 1
    assert verifier_crash.harness_errors == 1
    assert verifier_crash.complete is False
    assert completeness_statement(verifier_crash).startswith("RUN INCOMPLETE:")

    collisions = summarize(
        [
            {"task_id": "R1", "skipped": "env:fixture-collision:customers:Acme", "calls": []},
            {"task_id": "R2", "skipped": "env:fixture-collision:release_tags:eval-rc1", "calls": []},
        ],
        expected_rows=2,
    )
    assert collisions.completed_rows == 0
    assert collisions.unexpected_skips == 2
    assert collisions.unexpected_skip_reasons == {"fixture-collision": 2}
    assert collisions.complete is False

    unknown = summarize(
        [{"task_id": "L2", "skipped": "env:new-reason", "calls": []}],
        expected_rows=1,
    )
    assert unknown.unexpected_skips == 1
    assert unknown.unexpected_skip_reasons == {"env:new-reason": 1}
    assert unknown.complete is False

    cleanup = summarize(
        [{"task_id": "R1", "success": True, "cleanup_error": "RuntimeError: delete failed", "calls": []}],
        expected_rows=1,
    )
    assert cleanup.aggregate_k == cleanup.aggregate_n == 1
    assert cleanup.cleanup_errors == 1
    assert cleanup.complete is False


def test_result_pair_mismatch_preserves_outcome_but_excludes_trace_metrics():
    summary = summarize(
        [
            {
                "task_id": "R1",
                "success": True,
                "trace_integrity": False,
                "trace_integrity_reason": "result_pair_mismatch",
                "result_pair_mismatch": True,
                "num_calls": 99,
                "calls": [{"tool": "untrustworthy", "result_tokens": 200}],
            }
        ],
        expected_rows=1,
    )

    assert summary.complete is False
    assert summary.trace_invalid_rows == 1
    assert summary.aggregate_k == summary.aggregate_n == 1
    assert summary.tasks["R1"].med_calls is None
    assert summary.tasks["R1"].tool_reps == 0
    assert summary.tasks["R1"].med_result_tokens is None


def _summarize_excludes_infra_errors_from_success():
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
    assert summary.tasks["R1"].failed_tool_reps == 3
    assert "failed excluded=3" in format_tool_distribution(summary.tasks["R1"])
    assert is_infra_error_row(rows[1]) is True
    assert is_infra_error_row(rows[0]) is False


def _summarize_aggregate_wilson_and_call_variance():
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
    assert s.tasks["R1"].calls_q1 == 2.5
    assert s.tasks["R1"].med_calls == 3.0
    assert s.tasks["R1"].calls_q3 == 3.5
    assert s.tasks["R1"].calls_max == 4.0
    assert s.tasks["R1"].unstable is True
    assert s.tasks["R2"].unstable is False
    assert s.aggregate_k == 3
    assert s.aggregate_n == 4
    assert s.multi_rep is True
    assert s.unstable_task_ids == ["R1"]
    assert s.unstable_tasks == 1
    assert 0.0 <= s.aggregate_wilson_lo <= s.aggregate_wilson_hi <= 1.0


def _tool_distribution_uses_successful_repetitions():
    rows = [
        {
            "task_id": "R1",
            "rep": 0,
            "success": True,
            "num_calls": 3,
            "calls": [{"tool": "a"}, {"tool": "a"}, {"tool": "b"}],
        },
        {
            "task_id": "R1",
            "rep": 1,
            "success": True,
            "num_calls": 2,
            "calls": [{"tool": "a"}, {"tool": "c"}],
        },
        {
            "task_id": "R1",
            "rep": 2,
            "success": False,
            "num_calls": 1,
            "calls": [{"tool": "failed_only"}],
        },
        {
            "task_id": "R2",
            "rep": 0,
            "success": True,
            "num_calls": 1,
            "calls": [{"tool": "one_rep"}],
        },
        {
            "task_id": "R3",
            "rep": 0,
            "success": False,
            "num_calls": 1,
            "calls": [{"tool": "failed_only"}],
        },
        {"task_id": "R4", "rep": 0, "skipped": "unavailable", "calls": []},
    ]

    summary = summarize(rows)
    r1 = summary.tasks["R1"]
    assert r1.calls_min == 2.0  # failed one-call repetition is not an observed successful floor
    assert r1.calls_q1 == 2.25
    assert r1.med_calls == 2.5
    assert r1.calls_q3 == 2.75
    assert r1.calls_max == 3.0
    assert r1.calls_min <= r1.calls_q1 <= r1.med_calls <= r1.calls_q3 <= r1.calls_max
    assert r1.tool_reps == 2
    assert r1.failed_tool_reps == 1
    assert r1.tool_rep_frequency == {"a": 1.0, "b": 0.5, "c": 0.5}
    assert r1.tool_call_counts == {"a": 3, "b": 1, "c": 1}
    assert "failed_only" not in r1.tool_call_counts
    assert r1.variable_tool_names == ["b", "c"]
    assert summary.variable_tool_tasks == 1
    assert summary.total_tasks == 4
    assert format_tool_variability(summary) == "1/4 tasks"
    r1_distribution = format_tool_distribution(r1)
    assert "success-only n=2; failed excluded=1" in r1_distribution
    assert "core:a(3c)" in r1_distribution
    assert "variable:b=50%(1c),c=50%(1c)" in r1_distribution

    r2 = summary.tasks["R2"]
    assert r2.tool_reps == 1
    assert r2.failed_tool_reps == 0
    assert r2.tool_rep_frequency == {}
    assert r2.tool_call_counts == {"one_rep": 1}
    assert format_tool_distribution(r2) == "success-only n=1; failed excluded=0; frequency=—"

    r3 = summary.tasks["R3"]
    assert r3.tool_reps == 0
    assert r3.failed_tool_reps == 1
    assert r3.tool_rep_frequency == {}
    assert r3.tool_call_counts == {}
    assert format_tool_distribution(r3) == "success-only n=0; failed excluded=1; frequency=—"


@pytest.mark.parametrize(
    "case",
    case_params(
        _summarize_excludes_infra_errors_from_success,
        _summarize_aggregate_wilson_and_call_variance,
        _tool_distribution_uses_successful_repetitions,
    ),
)
def test_summarize_behaviours(case):
    case()


def test_wilson_interval_bounds():
    lo, hi = wilson_interval(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-4)
    assert hi == pytest.approx(0.7634, abs=1e-4)
    lo0, hi0 = wilson_interval(0, 10)
    assert lo0 == 0.0
    assert hi0 == pytest.approx(0.27754, abs=1e-4)
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_headline_interval_bootstraps_35_task_clusters_instead_of_175_repetitions():
    rows = [
        {"task_id": f"T{task_index:02d}", "rep": rep, "success": task_index < 17, "calls": []}
        for task_index in range(35)
        for rep in range(5)
    ]

    summary = summarize(rows)

    assert (summary.aggregate_k, summary.aggregate_n) == (85, 175)
    assert summary.aggregate_wilson_lo == pytest.approx(0.4127693534)
    assert summary.aggregate_wilson_hi == pytest.approx(0.5592729455)
    assert summary.task_mean_success == pytest.approx(17 / 35)
    assert summary.task_cluster_lo == pytest.approx(0.3142857143)
    assert summary.task_cluster_hi == pytest.approx(0.6571428571)
    assert summary.task_cluster_hi - summary.task_cluster_lo > (
        summary.aggregate_wilson_hi - summary.aggregate_wilson_lo
    )


def test_multi_rep_synthetic_file_reports_wilson_and_instability_without_noise_claim(tmp_path: Path, capsys):
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
    assert "noise floor" not in output


def test_single_rep_summary_renders_tool_distribution_unavailable(capsys):
    rows = [{"task_id": "R1", "rep": 0, "label": "local", "success": True, "num_calls": 2, "calls": []}]

    report_mod.print_table(summarize(rows), "Summary: sample.jsonl")

    assert capsys.readouterr().out == (
        "Summary: sample.jsonl\n"
        "task-cluster success: 100.0% across 1 tasks cluster-bootstrap95 [1.00,1.00]\n"
        "pooled repetition success: 1/1 (100.0%) Wilson95 [0.21,1.00]\n"
        "EXECUTION COVERAGE: 1/1 rows evaluated (100.0%)\n"
        "RUN COMPLETE: 1/1 rows completed\n"
        "tool variability: —\n"
        "task     n  success         wilson95 success_calls_med success_calls_min success_calls_q1-q3  "
        "err capped h_err i_err  "
        "med_rtok  p95_rtok med_cum_in  tool distribution\n"
        "----------------------------------------------------------------------------------------------------------------------------------------------------------------------\n"
        "R1       1      1/1      [0.21,1.00]               2.0               2.0             2.0-2.0    0      0 "
        "    0     0         -         -          0  success-only n=1; failed excluded=0; frequency=—\n"
    )
