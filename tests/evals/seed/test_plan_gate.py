"""Characterization of `is_plan_gate` against the payloads `api/` actually returns.

A gate becomes an environment skip and anything else stays a real error, so a
misclassification here either hides a defect or invents one.
"""

from __future__ import annotations

from plane.errors.errors import HttpError

from evals.seed import is_plan_gate

# --- genuine plan refusals ------------------------------------------------------------

PLAN_GATES = [
    (
        HttpError("Payment required", 402, {"error": "Payment required", "error_code": 1999}),
        "402-check_feature_flag-decorator",
    ),
    (HttpError("Payment required", 402, None), "402-with-no-body"),
    (
        HttpError(
            "Forbidden",
            403,
            {"detail": "Payment required. Upgrade your plan to access Initiatives"},
        ),
        "403-initiatives-permission-class",
    ),
    (
        HttpError(
            "Forbidden",
            403,
            {"detail": "Payment required. Upgrade your plan to access Teamspaces"},
        ),
        "403-teamspaces-permission-class",
    ),
    (HttpError("Bad request", 400, {"error": "Upgrade your plan to enable formula properties"}), "400-with-plan-prose"),
]

# --- refusals that are NOT plan limits -------------------------------------------------

NOT_PLAN_GATES = [
    (
        HttpError("Forbidden", 403, {"detail": "You don't have permission to create this project"}),
        "bare-403-is-rbac-not-a-plan-limit",
    ),
    (
        HttpError(
            "Forbidden",
            403,
            {"error": "Customer feature is not enabled for this workspace"},
        ),
        "403-customer-toggle-is-configuration-the-harness-controls",
    ),
    (HttpError("Not found", 404, {"message": "Worklog is not enabled for the project"}), "404-worklog-toggle"),
    (
        HttpError("Bad request", 400, {"non_field_errors": ["Cycles are not enabled for this project"]}),
        "400-cycle-toggle",
    ),
    (
        HttpError("Bad request", 400, {"non_field_errors": ["Modules are not enabled for this project"]}),
        "400-module-toggle",
    ),
    (HttpError("Bad request", 400, {"name": ["This field is required."]}), "400-ordinary-validation-error"),
    (HttpError("Server error", 500, {"error": "Internal server error"}), "500-never-a-gate"),
    (HttpError("Too many requests", 429, {"error": "Rate limit exceeded"}), "429-never-a-gate"),
]


def test_only_refusals_that_name_a_plan_limit_are_gates():
    """402 is unambiguous; 403/400 need the body to say so, since 403 is also plain RBAC."""
    for exc, label in PLAN_GATES:
        assert is_plan_gate(exc) is True, label
    for exc, label in NOT_PLAN_GATES:
        assert is_plan_gate(exc) is False, label


def test_non_http_exceptions_are_never_gates():
    """A transport failure is infrastructure, not a plan limit to be excused."""
    for exc in (RuntimeError("connection reset"), TimeoutError(), ValueError("upgrade your plan")):
        assert is_plan_gate(exc) is False, repr(exc)


def test_a_bare_403_would_previously_have_been_swallowed():
    """The regression this exists for: RBAC denial and a plan gate share status and shape."""
    rbac = HttpError("Forbidden", 403, {"detail": "You don't have permission to view this issue"})
    gate = HttpError("Forbidden", 403, {"detail": "Payment required. Upgrade your plan to access Initiatives"})
    assert rbac.status_code == gate.status_code
    assert is_plan_gate(rbac) is False
    assert is_plan_gate(gate) is True
