"""Offline tests for off-surface trace-signature indicators."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from evals.evidence import TARGET_ENTITY_EVIDENCE
from evals.report import (
    ANSWER_WITHOUT_PROVENANCE,
    IMPLAUSIBLY_FEW_CALLS,
    WRITE_WITHOUT_WRITE_CALL,
    ZERO_CALL_SUCCESS,
    ab_compare,
    measure_off_surface,
    print_ab_report,
    print_table,
    summarize,
)
from evals.results import RESULT_SCHEMA_VERSION


def _row(
    task_id: str,
    *,
    rep: int = 0,
    success: bool = True,
    num_calls: int = 1,
    calls: list[dict[str, Any]] | None = None,
    evidence_trace_available: bool = False,
    verify_note: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "task_id": task_id,
        "rep": rep,
        "success": success,
        "num_calls": num_calls,
        "calls": list(calls or []),
        "trace_integrity": True,
        "evidence_trace_available": evidence_trace_available,
        "verify_note": verify_note,
    }


def test_zero_call_success_flags_synthetic_bypass_and_clean_row():
    bypass = _row("R1", rep=0, success=True, num_calls=0, calls=[])
    clean = _row("R1", rep=1, success=True, calls=[{"tool": "list_work_items"}])
    trace_invalid = {**_row("R1", rep=2, success=True, num_calls=0, calls=[]), "trace_integrity": False}

    measurement = measure_off_surface([bypass, clean, trace_invalid])

    assert measurement.addresses(ZERO_CALL_SUCCESS) == ("R1[rep=0]",)
    assert all(row.address != "R1[rep=1]" for row in measurement.rows)
    assert all(row.address != "R1[rep=2]" for row in measurement.rows)

    # Indicators are measurements, not another pass/fail or completeness policy.
    summary = summarize([bypass], expected_rows=1)
    assert summary.aggregate_k == summary.aggregate_n == 1
    assert summary.complete is True
    assert summary.off_surface.addresses(ZERO_CALL_SUCCESS) == ("R1[rep=0]",)


def test_write_without_write_call_uses_catalog_tags():
    suspicious_write = _row("W1", rep=0, calls=[{"tool": "list_labels"}])
    clean_write = _row("W1", rep=1, calls=[{"tool": "manage_work_item_label"}])
    clean_setup = _row("S1", rep=0, calls=[{"tool": "create_work_item_property"}])
    future_setup = _row("FUTURE", rep=0, calls=[{"tool": "list_work_items"}])

    measurement = measure_off_surface(
        [suspicious_write, clean_write, clean_setup, future_setup],
        task_catalog={
            "W1": {"tags": {"write"}},
            "S1": {"tags": {"setup"}},
            "FUTURE": {"tags": {"setup"}},
        },
    )

    assert measurement.addresses(WRITE_WITHOUT_WRITE_CALL) == ("W1[rep=0]", "FUTURE[rep=0]")


def test_answer_without_provenance_counts_correct_answer():
    missing = _row(
        "R1",
        rep=0,
        success=False,  # Existing provenance enforcement already fails this row.
        calls=[{"tool": "retrieve_work_item", "observed_sentinels": []}],
        evidence_trace_available=True,
        verify_note="answer_correct=true (seed state reported); provenance=missing",
    )
    clean = _row(
        "R1",
        rep=1,
        calls=[
            {
                "tool": "retrieve_work_item",
                "observed_sentinels": [TARGET_ENTITY_EVIDENCE],
            }
        ],
        evidence_trace_available=True,
        verify_note="answer_correct=true (seed state reported); provenance=observed",
    )

    measurement = measure_off_surface([missing, clean])

    assert measurement.addresses(ANSWER_WITHOUT_PROVENANCE) == ("R1[rep=0]",)


def test_implausibly_few_calls_uses_observed_outer_fence():
    rows = [
        _row(
            "R2",
            rep=rep,
            num_calls=count,
            calls=[{"tool": "list_work_items"}] * count,
        )
        for rep, count in enumerate([10, 10, 10, 10, 1])
    ]

    measurement = measure_off_surface(rows)

    assert measurement.addresses(IMPLAUSIBLY_FEW_CALLS) == ("R2[rep=4]",)
    assert measure_off_surface(rows[:4]).addresses(IMPLAUSIBLY_FEW_CALLS) == ()
    benign_dispersion = [
        _row("R2", rep=rep, num_calls=count, calls=[{"tool": "list_work_items"}] * count)
        for rep, count in enumerate([3, 3, 3, 3, 2])
    ]
    assert measure_off_surface(benign_dispersion).addresses(IMPLAUSIBLY_FEW_CALLS) == ()


def test_reports_print_explicit_zero_addresses_rule_and_limitation(capsys):
    clean = _row("R1", calls=[{"tool": "list_work_items"}])
    print_table(summarize([clean]), "single")
    single_output = capsys.readouterr().out

    assert "EXECUTION COVERAGE:" in single_output
    assert "off-surface indicators: 0" in single_output
    assert "zero-call success: 0" in single_output
    assert "calls < Q1 - 3×IQR and calls ≤ half the task median" in single_output
    assert "cannot detect an agent" in single_output
    assert "RUN COMPLETE:" in single_output

    bypass = _row("W1", rep=3, num_calls=0, calls=[])
    # Mutation intent is a fact about the run, so the file carries it. Without the header a
    # hand-built row has no tags and the write indicator is correctly silent.
    write_meta = {"row_type": "meta", "task_metadata": {"W1": {"tags": ["write"]}}}
    comparison = ab_compare([write_meta, clean], [write_meta, bypass])
    print_ab_report(comparison, Path("a.jsonl"), Path("b.jsonl"))
    ab_output = capsys.readouterr().out

    assert "A off-surface indicators: 0" in ab_output
    assert "B off-surface indicators: 1 flagged rows" in ab_output
    assert "B   zero-call success: 1 [W1[rep=3]]" in ab_output
    assert "B   write without a write call: 1 [W1[rep=3]]" in ab_output
