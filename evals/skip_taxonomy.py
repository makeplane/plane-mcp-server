"""Explicit run-completeness taxonomy for task skip reasons.

Known missing environment capabilities are expected skips: they reduce execution
coverage but do not make an otherwise clean run incomplete. A dirty environment that
requires operator cleanup, such as a fixture collision, is unexpected. Unknown reasons
are also unexpected by default; there is deliberately no ``env:*`` catch-all. Plan-gate
reasons must name one of the explicitly supported capabilities below, and the activity
worker reason must match exactly.
"""

from __future__ import annotations

from typing import Literal

SkipDisposition = Literal["expected-capability", "dirty-environment", "unexpected"]

PLAN_GATED_PREFIX = "env:plan-gated:"
NO_ACTIVITY_WORKER_REASON = "env:no-activity-worker"
FIXTURE_COLLISION_PREFIX = "env:fixture-collision:"

# Derived from the plan-gated seed surfaces: customer and release fixture seeders, the
# work-item-type seeder, and the initiative/teamspace plan refusals characterized by
# seed.projects.is_plan_gate. Keep this closed: a new capability is unexpected until its
# actual gate site is reviewed and added deliberately.
PLAN_GATED_CAPABILITIES = frozenset(
    {
        "customers",
        "initiatives",
        "releases",
        "teamspaces",
        "work-item-types",
    }
)


def _plan_gated_capability(reason: str) -> str | None:
    if not reason.startswith(PLAN_GATED_PREFIX):
        return None
    capability = reason.removeprefix(PLAN_GATED_PREFIX)
    return capability if capability in PLAN_GATED_CAPABILITIES else None


def classify_skip_reason(reason: str) -> SkipDisposition:
    """Classify a known capability skip, dirty environment, or unknown reason."""
    if _plan_gated_capability(reason) is not None:
        return "expected-capability"
    if reason == NO_ACTIVITY_WORKER_REASON:
        return "expected-capability"
    if reason.startswith(FIXTURE_COLLISION_PREFIX) and reason.removeprefix(FIXTURE_COLLISION_PREFIX):
        return "dirty-environment"
    return "unexpected"


def is_expected_environment_capability_skip(reason: str) -> bool:
    """Return whether a known absent environment capability caused the skip."""
    return classify_skip_reason(reason) == "expected-capability"


def skip_reason_family(reason: str) -> str:
    """Return the stable reporting family for a skip reason."""
    if _plan_gated_capability(reason) is not None:
        return "plan-gated"
    if reason == NO_ACTIVITY_WORKER_REASON:
        return "no-activity-worker"
    if reason.startswith(FIXTURE_COLLISION_PREFIX) and reason.removeprefix(FIXTURE_COLLISION_PREFIX):
        return "fixture-collision"
    return reason or "<missing>"


__all__ = [
    "FIXTURE_COLLISION_PREFIX",
    "NO_ACTIVITY_WORKER_REASON",
    "PLAN_GATED_CAPABILITIES",
    "PLAN_GATED_PREFIX",
    "SkipDisposition",
    "classify_skip_reason",
    "is_expected_environment_capability_skip",
    "skip_reason_family",
]
