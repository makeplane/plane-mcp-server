"""Seeding a plan-gated capability skips the task instead of failing the run.

`DESIGN.md` states a plan gate is not rewritten as an agent task failure. Before this,
only the work item type seeder honoured it; a gate while seeding a release or customer
raised, became `infra_seed`, and killed the task-rep. That is what made a flag server
answering everything "on" a hard prerequisite.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from plane.errors.errors import HttpError

from evals.seed import seed_customer, seed_release
from evals.tasks.skip import TaskSkipped

PLAN_REFUSAL = HttpError("Payment required", 402, {"error": "Payment required", "error_code": 1999})
RBAC_REFUSAL = HttpError("Forbidden", 403, {"detail": "You don't have permission to do this"})


def _raising_client(exc: Exception) -> SimpleNamespace:
    def _boom(**_kwargs: Any):
        raise exc

    return SimpleNamespace(
        releases=SimpleNamespace(create=_boom, changelog=SimpleNamespace(update=_boom)),
        customers=SimpleNamespace(create=_boom, requests=SimpleNamespace(create=_boom)),
    )


@pytest.mark.parametrize(
    ("seeder", "feature"),
    [(seed_release, "releases"), (seed_customer, "customers")],
)
def test_plan_gate_becomes_a_skip_with_a_reason(seeder, feature):
    context: dict[str, Any] = {"workspace_objects": []}
    with pytest.raises(TaskSkipped) as caught:
        seeder(_raising_client(PLAN_REFUSAL), "ws", context)
    assert caught.value.reason == f"env:plan-gated:{feature}"


@pytest.mark.parametrize("seeder", [seed_release, seed_customer])
def test_a_non_gate_failure_still_raises(seeder):
    """Only plan limits are excused. A permission or transport failure is a real error.

    Swallowing these would let the harness report a clean battery while the fixtures it
    graded against were never built.
    """
    context: dict[str, Any] = {"workspace_objects": []}
    with pytest.raises(HttpError):
        seeder(_raising_client(RBAC_REFUSAL), "ws", context)


@pytest.mark.parametrize("seeder", [seed_release, seed_customer])
def test_transport_failures_are_not_excused(seeder):
    context: dict[str, Any] = {"workspace_objects": []}
    with pytest.raises(RuntimeError):
        seeder(_raising_client(RuntimeError("connection reset")), "ws", context)


def test_customer_gate_does_not_leave_a_half_built_fixture():
    """A gate on the follow-up request must not leave a customer recorded as seeded.

    The customer is created, then its request is refused. Recording the customer while
    the task skips would leave a verifier reading a fixture that was never finished.
    """
    created: list[str] = []

    def _create_customer(**_kwargs: Any):
        created.append("customer")
        return SimpleNamespace(id="cust-1")

    def _refuse(**_kwargs: Any):
        raise PLAN_REFUSAL

    plane = SimpleNamespace(
        customers=SimpleNamespace(
            create=_create_customer,
            requests=SimpleNamespace(create=_refuse),
        )
    )
    context: dict[str, Any] = {"workspace_objects": []}
    with pytest.raises(TaskSkipped):
        seed_customer(plane, "ws", context)

    assert created == ["customer"]
    assert "customer_request" not in context
