"""Offline eval tests for paired comparisons."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from evals.report import (
    ab_compare,
    paired_bootstrap_mean_ci,
    paired_permutation_pvalue,
    print_ab_report,
)


def test_paired_permutation_retains_ties_and_uses_delta_magnitudes():
    assert paired_permutation_pvalue([]) is None
    assert paired_permutation_pvalue([0.0, 0.0]) == 1.0

    # A sign test sees 6 positive versus 10 negative deltas and cannot detect
    # the coherent large-magnitude shift. The paired randomization distribution
    # uses those magnitudes while all 30 zero-delta task pairs remain in n=46.
    deltas = [10.0] * 6 + [-0.1] * 10 + [0.0] * 30
    permutation_p = paired_permutation_pvalue(deltas)
    old_sign_p = 2 * sum(math.comb(16, index) for index in range(7)) / (2**16)
    assert old_sign_p > 0.05
    assert permutation_p == pytest.approx(0.03125)


def test_paired_bootstrap_small_sample_is_task_paired_and_wide():
    deltas = [1.0, 1.0, -1.0, 0.0, 0.0]

    lower, upper = paired_bootstrap_mean_ci(deltas)

    assert lower is not None and upper is not None
    assert lower < 0.0 < upper
    assert upper - lower >= 1.0


def test_ab_compare_behaviours(capsys):
    rows_a = [
        {"task_id": "R1", "rep": 0, "success": True, "num_calls": 5, "calls": [], "trace_integrity": True},
        {"task_id": "R2", "rep": 0, "success": True, "num_calls": 3, "calls": [], "trace_integrity": True},
        {"task_id": "R3", "rep": 0, "success": False, "num_calls": 9, "calls": [], "trace_integrity": True},
    ]
    rows_b = [
        {"task_id": "R1", "rep": 0, "success": True, "num_calls": 2, "calls": [], "trace_integrity": True},
        {"task_id": "R2", "rep": 0, "success": True, "num_calls": 4, "calls": [], "trace_integrity": True},
        {"task_id": "R3", "rep": 0, "success": True, "num_calls": 1, "calls": [], "trace_integrity": True},
    ]

    comparison = ab_compare(rows_a, rows_b)

    assert comparison["n_paired"] == 2  # R3 has no successful A call count
    deltas = {pair["task_id"]: pair["delta"] for pair in comparison["paired_tasks"]}
    assert deltas == {"R1": -3.0, "R2": 1.0}
    assert comparison["mean_delta"] == pytest.approx(-1.0)
    assert comparison["median_delta"] == pytest.approx(-1.0)
    assert comparison["call_permutation_p"] is not None
    assert comparison["call_zero_deltas"] == 0
    assert comparison["n_paired_success"] == 3
    assert comparison["paired_success_delta"] == pytest.approx(1 / 3)
    assert comparison["success_a"]["k"] == 2 and comparison["success_a"]["n"] == 3
    assert comparison["success_b"]["k"] == 3 and comparison["success_b"]["n"] == 3

    print_ab_report(comparison, Path("a.jsonl"), Path("b.jsonl"))
    output = capsys.readouterr().out
    assert "paired-bootstrap95" in output
    assert "paired permutation p-value" in output
    assert "zero-delta ties retained" in output
    assert "sign-test" not in output
    assert "noise floor" not in output


def test_ab_report_distinguishes_identical_success_and_calls_by_errored_calls(capsys):
    rows_a = [
        {
            "task_id": "R1",
            "rep": 0,
            "success": True,
            "num_calls": 4,
            "errored_calls": 0,
            "calls": [],
            "trace_integrity": True,
        }
    ]
    rows_b = [
        {
            "task_id": "R1",
            "rep": 0,
            "success": True,
            "num_calls": 4,
            "errored_calls": 2,
            "calls": [],
            "trace_integrity": True,
        }
    ]

    comparison = ab_compare(rows_a, rows_b)

    assert comparison["success_a"]["k"] == comparison["success_b"]["k"] == 1
    assert comparison["mean_delta"] == 0.0
    assert comparison["mean_errored_call_delta"] == 2.0
    assert comparison["mean_errored_call_rate_delta"] == pytest.approx(0.5)
    assert comparison["paired_schema_friction"] == [
        {
            "task_id": "R1",
            "errored_calls_a": 0.0,
            "errored_calls_b": 2.0,
            "errored_call_delta": 2.0,
            "errored_call_rate_a": 0.0,
            "errored_call_rate_b": 0.5,
            "errored_call_rate_delta": 0.5,
            "raw_errored_calls_a": 0,
            "raw_total_calls_a": 4,
            "raw_errored_calls_b": 2,
            "raw_total_calls_b": 4,
        }
    ]

    print_ab_report(comparison, Path("a.jsonl"), Path("b.jsonl"))
    output = capsys.readouterr().out
    assert "mean errored-call delta (B−A): +2.0" in output
    assert "mean errored-call-rate delta (B−A): +50.0 percentage points" in output
    assert "R1: A=0/4 (0.0%), median=0.0; B=2/4 (50.0%), median=2.0" in output
    assert "is_error is the MCP-level error flag" in output


def test_ab_errored_call_rate_delta_averages_paired_tasks_instead_of_pooling_calls():
    rows_a = [
        {
            "task_id": "R1",
            "success": True,
            "num_calls": 1,
            "errored_calls": 0,
            "calls": [],
            "trace_integrity": True,
        },
        {
            "task_id": "R2",
            "success": True,
            "num_calls": 9,
            "errored_calls": 0,
            "calls": [],
            "trace_integrity": True,
        },
    ]
    rows_b = [
        {
            "task_id": "R1",
            "success": True,
            "num_calls": 1,
            "errored_calls": 1,
            "calls": [],
            "trace_integrity": True,
        },
        {
            "task_id": "R2",
            "success": True,
            "num_calls": 9,
            "errored_calls": 0,
            "calls": [],
            "trace_integrity": True,
        },
    ]

    comparison = ab_compare(rows_a, rows_b)

    assert comparison["mean_errored_call_delta"] == 0.5
    assert comparison["mean_errored_call_rate_delta"] == 0.5
    assert comparison["mean_errored_call_rate_delta"] != pytest.approx(1 / 10)
    assert comparison["n_paired_errored_call_rates"] == 2
    assert comparison["errored_call_rate_delta_ci"] == pytest.approx((0.0375, 0.9625))


def test_ab_compare_multi_rep_uses_median_successful_call_counts():
    rows_a = [
        {
            "task_id": "R1",
            "rep": 0,
            "success": True,
            "num_calls": 1,
            "errored_calls": 0,
            "calls": [],
            "trace_integrity": True,
        },
        {
            "task_id": "R1",
            "rep": 1,
            "success": False,
            "num_calls": 9,
            "errored_calls": 99,
            "calls": [],
            "trace_integrity": True,
        },
        {
            "task_id": "R1",
            "rep": 2,
            "success": True,
            "num_calls": 5,
            "errored_calls": 2,
            "calls": [],
            "trace_integrity": True,
        },
    ]
    rows_b = [
        {
            "task_id": "R1",
            "rep": 0,
            "success": True,
            "num_calls": 2,
            "errored_calls": 0,
            "calls": [],
            "trace_integrity": True,
        },
        {
            "task_id": "R1",
            "rep": 1,
            "success": True,
            "num_calls": 4,
            "errored_calls": 2,
            "calls": [],
            "trace_integrity": True,
        },
        {
            "task_id": "R1",
            "rep": 2,
            "success": True,
            "num_calls": 6,
            "errored_calls": 4,
            "calls": [],
            "trace_integrity": True,
        },
    ]

    comparison = ab_compare(rows_a, rows_b)

    assert comparison["multi_rep"] is True
    assert comparison["paired_tasks"] == [{"task_id": "R1", "calls_a": 3.0, "calls_b": 4.0, "delta": 1.0}]
    assert comparison["mean_errored_call_delta"] == 1.0
    assert comparison["mean_errored_call_rate_delta"] == pytest.approx(1 / 6)


def test_ab_compare_excludes_trace_invalid_call_counts():
    rows_a = [
        {
            "task_id": "R1",
            "success": True,
            "trace_integrity": False,
            "trace_integrity_reason": "result_pair_mismatch",
            "num_calls": 99,
            "calls": [],
        }
    ]
    rows_b = [{"task_id": "R1", "success": True, "trace_integrity": True, "num_calls": 1, "calls": []}]

    comparison = ab_compare(rows_a, rows_b)

    assert comparison["n_paired"] == 0
    assert comparison["paired_tasks"] == []
