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


def test_describe_exception_flattens_a_task_group():
    """The real failure must survive into the row, not the group's sub-exception count.

    An OpenAI 400 naming the exact unsupported parameter was recorded as "unhandled errors in
    a TaskGroup (1 sub-exception)", and recovering it meant reproducing the call by hand.

    The helper reads only ``.exceptions``, so the contract is testable on every supported
    Python; the genuine builtin is 3.11+, and this project still declares 3.10.
    """
    import builtins
    import sys

    from evals.core.errors import describe_exception

    class FakeGroup(Exception):
        """Anything exposing .exceptions -- which is all the helper looks at."""

        def __init__(self, message, exceptions):
            super().__init__(message)
            self.exceptions = tuple(exceptions)

    inner = ValueError("Function tools with reasoning_effort are not supported")
    described = describe_exception(FakeGroup("unhandled errors in a TaskGroup", [inner]))
    assert "reasoning_effort" in described, "the actual cause was dropped"
    assert "ValueError" in described

    # Nested groups flatten to their leaves.
    nested = FakeGroup("outer", [FakeGroup("inner", [RuntimeError("deep")])])
    assert "deep" in describe_exception(nested)

    # A plain exception is unchanged in substance.
    assert describe_exception(RuntimeError("plain")) == "RuntimeError: plain"

    # A pathological fan-out is bounded rather than unbounded.
    many = FakeGroup("many", [RuntimeError(f"e{i}") for i in range(20)])
    assert describe_exception(many, limit=3).count("RuntimeError") == 3

    # And against the real builtin wherever it exists -- looked up dynamically so this stays
    # importable on 3.10 and does not read as an undefined name to the linter.
    real_group = getattr(builtins, "BaseExceptionGroup", None)
    if real_group is not None and sys.version_info >= (3, 11):
        described = describe_exception(real_group("unhandled errors in a TaskGroup", [inner]))
        assert "reasoning_effort" in described
        assert "sub-exception" not in described.split(" -> ")[-1]
