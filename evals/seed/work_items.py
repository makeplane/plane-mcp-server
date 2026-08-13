"""Work item fixtures for evaluation projects."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from plane import PlaneClient
from plane.models.work_items import CreateWorkItem, CreateWorkItemComment, UpdateWorkItem

# Fixed fixture titles for the `items` group. Exactly 4 urgent; the rest medium/high/low.
# "Payment webhook drops retries" is the R1 target (urgent, non-default state).
WORK_ITEM_FIXTURES: list[tuple[str, str]] = [
    ("Payment webhook drops retries", "urgent"),
    ("Checkout times out on 3DS challenge", "urgent"),
    ("Session cookie not rotated after login", "urgent"),
    ("Inventory count goes negative under load", "urgent"),
    ("Search results ignore archived projects", "high"),
    ("CSV export truncates multi-byte chars", "high"),
    ("Webhook secret rotation docs missing", "medium"),
    ("Dark mode contrast fails WCAG AA", "medium"),
    ("Onboarding email template stale", "medium"),
    ("Sidebar collapse flickers on resize", "low"),
    ("Tooltip clipped inside modal dialog", "low"),
    ("Footer year still says 2024", "none"),
]

PAYMENT_WEBHOOK_TITLE = WORK_ITEM_FIXTURES[0][0]
# R5 discussion target + distinctive comment phrases (word-boundary matched at verify).
CHECKOUT_TIMEOUT_TITLE = "Checkout times out on 3DS challenge"
CHECKOUT_COMMENT_PHRASES = (
    "stripe callback race",
    "retry budget exhausted",
)
# W2 / W3 / W8 targets
SIDEBAR_TITLE = "Sidebar collapse flickers on resize"
DARK_MODE_TITLE = "Dark mode contrast fails WCAG AA"
# W7 relation pair + reference URL
BLOCKING_SOURCE_TITLE = "Search results ignore archived projects"
BLOCKING_TARGET_TITLE = "CSV export truncates multi-byte chars"
BLOCKING_REFERENCE_ADDRESS = "https://example.com/eval/runbook-w7"
# R3: assignees + due this week (seeded count stored in ctx)
DUE_THIS_WEEK_TITLES = (
    "Webhook secret rotation docs missing",
    "Onboarding email template stale",
)
# W6 unfinished items in Sprint 12
UNFINISHED_CYCLE_TITLES = (
    "Inventory count goes negative under load",
    "Tooltip clipped inside modal dialog",
)


def list_states(plane: PlaneClient, workspace_slug: str, project_id: str) -> list[Any]:
    page = plane.states.list(workspace_slug=workspace_slug, project_id=project_id)
    return list(page.results or [])


def find_completed_state(states: list[Any]) -> Any | None:
    completed = [state for state in states if getattr(state, "group", None) == "completed"]
    if not completed:
        return None
    # Prefer a non-default completed state named Done if present.
    for state in completed:
        if (state.name or "").strip().casefold() == "done":
            return state
    return completed[0]


def seed_work_items(plane: PlaneClient, workspace_slug: str, context: dict[str, Any]) -> None:
    project_id = context["project_id"]
    states = list_states(plane, workspace_slug, project_id)
    context["state_names"] = sorted({(state.name or "").strip() for state in states if (state.name or "").strip()})

    # Prefer a non-default started-group state so R1 cannot be passed by guessing the default.
    started = [
        state for state in states if getattr(state, "group", None) == "started" and not getattr(state, "default", False)
    ]
    if not started:
        started = [state for state in states if getattr(state, "group", None) == "started"]
    if not started:
        raise RuntimeError(
            "seed items: no started-group state available to place the R1 target; "
            f"states={[(state.name, state.group, state.default) for state in states]}"
        )
    r1_state = started[0]
    context["r1_state_name"] = r1_state.name
    context["r1_state_id"] = r1_state.id

    me = plane.users.get_me()
    me_id = str(me.id)
    context["me_id"] = me_id
    # Due dates must stay inside the current ISO week (Mon–Sun).
    # today+2d alone escapes the week on Sat/Sun — clamp to this week's Sunday.
    today = date.today()
    days_to_week_end = 6 - today.weekday()  # Mon=0 … Sun=6
    due_this_week = min(today + timedelta(days=2), today + timedelta(days=days_to_week_end)).isoformat()
    context["r3_due_date"] = due_this_week

    urgent_count = 0
    for title, priority in WORK_ITEM_FIXTURES:
        data_kwargs: dict[str, Any] = {"name": title, "priority": priority}
        if title == PAYMENT_WEBHOOK_TITLE:
            data_kwargs["state"] = str(r1_state.id)
        if title in DUE_THIS_WEEK_TITLES:
            data_kwargs["assignees"] = [me_id]
            data_kwargs["target_date"] = due_this_week
        item = plane.work_items.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=CreateWorkItem(**data_kwargs),  # type: ignore[arg-type]
        )
        # Some APIs ignore state on create; force via update if needed.
        if title == PAYMENT_WEBHOOK_TITLE:
            current = getattr(item, "state", None)
            current_id = current if isinstance(current, str) else getattr(current, "id", None)
            if str(current_id) != str(r1_state.id):
                item = plane.work_items.update(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    work_item_id=item.id,
                    data=UpdateWorkItem(state=str(r1_state.id)),
                )
        context["items"][title] = item.id
        context["item_ids"].append(item.id)
        sequence = getattr(item, "sequence_id", None)
        if sequence is not None and context.get("project_identifier"):
            context["item_identifiers"][title] = f"{context['project_identifier']}-{sequence}"
        if priority == "urgent":
            urgent_count += 1
    assert urgent_count == 4, f"fixture invariant: expected 4 urgent items, got {urgent_count}"

    # R5: seed discussion comments on the known item.
    target_id = context["items"].get(CHECKOUT_TIMEOUT_TITLE)
    if target_id:
        for phrase in CHECKOUT_COMMENT_PHRASES:
            plane.work_items.comments.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=target_id,
                data=CreateWorkItemComment(comment_html=f"<p>{phrase}</p>"),
            )


def require_activities(plane: PlaneClient, workspace_slug: str, context: dict[str, Any]) -> None:
    """Skip L2 when comments never materialize as activities (no activity worker).

    Raises :class:`evals.tasks.TaskSkipped` with reason ``env:no-activity-worker``
    so the harness records a skip, not a task failure.
    """
    from evals.tasks import TaskSkipped

    project_id = context.get("project_id")
    work_item_id = (context.get("items") or {}).get(CHECKOUT_TIMEOUT_TITLE)
    if not project_id or not work_item_id:
        raise TaskSkipped("env:no-activity-worker")
    try:
        page = plane.work_items.activities.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
        )
    except Exception as exc:
        raise TaskSkipped(f"env:no-activity-worker ({type(exc).__name__}: {exc})") from exc
    rows = page.results if hasattr(page, "results") else page
    if len(list(rows or [])) < 1:
        raise TaskSkipped("env:no-activity-worker")
