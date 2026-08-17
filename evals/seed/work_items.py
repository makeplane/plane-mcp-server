"""Work item fixtures for evaluation projects."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from plane import PlaneClient
from plane.models.query_params import WorkItemQueryParams
from plane.models.states import CreateState
from plane.models.work_items import CreateWorkItem, CreateWorkItemComment, UpdateWorkItem

from evals.changelog import normalize_changelog_text
from evals.errors import TaskSkipped
from evals.evidence import set_target_count_evidence, set_target_evidence
from evals.fixtures import (
    BLOCKING_REFERENCE_ADDRESS,
    BLOCKING_SOURCE_TITLE,
    BLOCKING_TARGET_TITLE,
    CHECKOUT_COMMENT_PHRASES,
    CHECKOUT_TIMEOUT_TITLE,
    DARK_MODE_TITLE,
    DUE_THIS_WEEK_TITLES,
    PAYMENT_WEBHOOK_TITLE,
    SIDEBAR_TITLE,
    UNFINISHED_CYCLE_TITLES,
    WORK_ITEM_FIXTURES,
)

from .identities import record_seeded_entity
from .randomize import random_truth_rng, random_truth_token, record_randomized_truth

__all__ = [
    "BLOCKING_REFERENCE_ADDRESS",
    "BLOCKING_SOURCE_TITLE",
    "BLOCKING_TARGET_TITLE",
    "CHECKOUT_COMMENT_PHRASES",
    "CHECKOUT_TIMEOUT_TITLE",
    "DARK_MODE_TITLE",
    "DUE_THIS_WEEK_TITLES",
    "PAYMENT_WEBHOOK_TITLE",
    "SIDEBAR_TITLE",
    "UNFINISHED_CYCLE_TITLES",
    "WORK_ITEM_FIXTURES",
    "find_completed_state",
    "list_states",
    "require_activities",
    "seed_work_items",
]


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


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _as_id(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("id") or "")
    return str(getattr(value, "id", None) or "")


def _list_all_work_items(plane: PlaneClient, workspace_slug: str, project_id: str) -> list[Any]:
    rows: list[Any] = []
    cursor: str | None = None
    while True:
        params = WorkItemQueryParams(cursor=cursor, per_page=100) if cursor else WorkItemQueryParams(per_page=100)
        page = plane.work_items.list(workspace_slug=workspace_slug, project_id=project_id, params=params)
        rows.extend(page.results or [])
        if not page.next_page_results:
            return rows
        cursor = page.next_cursor


def _resolve_state_name(
    plane: PlaneClient,
    workspace_slug: str,
    project_id: str,
    state_ref: Any,
) -> str:
    direct = getattr(state_ref, "name", None) or (state_ref.get("name") if isinstance(state_ref, dict) else None)
    if direct:
        return str(direct)
    state_id = _as_id(state_ref)
    if not state_id:
        return ""
    state = plane.states.retrieve(workspace_slug=workspace_slug, project_id=project_id, state_id=state_id)
    return str(state.name or "")


def _confirm_open_urgent_items(plane: PlaneClient, workspace_slug: str, project_id: str) -> list[str]:
    states = list_states(plane, workspace_slug, project_id)
    closed = {
        str(state.id)
        for state in states
        if _enum_value(getattr(state, "group", None)).casefold() in {"completed", "cancelled"}
    }
    titles: list[str] = []
    for item in _list_all_work_items(plane, workspace_slug, project_id):
        if _enum_value(getattr(item, "priority", None)).casefold() != "urgent":
            continue
        if _as_id(getattr(item, "state", None)) in closed:
            continue
        title = str(getattr(item, "name", None) or "").strip()
        if not title:
            raise RuntimeError(f"seed R2: API readback returned urgent item without a name: {item!r}")
        titles.append(title)
    return titles


def _serialized_rows(rows: list[Any]) -> str:
    payload = [row.model_dump(mode="json") if hasattr(row, "model_dump") else row for row in rows]
    return json.dumps(payload, default=str, ensure_ascii=False)


def _comment_text(comment: Any) -> str:
    stripped = str(getattr(comment, "comment_stripped", None) or "").strip()
    if stripped:
        return " ".join(stripped.split())
    return normalize_changelog_text(str(getattr(comment, "comment_html", None) or ""))


def seed_work_items(plane: PlaneClient, workspace_slug: str, context: dict[str, Any]) -> None:
    project_id = context["project_id"]
    task_id = str(context.get("task_id") or "")
    rng = random_truth_rng(context, f"{task_id or 'shared'}:work-items")
    hidden_token = random_truth_token(context, f"{task_id or 'shared'}:work-items")
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
    base_started_state = started[0]
    state_targets: dict[str, Any] = {PAYMENT_WEBHOOK_TITLE: base_started_state}
    if task_id in {"R1", "I2"}:
        random_state_name = f"Investigating {hidden_token}"
        random_state = plane.states.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=CreateState(
                name=random_state_name,
                color="#5E6AD2",
                group="started",
            ),
        )
        if not getattr(random_state, "id", None):
            raise RuntimeError(f"seed {task_id}: random state create returned no id")
        state_targets[PAYMENT_WEBHOOK_TITLE if task_id == "R1" else SIDEBAR_TITLE] = random_state
        record_seeded_entity(context, "state", random_state.id)
        record_randomized_truth(
            context,
            f"{task_id}.state",
            {"intended": random_state_name, "created_id": str(random_state.id)},
        )

    r1_state = state_targets[PAYMENT_WEBHOOK_TITLE]
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

    urgent_target = rng.randint(2, 7) if task_id == "R2" else 4
    if task_id == "R2":
        record_randomized_truth(context, "R2.urgent_open_count", {"intended": urgent_target})

    r3_templates: set[str] = set(DUE_THIS_WEEK_TITLES)
    if task_id == "R3":
        r3_count = rng.randint(1, 4)
        candidates = [title for title, _priority in WORK_ITEM_FIXTURES]
        r3_templates = set(rng.sample(candidates, r3_count))
        record_randomized_truth(context, "R3.due_templates", sorted(r3_templates))

    randomize_titles = task_id in {"R2", "R3", "R4"}
    urgent_count = 0
    for index, (fixture_title, fixture_priority) in enumerate(WORK_ITEM_FIXTURES):
        title = (
            f"{fixture_title} · case {hidden_token}-{index + 1}"
            if randomize_titles and (task_id in {"R2", "R4"} or fixture_title in r3_templates)
            else fixture_title
        )
        priority = "urgent" if index < urgent_target else ("high" if fixture_priority == "urgent" else fixture_priority)
        data_kwargs: dict[str, Any] = {"name": title, "priority": priority}
        target_state = state_targets.get(fixture_title)
        if target_state is not None:
            data_kwargs["state"] = str(target_state.id)
        if fixture_title in r3_templates:
            data_kwargs["assignees"] = [me_id]
            data_kwargs["target_date"] = due_this_week
        item = plane.work_items.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=CreateWorkItem(**data_kwargs),  # type: ignore[arg-type]
        )
        # Some APIs ignore state on create; force via update if needed.
        if target_state is not None:
            current = getattr(item, "state", None)
            current_id = current if isinstance(current, str) else getattr(current, "id", None)
            if str(current_id) != str(target_state.id):
                item = plane.work_items.update(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    work_item_id=item.id,
                    data=UpdateWorkItem(state=str(target_state.id)),
                )
        context["items"][title] = item.id
        context["fixture_item_ids"][fixture_title] = item.id
        context["fixture_item_titles"][fixture_title] = title
        context["item_ids"].append(item.id)
        record_seeded_entity(context, "work_item", item.id)
        sequence = getattr(item, "sequence_id", None)
        if sequence is not None and context.get("project_identifier"):
            context["item_identifiers"][fixture_title] = f"{context['project_identifier']}-{sequence}"
        if priority == "urgent":
            urgent_count += 1
    assert urgent_count == urgent_target, (
        f"fixture invariant: expected {urgent_target} urgent items, got {urgent_count}"
    )

    # R5: seed discussion comments on the known item.
    target_id = context["fixture_item_ids"].get(CHECKOUT_TIMEOUT_TITLE)
    comment_phrases = list(CHECKOUT_COMMENT_PHRASES)
    if task_id in {"R5", "L2"}:
        comment_count = rng.randint(1, 4)
        comment_phrases = [
            f"{CHECKOUT_COMMENT_PHRASES[index % len(CHECKOUT_COMMENT_PHRASES)]} ref-{hidden_token}-{index + 1}"
            for index in range(comment_count)
        ]
        truth_key = "R5.comments" if task_id == "R5" else "L2.activity_count"
        truth_value = (
            {"intended": list(comment_phrases)} if task_id == "R5" else {"intended_comment_count": comment_count}
        )
        record_randomized_truth(context, truth_key, truth_value)
        if task_id == "L2":
            context["l2_comment_phrases"] = list(comment_phrases)
    comment_ids: set[str] = set()
    if target_id:
        for phrase in comment_phrases:
            created_comment = plane.work_items.comments.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=target_id,
                data=CreateWorkItemComment(comment_html=f"<p>{phrase}</p>"),
            )
            if getattr(created_comment, "id", None) is not None:
                comment_ids.add(str(created_comment.id))
                record_seeded_entity(context, "comment", created_comment.id)

    # Capture every affected read oracle from API-confirmed state, never from the random choice.
    if task_id in {"R1", "I2"}:
        target_fixture = PAYMENT_WEBHOOK_TITLE if task_id == "R1" else SIDEBAR_TITLE
        target_id = context["fixture_item_ids"][target_fixture]
        detail = plane.work_items.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=target_id,
        )
        confirmed_name = _resolve_state_name(plane, workspace_slug, project_id, detail.state)
        if not confirmed_name:
            raise RuntimeError(f"seed {task_id}: API readback could not resolve target state")
        oracle_key = "r1_state_name" if task_id == "R1" else "i2_state_name"
        context[oracle_key] = confirmed_name
        context["randomized_truth"][f"{task_id}.state"]["confirmed"] = confirmed_name
        set_target_evidence(context, [confirmed_name])

    if task_id == "R2":
        confirmed_titles = _confirm_open_urgent_items(plane, workspace_slug, project_id)
        if not confirmed_titles:
            raise RuntimeError("seed R2: API readback found no urgent open work items")
        context["r2_urgent_open_count"] = len(confirmed_titles)
        context["randomized_truth"]["R2.urgent_open_count"]["confirmed"] = len(confirmed_titles)
        set_target_evidence(context, confirmed_titles)
        set_target_count_evidence(context, len(confirmed_titles), target_ids=[project_id])

    if task_id == "R3":
        confirmed_due_titles: list[str] = []
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
        for item_id in context["item_ids"]:
            detail = plane.work_items.retrieve(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=item_id,
            )
            target_date = str(getattr(detail, "target_date", None) or "")[:10]
            assignee_ids = {_as_id(value) for value in (getattr(detail, "assignees", None) or [])}
            if target_date and week_start.isoformat() <= target_date <= week_end.isoformat() and me_id in assignee_ids:
                confirmed_due_titles.append(str(detail.name))
        if not confirmed_due_titles:
            raise RuntimeError("seed R3: API readback found no assigned due-this-week items")
        context["r3_due_titles"] = confirmed_due_titles
        context["r3_due_count"] = len(confirmed_due_titles)
        context["randomized_truth"]["R3.due_templates"] = {
            "intended": sorted(r3_templates),
            "confirmed": {
                "titles": list(confirmed_due_titles),
                "count": len(confirmed_due_titles),
            },
        }
        set_target_evidence(context, confirmed_due_titles)

    if task_id == "R5":
        page = plane.work_items.comments.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=target_id,
        )
        confirmed_comments = [
            _comment_text(comment)
            for comment in (page.results or [])
            if not comment_ids or str(getattr(comment, "id", "")) in comment_ids
        ]
        confirmed_comments = [text for text in confirmed_comments if text]
        if len(confirmed_comments) != len(comment_phrases):
            raise RuntimeError(
                f"seed R5: API readback returned {len(confirmed_comments)} eval comments; want {len(comment_phrases)}"
            )
        context["r5_comment_phrases"] = confirmed_comments
        context["randomized_truth"]["R5.comments"]["confirmed"] = list(confirmed_comments)
        set_target_evidence(context, confirmed_comments)

    if task_id == "L1":
        work_item_id = str(context["fixture_item_ids"].get(PAYMENT_WEBHOOK_TITLE) or "")
        if not work_item_id:
            raise RuntimeError("seed L1: target work item id missing")
        detail = plane.work_items.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
        )
        confirmed_id = str(getattr(detail, "id", None) or "")
        if confirmed_id != work_item_id:
            raise RuntimeError(f"seed L1: target work item readback id={confirmed_id!r}; want {work_item_id!r}")
        context["l1_expected_summary_ids"] = [confirmed_id]
        set_target_evidence(context, [confirmed_id])

    if task_id == "L5":
        attachment_target_id = context["fixture_item_ids"][PAYMENT_WEBHOOK_TITLE]
        attachment_count = rng.randint(1, 3)
        record_randomized_truth(context, "L5.attachment_count", {"intended": attachment_count})
        intended_names: list[str] = []
        for index in range(attachment_count):
            name = f"diagnostic-{hidden_token}-{index + 1}.txt"
            intended_names.append(name)
            plane.work_items.attachments.upload_from_bytes(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=attachment_target_id,
                file_bytes=f"eval attachment {hidden_token}-{index + 1}\n".encode(),
                name=name,
                content_type="text/plain",
            )
        attachments = plane.work_items.attachments.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=attachment_target_id,
        )
        # attachments.list returns a bare list[WorkItemAttachment], not the paged envelope the
        # other list endpoints return. Assuming `.results` raised AttributeError on every L5
        # repetition, which surfaced as infra_seed and cost the task its whole row budget.
        confirmed_rows = list(attachments if isinstance(attachments, list) else (attachments.results or []))
        for attachment in confirmed_rows:
            record_seeded_entity(context, "attachment", getattr(attachment, "id", None))
        confirmed_attachment_count = len(confirmed_rows)
        context["l5_attachment_count"] = confirmed_attachment_count
        context["randomized_truth"]["L5.attachment_count"]["confirmed"] = confirmed_attachment_count
        response_blob = _serialized_rows(confirmed_rows)
        confirmed_names = [name for name in intended_names if name in response_blob]
        if len(confirmed_names) != attachment_count:
            raise RuntimeError(
                f"seed L5: attachment readback exposed {len(confirmed_names)} randomized names; want {attachment_count}"
            )
        set_target_evidence(context, confirmed_names)


def require_activities(plane: PlaneClient, workspace_slug: str, context: dict[str, Any]) -> None:
    """Require L2's seeded comments to materialize as activities.

    Only a successful, empty read evidences a missing activity worker and becomes the
    expected ``env:no-activity-worker`` capability skip. Missing fixture identifiers and
    inconsistent readback are fixture errors; API read failures propagate as infrastructure.
    """
    project_id = context.get("project_id")
    work_item_id = (context.get("items") or {}).get(CHECKOUT_TIMEOUT_TITLE)
    if not project_id or not work_item_id:
        missing = [name for name, value in (("project_id", project_id), ("work_item_id", work_item_id)) if not value]
        raise RuntimeError(f"seed L2 fixture error: missing {', '.join(missing)}")

    page = plane.work_items.activities.list(
        workspace_slug=workspace_slug,
        project_id=project_id,
        work_item_id=work_item_id,
    )
    rows = page.results if hasattr(page, "results") else page
    activity_rows = list(rows or [])
    activity_count = len(activity_rows)
    if activity_count < 1:
        raise TaskSkipped("env:no-activity-worker")
    context["l2_activity_count"] = activity_count
    if str(context.get("task_id") or "") == "L2":
        randomised = context.setdefault("randomized_truth", {}).setdefault("L2.activity_count", {})
        randomised["confirmed"] = activity_count
        # Evidence is the activity count, which is what L2 actually asks for and what its
        # verifier checks. It used to require the seeded comment phrase to appear in the
        # activity readback — evidence Plane's activity API never emits: the endpoint returns
        # the creation row and no comment text, so every L2 repetition died in seeding.
        set_target_count_evidence(context, activity_count, target_ids=[work_item_id])
