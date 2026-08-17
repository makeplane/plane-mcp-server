"""Project-state fixtures and immutable read oracles."""

from __future__ import annotations

from typing import Any

from plane import PlaneClient
from plane.models.states import CreateState

from evals.evidence import set_target_evidence
from evals.state_oracle import state_name_group_pairs

from .identities import record_seeded_entity
from .randomize import random_truth_rng, random_truth_token, record_randomized_truth


def seed_r7_state_oracle(plane: PlaneClient, workspace_slug: str, context: dict[str, Any]) -> None:
    """Add one hidden state and capture the complete API-confirmed state baseline."""
    project_id = str(context.get("project_id") or "")
    if not project_id:
        raise RuntimeError("seed R7: project id missing")
    rng = random_truth_rng(context, "R7:states")
    hidden_token = random_truth_token(context, "R7:states")
    state_name = f"Review {hidden_token}"
    state_group = rng.choice(("unstarted", "started", "completed"))
    created = plane.states.create(
        workspace_slug=workspace_slug,
        project_id=project_id,
        data=CreateState(name=state_name, color="#5E6AD2", group=state_group),
    )
    created_id = str(getattr(created, "id", None) or "")
    if not created_id:
        raise RuntimeError("seed R7: randomized state create returned no id")

    page = plane.states.list(workspace_slug=workspace_slug, project_id=project_id)
    rows = list(page.results or [])
    if not rows:
        raise RuntimeError("seed R7: API readback returned no project states")
    pairs = state_name_group_pairs(rows)
    expected_pair = f"{state_name} | group: {state_group}"
    if expected_pair not in pairs:
        raise RuntimeError(
            f"seed R7: randomized state missing from API readback; want {expected_pair!r}; have={pairs!r}"
        )
    context["r7_state_pairs"] = pairs
    context["r7_random_state_id"] = created_id
    record_seeded_entity(context, "state", created_id)
    record_randomized_truth(
        context,
        "R7.states",
        {
            "intended": expected_pair,
            "confirmed": list(pairs),
        },
    )
    set_target_evidence(context, [state_name])


__all__ = ["seed_r7_state_oracle"]
