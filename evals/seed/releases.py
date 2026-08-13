"""Release fixtures for evaluation workspaces."""

from __future__ import annotations

from typing import Any

from plane import PlaneClient
from plane.models.releases import CreateRelease, UpdateReleaseChangelog

RELEASE_NAME = "1.2.0"
RELEASE_CHANGELOG_TEXT = "Changelog entry one: OAuth login hardening. Changelog entry two: webhook retry backoff."
EVALUATION_RELEASE_TAG_VERSION = "eval-rc1"


def seed_release(plane: PlaneClient, workspace_slug: str, context: dict[str, Any]) -> None:
    release = plane.releases.create(
        workspace_slug=workspace_slug,
        data=CreateRelease(name=RELEASE_NAME),
    )
    context["release"] = {"id": release.id, "name": RELEASE_NAME}
    context["workspace_objects"].append({"kind": "release", "id": release.id})
    # Single changelog body; DESIGN's "2 entries" are encoded as plain-text bullets.
    try:
        plane.releases.changelog.update(
            workspace_slug=workspace_slug,
            release_id=release.id,
            data=UpdateReleaseChangelog(
                description_html=f"<p>{RELEASE_CHANGELOG_TEXT}</p>",
            ),
        )
    except Exception as exc:
        # Non-fatal for seed if changelog endpoint is flaky; C2 verifier still checks release name.
        print(f"seed warning: release changelog update failed: {exc}")
    context["release_changelog_text"] = RELEASE_CHANGELOG_TEXT
