"""Offline tests for success-conditioned MCP error measurements."""

from __future__ import annotations

import pytest

from evals.report import measure_schema_friction, schema_friction_statement


def _row(task_id: str, **overrides):
    row = {
        "task_id": task_id,
        "rep": 0,
        "success": True,
        "trace_integrity": True,
        "num_calls": 4,
        "errored_calls": 1,
        "calls": [],
    }
    row.update(overrides)
    return row


def test_schema_friction_uses_exact_successful_call_delta_population():
    measurement = measure_schema_friction(
        [
            _row("R1", errored_calls=2),
            _row("R2", success=False, errored_calls=20),
            _row("R3", trace_integrity=False, errored_calls=30),
            _row("R4", error="harness failed", errored_calls=40),
            _row("R5", error_class="infra_cli", errored_calls=50),
            _row("R6", skipped="env:plan-gated:feature", errored_calls=60),
            {
                "row_type": "meta",
                "expected_rows": 6,
                "success": True,
                "trace_integrity": True,
                "num_calls": 4,
                "errored_calls": 70,
            },
        ]
    )

    assert list(measurement.tasks) == ["R1"]
    assert measurement.tasks["R1"].errored_calls == 2
    assert measurement.tasks["R1"].total_calls == 4
    assert measurement.task_mean_errored_calls == 2.0
    assert measurement.task_mean_errored_call_rate == 0.5


def test_schema_friction_rate_is_task_mean_instead_of_pooled_call_rate():
    measurement = measure_schema_friction(
        [
            _row("R1", num_calls=1, errored_calls=1),
            _row("R2", num_calls=9, errored_calls=0),
        ]
    )

    assert measurement.task_mean_errored_calls == 0.5
    assert measurement.task_mean_errored_call_rate == 0.5
    assert measurement.task_mean_errored_call_rate != pytest.approx(1 / 10)


def test_schema_friction_absolute_count_is_per_task_median_across_repetitions():
    measurement = measure_schema_friction(
        [
            _row("R1", rep=0, num_calls=10, errored_calls=0),
            _row("R1", rep=1, num_calls=10, errored_calls=0),
            _row("R1", rep=2, num_calls=10, errored_calls=9),
        ]
    )

    assert measurement.tasks["R1"].median_errored_calls == 0.0
    assert measurement.tasks["R1"].errored_calls == 9
    assert measurement.tasks["R1"].errored_call_rate == pytest.approx(0.3)


def test_schema_friction_prints_zero_and_does_not_invent_zero_attempt_rate():
    statement = schema_friction_statement(measure_schema_friction([_row("R1", num_calls=0, errored_calls=0)]))

    assert "task-mean median errored calls=0.0 across 1 tasks" in statement
    assert "task-mean errored-call rate=n/a across 0 tasks with calls" in statement
    assert "errored-call tasks: 0/1 []" in statement
    assert "is_error is the MCP-level error flag" in statement
    assert "correct task outcome still contributes" in statement
    assert "wrong tool successfully does not" in statement

    no_data = schema_friction_statement(measure_schema_friction([]))
    assert "task-mean median errored calls=n/a across 0 tasks" in no_data
    assert "errored-call tasks: 0/0 []" in no_data
