"""Write-task definitions and their verifiers."""

from __future__ import annotations

from typing import Any

from plane.errors.errors import HttpError
from plane.models.query_params import RetrieveQueryParams, WorkItemQueryParams

from evals.seed import (
    CYCLE_CURRENT,
    CYCLE_PAST,
    MODULE_COMPLETED_TITLES,
    MODULE_NAME,
    W2_TITLE,
    W3_TITLE,
    W7_SOURCE_TITLE,
    W7_TARGET_TITLE,
    W7_URL,
    W8_TITLE,
)
from evals.tasks.answers import word_boundary
from evals.tasks.lookups import (
    find_item_by_name,
    find_items_by_name,
    ids,
    is_not_found,
    state_group,
    state_name,
)


async def verify_w1(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """W1: assert end-state via Plane API (title, priority, assignee, auth label)."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    title = "Login page 500s on empty password"
    matches = find_items_by_name(plane, workspace_slug, project_id, title)
    if not matches:
        return False, f"work item {title!r} not found"
    item = matches[0]  # newest first
    notes: list[str] = []
    if len(matches) > 1:
        notes.append(f"warning: {len(matches)} items with title (verifying newest)")

    detail = plane.work_items.retrieve(
        workspace_slug=workspace_slug,
        project_id=project_id,
        work_item_id=item.id,
        params=RetrieveQueryParams(expand="assignees,labels"),
    )

    ok = True

    priority = (detail.priority or "").lower() if detail.priority else ""
    if priority != "urgent":
        ok = False
        notes.append(f"priority={priority!r} (want urgent)")
    else:
        notes.append("priority=urgent")

    me = plane.users.get_me()
    me_id = str(me.id)
    assignee_ids = ids(detail.assignees)
    if me_id not in assignee_ids:
        ok = False
        notes.append(f"assignees={sorted(assignee_ids)} missing me={me_id}")
    else:
        notes.append("assigned to me")

    auth_label_id = (ctx.get("labels") or {}).get("auth")
    label_ids = ids(detail.labels)
    if not auth_label_id:
        ok = False
        notes.append("auth label id missing from seed ctx")
    elif str(auth_label_id) not in label_ids:
        ok = False
        notes.append(f"labels={sorted(label_ids)} missing auth={auth_label_id}")
    else:
        notes.append("auth label attached")

    return ok, "; ".join(notes)


W1_TASK: dict[str, Any] = {
    "id": "W1",
    "tags": {"write", "tier1"},
    "prompt": (
        "Create a work item in project {project}: title 'Login page 500s on empty "
        "password', priority urgent, assign it to me, and add the 'auth' label."
    ),
    "optimal_calls": 4,
    "optimal_tools": {"get_me", "list_projects", "list_labels", "create_work_item"},
    "alternate_tools": {
        "search_work_items",
        "list_states",
        "retrieve_project",
        "get_workspace_members",
        "manage_work_item_assignee",
        "manage_work_item_label",
        "update_work_item",
        "list_work_items",
    },
    "needs": {"labels"},
    "verify": verify_w1,
}


async def verify_w2(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """W2: target item is in a completed-group state (prefer name Done)."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    item = find_item_by_name(plane, workspace_slug, project_id, W2_TITLE)
    if item is None:
        return False, f"item {W2_TITLE!r} not found"
    detail = plane.work_items.retrieve(workspace_slug=workspace_slug, project_id=project_id, work_item_id=item.id)
    name = state_name(plane, workspace_slug, project_id, detail.state)
    group = state_group(plane, workspace_slug, project_id, detail.state)
    if group == "completed" or (name and name.casefold() == "done"):
        return True, f"state={name!r} group={group!r}"
    return False, f"state={name!r} group={group!r} (want completed/Done)"


W2_TASK: dict[str, Any] = {
    "id": "W2",
    "tags": {"write", "tier1"},
    "prompt": (f"In project {{project}}, move the work item titled '{W2_TITLE}' to the Done state."),
    "optimal_calls": 3,
    "optimal_tools": {"list_work_items", "list_states", "update_work_item"},
    "alternate_tools": {
        "search_work_items",
        "retrieve_work_item",
        "retrieve_state",
        "list_projects",
    },
    "needs": {"items"},
    "verify": verify_w2,
}


async def verify_w3(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """W3: target item has a comment containing the prompt phrase 'contrast tokens'."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    item = find_item_by_name(plane, workspace_slug, project_id, W3_TITLE)
    if item is None:
        return False, f"item {W3_TITLE!r} not found"
    resp = plane.work_items.comments.list(
        workspace_slug=workspace_slug,
        project_id=project_id,
        work_item_id=item.id,
    )
    results = list(resp.results if hasattr(resp, "results") else resp or [])
    if not results:
        return False, "no comments on target item"
    phrase = "contrast tokens"
    pat = word_boundary(phrase)
    for c in results:
        html = getattr(c, "comment_html", None) or ""
        stripped = getattr(c, "comment_stripped", None) or ""
        # Some APIs expose plain text under comment_stripped; fall back to html.
        blob = f"{stripped}\n{html}"
        if pat.search(blob):
            return True, f"comment matches {phrase!r}"
    return False, f"no comment contains {phrase!r} ({len(results)} comment(s))"


W3_TASK: dict[str, Any] = {
    "id": "W3",
    "tags": {"write", "tier1"},
    "prompt": (
        f"In project {{project}}, add a comment on the work item titled '{W3_TITLE}' "
        "saying 'Reviewed contrast tokens — needs design pass'."
    ),
    "optimal_calls": 2,
    "optimal_tools": {"list_work_items", "create_work_item_comment"},
    "alternate_tools": {
        "search_work_items",
        "retrieve_work_item",
        "list_work_item_comments",
        "list_projects",
    },
    "needs": {"items"},
    "verify": verify_w3,
}


async def verify_w4(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """W4: the seeded triage label id is now named needs-triage.

    Authoritative path: retrieve ctx['labels']['triage'] by id. Name-scan is
    only a fallback when the seed id is missing from ctx.
    """
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    triage_id = (ctx.get("labels") or {}).get("triage")
    if triage_id:
        try:
            lb = plane.labels.retrieve(workspace_slug=workspace_slug, project_id=project_id, label_id=triage_id)
            name = (lb.name or "").strip().casefold()
            if name in ("needs-triage", "needs triage"):
                return True, f"label id {triage_id} now named {lb.name!r}"
            return False, f"label id {triage_id} still named {lb.name!r}"
        except HttpError as exc:
            if not is_not_found(exc):
                raise
            return False, f"seeded triage label id {triage_id} not found (deleted?)"

    # Fallback only when seed id is absent from ctx.
    page = plane.labels.list(workspace_slug=workspace_slug, project_id=project_id)
    names = {(lb.name or "").strip().casefold(): (lb.name or "").strip() for lb in (page.results or [])}
    if "needs-triage" in names or "needs triage" in names:
        if "triage" in names:
            return False, "both triage and needs-triage still present"
        return True, "label renamed to needs-triage (no seed id; name-scan fallback)"
    return False, f"needs-triage not found; labels={sorted(names.values())}"


W4_TASK: dict[str, Any] = {
    "id": "W4",
    "tags": {"write", "tier1"},
    "prompt": ("In project {project}, rename the label 'triage' to 'needs-triage'."),
    "optimal_calls": 2,
    "optimal_tools": {"list_labels", "update_label"},
    "alternate_tools": {
        "retrieve_label",
        "create_label",
        "delete_label",
        "list_projects",
    },
    "needs": {"labels"},
    "verify": verify_w4,
}


async def verify_w5(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """W5: all seeded module completed items are archived (not merely deleted)."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    ids = [str(i) for i in (ctx.get("module_completed_ids") or [])]
    if not ids:
        # Fall back to titles.
        for title in MODULE_COMPLETED_TITLES:
            item = find_item_by_name(plane, workspace_slug, project_id, title)
            if item:
                ids.append(str(item.id))
    if not ids:
        return False, "no module completed item ids"

    not_archived: list[str] = []
    need_archive_list: list[str] = []  # 404 on retrieve — must appear in archived list
    for wid in ids:
        try:
            detail = plane.work_items.retrieve(workspace_slug=workspace_slug, project_id=project_id, work_item_id=wid)
        except HttpError as exc:
            if is_not_found(exc):
                # Deleted OR archived-as-404 — require confirmation via list_archived.
                need_archive_list.append(str(wid))
                continue
            raise
        archived_at = getattr(detail, "archived_at", None)
        if not archived_at:
            not_archived.append(str(wid))

    arch_ids: set[str] = set()
    if need_archive_list or not_archived:
        try:
            arch = plane.work_items.list_archived(
                workspace_slug=workspace_slug,
                project_id=project_id,
                params=WorkItemQueryParams(per_page=100),
            )
            arch_ids = {str(i.id) for i in (arch.results or [])}
        except Exception as exc:
            if need_archive_list:
                return False, f"list_archived failed while confirming 404 items: {exc}"

    # 404s only count as archived if present on the archived list (deletes fail).
    for wid in need_archive_list:
        if wid not in arch_ids:
            not_archived.append(wid)
    not_archived = [i for i in not_archived if i not in arch_ids]

    if not_archived:
        return False, f"{len(not_archived)} module items not archived: {not_archived}"
    return True, f"{len(ids)} module completed items archived"


W5_TASK: dict[str, Any] = {
    "id": "W5",
    "tags": {"write", "tier1"},
    "prompt": (f"In project {{project}}, archive all completed work items in the module '{MODULE_NAME}'."),
    "optimal_calls": 5,  # list_modules + list_module_work_items + 3× archive
    "optimal_tools": {
        "list_modules",
        "list_module_work_items",
        "manage_work_item_archive",
    },
    "alternate_tools": {
        "list_work_items",
        "retrieve_module",
        "list_projects",
        "list_states",
    },
    "needs": {"module"},
    "verify": verify_w5,
}


async def verify_w6(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """W6: Sprint 12 closed by a real completion signal + unfinished items on Sprint 13.

    complete_cycle (SDK) sets end_date to *today* — a no-op agent leaves the seeded
    past end_date unchanged, so requiring end_date==today (or archived_at set) is
    non-vacuous. progress_snapshot non-null is also accepted when the API flips it.
    """
    from datetime import date as _date

    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    past_id = ctx.get("cycle_past_id") or (ctx.get("cycles") or {}).get(CYCLE_PAST)
    cur_id = ctx.get("cycle_current_id") or (ctx.get("cycles") or {}).get(CYCLE_CURRENT)
    if not past_id:
        return False, "Sprint 12 id missing from seed"
    notes: list[str] = []
    ok = True
    past = plane.cycles.retrieve(workspace_slug=workspace_slug, project_id=project_id, cycle_id=past_id)
    end = getattr(past, "end_date", None)
    archived_at = getattr(past, "archived_at", None)
    snapshot = getattr(past, "progress_snapshot", None)
    today = _date.today().isoformat()
    seed_end = ctx.get("cycle_past_seed_end_date")

    # Real close signals (any one suffices):
    # 1) complete_cycle → end_date becomes today
    # 2) manage_cycle_archive → archived_at set
    # 3) progress_snapshot populated (Plane completion snapshot)
    # end_date comes back as a timestamp ('2026-08-12T00:00:00Z'), so compare the
    # date part — a whole-string match against today's date can never be true.
    end_day = str(end or "")[:10]
    closed = False
    if archived_at:
        closed = True
        notes.append(f"Sprint 12 archived_at={archived_at}")
    elif end_day == today:
        closed = True
        notes.append(f"Sprint 12 end_date={end} (complete_cycle today)")
    elif snapshot not in (None, {}, []):
        closed = True
        notes.append("Sprint 12 progress_snapshot set")
    if not closed:
        ok = False
        notes.append(
            f"Sprint 12 not closed: end_date={end!r} seed_end={seed_end!r} "
            f"archived_at={archived_at!r} snapshot={snapshot!r} "
            f"(want end_date={today!r} or archived_at or progress_snapshot)"
        )

    unfinished = list(ctx.get("w6_unfinished_titles") or [])
    if cur_id and unfinished:
        try:
            on13 = plane.cycles.list_work_items(
                workspace_slug=workspace_slug,
                project_id=project_id,
                cycle_id=cur_id,
                params=WorkItemQueryParams(per_page=100),
            )
            names = {(i.name or "").strip() for i in (on13.results or [])}
            missing = [t for t in unfinished if t not in names]
            if missing:
                ok = False
                notes.append(f"unfinished not on Sprint 13: {missing}")
            else:
                notes.append(f"{len(unfinished)} unfinished on Sprint 13")
        except Exception as exc:
            notes.append(f"list Sprint 13 items failed: {exc}")
    return ok, "; ".join(notes)


W6_TASK: dict[str, Any] = {
    "id": "W6",
    "tags": {"write", "tier1"},
    "prompt": (
        f"In project {{project}}, '{CYCLE_PAST}' is wrapping up. Close it and make sure "
        f"its unfinished work items end up on '{CYCLE_CURRENT}'."
    ),
    "optimal_calls": 4,
    "optimal_tools": {
        "list_cycles",
        "transfer_cycle_work_items",
        "complete_cycle",
    },
    "alternate_tools": {
        "list_cycle_work_items",
        "manage_cycle_work_items",
        "update_cycle",
        "list_work_items",
        "list_projects",
    },
    # cycles_open_past: Sprint 12 must still be open, or "close it" is impossible —
    # Plane rejects every edit to an ended cycle. See _seed_cycles.
    "needs": {"items", "cycles", "cycles_open_past"},
    "verify": verify_w6,
}


async def verify_w7(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """W7: source blocks target (dependency) AND reference URL link exists on source.

    Only dump['blocking'] ids count — a reverse blocked_by match must not pass.
    """
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    src = find_item_by_name(plane, workspace_slug, project_id, W7_SOURCE_TITLE)
    tgt = find_item_by_name(plane, workspace_slug, project_id, W7_TARGET_TITLE)
    if not src or not tgt:
        return False, "W7 source/target items not found"
    notes: list[str] = []
    ok = True

    # Dependencies — require tgt in blocking specifically.
    try:
        deps = plane.work_items.dependencies.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=src.id,
        )
        dump = deps.model_dump() if hasattr(deps, "model_dump") else (deps if isinstance(deps, dict) else {})
        blocking = dump.get("blocking") or []
        if isinstance(blocking, dict):
            blocking = blocking.get("results") or list(blocking.values())
        blocking_ids = ids(blocking)
        # blocking may also be plain UUID strings
        for b in blocking if isinstance(blocking, list) else []:
            if isinstance(b, str):
                blocking_ids.add(b)
        if str(tgt.id) not in blocking_ids:
            ok = False
            blob_hit = str(tgt.id) in str(dump)
            note = f"no blocking relation from source to {tgt.id}; blocking_ids={sorted(blocking_ids)}"
            if blob_hit:
                note += " (target id appears elsewhere in dump — wrong direction)"
            notes.append(note)
        else:
            notes.append("blocking relation present")
    except Exception as exc:
        ok = False
        notes.append(f"dependencies list failed: {exc}")

    # Links
    try:
        links = plane.work_items.links.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=src.id,
        )
        rows = links.results if hasattr(links, "results") else links
        urls = {(getattr(ln, "url", None) or "").strip() for ln in (rows or [])}
        if W7_URL not in urls:
            ok = False
            notes.append(f"link {W7_URL!r} missing; have {sorted(urls)}")
        else:
            notes.append("reference URL present")
    except Exception as exc:
        ok = False
        notes.append(f"links list failed: {exc}")

    return ok, "; ".join(notes)


W7_TASK: dict[str, Any] = {
    "id": "W7",
    "tags": {"write", "tier1"},
    "prompt": (
        f"In project {{project}}, mark the work item '{W7_SOURCE_TITLE}' as blocking "
        f"'{W7_TARGET_TITLE}', and add the reference URL {W7_URL} on the blocking item."
    ),
    "optimal_calls": 3,
    "optimal_tools": {
        "list_work_items",
        "create_work_item_relation",
        "create_work_item_link",
    },
    "alternate_tools": {
        "search_work_items",
        "list_work_item_relations",
        "list_work_item_relation_definitions",
        "list_work_item_links",
        "retrieve_work_item",
    },
    "needs": {"items"},
    "verify": verify_w7,
}


async def verify_w8(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """W8: work log of exactly 120 minutes exists on the target item.

    Note: plane-sdk Create work log has no logged-date field — 'yesterday' in the
    prompt cannot be asserted; only duration is verified.
    """
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    item = find_item_by_name(plane, workspace_slug, project_id, W8_TITLE)
    if item is None:
        return False, f"item {W8_TITLE!r} not found"
    logs = plane.work_items.work_logs.list(
        workspace_slug=workspace_slug,
        project_id=project_id,
        work_item_id=item.id,
    )
    rows = logs if isinstance(logs, list) else (logs.results if hasattr(logs, "results") else logs)
    durations = [int(getattr(w, "duration", 0) or 0) for w in (rows or [])]
    if 120 in durations:
        return True, "work log duration=120 present"
    return False, f"no 120-minute work log; durations={durations}"


W8_TASK: dict[str, Any] = {
    "id": "W8",
    "tags": {"write", "tier1"},
    "prompt": (f"In project {{project}}, log 2 hours of work on the item titled '{W8_TITLE}' for yesterday."),
    "optimal_calls": 2,
    "optimal_tools": {"list_work_items", "create_work_log"},
    "alternate_tools": {
        "search_work_items",
        "list_work_logs",
        "retrieve_work_item",
        "list_projects",
    },
    "needs": {"items"},
    "verify": verify_w8,
}


async def verify_w9(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """W9 (extra): bulk priority change — the three non-R1 urgent titles are now high."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    # All urgent fixtures except we ask agent to set medium-priority batch targets.
    # Prompt targets the three titles starting with Session/Inventory/Checkout (non-R1 urgent).
    targets = [
        "Checkout times out on 3DS challenge",
        "Session cookie not rotated after login",
        "Inventory count goes negative under load",
    ]
    wrong: list[str] = []
    for title in targets:
        item = find_item_by_name(plane, workspace_slug, project_id, title)
        if not item:
            wrong.append(f"{title}: missing")
            continue
        detail = plane.work_items.retrieve(workspace_slug=workspace_slug, project_id=project_id, work_item_id=item.id)
        pr = (detail.priority or "").lower()
        if pr != "high":
            wrong.append(f"{title}: priority={pr!r}")
    if wrong:
        return False, "; ".join(wrong)
    return True, "3 items priority=high"


W9_TASK: dict[str, Any] = {
    "id": "W9",
    "tags": {"write", "tier1", "extra"},
    "prompt": (
        "In project {project}, set priority to high on these three work items in one "
        "batch: 'Checkout times out on 3DS challenge', "
        "'Session cookie not rotated after login', "
        "'Inventory count goes negative under load'."
    ),
    # Extra: exercises bulk_update_work_items (not in original DESIGN 20).
    "optimal_calls": 4,
    "optimal_tools": {
        "list_work_items",
        "update_work_item",
    },
    "alternate_tools": {
        "search_work_items",
        "list_projects",
        "retrieve_work_item",
    },
    "needs": {"items"},
    "verify": verify_w9,
}


async def verify_w10(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """W10 (extra): project page named Eval Runbook exists."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    try:
        resp = plane.pages.list_project_pages(workspace_slug=workspace_slug, project_id=project_id)
        rows = resp.results if hasattr(resp, "results") else resp
    except Exception as exc:
        return False, f"list pages failed: {exc}"
    names = {(getattr(p, "name", None) or "").strip() for p in (rows or [])}
    if "Eval Runbook" not in names:
        return False, f"page 'Eval Runbook' missing; have {sorted(names)}"
    return True, "page Eval Runbook present"


W10_TASK: dict[str, Any] = {
    "id": "W10",
    "tags": {"write", "tier1", "extra"},
    "prompt": (
        "In project {project}, create a project page named 'Eval Runbook' with body "
        "text 'Rollback steps for eval harness'."
    ),
    # Extra: exercises pages family (create_page / get_page).
    "optimal_calls": 2,
    "optimal_tools": {"list_projects", "create_page"},
    "alternate_tools": {
        "list_pages",
        "retrieve_page",
        "attach_page_to_work_item",
    },
    "needs": set(),
    "verify": verify_w10,
}


WRITE_TASKS: list[dict[str, Any]] = [
    W1_TASK,
    W2_TASK,
    W3_TASK,
    W4_TASK,
    W5_TASK,
    W6_TASK,
    W7_TASK,
    W8_TASK,
    W9_TASK,
    W10_TASK,
]


__all__ = [
    "WRITE_TASKS",
    "verify_w1",
    "verify_w2",
    "verify_w3",
    "verify_w4",
    "verify_w5",
    "verify_w6",
    "verify_w7",
    "verify_w8",
    "verify_w9",
    "verify_w10",
]
