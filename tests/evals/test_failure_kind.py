"""The observed notes are the case table.

Every string here was emitted by a real verifier in a recorded run; the scan behind
this file covered 100 distinct notes across every result file on disk.
"""

from __future__ import annotations

import pytest

from evals.core.failure_kind import (
    ABANDONED,
    ENVIRONMENT,
    FAILURE_KINDS,
    MISSING_WRITE,
    PARTIAL_WRITE,
    UNCLASSIFIED,
    UNPROVEN,
    WRONG_VALUE,
    classify_failure,
)


def test_the_three_defects_that_motivated_this_are_distinct():
    """W7, I1 and S2 were three different defects reported identically as 'failed'."""
    w7 = classify_failure("blocking relation present; link 'https://example.com/eval/runbook-w7' missing; have []")
    i1 = classify_failure("work_item 0cf779b6 priority='urgent' (want high)")
    s2 = classify_failure("estimate points missing fib subset; have []; item estimate_point=None (want 5)")
    assert w7 == PARTIAL_WRITE
    assert i1 == WRONG_VALUE
    assert s2 == MISSING_WRITE
    assert len({w7, i1, s2}) == 3


def test_a_correct_answer_the_harness_could_not_prove_is_not_an_agent_defect():
    """The largest family in the corpus. Counting these as defects would be wrong.

    The agent answered correctly; the run could not evidence it. That is a harness
    property, and lumping it with wrong answers would misattribute the biggest
    single group of failures to the model.
    """
    for note in (
        "answer_correct=true (final text reports exactly 1 seeded comments); provenance=trace incomplete "
        "(source=proxy; proxy sidecar was not authoritative)",
        "answer_correct=true (final text reports activity count 1 via contract); provenance=missing "
        "(0 evidence-bearing of 4 successful Plane calls; 5 total)",
    ):
        assert classify_failure(note) == UNPROVEN


def test_a_wrong_answer_is_a_wrong_value_even_when_provenance_is_named():
    note = (
        "answer_correct=false (logged-minutes values=['90', '90']; want ['90']); "
        "provenance=observed seeded-value response evidence (source=proxy)"
    )
    assert classify_failure(note) == WRONG_VALUE


@pytest.mark.parametrize(
    "note",
    [
        "customer 'Acme Corp' not found",
        "Severity property not found on Bug type",
        "project estimate not found; requested Fibonacci scale was not created",
    ],
)
def test_absent_entities_are_missing_writes(note):
    assert classify_failure(note) == MISSING_WRITE


def test_a_half_landed_write_is_partial_not_missing():
    assert classify_failure("names 1.2.0; missing changelog content") == PARTIAL_WRITE


@pytest.mark.parametrize(
    "note",
    [
        "state='Backlog' (want exact 'Done')",
        "Sprint 12 not closed: end_date='2026-08-20T23:59:00Z' (want end_date='2026-08-19' or archived_at)",
    ],
)
def test_value_mismatches_are_wrong_value(note):
    assert classify_failure(note) == WRONG_VALUE


def test_environment_skips_are_not_defects():
    assert classify_failure("env:no-activity-worker") == ENVIRONMENT


def test_running_out_of_iterations_beats_whatever_the_note_says():
    """A capped run's note describes the unfinished state, not why it stopped."""
    assert classify_failure("estimate points missing fib subset", hit_max_iterations=True) == ABANDONED
    assert classify_failure("anything at all", stop_reason="max_tokens") == ABANDONED


def test_s2_is_not_abandoned_despite_giving_up():
    """S2 spent 43 calls and stopped voluntarily with end_turn.

    The plan assumed stop_reason would classify this directly. It does not -- the
    run ended normally, so only the note carries the defect.
    """
    assert (
        classify_failure(
            "estimate points missing fib subset; have []; item estimate_point=None (want 5)",
            stop_reason="end_turn",
            hit_max_iterations=False,
        )
        == MISSING_WRITE
    )


def test_an_unrecognised_note_is_unclassified_never_silently_bucketed():
    """A zero in some kind must mean zero, not 'the classifier did not recognise it'."""
    assert classify_failure("3 module completed items archived") == UNCLASSIFIED
    assert classify_failure("") == UNCLASSIFIED
    assert classify_failure(None) == UNCLASSIFIED


def test_unclassified_is_a_first_class_member():
    assert UNCLASSIFIED in FAILURE_KINDS
