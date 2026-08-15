"""Intake fixtures for evaluation projects."""

from __future__ import annotations

from typing import Any

from plane import PlaneClient
from plane.models.intake import CreateIntakeWorkItem, WorkItemForIntakeRequest

from evals.fixtures import INTAKE_BILLING_TITLE, INTAKE_SPAM_TITLE

from .identities import record_seeded_entity


def seed_intake(plane: PlaneClient, workspace_slug: str, context: dict[str, Any]) -> None:
    project_id = context["project_id"]
    billing = plane.intake.create(
        workspace_slug=workspace_slug,
        project_id=project_id,
        data=CreateIntakeWorkItem(
            issue=WorkItemForIntakeRequest(name=INTAKE_BILLING_TITLE, priority="high"),
        ),
    )
    spam = plane.intake.create(
        workspace_slug=workspace_slug,
        project_id=project_id,
        data=CreateIntakeWorkItem(
            issue=WorkItemForIntakeRequest(name=INTAKE_SPAM_TITLE, priority="none"),
        ),
    )
    # IntakeWorkItem.issue is the work-item id used by triage tools.
    context["intake"] = {
        "billing": {
            "intake_id": billing.id,
            "issue_id": getattr(billing, "issue", None) or billing.id,
            "title": INTAKE_BILLING_TITLE,
        },
        "spam": {
            "intake_id": spam.id,
            "issue_id": getattr(spam, "issue", None) or spam.id,
            "title": INTAKE_SPAM_TITLE,
        },
    }
    for row in (billing, spam):
        record_seeded_entity(context, "intake", row.id)
        record_seeded_entity(context, "work_item", getattr(row, "issue", None) or row.id)
