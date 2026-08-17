"""Release fixtures for evaluation workspaces."""

from __future__ import annotations

from typing import Any

from plane import PlaneClient
from plane.models.releases import CreateRelease, UpdateReleaseChangelog

from evals.changelog import changelog_items, normalize_changelog_text
from evals.evidence import set_target_evidence
from evals.fixtures import (
    EVALUATION_RELEASE_TAG_VERSION,
    RELEASE_CHANGELOG_TEXT,
    RELEASE_NAME,
)

from .gates import plan_gate_skips
from .identities import record_seeded_entity
from .randomize import random_truth_rng, random_truth_token, record_randomized_truth

__all__ = [
    "EVALUATION_RELEASE_TAG_VERSION",
    "RELEASE_CHANGELOG_TEXT",
    "RELEASE_NAME",
    "seed_release",
]


def seed_release(plane: PlaneClient, workspace_slug: str, context: dict[str, Any]) -> None:
    """Seed the C2 release fixture, skipping the task when the plan excludes releases."""
    task_id = str(context.get("task_id") or "")
    release_name = RELEASE_NAME
    changelog_text = RELEASE_CHANGELOG_TEXT
    if task_id == "C2":
        rng = random_truth_rng(context, "C2:release")
        hidden_token = random_truth_token(context, "C2:release")
        release_name = f"1.{rng.randint(2, 9)}.{rng.randint(0, 20)}-eval.{hidden_token[:8]}"
        changelog_text = (
            f"Changelog entry one: OAuth login hardening ticket EVAL-{hidden_token}. "
            f"Changelog entry two: webhook retry backoff window {rng.randint(3, 12)}-{hidden_token}."
        )
        record_randomized_truth(
            context,
            "C2.release",
            {"intended_name": release_name, "intended_changelog": changelog_text},
        )
    with plan_gate_skips("releases"):
        release = plane.releases.create(
            workspace_slug=workspace_slug,
            data=CreateRelease(name=release_name),
        )
        confirmed_release_name = str(getattr(release, "name", None) or "").strip()
        if task_id == "C2" and not confirmed_release_name:
            raise RuntimeError("release create response did not confirm the randomized release name")
        confirmed_release_name = confirmed_release_name or release_name
        context["release"] = {"id": release.id, "name": confirmed_release_name}
        record_seeded_entity(context, "release", release.id)
        context["release_name"] = confirmed_release_name
        context["workspace_objects"].append({"kind": "release", "id": release.id})
        # Single changelog body; DESIGN's "2 entries" are encoded as plain text.
        plane.releases.changelog.update(
            workspace_slug=workspace_slug,
            release_id=release.id,
            data=UpdateReleaseChangelog(
                description_html=f"<p>{changelog_text}</p>",
            ),
        )
        confirmed = plane.releases.changelog.retrieve(
            workspace_slug=workspace_slug,
            release_id=release.id,
        )
        confirmed_text = normalize_changelog_text(confirmed)
        if not confirmed_text:
            raise RuntimeError("release changelog readback was empty after seeding")
    context["release_changelog_text"] = confirmed_text
    if task_id == "C2":
        items = changelog_items(confirmed_text)
        if not items:
            raise RuntimeError("release changelog readback had no parseable entries after seeding")
        context["randomized_truth"]["C2.release"]["confirmed"] = {
            "name": confirmed_release_name,
            "changelog": confirmed_text,
            "items": list(items),
        }
        set_target_evidence(context, items)
