"""Task definitions (plain dicts) and verifier functions for the eval harness."""

from __future__ import annotations

import hashlib
import json
import re
import string
from typing import Any

from plane.errors.errors import HttpError
from plane.models.enums import PropertyType
from plane.models.query_params import RetrieveQueryParams, WorkItemQueryParams

from evals.seed import (
    CUSTOMER_NAME,
    CUSTOMER_REQUEST_NAME,
    CYCLE_CURRENT,
    CYCLE_PAST,
    DEBIAS_CUSTOMER_PROP_DISPLAY,
    DEBIAS_RELEASE_TAG_VERSION,
    INTAKE_BILLING_TITLE,
    INTAKE_SPAM_TITLE,
    MODULE_COMPLETED_TITLES,
    MODULE_NAME,
    R1_TITLE,
    R5_COMMENT_PHRASES,
    R5_TITLE,
    RELEASE_CHANGELOG_TEXT,
    RELEASE_NAME,
    W2_TITLE,
    W3_TITLE,
    W7_SOURCE_TITLE,
    W7_TARGET_TITLE,
    W7_URL,
    W8_TITLE,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TaskSkipped(Exception):
    """Verifier signals that this task-rep should be recorded as skipped, not failed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PromptBindError(RuntimeError):
    """Live prompt could not bind required seed IDs (classified as infra_seed)."""


def format_task_prompt(
    task: dict[str, Any],
    ctx: dict[str, Any] | None = None,
    *,
    strict: bool = False,
) -> str:
    """Render a task prompt with seed-bound placeholders.

    Always provides ``project`` (from ctx or a dry-run sample). Tasks that hand
    the agent concrete UUIDs / PROJ-N identifiers supply extra keys via an
    optional ``prompt_bind(ctx) -> dict`` callable on the task dict.

    When ``strict=True`` (live runs), empty-string values or binder exceptions
    raise ``PromptBindError`` so the harness records ``infra_seed`` rather than
    sending a blank-ID prompt to the agent. Dry-run uses ``strict=False`` and
    fills missing keys with explicit ``<name>`` markers.
    """
    tpl = str(task.get("prompt") or "")
    fields: dict[str, Any] = {
        "project": (ctx or {}).get("project_name") or "EVAL deadbeef",
    }
    binder = task.get("prompt_bind")
    if callable(binder) and ctx is not None:
        try:
            extra = binder(ctx) or {}
        except Exception as exc:
            if strict:
                raise PromptBindError(
                    f"prompt_bind failed for task {task.get('id')}: {type(exc).__name__}: {exc}"
                ) from exc
            extra = {}
        if isinstance(extra, dict):
            for key, val in extra.items():
                if val is None:
                    if strict:
                        raise PromptBindError(f"prompt_bind returned None for {{{key}}} (task {task.get('id')})")
                    continue
                text = str(val).strip()
                if not text:
                    if strict:
                        raise PromptBindError(f"prompt_bind returned empty {{{key}}} for task {task.get('id')}")
                    continue
                fields[key] = text
    # Collect required placeholders from the template.
    required = [name for _, name, _, _ in string.Formatter().parse(tpl) if name]
    for name in required:
        if name in fields and str(fields[name]).strip() and not str(fields[name]).startswith("<"):
            continue
        if strict:
            raise PromptBindError(f"missing prompt field {{{name}}} for task {task.get('id')}")
        fields.setdefault(name, f"<{name}>")
    return tpl.format(**fields)


def _word_boundary(value: str) -> re.Pattern[str]:
    """Compile a case-insensitive word-boundary match for an exact seeded value."""
    return re.compile(rf"\b{re.escape(value)}\b", re.IGNORECASE)


def _reports_exact_int(text: str, n: int) -> bool:
    """True when ``text`` contains integer ``n`` as a whole word (not a substring of 10)."""
    return bool(_word_boundary(str(int(n))).search(text or ""))


def _whole_answer_int(text: str) -> int | None:
    """If the answer (or its last non-empty line) is exactly an integer, return it.

    Letters must not appear — only surrounding whitespace/punctuation is ignored —
    so prose like ``There are 3 comments…`` is not a whole-answer int. A **leading
    minus** attached to the number is preserved (``-3`` → -3, not 3).
    """

    def _as_int(s: str) -> int | None:
        # Collapse whitespace; then the whole string must be optional sign + digits
        # with only non-word punctuation wrappers (prefix must not eat the sign).
        compact = re.sub(r"\s+", "", s or "")
        m = re.fullmatch(r"[^\w+-]*([+-]?\d+)[^\w+-]*", compact, flags=re.UNICODE)
        if m:
            return int(m.group(1))
        return None

    blob = text or ""
    v = _as_int(blob)
    if v is not None:
        return v
    lines = [ln for ln in blob.splitlines() if ln.strip()]
    if lines:
        return _as_int(lines[-1])
    return None


def reports_contract_int(text: str, truth: int) -> bool:
    """True when final text reports ``truth`` via the explicit ``count: N`` contract.

    1. Scan lines matching ``^count:\\s*(-?\\d+)\\s*$`` (case-insensitive, surrounding
       whitespace allowed). Use the **last** match; require signed equality with
       ``truth``.
    2. Fallback: whole-answer / last-line bare integer (:func:`_whole_answer_int`).
    3. No match at all → False (ignoring an explicit format instruction is a fail).
    """
    last: int | None = None
    for line in (text or "").splitlines():
        m = re.fullmatch(r"\s*count:\s*(-?\d+)\s*", line, flags=re.IGNORECASE)
        if m:
            last = int(m.group(1))
    if last is not None:
        return last == int(truth)
    whole = _whole_answer_int(text)
    if whole is not None:
        return whole == int(truth)
    return False


def _as_id(obj: Any) -> str | None:
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj
    return getattr(obj, "id", None) or (obj.get("id") if isinstance(obj, dict) else None)


def _ids(items: Any) -> set[str]:
    out: set[str] = set()
    for item in items or []:
        i = _as_id(item)
        if i:
            out.add(str(i))
    return out


def _find_items_by_name(plane: Any, workspace_slug: str, project_id: str, name: str) -> list[Any]:
    """Return all work items with exact name, newest first (by created_at)."""
    matches: list[Any] = []
    cursor = None
    while True:
        params = WorkItemQueryParams(cursor=cursor, per_page=100) if cursor else WorkItemQueryParams(per_page=100)
        page = plane.work_items.list(workspace_slug=workspace_slug, project_id=project_id, params=params)
        for item in page.results or []:
            if (item.name or "").strip() == name:
                matches.append(item)
        if not page.next_page_results:
            break
        cursor = page.next_cursor

    def _created_key(item: Any) -> str:
        return str(getattr(item, "created_at", None) or "")

    matches.sort(key=_created_key, reverse=True)
    return matches


def _find_item_by_name(plane: Any, workspace_slug: str, project_id: str, name: str) -> Any | None:
    """Locate a work item by exact name; when duplicates exist, prefer the newest."""
    matches = _find_items_by_name(plane, workspace_slug, project_id, name)
    return matches[0] if matches else None


def _state_name(plane: Any, workspace_slug: str, project_id: str, state_ref: Any) -> str | None:
    """Resolve a state UUID or expanded object to its display name."""
    if state_ref is None:
        return None
    if hasattr(state_ref, "name") and state_ref.name:
        return str(state_ref.name)
    if isinstance(state_ref, dict) and state_ref.get("name"):
        return str(state_ref["name"])
    state_id = _as_id(state_ref)
    if not state_id:
        return None
    try:
        state = plane.states.retrieve(workspace_slug=workspace_slug, project_id=project_id, state_id=state_id)
        return state.name
    except HttpError as exc:
        if exc.status_code not in (404, 405):
            raise
    # Fall back to listing states and matching by id.
    page = plane.states.list(workspace_slug=workspace_slug, project_id=project_id)
    results = page.results if hasattr(page, "results") else page
    for s in results or []:
        if str(s.id) == str(state_id):
            return s.name
    return None


def _state_group(plane: Any, workspace_slug: str, project_id: str, state_ref: Any) -> str | None:
    if state_ref is None:
        return None
    if hasattr(state_ref, "group") and state_ref.group:
        return str(state_ref.group)
    if isinstance(state_ref, dict) and state_ref.get("group"):
        return str(state_ref["group"])
    state_id = _as_id(state_ref)
    if not state_id:
        return None
    page = plane.states.list(workspace_slug=workspace_slug, project_id=project_id)
    for s in page.results or []:
        if str(s.id) == str(state_id):
            return getattr(s, "group", None)
    return None


def _is_not_found(exc: BaseException) -> bool:
    return isinstance(exc, HttpError) and exc.status_code in (404, 405)


def _final_text(run: dict[str, Any]) -> str:
    return run.get("final_text") or ""


def _count_open_urgent(plane: Any, workspace_slug: str, project_id: str) -> int:
    """Count urgent items whose state group is not completed/cancelled (resolve at verify)."""
    page = plane.states.list(workspace_slug=workspace_slug, project_id=project_id)
    closed_ids = {str(s.id) for s in (page.results or []) if getattr(s, "group", None) in ("completed", "cancelled")}
    n = 0
    cursor = None
    while True:
        params = WorkItemQueryParams(cursor=cursor, per_page=100) if cursor else WorkItemQueryParams(per_page=100)
        resp = plane.work_items.list(workspace_slug=workspace_slug, project_id=project_id, params=params)
        for item in resp.results or []:
            if (getattr(item, "priority", None) or "").lower() != "urgent":
                continue
            sid = _as_id(item.state)
            if sid and str(sid) in closed_ids:
                continue
            n += 1
        if not resp.next_page_results:
            break
        cursor = resp.next_cursor
    return n


# ---------------------------------------------------------------------------
# Verifiers — async (plane, ctx, run) -> (bool, note)
# ---------------------------------------------------------------------------


async def verify_r1(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R1: final text must name the target item's state and no other seeded state.

    Matching rule: word-boundary, case-insensitive regex on the exact state name
    resolved from the API at verify time (never hardcoded). Additionally fail if
    any *other* project state name also matches (blocks guessing/list_states echo).
    """
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    title = R1_TITLE
    item = _find_item_by_name(plane, workspace_slug, project_id, title)
    if item is None:
        return False, f"seeded item {title!r} not found"
    detail = plane.work_items.retrieve(workspace_slug=workspace_slug, project_id=project_id, work_item_id=item.id)
    expected = _state_name(plane, workspace_slug, project_id, detail.state)
    if not expected:
        # Prefer the seeded name when API is sparse.
        expected = ctx.get("r1_state_name")
    if not expected:
        return False, "could not resolve expected state name from API"

    final_text = _final_text(run)
    if not _word_boundary(expected).search(final_text):
        return False, f"final text missing state name {expected!r}"

    other_states = [n for n in (ctx.get("state_names") or []) if n and n.casefold() != expected.casefold()]
    collisions = [n for n in other_states if _word_boundary(n).search(final_text)]
    if collisions:
        return (
            False,
            f"final text names other state(s) {collisions!r} besides expected {expected!r}",
        )
    return True, f"final text names only state {expected!r}"


async def verify_r2(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R2: final text must contain the exact urgent-open count (word-boundary)."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    expected = _count_open_urgent(plane, workspace_slug, project_id)
    final_text = _final_text(run)
    # Word-boundary on the decimal form of the count (blocks "4" matching "24").
    if not _word_boundary(str(expected)).search(final_text):
        return False, f"final text missing urgent-open count {expected}"
    return True, f"final text names count {expected}"


async def verify_r3(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R3: final text must include each seeded assigned-to-me / due-this-week title."""
    titles = list(ctx.get("r3_due_titles") or [])
    if not titles:
        return False, "no R3 due titles in seed ctx"
    final_text = _final_text(run)
    missing = [t for t in titles if not _word_boundary(t).search(final_text)]
    if missing:
        return False, f"final text missing title(s) {missing!r}"
    return True, f"final text names {len(titles)} due-this-week assigned items"


async def verify_r4(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R4: final text must mention the active cycle name and the overdue item title."""
    final_text = _final_text(run)
    notes: list[str] = []
    ok = True
    if not _word_boundary(CYCLE_CURRENT).search(final_text):
        ok = False
        notes.append(f"missing active cycle {CYCLE_CURRENT!r}")
    else:
        notes.append(f"names {CYCLE_CURRENT}")
    overdue = ctx.get("r4_overdue_title")
    if overdue:
        if not _word_boundary(overdue).search(final_text):
            # Soft: also accept "overdue" keyword + any active item title.
            if "overdue" not in final_text.casefold():
                ok = False
                notes.append(f"missing overdue title {overdue!r}")
            else:
                notes.append("mentions overdue (title not exact)")
        else:
            notes.append(f"names overdue {overdue!r}")
    return ok, "; ".join(notes)


async def verify_r5(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R5: final text must include seeded comment phrases (word-boundary)."""
    phrases = list(ctx.get("r5_comment_phrases") or R5_COMMENT_PHRASES)
    final_text = _final_text(run)
    missing = [p for p in phrases if not _word_boundary(p).search(final_text)]
    if missing:
        return False, f"final text missing comment phrase(s) {missing!r}"
    return True, f"final text names {len(phrases)} discussion phrases"


async def verify_r6(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R6: final text must name the project that has more open bugs (resolved at verify)."""
    expected = ctx.get("r6_more_bugs_project") or ctx.get("second_project_name")
    if not expected:
        return False, "second project name missing from seed ctx"
    final_text = _final_text(run)
    # Match the full project name or the distinctive " B" suffix run8 form.
    if _word_boundary(expected).search(final_text):
        return True, f"final text names project with more bugs {expected!r}"
    # Allow matching just the identifier-ish trailing token (e.g. run8 + B).
    run8 = ctx.get("run8") or ""
    alt = f"EVAL {run8} B"
    if _word_boundary(alt).search(final_text) or (run8 and run8 in final_text and " B" in final_text):
        return True, f"final text names second project ({alt})"
    return False, f"final text missing project with more bugs {expected!r}"


async def verify_w1(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """W1: assert end-state via Plane API (title, priority, assignee, auth label)."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    title = "Login page 500s on empty password"
    matches = _find_items_by_name(plane, workspace_slug, project_id, title)
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
    assignee_ids = _ids(detail.assignees)
    if me_id not in assignee_ids:
        ok = False
        notes.append(f"assignees={sorted(assignee_ids)} missing me={me_id}")
    else:
        notes.append("assigned to me")

    auth_label_id = (ctx.get("labels") or {}).get("auth")
    label_ids = _ids(detail.labels)
    if not auth_label_id:
        ok = False
        notes.append("auth label id missing from seed ctx")
    elif str(auth_label_id) not in label_ids:
        ok = False
        notes.append(f"labels={sorted(label_ids)} missing auth={auth_label_id}")
    else:
        notes.append("auth label attached")

    return ok, "; ".join(notes)


async def verify_w2(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """W2: target item is in a completed-group state (prefer name Done)."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    item = _find_item_by_name(plane, workspace_slug, project_id, W2_TITLE)
    if item is None:
        return False, f"item {W2_TITLE!r} not found"
    detail = plane.work_items.retrieve(workspace_slug=workspace_slug, project_id=project_id, work_item_id=item.id)
    name = _state_name(plane, workspace_slug, project_id, detail.state)
    group = _state_group(plane, workspace_slug, project_id, detail.state)
    if group == "completed" or (name and name.casefold() == "done"):
        return True, f"state={name!r} group={group!r}"
    return False, f"state={name!r} group={group!r} (want completed/Done)"


async def verify_w3(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """W3: target item has a comment containing the prompt phrase 'contrast tokens'."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    item = _find_item_by_name(plane, workspace_slug, project_id, W3_TITLE)
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
    pat = _word_boundary(phrase)
    for c in results:
        html = getattr(c, "comment_html", None) or ""
        stripped = getattr(c, "comment_stripped", None) or ""
        # Some APIs expose plain text under comment_stripped; fall back to html.
        blob = f"{stripped}\n{html}"
        if pat.search(blob):
            return True, f"comment matches {phrase!r}"
    return False, f"no comment contains {phrase!r} ({len(results)} comment(s))"


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
            if not _is_not_found(exc):
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


async def verify_w5(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """W5: all seeded module completed items are archived (not merely deleted)."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    ids = [str(i) for i in (ctx.get("module_completed_ids") or [])]
    if not ids:
        # Fall back to titles.
        for title in MODULE_COMPLETED_TITLES:
            item = _find_item_by_name(plane, workspace_slug, project_id, title)
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
            if _is_not_found(exc):
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


async def verify_w7(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """W7: source blocks target (dependency) AND reference URL link exists on source.

    Only dump['blocking'] ids count — a reverse blocked_by match must not pass.
    """
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    src = _find_item_by_name(plane, workspace_slug, project_id, W7_SOURCE_TITLE)
    tgt = _find_item_by_name(plane, workspace_slug, project_id, W7_TARGET_TITLE)
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
        blocking_ids = _ids(blocking)
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


async def verify_w8(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """W8: work log of exactly 120 minutes exists on the target item.

    Note: plane-sdk Create work log has no logged-date field — 'yesterday' in the
    prompt cannot be asserted; only duration is verified.
    """
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    item = _find_item_by_name(plane, workspace_slug, project_id, W8_TITLE)
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
        item = _find_item_by_name(plane, workspace_slug, project_id, title)
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


async def verify_s1(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """S1: Bug type has an OPTION property 'Severity' with Critical/Major/Minor.

    Only type-scoped property listing is accepted (no project/workspace fallbacks that
    would pass an unattached Severity). Unexpected API errors propagate as harness errors.
    """
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    bug_type_id = (
        (ctx.get("bug_type") or {}).get("id") if isinstance(ctx.get("bug_type"), dict) else ctx.get("bug_type")
    )
    if not bug_type_id:
        raise TaskSkipped("bug_type not seeded")

    try:
        props = list(
            plane.work_item_properties.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                type_id=str(bug_type_id),
            )
            or []
        )
    except HttpError as exc:
        if _is_not_found(exc):
            return False, "Severity property not found on Bug type (type-scoped list empty/404)"
        raise

    severity = None
    for p in props:
        display = (getattr(p, "display_name", None) or getattr(p, "name", None) or "").strip()
        if display.lower() == "severity":
            # Prefer an explicit type link when the API exposes it.
            issue_type = getattr(p, "issue_type", None)
            if issue_type is not None and str(issue_type) not in ("", str(bug_type_id)):
                continue
            severity = p
            break
    if severity is None:
        return False, "Severity property not found on Bug type"

    prop_type = getattr(severity, "property_type", None)
    prop_type_val = prop_type.value if isinstance(prop_type, PropertyType) else prop_type
    if str(prop_type_val or "").upper() != PropertyType.OPTION.value:
        return False, f"Severity property_type={prop_type_val!r} (want OPTION)"

    option_names = {
        (getattr(o, "name", None) or (o.get("name") if isinstance(o, dict) else "") or "").strip()
        for o in (getattr(severity, "options", None) or [])
    }
    if not option_names:
        try:
            opts = plane.work_item_properties.options.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                property_id=severity.id,
            )
            option_names = {(getattr(o, "name", None) or "").strip() for o in (opts or [])}
        except HttpError as exc:
            if not _is_not_found(exc):
                raise
            option_names = set()

    required = {"critical", "major", "minor"}
    have = {n.casefold() for n in option_names if n}
    missing = required - have
    if missing:
        return False, f"Severity options missing {sorted(missing)}; have {sorted(option_names)}"
    return True, "Severity OPTION with Critical/Major/Minor present on Bug type"


async def verify_s2(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """S2: Fibonacci estimate scale exists and target item estimate_point is 5."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    notes: list[str] = []
    ok = True

    # Resolve active estimate + points.
    try:
        est = plane.estimates.retrieve(workspace_slug=workspace_slug, project_id=project_id)
    except Exception as exc:
        return False, f"no project estimate: {exc}"
    est_id = getattr(est, "id", None) or _as_id(est)
    points = plane.estimates.list_points(workspace_slug=workspace_slug, project_id=project_id, estimate_id=est_id)
    point_rows = points if isinstance(points, list) else (points.results if hasattr(points, "results") else points)
    values = {(getattr(p, "value", None) or "").strip() for p in (point_rows or [])}
    fib_like = {"1", "2", "3", "5", "8"}
    if not fib_like.issubset(values):
        ok = False
        notes.append(f"estimate points missing fib subset; have {sorted(values)}")
    else:
        notes.append("fibonacci points present")

    five = next((p for p in (point_rows or []) if (getattr(p, "value", None) or "").strip() == "5"), None)
    item = _find_item_by_name(plane, workspace_slug, project_id, W8_TITLE)
    if item is None:
        ok = False
        notes.append(f"target item {W8_TITLE!r} missing")
    else:
        detail = plane.work_items.retrieve(workspace_slug=workspace_slug, project_id=project_id, work_item_id=item.id)
        ep = getattr(detail, "estimate_point", None)
        ep_id = _as_id(ep) if not isinstance(ep, (int, float)) else None
        # estimate_point may be expanded object or UUID.
        if five is not None and ep_id and str(ep_id) == str(five.id):
            notes.append("item estimate_point=5")
        elif ep is not None and str(getattr(ep, "value", ep)) in ("5", "5.0"):
            notes.append("item estimate value=5")
        else:
            ok = False
            notes.append(f"item estimate_point={ep!r} (want 5)")
    return ok, "; ".join(notes)


async def verify_s3(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """S3: Incident type exists with a required TEXT property.

    Workspace-owned types: probe get_features.is_work_item_types_enabled at verify
    time (S3 needs is empty, so seed never sets bug_type_workspace_level).
    """
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    # Find Incident type (project list first).
    types = list(plane.work_item_types.list(workspace_slug=workspace_slug, project_id=project_id) or [])
    incident = next((t for t in types if (t.name or "").strip().casefold() == "incident"), None)
    if incident is None:
        # Probe workspace feature at verify time — do not rely on seed ctx flags.
        workspace_owns = False
        try:
            features = plane.workspaces.get_features(workspace_slug=workspace_slug)
            dump = features.model_dump() if hasattr(features, "model_dump") else {}
            workspace_owns = bool(dump.get("is_work_item_types_enabled"))
        except Exception:
            workspace_owns = False
        if workspace_owns:
            try:
                wtypes = list(plane.workspace_work_item_types.list(workspace_slug=workspace_slug) or [])
                incident = next((t for t in wtypes if (t.name or "").strip().casefold() == "incident"), None)
            except Exception:
                pass
    if incident is None:
        return False, "Incident work item type not found"

    try:
        props = list(
            plane.work_item_properties.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                type_id=str(incident.id),
            )
            or []
        )
    except HttpError as exc:
        if _is_not_found(exc):
            return False, "no properties on Incident type"
        raise

    required_text = None
    for p in props:
        prop_type = getattr(p, "property_type", None)
        prop_type_val = str(prop_type.value if isinstance(prop_type, PropertyType) else prop_type or "").upper()
        is_required = bool(getattr(p, "is_required", False))
        # TEXT only — no fallback to other required types (OPTION etc.).
        if is_required and prop_type_val in (PropertyType.TEXT.value, "TEXT", "STRING"):
            required_text = p
            break
    if required_text is None:
        return False, f"no required TEXT property on Incident; props={len(props)}"
    display = getattr(required_text, "display_name", None) or getattr(required_text, "name", None)
    return True, f"Incident type + required TEXT property {display!r}"


async def verify_s4(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """S4: billing intake accepted (status=1), spam declined (status=-1)."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    intake = ctx.get("intake") or {}
    billing = intake.get("billing") or {}
    spam = intake.get("spam") or {}
    notes: list[str] = []
    ok = True

    def _status_of(issue_id: str | None, title: str) -> int | None:
        if not issue_id:
            return None
        try:
            row = plane.intake.retrieve(workspace_slug=workspace_slug, project_id=project_id, work_item_id=issue_id)
            return getattr(row, "status", None)
        except Exception:
            # Fall back to list + match title
            try:
                rows = plane.intake.list(workspace_slug=workspace_slug, project_id=project_id)
                results = rows.results if hasattr(rows, "results") else rows
                for r in results or []:
                    detail = getattr(r, "issue_detail", None)
                    name = getattr(detail, "name", None) if detail is not None else None
                    if name and name.strip() == title:
                        return getattr(r, "status", None)
            except Exception:
                return None
        return None

    b_status = _status_of(billing.get("issue_id"), INTAKE_BILLING_TITLE)
    s_status = _status_of(spam.get("issue_id"), INTAKE_SPAM_TITLE)
    # accept=1, decline=-1 per IntakeWorkItemStatusEnum
    if b_status != 1:
        ok = False
        notes.append(f"billing status={b_status!r} (want 1/accepted)")
    else:
        notes.append("billing accepted")
    if s_status != -1:
        ok = False
        notes.append(f"spam status={s_status!r} (want -1/declined)")
    else:
        notes.append("spam declined")
    return ok, "; ".join(notes)


async def verify_s5(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """S5: project cycles + worklogs AND workspace customers enabled.

    Gates (plane-ee):
      - project.cycle_view — cycles create/list
      - project.is_time_tracking_enabled — worklogs
      - WorkspaceFeature.is_customer_enabled (API field ``customers``) — customer create 403
    """
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    notes: list[str] = []
    ok = True

    proj = plane.projects.retrieve(workspace_slug=workspace_slug, project_id=project_id)
    cycle_view = bool(getattr(proj, "cycle_view", None))
    time_tracking = bool(getattr(proj, "is_time_tracking_enabled", None))
    if not cycle_view:
        ok = False
        notes.append(f"cycle_view={getattr(proj, 'cycle_view', None)!r} (want True)")
    else:
        notes.append("cycle_view=True")
    if not time_tracking:
        ok = False
        notes.append(f"is_time_tracking_enabled={getattr(proj, 'is_time_tracking_enabled', None)!r} (want True)")
    else:
        notes.append("is_time_tracking_enabled=True")

    try:
        feat = plane.projects.get_features(workspace_slug=workspace_slug, project_id=project_id)
        dump = feat.model_dump() if hasattr(feat, "model_dump") else (feat if isinstance(feat, dict) else {})
        cycles_flag = dump.get("cycles") if isinstance(dump, dict) else getattr(feat, "cycles", None)
        if not cycles_flag:
            ok = False
            notes.append(f"features.cycles={cycles_flag!r} (want True)")
        else:
            notes.append("features.cycles=True")
    except Exception as exc:
        ok = False
        notes.append(f"project get_features failed: {exc}")

    # Workspace customers toggle (is_customer_enabled behind API field ``customers``).
    try:
        ws_feat = plane.workspaces.get_features(workspace_slug=workspace_slug)
        ws_dump = (
            ws_feat.model_dump() if hasattr(ws_feat, "model_dump") else (ws_feat if isinstance(ws_feat, dict) else {})
        )
        customers_on = None
        if isinstance(ws_dump, dict):
            customers_on = ws_dump.get("customers")
            if customers_on is None:
                customers_on = ws_dump.get("is_customer_enabled")
        if customers_on is None:
            customers_on = getattr(ws_feat, "customers", None)
            if customers_on is None:
                customers_on = getattr(ws_feat, "is_customer_enabled", None)
        if not customers_on:
            ok = False
            notes.append(f"workspace.customers={customers_on!r} (want True)")
        else:
            notes.append("workspace.customers=True")
    except Exception as exc:
        ok = False
        notes.append(f"workspace get_features failed: {exc}")

    return ok, "; ".join(notes)


async def verify_c1(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """C1: customer 'Acme Corp' has request 'SSO support' linked to the R1 work item.

    Anchors: exact customer name, exact request name, and the R1_TITLE work item id
    resolved from the eval project at verify time (must be among linked ids).
    """
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    notes: list[str] = []
    ok = True

    # Resolve the required link target first (seeded Payment webhook item).
    r1 = _find_item_by_name(plane, workspace_slug, project_id, R1_TITLE)
    if r1 is None:
        return False, f"R1 item {R1_TITLE!r} not found in project"

    customers = plane.customers.list(workspace_slug=workspace_slug)
    rows = customers.results if hasattr(customers, "results") else customers
    # Exact name only — do not match arbitrary acme* customers.
    acme = next((c for c in (rows or []) if (c.name or "").strip() == CUSTOMER_NAME), None)
    if acme is None:
        return False, f"customer {CUSTOMER_NAME!r} not found"

    # Track for teardown if agent-created
    if not ctx.get("customer"):
        ctx.setdefault("workspace_objects", []).append({"kind": "customer", "id": acme.id})

    reqs = plane.customers.requests.list(workspace_slug=workspace_slug, customer_id=acme.id)
    rrows = reqs.results if hasattr(reqs, "results") else reqs
    sso = next(
        (r for r in (rrows or []) if (r.name or "").strip() == CUSTOMER_REQUEST_NAME),
        None,
    )
    if sso is None:
        ok = False
        notes.append(f"request {CUSTOMER_REQUEST_NAME!r} missing")
    else:
        notes.append("SSO request present")

    # Require the R1 work item among customer-linked work items.
    try:
        wi = plane.customers.work_items.list(workspace_slug=workspace_slug, customer_id=acme.id)
        wi_rows = list(wi.results if hasattr(wi, "results") else wi or [])
        linked_ids = _ids(wi_rows)
        # Plain string ids also count.
        for row in wi_rows:
            if isinstance(row, str):
                linked_ids.add(row)
            elif isinstance(row, dict) and row.get("id"):
                linked_ids.add(str(row["id"]))
            else:
                # Customer work item wrappers may expose work_item / issue field.
                for attr in ("work_item", "work_item_id", "issue", "issue_id"):
                    ref = getattr(row, attr, None) if not isinstance(row, dict) else row.get(attr)
                    rid = _as_id(ref)
                    if rid:
                        linked_ids.add(str(rid))
        if str(r1.id) not in linked_ids:
            ok = False
            notes.append(f"R1 item {r1.id} not linked; linked={sorted(linked_ids)}")
        else:
            notes.append(f"R1 item {r1.id} linked")
    except Exception as exc:
        ok = False
        notes.append(f"list customer work items failed: {exc}")

    return ok, "; ".join(notes)


async def verify_c2(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """C2: final text mentions release 1.2.0 and at least one seeded changelog phrase."""
    final_text = _final_text(run)
    notes: list[str] = []
    ok = True
    if not _word_boundary(RELEASE_NAME).search(final_text):
        ok = False
        notes.append(f"missing release name {RELEASE_NAME!r}")
    else:
        notes.append(f"names {RELEASE_NAME}")
    changelog = ctx.get("release_changelog_text") or RELEASE_CHANGELOG_TEXT
    # Match distinctive fragments from the seeded changelog.
    fragments = ["OAuth login hardening", "webhook retry backoff"]
    hit = [f for f in fragments if _word_boundary(f).search(final_text)]
    if not hit:
        # Also accept substring of full changelog without word-boundary if short.
        if changelog[:40].casefold() not in final_text.casefold():
            ok = False
            notes.append("missing changelog content")
        else:
            notes.append("changelog substring present")
    else:
        notes.append(f"changelog phrases {hit}")
    return ok, "; ".join(notes)


async def verify_r7(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """R7 (extra): final text names at least one legal next state for the R1 item.

    Resolves available completed/started/unstarted states at verify time and
    requires a word-boundary hit on one of them (or explicit 'unrestricted').
    """
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    page = plane.states.list(workspace_slug=workspace_slug, project_id=project_id)
    names = [(s.name or "").strip() for s in (page.results or []) if (s.name or "").strip()]
    final_text = _final_text(run)
    if "unrestricted" in final_text.casefold() or "any state" in final_text.casefold():
        return True, "agent reported unrestricted transitions"
    hits = [n for n in names if _word_boundary(n).search(final_text)]
    if not hits:
        return False, f"final text names none of project states {names}"
    return True, f"final text names state(s) {hits}"


# ---------------------------------------------------------------------------
# ID-in-hand (I*) + long-tail (L*) de-biasing verifiers
# ---------------------------------------------------------------------------

# Stable titles used by ID-in-hand binders (seeded by needs={"items", ...}).
I1_TITLE = R1_TITLE  # update priority by UUID
I2_TITLE = W2_TITLE  # fetch by PROJ-N identifier
I3_TITLE = "Footer year still says 2024"  # add to cycle by UUIDs (not on a cycle)
I4_TITLE = W3_TITLE  # attach label by UUIDs
L1_TITLE = W8_TITLE  # worklog + project summary
L2_TITLE = R5_TITLE  # activities (seeded comments produce activity rows)
L5_TITLE = R1_TITLE  # attachment listing
L3_TAG_VERSION = DEBIAS_RELEASE_TAG_VERSION
L4_PROP_DISPLAY = DEBIAS_CUSTOMER_PROP_DISPLAY
L4_PROP_VALUE = "Enterprise"


def _bind_item_uuid(title: str):
    def _bind(ctx: dict[str, Any]) -> dict[str, str]:
        wid = str((ctx.get("items") or {}).get(title) or "")
        return {"work_item_id": wid}

    return _bind


def _bind_item_identifier(title: str):
    def _bind(ctx: dict[str, Any]) -> dict[str, str]:
        ident = str((ctx.get("item_identifiers") or {}).get(title) or "")
        return {"work_item_identifier": ident}

    return _bind


def _bind_i3(ctx: dict[str, Any]) -> dict[str, str]:
    wid = str((ctx.get("items") or {}).get(I3_TITLE) or "")
    cycle_id = str(ctx.get("cycle_current_id") or (ctx.get("cycles") or {}).get(CYCLE_CURRENT) or "")
    return {"work_item_id": wid, "cycle_id": cycle_id}


def _bind_i4(ctx: dict[str, Any]) -> dict[str, str]:
    wid = str((ctx.get("items") or {}).get(I4_TITLE) or "")
    label_id = str((ctx.get("labels") or {}).get("perf") or "")
    return {"work_item_id": wid, "label_id": label_id}


async def verify_i1(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """I1: seeded R1 item priority is high (updated by UUID, not name)."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    wid = (ctx.get("items") or {}).get(I1_TITLE)
    if not wid:
        return False, f"seed item {I1_TITLE!r} missing"
    detail = plane.work_items.retrieve(workspace_slug=workspace_slug, project_id=project_id, work_item_id=wid)
    pr = (detail.priority or "").lower() if detail.priority else ""
    if pr == "high":
        return True, f"work_item {wid} priority=high"
    return False, f"work_item {wid} priority={pr!r} (want high)"


async def verify_i2(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """I2: final text names the state of the identifier-target item."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    wid = (ctx.get("items") or {}).get(I2_TITLE)
    if not wid:
        return False, f"seed item {I2_TITLE!r} missing"
    detail = plane.work_items.retrieve(workspace_slug=workspace_slug, project_id=project_id, work_item_id=wid)
    name = _state_name(plane, workspace_slug, project_id, detail.state)
    if not name:
        return False, "target state name unresolved"
    final_text = _final_text(run)
    if _word_boundary(name).search(final_text):
        return True, f"final text names state {name!r}"
    return False, f"final text missing state {name!r}"


async def verify_i3(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """I3: work item UUID is on the target cycle UUID."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    wid = str((ctx.get("items") or {}).get(I3_TITLE) or "")
    cycle_id = str(ctx.get("cycle_current_id") or (ctx.get("cycles") or {}).get(CYCLE_CURRENT) or "")
    if not wid or not cycle_id:
        return False, "seed work_item_id/cycle_id missing"
    page = plane.cycles.list_work_items(workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id)
    rows = page.results if hasattr(page, "results") else page
    ids = {str(getattr(r, "id", None) or r) for r in (rows or [])}
    # list may return issue wrappers with issue/id fields
    for r in rows or []:
        for attr in ("id", "issue", "work_item_id"):
            v = getattr(r, attr, None)
            if v is not None:
                ids.add(str(v if not hasattr(v, "id") else v.id))
    if wid in ids:
        return True, f"item {wid} on cycle {cycle_id}"
    return False, f"item {wid} not on cycle {cycle_id}; have {sorted(ids)[:12]}"


async def verify_i4(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """I4: work item has the seeded perf label id attached."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    wid = (ctx.get("items") or {}).get(I4_TITLE)
    label_id = (ctx.get("labels") or {}).get("perf")
    if not wid or not label_id:
        return False, "seed work_item_id/label_id missing"
    detail = plane.work_items.retrieve(
        workspace_slug=workspace_slug,
        project_id=project_id,
        work_item_id=wid,
        params=RetrieveQueryParams(expand="labels"),
    )
    label_ids = _ids(detail.labels)
    if str(label_id) in label_ids:
        return True, f"label {label_id} on {wid}"
    return False, f"labels={sorted(label_ids)} missing perf={label_id}"


async def verify_i5(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """I5: target item priority is low (updated by UUID)."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    wid = (ctx.get("items") or {}).get(I3_TITLE)
    if not wid:
        return False, f"seed item {I3_TITLE!r} missing"
    detail = plane.work_items.retrieve(workspace_slug=workspace_slug, project_id=project_id, work_item_id=wid)
    pr = (detail.priority or "").lower() if detail.priority else ""
    if pr == "low":
        return True, f"work_item {wid} priority=low"
    return False, f"work_item {wid} priority={pr!r} (want low)"


def _l1_duration_reported(final_text: str) -> bool:
    """Numeric duration only: whole-word 90 or 1.5 (not English 'ninety')."""
    return bool(_word_boundary("90").search(final_text)) or bool(re.search(r"\b1\.5\b", final_text))


def _l1_person_names_from_summary(sum_rows: Any) -> list[str]:
    """Best-effort actor/assignee display strings from project worklog summary rows."""
    names: list[str] = []
    for row in sum_rows or []:
        dump = row.model_dump() if hasattr(row, "model_dump") else {}
        if not isinstance(dump, dict):
            dump = {}
        candidates: list[Any] = []
        for attr in (
            "actor",
            "user",
            "display_name",
            "owned_by",
            "created_by",
            "assignee",
            "email",
            "first_name",
            "last_name",
        ):
            v = getattr(row, attr, None)
            if v is None and dump:
                v = dump.get(attr)
            if v is None:
                continue
            if hasattr(v, "display_name") or hasattr(v, "email"):
                candidates.append(
                    getattr(v, "display_name", None) or getattr(v, "email", None) or getattr(v, "id", None)
                )
            elif isinstance(v, dict):
                candidates.append(v.get("display_name") or v.get("email") or v.get("id"))
            else:
                candidates.append(v)
        for c in candidates:
            s = str(c or "").strip()
            if s and s not in names:
                names.append(s)
    return names


def _l1_summary_substance(final_text: str, *, title: str, sum_rows: Any) -> bool:
    """Summary half of L1: item title, person from summary, or words summary/total.

    Deliberately does *not* accept bare 'logged' / 'worklog' — the prompt asks to
    report the project worklog summary (who/what has time logged).
    """
    low = final_text.casefold()
    if "summary" in low or "total" in low:
        return True
    if title and _word_boundary(title).search(final_text):
        return True
    for person in _l1_person_names_from_summary(sum_rows):
        if len(person) >= 2 and _word_boundary(person).search(final_text):
            return True
    return False


async def verify_l1(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """L1: 90-minute work log on the correct item AND final text reports duration + summary.

    Duration: numeric whole-word ``90`` or ``1.5`` only (not English 'ninety').
    Summary substance: item title, a person/assignee from the project summary, or
    the words ``summary`` / ``total``. Bare "90 minutes of work" fails by design.
    """
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    wid = (ctx.get("items") or {}).get(L1_TITLE)
    if not wid:
        return False, f"seed item {L1_TITLE!r} missing"
    # SDK: 90m log must be on THIS work item (list is already scoped to work_item_id).
    logs = plane.work_items.work_logs.list(workspace_slug=workspace_slug, project_id=project_id, work_item_id=wid)
    rows = logs if isinstance(logs, list) else (logs.results if hasattr(logs, "results") else logs)
    durations = [int(getattr(w, "duration", 0) or 0) for w in (rows or [])]
    if 90 not in durations:
        return False, f"no 90-minute work log on target item {wid}; durations={durations}"

    sum_rows: list[Any] = []
    try:
        summary = plane.projects.get_worklog_summary(workspace_slug=workspace_slug, project_id=project_id)
        raw = summary if isinstance(summary, list) else (getattr(summary, "results", None) or summary or [])
        sum_rows = list(raw or [])
    except Exception:
        # Summary fetch is optional for person names; duration + title/summary/total still work.
        sum_rows = []

    final_text = _final_text(run)
    if not _l1_duration_reported(final_text):
        return False, "final text missing logged duration (numeric 90 or 1.5)"
    if not _l1_summary_substance(final_text, title=L1_TITLE, sum_rows=sum_rows):
        return False, (
            "final text lacks worklog summary substance "
            "(need item title, person from summary, or words 'summary'/'total')"
        )
    return True, f"90m log on {wid} + final text reports duration and summary substance"


async def verify_l2(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """L2: target has activities; final text reports the count via ``count: N`` contract."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    wid = (ctx.get("items") or {}).get(L2_TITLE)
    if not wid:
        return False, f"seed item {L2_TITLE!r} missing"
    try:
        page = plane.work_items.activities.list(workspace_slug=workspace_slug, project_id=project_id, work_item_id=wid)
    except Exception as exc:
        return False, f"activities.list failed: {exc}"
    rows = page.results if hasattr(page, "results") else page
    n = len(list(rows or []))
    if n < 1:
        return False, "no activities on target (seed comments should create some)"
    final_text = _final_text(run)
    if not reports_contract_int(final_text, n):
        return False, f"final text missing contract count: {n} (need 'count: {n}' or bare integer)"
    return True, f"final text reports activity count {n} via contract"


async def verify_l5(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """L5: final text reports the attachment count via ``count: N`` contract."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    wid = (ctx.get("items") or {}).get(L5_TITLE)
    if not wid:
        return False, f"seed item {L5_TITLE!r} missing"
    try:
        page = plane.work_items.attachments.list(workspace_slug=workspace_slug, project_id=project_id, work_item_id=wid)
    except Exception as exc:
        return False, f"attachments.list failed: {exc}"
    rows = page.results if hasattr(page, "results") else page
    n = len(list(rows or []))
    final_text = _final_text(run)
    if not reports_contract_int(final_text, n):
        return False, f"final text missing contract count: {n} (need 'count: {n}' or bare integer)"
    return True, f"final text reports attachment count {n} via contract"


async def verify_l3(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """L3: workspace has a release tag with version eval-rc1."""
    workspace_slug = ctx["workspace_slug"]
    try:
        page = plane.releases.tags.list(workspace_slug=workspace_slug)
    except Exception as exc:
        return False, f"list release tags failed: {exc}"
    rows = page.results if hasattr(page, "results") else page
    versions = {(getattr(t, "version", None) or "").strip() for t in (rows or [])}
    if L3_TAG_VERSION in versions:
        # Track for teardown if id available.
        for t in rows or []:
            if (getattr(t, "version", None) or "").strip() == L3_TAG_VERSION:
                tid = getattr(t, "id", None)
                if tid:
                    objs = ctx.setdefault("workspace_objects", [])
                    if not any(o.get("kind") == "release_tag" and str(o.get("id")) == str(tid) for o in objs):
                        objs.append({"kind": "release_tag", "id": tid})
                break
        return True, f"release tag {L3_TAG_VERSION!r} present"
    return False, f"tag {L3_TAG_VERSION!r} missing; have {sorted(versions)}"


def _property_type_is_text(prop: Any) -> bool:
    raw = getattr(prop, "property_type", None)
    if raw is None:
        raw = getattr(prop, "type", None)
    if raw is None:
        return False
    if hasattr(raw, "value"):
        raw = raw.value
    if hasattr(raw, "name"):
        # Enum member: PropertyType.TEXT
        name = str(raw.name)
        if name.upper() == "TEXT":
            return True
    s = str(raw).upper()
    return s == "TEXT" or s.endswith(".TEXT") or s == "PROPERTYTYPE.TEXT"


async def verify_l4(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """L4: right customer has TEXT property 'Eval Industry' = Enterprise (exact name)."""
    workspace_slug = ctx["workspace_slug"]
    cust = ctx.get("customer") or {}
    customer_id = cust.get("id") if isinstance(cust, dict) else cust
    if not customer_id:
        return False, "customer missing from seed"
    try:
        props = plane.customers.properties.list(workspace_slug=workspace_slug)
    except Exception as exc:
        return False, f"list customer properties failed: {exc}"
    prop_rows = props.results if hasattr(props, "results") else props
    target_prop: Any | None = None
    for p in prop_rows or []:
        # Exact display_name match only (case-insensitive full match) — not substring "Industry".
        display = (getattr(p, "display_name", None) or "").strip()
        if display.casefold() != L4_PROP_DISPLAY.casefold():
            continue
        if not _property_type_is_text(p):
            return False, (
                f"property {display!r} exists but property_type is not TEXT (got {getattr(p, 'property_type', None)!r})"
            )
        target_prop = p
        break
    if target_prop is None:
        return False, f"no TEXT customer property named exactly {L4_PROP_DISPLAY!r}"
    pid = str(target_prop.id)
    # Track for teardown.
    objs = ctx.setdefault("workspace_objects", [])
    if not any(o.get("kind") == "customer_property" and str(o.get("id")) == pid for o in objs):
        objs.append({"kind": "customer_property", "id": pid})
    try:
        values = plane.customers.property_values.list(workspace_slug=workspace_slug, customer_id=customer_id)
    except Exception as exc:
        return False, f"get property values failed: {exc}"
    if not isinstance(values, dict):
        return False, f"unexpected property_values shape: {type(values)}"
    vals = values.get(pid) or values.get(str(pid)) or []
    flat = [str(v) for v in (vals if isinstance(vals, list) else [vals])]
    if any(L4_PROP_VALUE.casefold() == v.casefold() for v in flat):
        return True, f"customer {customer_id} property {pid} ({L4_PROP_DISPLAY})={L4_PROP_VALUE!r}"
    return False, f"customer {customer_id} property {pid} values {flat} lack {L4_PROP_VALUE!r}"


# ---------------------------------------------------------------------------
# Task catalog (full DESIGN list + extras for uncovered v2 families)
# ---------------------------------------------------------------------------

TASKS: list[dict[str, Any]] = [
    {
        "id": "R1",
        "tags": {"read", "tier1"},
        "prompt": (
            "In project {project}, what is the current state of the work item titled "
            f"'{R1_TITLE}'? Answer with the state name."
        ),
        "optimal_calls": 1,
        "optimal_tools": {"list_work_items"},
        "alternate_tools": {
            "search_work_items",
            "list_archived_work_items",
            "count_work_items",
            "retrieve_work_item",
            "retrieve_work_item_by_identifier",
            "list_projects",
            "list_states",
        },
        "surface_tools": {
            "v2": {
                "optimal_calls": 1,
                "optimal_tools": {"find_work_items"},
                "alternate_tools": {
                    "get_work_item",
                    "search_projects",
                    "list_states",
                    "get_workspace_context",
                    "get_pql_reference",
                },
            },
        },
        "needs": {"items"},
        "verify": verify_r1,
    },
    {
        "id": "R2",
        "tags": {"read", "tier1"},
        "prompt": (
            "In project {project}, how many urgent open work items are there? Answer with the integer count only."
        ),
        "optimal_calls": 1,
        "optimal_tools": {"count_work_items"},
        "alternate_tools": {
            "list_work_items",
            "search_work_items",
            "list_projects",
            "list_states",
            "get_pql_reference",
        },
        "surface_tools": {
            "v2": {
                # No count tool on v2 — find_work_items with priority/state filters is optimal.
                "optimal_calls": 1,
                "optimal_tools": {"find_work_items"},
                "alternate_tools": {
                    "get_work_item",
                    "search_projects",
                    "list_states",
                    "get_workspace_context",
                    "get_pql_reference",
                },
            },
        },
        "needs": {"items"},
        "verify": verify_r2,
    },
    {
        "id": "R3",
        "tags": {"read", "tier1"},
        "prompt": (
            "In project {project}, list work items assigned to me that are due this week. Answer with their titles."
        ),
        "optimal_calls": 2,
        "optimal_tools": {"get_me", "list_work_items"},
        "alternate_tools": {
            "search_work_items",
            "count_work_items",
            "list_projects",
            "get_workspace_members",
            "get_pql_reference",
            "retrieve_work_item",
        },
        "surface_tools": {
            "v2": {
                "optimal_calls": 1,
                "optimal_tools": {"find_work_items"},
                "alternate_tools": {
                    "get_workspace_context",
                    "get_work_item",
                    "search_projects",
                    "get_pql_reference",
                },
            },
        },
        "needs": {"items"},
        "verify": verify_r3,
    },
    {
        "id": "R4",
        "tags": {"read", "tier1"},
        "prompt": (
            "In project {project}, what is in the active cycle, and is anything overdue? "
            f"Name the cycle (expect '{CYCLE_CURRENT}') and any overdue item titles."
        ),
        "optimal_calls": 2,
        "optimal_tools": {"list_cycles", "list_work_items"},
        "alternate_tools": {
            "list_cycle_work_items",
            "retrieve_cycle",
            "search_work_items",
            "list_projects",
            "get_pql_reference",
        },
        "surface_tools": {
            "v2": {
                "optimal_calls": 1,
                "optimal_tools": {"find_work_items"},
                "alternate_tools": {
                    "list_cycles",
                    "get_work_item",
                    "get_pql_reference",
                    "search_projects",
                    "get_workspace_context",
                },
            },
        },
        "needs": {"items", "cycles"},
        "verify": verify_r4,
    },
    {
        "id": "R5",
        "tags": {"read", "tier1"},
        "prompt": (
            f"In project {{project}}, summarize the discussion on the work item titled '{R5_TITLE}'. "
            "Include the key phrases from its comments."
        ),
        "optimal_calls": 2,
        "optimal_tools": {"list_work_items", "list_work_item_comments"},
        "alternate_tools": {
            "search_work_items",
            "retrieve_work_item",
            "retrieve_work_item_by_identifier",
            "list_work_item_activities",
            "list_projects",
        },
        "surface_tools": {
            "v2": {
                # include= depth: single get_work_item with include=comments after resolve,
                # or find + get with include.
                "optimal_calls": 2,
                "optimal_tools": {"find_work_items", "get_work_item"},
                "alternate_tools": {
                    "search_projects",
                    "get_workspace_context",
                    "create_comment",
                },
            },
        },
        "needs": {"items"},
        "verify": verify_r5,
    },
    {
        "id": "R6",
        "tags": {"read", "tier1"},
        "prompt": (
            "Across the eval projects created for this run (main project {project} and its "
            "sibling 'B' project), which project has more open Bug-typed work items? "
            "Answer with the project name."
        ),
        "optimal_calls": 3,
        "optimal_tools": {"list_projects", "list_work_items", "resolve_work_item_type"},
        "alternate_tools": {
            "count_work_items",
            "search_work_items",
            "list_work_item_types",
            "retrieve_project",
            "get_pql_reference",
        },
        "surface_tools": {
            "v2": {
                "optimal_calls": 3,
                "optimal_tools": {"search_projects", "find_work_items", "get_workspace_context"},
                "alternate_tools": {
                    "get_work_item",
                    "get_pql_reference",
                    "list_states",
                },
            },
            # Type id resolution cleaner on v2-schema
            "v2-schema": {
                "optimal_calls": 3,
                "optimal_tools": {"search_projects", "find_work_items", "resolve_work_item_type"},
                "alternate_tools": {
                    "list_work_item_types",
                    "get_workspace_context",
                    "get_work_item",
                    "get_pql_reference",
                },
            },
        },
        "needs": {"items", "bug_type", "second_project"},
        "verify": verify_r6,
    },
    {
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
        "surface_tools": {
            "v2": {
                "optimal_calls": 2,
                "optimal_tools": {"get_workspace_context", "create_work_item"},
                "alternate_tools": {
                    "search_projects",
                    "list_labels",
                    "find_work_items",
                    "get_work_item",
                    "update_work_item",
                },
            },
        },
        "needs": {"labels"},
        "verify": verify_w1,
    },
    {
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
        "surface_tools": {
            "v2": {
                "optimal_calls": 2,
                "optimal_tools": {"find_work_items", "update_work_item"},
                "alternate_tools": {
                    "list_states",
                    "get_work_item",
                    "list_available_transitions",
                    "search_projects",
                },
            },
        },
        "needs": {"items"},
        "verify": verify_w2,
    },
    {
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
        "surface_tools": {
            "v2": {
                "optimal_calls": 2,
                "optimal_tools": {"find_work_items", "create_comment"},
                "alternate_tools": {
                    "get_work_item",
                    "modify_comment",
                    "search_projects",
                },
            },
        },
        "needs": {"items"},
        "verify": verify_w3,
    },
    {
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
        "surface_tools": {
            "v2": {
                # Default v2 has list_labels but no update_label (schema tier).
                "unsupported": True,
                "reason": ("W4 needs update_label which is only on the v2-schema surface — use --surface v2-schema"),
            },
            "v2-schema": {
                "optimal_calls": 2,
                "optimal_tools": {"list_labels", "update_label"},
                "alternate_tools": {
                    "create_label",
                    "delete_label",
                    "search_projects",
                    "get_workspace_context",
                },
            },
        },
        "needs": {"labels"},
        "verify": verify_w4,
    },
    {
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
        "surface_tools": {
            "v2": {
                "optimal_calls": 5,
                "optimal_tools": {"list_modules", "find_work_items", "archive_work_item"},
                "alternate_tools": {
                    "get_work_item",
                    "assign_to_module",
                    "search_projects",
                    "list_states",
                },
            },
        },
        "needs": {"module"},
        "verify": verify_w5,
    },
    {
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
        "surface_tools": {
            "v2": {
                # close_cycle with transfer_to is the consolidated path.
                "optimal_calls": 2,
                "optimal_tools": {"list_cycles", "close_cycle"},
                "alternate_tools": {
                    "assign_to_cycle",
                    "find_work_items",
                    "search_projects",
                    "get_workspace_context",
                },
            },
        },
        # cycles_open_past: Sprint 12 must still be open, or "close it" is impossible —
        # Plane rejects every edit to an ended cycle. See _seed_cycles.
        "needs": {"items", "cycles", "cycles_open_past"},
        "verify": verify_w6,
    },
    {
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
        "surface_tools": {
            "v2": {
                "optimal_calls": 3,
                "optimal_tools": {"find_work_items", "link_work_items", "add_work_item_link"},
                "alternate_tools": {
                    "get_work_item",
                    "search_projects",
                    "update_work_item",
                },
            },
        },
        "needs": {"items"},
        "verify": verify_w7,
    },
    {
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
        "surface_tools": {
            "v2": {
                "optimal_calls": 2,
                "optimal_tools": {"find_work_items", "log_work"},
                "alternate_tools": {
                    "get_work_item",
                    "search_projects",
                    "update_work_item",
                },
            },
        },
        "needs": {"items"},
        "verify": verify_w8,
    },
    {
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
        "surface_tools": {
            "v2": {
                "optimal_calls": 2,
                "optimal_tools": {"find_work_items", "bulk_update_work_items"},
                "alternate_tools": {
                    "update_work_item",
                    "get_work_item",
                    "search_projects",
                },
            },
        },
        "needs": {"items"},
        "verify": verify_w9,
    },
    {
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
        "surface_tools": {
            "v2": {
                "optimal_calls": 1,
                "optimal_tools": {"create_page"},
                "alternate_tools": {
                    "list_pages",
                    "get_page",
                    "search_projects",
                    "get_workspace_context",
                },
            },
        },
        "needs": set(),
        "verify": verify_w10,
    },
    {
        "id": "S1",
        "tags": {"setup", "tier1"},
        "prompt": (
            "In project {project}, add a Severity dropdown property (options: Critical, "
            "Major, Minor) to the Bug work item type."
        ),
        "optimal_calls": 3,
        "optimal_tools": {
            "list_projects",
            "resolve_work_item_type",
            "create_work_item_property",
        },
        "alternate_tools": {
            "list_work_item_types",
            "create_work_item_property_option",
            "retrieve_work_item_type",
            "list_work_item_properties",
            "retrieve_work_item_property",
            "manage_work_item_type_properties",
            "create_work_item_type",
            "import_work_item_types_to_project",
            "update_project_features",
        },
        "surface_tools": {
            "v2": {
                "unsupported": True,
                "reason": (
                    "S1 needs work-item-type/property schema tools "
                    "(resolve_work_item_type, create_work_item_property) which are "
                    "not on the default v2 surface — use --surface v2-schema"
                ),
            },
            "v2-schema": {
                "optimal_calls": 2,
                "optimal_tools": {
                    "resolve_work_item_type",
                    "create_work_item_property",
                },
                "alternate_tools": {
                    "list_work_item_types",
                    "list_work_item_properties",
                    "add_property_option",
                    "search_projects",
                    "get_workspace_context",
                    "get_features",
                    "configure_features",
                    "update_work_item_type",
                },
            },
        },
        "needs": {"bug_type"},
        "verify": verify_s1,
    },
    {
        "id": "S2",
        "tags": {"setup", "tier1"},
        "prompt": (
            f"In project {{project}}, add a Fibonacci estimate scale (points 1,2,3,5,8) "
            f"and set the work item '{W8_TITLE}' to 5 points."
        ),
        "optimal_calls": 5,
        "optimal_tools": {
            "list_projects",
            "create_project_estimate",
            "create_project_estimate_points",
            "link_estimate_to_project",
            "update_work_item",
        },
        "alternate_tools": {
            "get_project_estimate",
            "list_project_estimate_points",
            "list_work_items",
            "search_work_items",
            "update_project_estimate",
        },
        "surface_tools": {
            "v2": {
                "unsupported": True,
                "reason": (
                    "S2 needs configure_estimate (schema tier) to create the Fibonacci "
                    "scale — default v2 has no estimate schema tools. "
                    "(v2 update_work_item does accept estimate_point.) Use v2-schema."
                ),
            },
            "v2-schema": {
                # configure_estimate creates scale+points+link in one call;
                # update_work_item(estimate_point="5") resolves the value server-side.
                "optimal_calls": 2,
                "optimal_tools": {"configure_estimate", "update_work_item"},
                "alternate_tools": {
                    "search_projects",
                    "get_features",
                    "find_work_items",
                    "get_work_item",
                    "get_workspace_context",
                    "bulk_update_work_items",
                },
            },
        },
        "needs": {"items"},
        "verify": verify_s2,
    },
    {
        "id": "S3",
        "tags": {"setup", "tier1"},
        "prompt": (
            "In project {project}, create a work item type named 'Incident' and add a "
            "required text property (e.g. 'Impact summary') on it."
        ),
        "optimal_calls": 3,
        "optimal_tools": {
            "list_projects",
            "resolve_work_item_type",
            "create_work_item_property",
        },
        "alternate_tools": {
            "create_work_item_type",
            "list_work_item_types",
            "import_work_item_types_to_project",
            "list_work_item_properties",
            "manage_work_item_type_properties",
            "update_project_features",
        },
        "surface_tools": {
            "v2": {
                "unsupported": True,
                "reason": (
                    "S3 needs resolve_work_item_type + create_work_item_property "
                    "(schema tier) — use --surface v2-schema"
                ),
            },
            "v2-schema": {
                "optimal_calls": 2,
                "optimal_tools": {
                    "resolve_work_item_type",
                    "create_work_item_property",
                },
                "alternate_tools": {
                    "list_work_item_types",
                    "list_work_item_properties",
                    "update_work_item_type",
                    "search_projects",
                    "get_features",
                    "configure_features",
                },
            },
        },
        "needs": set(),
        "verify": verify_s3,
    },
    {
        "id": "S4",
        "tags": {"setup", "tier1"},
        "prompt": (
            f"In project {{project}}, triage intake: accept the billing request "
            f"'{INTAKE_BILLING_TITLE}' and reject/decline the spam item "
            f"'{INTAKE_SPAM_TITLE}'."
        ),
        "optimal_calls": 3,
        "optimal_tools": {
            "list_intake_work_items",
            "update_intake_work_item",
        },
        "alternate_tools": {
            "retrieve_intake_work_item",
            "list_work_items",
            "list_projects",
            "create_intake_work_item",
        },
        "surface_tools": {
            "v2": {
                "optimal_calls": 3,
                "optimal_tools": {"list_intake", "triage_intake"},
                "alternate_tools": {
                    "find_work_items",
                    "get_work_item",
                    "search_projects",
                    "get_workspace_context",
                },
            },
        },
        "needs": {"intake"},
        "verify": verify_s4,
    },
    {
        "id": "S5",
        "tags": {"setup", "tier1"},
        "prompt": (
            "Enable cycles and time tracking (worklogs) for project {project}, "
            "and enable the customers feature for the workspace."
        ),
        # Minimal legacy path (2 calls):
        #   1. update_project(cycle_view=True, is_time_tracking_enabled=True)
        #   2. update_workspace_features(customers=True)
        # (features PATCH can set cycles→cycle_view but cannot set worklogs.)
        "optimal_calls": 2,
        "optimal_tools": {"update_project", "update_workspace_features"},
        "alternate_tools": {
            "update_project_features",
            "list_projects",
            "retrieve_project",
            "get_features",
        },
        "surface_tools": {
            "v2": {
                "unsupported": True,
                "reason": (
                    "S5 needs configure_features (schema tier) for project cycles/worklogs "
                    "and workspace customers — use --surface v2-schema"
                ),
            },
            "v2-schema": {
                # 2 calls: configure_features(project, cycles+worklogs) +
                # configure_features(customers=True) without project.
                "optimal_calls": 2,
                "optimal_tools": {"configure_features"},
                "alternate_tools": {
                    "get_features",
                    "search_projects",
                    "get_workspace_context",
                    "update_work_item",
                },
            },
        },
        # Seed leaves project cycles+worklogs and workspace customers off.
        "needs": {"leave_cycles_worklogs_off"},
        "verify": verify_s5,
    },
    {
        "id": "C1",
        "tags": {"write", "tier1"},
        "prompt": (
            f"Create customer '{CUSTOMER_NAME}' (if it does not already exist), add a "
            f"request named '{CUSTOMER_REQUEST_NAME}', and link that request to the work "
            f"item '{R1_TITLE}' in project {{project}}."
        ),
        "optimal_calls": 4,
        "optimal_tools": {
            "list_customers",
            "create_customer",
            "create_customer_request",
            "list_work_items",
        },
        "alternate_tools": {
            "retrieve_customer",
            "manage_customer_work_items",
            "list_customer_requests",
            "list_customer_work_items",
            "search_work_items",
            "list_projects",
        },
        "surface_tools": {
            "v2": {
                "optimal_calls": 4,
                "optimal_tools": {
                    "list_customers",
                    "create_customer",
                    "log_customer_request",
                    "link_customer_work_items",
                },
                "alternate_tools": {
                    "get_customer",
                    "find_work_items",
                    "update_customer",
                    "search_projects",
                },
            },
        },
        # No pre-seeded customer — agent creates; items needed for link target.
        "needs": {"items"},
        "verify": verify_c1,
    },
    {
        "id": "C2",
        "tags": {"read", "tier1"},
        "prompt": (f"What shipped in release {RELEASE_NAME}? Summarize the changelog."),
        "optimal_calls": 2,
        "optimal_tools": {"list_releases", "get_release_changelog"},
        "alternate_tools": {
            "retrieve_release",
            "list_release_work_items",
            "update_release_changelog",
        },
        "surface_tools": {
            "v2": {
                "optimal_calls": 1,
                "optimal_tools": {"get_release"},
                "alternate_tools": {
                    "list_releases",
                    "assign_to_release",
                    "get_workspace_context",
                },
            },
        },
        "needs": {"release"},
        "verify": verify_c2,
    },
    {
        "id": "R7",
        "tags": {"read", "tier1", "extra"},
        "prompt": (
            f"In project {{project}}, what states can the work item '{R1_TITLE}' "
            "legally transition to under workflow rules? List the state names "
            "(or say unrestricted if none)."
        ),
        # Extra: exercises list_available_transitions.
        "optimal_calls": 2,
        "optimal_tools": {"list_work_items", "list_states"},
        "alternate_tools": {
            "retrieve_work_item",
            "search_work_items",
            "list_projects",
        },
        "surface_tools": {
            "v2": {
                "optimal_calls": 1,
                "optimal_tools": {"list_available_transitions"},
                "alternate_tools": {
                    "find_work_items",
                    "get_work_item",
                    "list_states",
                    "search_projects",
                },
            },
        },
        "needs": {"items"},
        "verify": verify_r7,
    },
    # ------------------------------------------------------------------
    # ID-in-hand class (I*): identifiers handed in the prompt — no name resolution advantage
    # ------------------------------------------------------------------
    {
        "id": "I1",
        "author": "post-hoc-debias",
        "tags": {"write", "tier1", "id_in_hand", "debias"},
        "prompt": ("In project {project}, update work item {work_item_id}: set its priority to high."),
        "prompt_bind": _bind_item_uuid(I1_TITLE),
        "optimal_calls": 1,
        "optimal_tools": {"update_work_item"},
        "alternate_tools": {
            "retrieve_work_item",
            "list_work_items",
            "search_work_items",
            "retrieve_work_item_by_identifier",
        },
        "surface_tools": {
            "v2": {
                "optimal_calls": 1,
                "optimal_tools": {"update_work_item"},
                "alternate_tools": {"get_work_item", "find_work_items", "search_projects"},
            },
        },
        "needs": {"items"},
        "verify": verify_i1,
    },
    {
        "id": "I2",
        "author": "post-hoc-debias",
        "tags": {"read", "tier1", "id_in_hand", "debias"},
        "prompt": (
            "In project {project}, what is the current state of work item "
            "{work_item_identifier}? Answer with the state name only."
        ),
        "prompt_bind": _bind_item_identifier(I2_TITLE),
        "optimal_calls": 1,
        "optimal_tools": {"retrieve_work_item_by_identifier"},
        "alternate_tools": {
            "retrieve_work_item",
            "list_work_items",
            "search_work_items",
            "list_states",
        },
        "surface_tools": {
            "v2": {
                # get_work_item requires UUIDs (forwards work_item_id directly).
                # PROJ-N on v2 is resolved via find_work_items (list/filter).
                "optimal_calls": 1,
                "optimal_tools": {"find_work_items"},
                "alternate_tools": {
                    "get_work_item",
                    "list_states",
                    "search_projects",
                    "get_workspace_context",
                },
            },
        },
        "needs": {"items"},
        "verify": verify_i2,
    },
    {
        "id": "I3",
        "author": "post-hoc-debias",
        "tags": {"write", "tier1", "id_in_hand", "debias"},
        "prompt": ("In project {project}, add work item {work_item_id} to cycle {cycle_id}."),
        "prompt_bind": _bind_i3,
        "optimal_calls": 1,
        "optimal_tools": {"manage_cycle_work_items"},
        "alternate_tools": {
            "list_cycles",
            "list_cycle_work_items",
            "list_work_items",
            "retrieve_cycle",
        },
        "surface_tools": {
            "v2": {
                "optimal_calls": 1,
                "optimal_tools": {"assign_to_cycle"},
                "alternate_tools": {"list_cycles", "find_work_items", "get_work_item"},
            },
        },
        "needs": {"items", "cycles"},
        "verify": verify_i3,
    },
    {
        "id": "I4",
        "author": "post-hoc-debias",
        "tags": {"write", "tier1", "id_in_hand", "debias"},
        "prompt": ("In project {project}, attach label {label_id} to work item {work_item_id}."),
        "prompt_bind": _bind_i4,
        "optimal_calls": 1,
        "optimal_tools": {"manage_work_item_label"},
        "alternate_tools": {
            "update_work_item",
            "list_labels",
            "retrieve_work_item",
            "list_work_items",
        },
        "surface_tools": {
            "v2": {
                # Default v2 update_work_item accepts labels; no manage_work_item_label.
                "optimal_calls": 1,
                "optimal_tools": {"update_work_item"},
                "alternate_tools": {"get_work_item", "list_labels", "find_work_items"},
            },
        },
        "needs": {"items", "labels"},
        "verify": verify_i4,
    },
    {
        "id": "I5",
        "author": "post-hoc-debias",
        "tags": {"write", "tier1", "id_in_hand", "debias"},
        "prompt": ("In project {project}, set the priority of work item {work_item_id} to low."),
        "prompt_bind": _bind_item_uuid(I3_TITLE),  # footer item; not high-traffic elsewhere
        "optimal_calls": 1,
        "optimal_tools": {"update_work_item"},
        "alternate_tools": {
            "retrieve_work_item",
            "list_work_items",
            "search_work_items",
        },
        "surface_tools": {
            "v2": {
                "optimal_calls": 1,
                "optimal_tools": {"update_work_item"},
                "alternate_tools": {"get_work_item", "find_work_items"},
            },
        },
        "needs": {"items"},
        "verify": verify_i5,
    },
    # ------------------------------------------------------------------
    # Long-tail class (L*): tools outside the default v2 curated surface
    # ------------------------------------------------------------------
    {
        "id": "L1",
        "author": "post-hoc-debias",
        "tags": {"write", "read", "tier1", "long_tail", "debias"},
        "prompt": (
            f"In project {{project}}, log 1.5 hours (90 minutes) of work on the item titled "
            f"'{L1_TITLE}', then report the project's worklog summary (who/what has time logged)."
        ),
        "optimal_calls": 3,
        "optimal_tools": {"list_work_items", "create_work_log", "get_project_worklog_summary"},
        "alternate_tools": {
            "search_work_items",
            "list_work_logs",
            "retrieve_work_item",
            "list_projects",
        },
        "surface_tools": {
            "v2": {
                "expected_skip": True,
                "reason": (
                    "L1 needs get_project_worklog_summary (legacy project tool) — not on the default v2 surface"
                ),
            },
        },
        "needs": {"items"},
        "verify": verify_l1,
    },
    {
        "id": "L2",
        "author": "post-hoc-debias",
        "tags": {"read", "tier1", "long_tail", "debias"},
        "prompt": (
            f"In project {{project}}, list the activity history for the work item titled "
            f"'{L2_TITLE}'. Summarize how many activities there are and mention any "
            "notable comment phrases you see. End your answer with a line of the form "
            "'count: N' where N is the number of activities."
        ),
        "optimal_calls": 2,
        "optimal_tools": {"list_work_items", "list_work_item_activities"},
        "alternate_tools": {
            "search_work_items",
            "retrieve_work_item",
            "list_work_item_comments",
            "retrieve_work_item_activity",
        },
        "surface_tools": {
            "v2": {
                "expected_skip": True,
                "reason": (
                    "L2 needs list_work_item_activities — not on the default v2 surface "
                    "(v2 has comments include= but not the activities feed)"
                ),
            },
        },
        "needs": {"items", "activity_feed"},
        "verify": verify_l2,
    },
    {
        "id": "L3",
        "author": "post-hoc-debias",
        "tags": {"write", "tier1", "long_tail", "debias"},
        "prompt": (f"Create a release tag with version '{L3_TAG_VERSION}' (a version marker for the eval run)."),
        "optimal_calls": 1,
        "optimal_tools": {"create_release_tag"},
        "alternate_tools": {
            "list_release_tags",
            "retrieve_release_tag",
            "list_releases",
            "update_release_tag",
        },
        "surface_tools": {
            "v2": {
                "expected_skip": True,
                "reason": "L3 needs create_release_tag — not on the default v2 surface",
            },
        },
        "needs": set(),  # workspace-level tag; no project fixture required
        "verify": verify_l3,
    },
    {
        "id": "L4",
        "author": "post-hoc-debias",
        "tags": {"write", "tier1", "long_tail", "debias"},
        "prompt": (
            f"For customer '{CUSTOMER_NAME}', ensure there is a text customer property "
            f"named '{L4_PROP_DISPLAY}' and set its value to '{L4_PROP_VALUE}'."
        ),
        "optimal_calls": 3,
        "optimal_tools": {
            "list_customers",
            "create_customer_property",
            "set_customer_property_values",
        },
        "alternate_tools": {
            "list_customer_properties",
            "get_customer_property_values",
            "retrieve_customer",
            "update_customer_property",
        },
        "surface_tools": {
            "v2": {
                "expected_skip": True,
                "reason": (
                    "L4 needs create_customer_property / set_customer_property_values — not on the default v2 surface"
                ),
            },
        },
        "needs": {"customer"},
        "verify": verify_l4,
    },
    {
        "id": "L5",
        "author": "post-hoc-debias",
        "tags": {"read", "tier1", "long_tail", "debias"},
        "prompt": (
            f"In project {{project}}, how many file attachments does the work item titled "
            f"'{L5_TITLE}' have? End your answer with a line of the form 'count: N' "
            "where N is the number of file attachments."
        ),
        "optimal_calls": 2,
        "optimal_tools": {"list_work_items", "list_work_item_attachments"},
        "alternate_tools": {
            "search_work_items",
            "retrieve_work_item",
            "get_work_item_attachment_download_url",
        },
        "surface_tools": {
            "v2": {
                # Achievable on default v2 via include=attachments on get_work_item.
                "optimal_calls": 2,
                "optimal_tools": {"find_work_items", "get_work_item"},
                "alternate_tools": {
                    "search_projects",
                    "get_workspace_context",
                    "list_states",
                },
            },
        },
        "needs": {"items"},
        "verify": verify_l5,
    },
]


def resolve_surface_tool_sets(
    task: dict[str, Any],
    surface: str,
) -> dict[str, Any]:
    """Resolve optimal/alternate tool sets for a surface.

    Returns a dict with:
      - skip (str | None): if set, the runner should SKIP the task on this surface
      - optimal_tools / alternate_tools: classification sets
      - optimal_calls: optional override
      - classification: ``exact`` when an overlay or full/legacy sets apply;
        ``approximate`` when falling back to flat legacy-named sets on a non-full
        surface that has no overlay
    """
    surface = (surface or "full").strip().lower()
    overlays = task.get("surface_tools") or {}

    if surface in ("full", "legacy", ""):
        return {
            "skip": None,
            "optimal_tools": set(task["optimal_tools"]),
            "alternate_tools": set(task["alternate_tools"]),
            "optimal_calls": task.get("optimal_calls"),
            "classification": "exact",
        }

    ov = overlays.get(surface)
    # v2-schema is a superset of v2 for *supported* tools, but schema adds none of
    # the long-tail APIs (worklog summary, activities, release tags, customer
    # property values). Inherit the full v2 overlay — including expected_skip /
    # unsupported — when no schema-specific entry exists.
    if ov is None and surface == "v2-schema":
        ov = overlays.get("v2")

    if ov is None:
        return {
            "skip": None,
            "optimal_tools": set(task["optimal_tools"]),
            "alternate_tools": set(task["alternate_tools"]),
            "optimal_calls": task.get("optimal_calls"),
            "classification": "approximate",
        }

    if ov.get("unsupported") or ov.get("expected_skip"):
        return {
            "skip": ov.get("reason") or f"task {task.get('id')} unsupported on surface {surface}",
            "optimal_tools": set(),
            "alternate_tools": set(),
            "optimal_calls": None,
            "classification": "exact",
        }

    optimal = set(ov["optimal_tools"])
    alternate = set(ov["alternate_tools"])
    if not optimal.isdisjoint(alternate):
        raise ValueError(f"{task.get('id')}/{surface}: optimal/alternate overlap")
    return {
        "skip": None,
        "optimal_tools": optimal,
        "alternate_tools": alternate,
        "optimal_calls": ov.get("optimal_calls", task.get("optimal_calls")),
        "classification": "exact",
    }


TASKS_BY_ID: dict[str, dict[str, Any]] = {t["id"]: t for t in TASKS}


def get_tasks(ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Return tasks filtered by id list (None = all)."""
    if ids is None:
        return list(TASKS)
    missing = [i for i in ids if i not in TASKS_BY_ID]
    if missing:
        raise SystemExit(f"Unknown task id(s): {', '.join(missing)}. Known: {', '.join(TASKS_BY_ID)}")
    return [TASKS_BY_ID[i] for i in ids]


def task_author(task: dict[str, Any]) -> str:
    """Return the task author; default ``claude`` when the key is absent."""
    return str(task.get("author") or "claude")


def _serialize_surface_tools(surface_tools: dict[str, Any] | None) -> dict[str, Any]:
    """Stable JSON-friendly form of a task's surface_tools overlay."""
    if not surface_tools:
        return {}
    out: dict[str, Any] = {}
    for surface in sorted(surface_tools):
        ov = surface_tools[surface] or {}
        if not isinstance(ov, dict):
            out[surface] = ov
            continue
        entry: dict[str, Any] = {}
        for key in sorted(ov):
            val = ov[key]
            if isinstance(val, set | frozenset):
                entry[key] = sorted(val)
            elif isinstance(val, list | tuple):
                entry[key] = list(val)
            else:
                entry[key] = val
        out[surface] = entry
    return out


def battery_fingerprint(tasks: list[dict[str, Any]] | None = None) -> str:
    """Stable short hash of the task battery used for a run.

    SHA-256 (first 12 hex chars) over a canonical serialization of every task
    sorted by id: id, prompt, sorted optimal/alternate tools, optimal_calls,
    and the surface_tools overlay (sets sorted, keys sorted).

    Ceilings (intentionally *not* covered by the hash):
    - Verifier functions and ``needs`` fixtures do not alter the fingerprint —
      prompt/tool-set drift is the stability signal, not seed/verify logic.
    - The hash covers the *selected* task list: ``--tasks`` subsets produce
      different fingerprints than a full-catalog run.
    """
    src = list(TASKS if tasks is None else tasks)
    payload: list[dict[str, Any]] = []
    for t in sorted(src, key=lambda x: str(x.get("id") or "")):
        payload.append(
            {
                "id": t.get("id"),
                "prompt": t.get("prompt"),
                "optimal_tools": sorted(t.get("optimal_tools") or []),
                "alternate_tools": sorted(t.get("alternate_tools") or []),
                "optimal_calls": t.get("optimal_calls"),
                "surface_tools": _serialize_surface_tools(t.get("surface_tools")),
            }
        )
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]
