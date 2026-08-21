"""Offline tests for the explicit environment skip taxonomy."""

from __future__ import annotations

import pytest

from evals.skip_taxonomy import (
    PLAN_GATED_CAPABILITIES,
    classify_skip_reason,
    is_expected_environment_capability_skip,
    skip_reason_family,
)


@pytest.mark.parametrize(
    ("reason", "disposition", "family"),
    [
        pytest.param("env:plan-gated:customers", "expected-capability", "plan-gated", id="plan-gated"),
        pytest.param("env:no-activity-worker", "expected-capability", "no-activity-worker", id="activity-worker"),
        pytest.param(
            "env:no-activity-worker (ConnectionError: unavailable)",
            "unexpected",
            "env:no-activity-worker (ConnectionError: unavailable)",
            id="activity-worker-detail-is-not-a-capability-skip",
        ),
        pytest.param(
            "env:fixture-collision:customers:Acme",
            "dirty-environment",
            "fixture-collision",
            id="fixture-collision",
        ),
        pytest.param("env:new-capability", "unexpected", "env:new-capability", id="unknown-env-reason"),
        pytest.param(
            "env:plan-gated:customerz",
            "unexpected",
            "env:plan-gated:customerz",
            id="unknown-plan-gated-capability",
        ),
        pytest.param("env:plan-gated:", "unexpected", "env:plan-gated:", id="malformed-plan-gate"),
        pytest.param("env:no-activity-worker-new", "unexpected", "env:no-activity-worker-new", id="near-miss"),
    ],
)
def test_skip_reason_taxonomy_is_explicit_and_fail_closed(reason, disposition, family):
    assert classify_skip_reason(reason) == disposition
    assert is_expected_environment_capability_skip(reason) is (disposition == "expected-capability")
    assert skip_reason_family(reason) == family


def test_plan_gated_capability_allowlist_matches_reviewed_seed_surfaces():
    assert PLAN_GATED_CAPABILITIES == frozenset(
        {"customers", "releases", "work-item-types", "initiatives", "teamspaces"}
    )
    for capability in PLAN_GATED_CAPABILITIES:
        assert classify_skip_reason(f"env:plan-gated:{capability}") == "expected-capability", capability


def test_task_capability_pairs_are_derived_from_fixture_needs_and_fail_closed():
    assert classify_skip_reason("env:plan-gated:customers", task_id="L4") == "expected-capability"
    assert classify_skip_reason("env:plan-gated:customers", task_id="W1") == "unexpected"
    assert classify_skip_reason("env:plan-gated:releases", task_id="C2") == "expected-capability"
    assert classify_skip_reason("env:plan-gated:releases", task_id="L3") == "unexpected"
    assert classify_skip_reason("env:plan-gated:work-item-types", task_id="S1") == "expected-capability"
    assert classify_skip_reason("env:no-activity-worker", task_id="L2") == "expected-capability"
    assert classify_skip_reason("env:no-activity-worker", task_id="R1") == "unexpected"
