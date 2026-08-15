"""Cycle fixtures for evaluation projects."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from plane import PlaneClient
from plane.models.cycles import CreateCycle, UpdateCycle
from plane.models.work_items import UpdateWorkItem

from evals.evidence import set_target_evidence
from evals.fixtures import CYCLE_CURRENT, CYCLE_PAST, PAYMENT_WEBHOOK_TITLE, UNFINISHED_CYCLE_TITLES

from .randomize import random_truth_rng, record_randomized_truth


def seed_cycles(
    plane: PlaneClient,
    workspace_slug: str,
    context: dict[str, Any],
    leave_past_open: bool = False,
) -> None:
    """Seed Sprint 12 (past) + Sprint 13 (active) with work items.

    Plane refuses to add issues to an ended cycle, so Sprint 12 is created with an active
    window, filled, then backdated. ``leave_past_open`` skips the backdate: Plane also
    rejects every edit to an ended cycle, so pre-closing it makes W6's "close it"
    unachievable and leaves progress_snapshot (a transfer side effect) as the only signal.
    """
    project_id = context["project_id"]
    task_id = str(context.get("task_id") or "")
    past_name = CYCLE_PAST
    current_name = CYCLE_CURRENT
    active_fixture_titles = [PAYMENT_WEBHOOK_TITLE, "Session cookie not rotated after login"]
    overdue_fixture_title = "Session cookie not rotated after login"
    if task_id == "R4":
        rng = random_truth_rng(context, "R4:cycles")
        current_number = rng.randrange(20, 100)
        past_name = f"Sprint {current_number - 1}"
        current_name = f"Sprint {current_number}"
        active_candidates = [
            title for title in context.get("fixture_item_ids") or {} if title not in set(UNFINISHED_CYCLE_TITLES)
        ]
        active_fixture_titles = rng.sample(active_candidates, rng.randint(1, min(4, len(active_candidates))))
        overdue_fixture_title = rng.choice(active_fixture_titles)
        record_randomized_truth(
            context,
            "R4.cycle_inventory",
            {
                "intended_cycle": current_name,
                "intended_active_templates": list(active_fixture_titles),
                "intended_overdue_template": overdue_fixture_title,
            },
        )
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
            name=past_name,
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
            name=current_name,
            start_date=current_start,
            end_date=current_end,
            owned_by=me_id,
            project_id=str(project_id),
        ),
    )
    context["cycles"] = {
        past_name: past.id,
        current_name: current.id,
    }
    context["cycle_past_name"] = past_name
    context["cycle_current_name"] = current_name
    context["cycle_past_id"] = past.id
    context["cycle_current_id"] = current.id

    # 2) Add unfinished items to Sprint 12 *before* backdating.
    fixture_item_ids = context.get("fixture_item_ids") or context.get("items") or {}
    unfinished_ids = [fixture_item_ids[title] for title in UNFINISHED_CYCLE_TITLES if title in fixture_item_ids]
    if unfinished_ids:
        plane.cycles.add_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=past.id,
            issue_ids=unfinished_ids,
        )
    # R4: items on the active cycle (window still open).
    active_ids: list[str] = []
    for title in active_fixture_titles:
        item_id = fixture_item_ids.get(title)
        if item_id:
            active_ids.append(item_id)
    if active_ids:
        plane.cycles.add_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=current.id,
            issue_ids=active_ids,
        )
    overdue_id = fixture_item_ids.get(overdue_fixture_title)
    if overdue_id:
        plane.work_items.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=overdue_id,
            data=UpdateWorkItem(target_date=(today - timedelta(days=3)).isoformat()),
        )
        context["r4_overdue_title"] = (context.get("fixture_item_titles") or {}).get(
            overdue_fixture_title, overdue_fixture_title
        )
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

    if task_id == "R4":
        confirmed_cycle = plane.cycles.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=current.id,
        )
        confirmed_rows_page = plane.cycles.list_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=current.id,
        )
        confirmed_rows = confirmed_rows_page.results if hasattr(confirmed_rows_page, "results") else confirmed_rows_page
        confirmed_active_titles: list[str] = []
        confirmed_overdue_titles: list[str] = []
        for row in confirmed_rows or []:
            item_id = getattr(row, "work_item_id", None) or getattr(row, "issue", None) or getattr(row, "id", None)
            if hasattr(item_id, "id"):
                item_id = item_id.id
            if not item_id:
                continue
            detail = plane.work_items.retrieve(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=str(item_id),
            )
            name = str(getattr(detail, "name", None) or "").strip()
            if not name:
                raise RuntimeError(f"seed R4: active item {item_id} readback has no name")
            confirmed_active_titles.append(name)
            target_date = str(getattr(detail, "target_date", None) or "")[:10]
            if target_date and target_date < today.isoformat():
                confirmed_overdue_titles.append(name)
        if not confirmed_active_titles:
            raise RuntimeError("seed R4: API readback found no active-cycle items")
        context["r4_cycle_name"] = str(getattr(confirmed_cycle, "name", None) or "")
        if not context["r4_cycle_name"]:
            raise RuntimeError("seed R4: API readback returned an active cycle without a name")
        context["r4_active_titles"] = confirmed_active_titles
        context["r4_overdue_titles"] = confirmed_overdue_titles
        context["randomized_truth"]["R4.cycle_inventory"]["confirmed"] = {
            "cycle": context["r4_cycle_name"],
            "active_titles": list(confirmed_active_titles),
            "overdue_titles": list(confirmed_overdue_titles),
        }
        set_target_evidence(
            context,
            [context["r4_cycle_name"], *confirmed_active_titles],
            target_ids=[current.id],
        )
