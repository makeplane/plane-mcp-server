"""Cycle fixtures for evaluation projects."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from plane import PlaneClient
from plane.models.cycles import CreateCycle, UpdateCycle
from plane.models.work_items import UpdateWorkItem

from .work_items import PAYMENT_WEBHOOK_TITLE, UNFINISHED_CYCLE_TITLES

CYCLE_PAST = "Sprint 12"
CYCLE_CURRENT = "Sprint 13"


def seed_cycles(
    plane: PlaneClient,
    workspace_slug: str,
    context: dict[str, Any],
    leave_past_open: bool = False,
) -> None:
    """Seed Sprint 12 (past) + Sprint 13 (active) with work items.

    Plane forbids adding issues to a cycle whose end_date is already past
    (``The Cycle has already been completed so no new issues can be added`` —
    plane-ee cycle/issue.py). Ordering for Sprint 12:

      1. create with an *active* window (start past, end future)
      2. add_work_items while still active
      3. update end_date to the past (backdate) so the cycle is completed

    ``leave_past_open`` skips step 3, leaving Sprint 12 ending tomorrow. Closing a
    cycle is only legal while it is still open — Plane rejects every edit to an
    ended cycle (``The Cycle has already been completed so it cannot be edited``)
    and rejects a transfer out of a still-running one (``The old cycle is not
    completed yet``), so a fixture that pre-closes Sprint 12 makes "close it"
    unachievable and leaves ``progress_snapshot`` (a transfer side effect) as the
    only observable close signal. W6 asks the agent to close, so it seeds open.

    Sprint 13 is created and populated while genuinely active (start ≤ today ≤ end).
    """
    project_id = context["project_id"]
    me_id = context.get("me_id") or str(plane.users.get_me().id)
    today = date.today()
    # Final past window for Sprint 12 after backdate (completedCycles / W6 transfer source).
    past_start = (today - timedelta(days=28)).isoformat()
    past_end_final = (today - timedelta(days=14)).isoformat()
    # Temporary active end so create + add succeed (end must be ≥ now). When the
    # cycle stays open this is its final window, so keep it short — Sprint 12 ends
    # tomorrow, which is what makes "close it and roll the rest over" natural.
    past_end_active = (today + timedelta(days=1 if leave_past_open else 7)).isoformat()
    # Sprint 13: genuinely active at seed time (start ≤ today ≤ end).
    current_start = (today - timedelta(days=3)).isoformat()
    current_end = (today + timedelta(days=10)).isoformat()

    # 1) Create Sprint 12 still active (items can be added).
    past = plane.cycles.create(
        workspace_slug=workspace_slug,
        project_id=project_id,
        data=CreateCycle(
            name=CYCLE_PAST,
            start_date=past_start,
            end_date=past_end_active,
            owned_by=me_id,
            project_id=str(project_id),
        ),
    )
    # Sprint 13: active window for R4 / W6 transfer target.
    current = plane.cycles.create(
        workspace_slug=workspace_slug,
        project_id=project_id,
        data=CreateCycle(
            name=CYCLE_CURRENT,
            start_date=current_start,
            end_date=current_end,
            owned_by=me_id,
            project_id=str(project_id),
        ),
    )
    context["cycles"] = {
        CYCLE_PAST: past.id,
        CYCLE_CURRENT: current.id,
    }
    context["cycle_past_id"] = past.id
    context["cycle_current_id"] = current.id

    # 2) Add unfinished items to Sprint 12 *before* backdating.
    unfinished_ids = [context["items"][title] for title in UNFINISHED_CYCLE_TITLES if title in context["items"]]
    if unfinished_ids:
        plane.cycles.add_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=past.id,
            issue_ids=unfinished_ids,
        )
    # R4: items on the active cycle (window still open).
    active_ids: list[str] = []
    for title in (PAYMENT_WEBHOOK_TITLE, "Session cookie not rotated after login"):
        item_id = context["items"].get(title)
        if item_id:
            active_ids.append(item_id)
    if active_ids:
        plane.cycles.add_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=current.id,
            issue_ids=active_ids,
        )
    overdue_id = context["items"].get("Session cookie not rotated after login")
    if overdue_id:
        plane.work_items.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=overdue_id,
            data=UpdateWorkItem(target_date=(today - timedelta(days=3)).isoformat()),
        )
        context["r4_overdue_title"] = "Session cookie not rotated after login"
        context["r4_overdue_id"] = overdue_id
    context["r4_active_item_ids"] = active_ids

    # 3) Backdate Sprint 12 so it is a completed cycle for R4 semantics — unless the
    # task needs to close it itself, in which case it must still be open.
    # UpdateCycle.end_date is writable; API allows past end_dates (no "can't backdate" gate
    # on the update path — only add_work_items checks end_date < now).
    if not leave_past_open:
        plane.cycles.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=past.id,
            data=UpdateCycle(end_date=past_end_final),
        )
    # Final seeded end_date for W6 close assertion (complete_cycle sets end_date=today).
    context["cycle_past_seed_end_date"] = past_end_active if leave_past_open else past_end_final
    context["cycle_past_open"] = leave_past_open
    context["cycle_past_end_date_before_backdate"] = past_end_active
