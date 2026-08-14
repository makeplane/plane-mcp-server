"""Characterization of `is_plan_gate` against the refusals Plane actually returns.

Every payload below is the real shape from `plane-ee/apps/api/plane/api/` — the v1
external layer the SDK talks to — rather than an invented one. A plan gate makes the
harness record an environment skip; anything else must stay a real error, so a
misclassification here either hides a defect or reports one that is not there.
"""

from __future__ import annotations

import pytest
from plane.errors.errors import HttpError

from evals.seed import is_plan_gate

# --- genuine plan refusals ------------------------------------------------------------

PLAN_GATES = [
    pytest.param(
        HttpError("Payment required", 402, {"error": "Payment required", "error_code": 1999}),
        id="402-check_feature_flag-decorator",
    ),
    pytest.param(
        HttpError("Payment required", 402, None),
        id="402-with-no-body",
    ),
    pytest.param(
        HttpError(
            "Forbidden",
            403,
            {"detail": "Payment required. Upgrade your plan to access Initiatives"},
        ),
        id="403-initiatives-permission-class",
    ),
    pytest.param(
        HttpError(
            "Forbidden",
            403,
            {"detail": "Payment required. Upgrade your plan to access Teamspaces"},
        ),
        id="403-teamspaces-permission-class",
    ),
    pytest.param(
        HttpError("Bad request", 400, {"error": "Upgrade your plan to enable formula properties"}),
        id="400-with-plan-prose",
    ),
]

# --- refusals that are NOT plan limits -------------------------------------------------

NOT_PLAN_GATES = [
    pytest.param(
        HttpError("Forbidden", 403, {"detail": "You don't have permission to create this project"}),
        id="bare-403-is-rbac-not-a-plan-limit",
    ),
    pytest.param(
        HttpError(
            "Forbidden",
            403,
            {"error": "Customer feature is not enabled for this workspace"},
        ),
        id="403-customer-toggle-is-configuration-the-harness-controls",
    ),
    pytest.param(
        HttpError("Not found", 404, {"message": "Worklog is not enabled for the project"}),
        id="404-worklog-toggle",
    ),
    pytest.param(
        HttpError("Bad request", 400, {"non_field_errors": ["Cycles are not enabled for this project"]}),
        id="400-cycle-toggle",
    ),
    pytest.param(
        HttpError("Bad request", 400, {"non_field_errors": ["Modules are not enabled for this project"]}),
        id="400-module-toggle",
    ),
    pytest.param(
        HttpError("Bad request", 400, {"name": ["This field is required."]}),
        id="400-ordinary-validation-error",
    ),
    pytest.param(
        HttpError("Server error", 500, {"error": "Internal server error"}),
        id="500-never-a-gate",
    ),
    pytest.param(
        HttpError("Too many requests", 429, {"error": "Rate limit exceeded"}),
        id="429-never-a-gate",
    ),
]


@pytest.mark.parametrize("exc", PLAN_GATES)
def test_plan_refusals_are_gates(exc):
    assert is_plan_gate(exc) is True


@pytest.mark.parametrize("exc", NOT_PLAN_GATES)
def test_other_refusals_are_not_gates(exc):
    assert is_plan_gate(exc) is False


@pytest.mark.parametrize(
    "exc",
    [
        pytest.param(RuntimeError("connection reset"), id="non-http-exception"),
        pytest.param(TimeoutError(), id="timeout"),
        pytest.param(ValueError("upgrade your plan"), id="non-http-even-with-plan-wording"),
    ],
)
def test_non_http_exceptions_are_never_gates(exc):
    """A transport failure must surface as infrastructure, not be excused as a plan limit."""
    assert is_plan_gate(exc) is False


def test_a_bare_403_would_previously_have_been_swallowed():
    """The regression this tightening exists for.

    An RBAC denial and an initiatives plan gate are both 403 with a ``detail`` string.
    Classifying on status alone turned a permission bug into an environment skip, which
    reads as 'nothing to see here' in every report that excludes skips from denominators.
    """
    rbac = HttpError("Forbidden", 403, {"detail": "You don't have permission to view this issue"})
    gate = HttpError("Forbidden", 403, {"detail": "Payment required. Upgrade your plan to access Initiatives"})

    assert rbac.status_code == gate.status_code
    assert is_plan_gate(rbac) is False
    assert is_plan_gate(gate) is True
