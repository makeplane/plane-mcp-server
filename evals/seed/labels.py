"""Label fixtures for evaluation projects."""

from __future__ import annotations

from typing import Any

from plane import PlaneClient
from plane.models.labels import CreateLabel

LABEL_NAMES = ("auth", "triage", "perf")


def seed_labels(plane: PlaneClient, workspace_slug: str, context: dict[str, Any]) -> None:
    for name in LABEL_NAMES:
        label = plane.labels.create(
            workspace_slug=workspace_slug,
            project_id=context["project_id"],
            data=CreateLabel(name=name),
        )
        context["labels"][name] = label.id
