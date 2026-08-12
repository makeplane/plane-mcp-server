"""Shared prompt, matching, and API lookup machinery for eval tasks."""

from __future__ import annotations

import re
import string
from typing import Any

from plane.errors.errors import HttpError
from plane.models.query_params import WorkItemQueryParams


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


def word_boundary(value: str) -> re.Pattern[str]:
    """Compile a case-insensitive word-boundary match for an exact seeded value."""
    return re.compile(rf"\b{re.escape(value)}\b", re.IGNORECASE)


def reports_exact_int(text: str, n: int) -> bool:
    """True when ``text`` contains integer ``n`` as a whole word (not a substring of 10)."""
    return bool(word_boundary(str(int(n))).search(text or ""))


def whole_answer_int(text: str) -> int | None:
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
    2. Fallback: whole-answer / last-line bare integer (:func:`whole_answer_int`).
    3. No match at all → False (ignoring an explicit format instruction is a fail).
    """
    last: int | None = None
    for line in (text or "").splitlines():
        m = re.fullmatch(r"\s*count:\s*(-?\d+)\s*", line, flags=re.IGNORECASE)
        if m:
            last = int(m.group(1))
    if last is not None:
        return last == int(truth)
    whole = whole_answer_int(text)
    if whole is not None:
        return whole == int(truth)
    return False


def as_id(obj: Any) -> str | None:
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj
    return getattr(obj, "id", None) or (obj.get("id") if isinstance(obj, dict) else None)


def ids(items: Any) -> set[str]:
    out: set[str] = set()
    for item in items or []:
        i = as_id(item)
        if i:
            out.add(str(i))
    return out


def find_items_by_name(plane: Any, workspace_slug: str, project_id: str, name: str) -> list[Any]:
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


def find_item_by_name(plane: Any, workspace_slug: str, project_id: str, name: str) -> Any | None:
    """Locate a work item by exact name; when duplicates exist, prefer the newest."""
    matches = find_items_by_name(plane, workspace_slug, project_id, name)
    return matches[0] if matches else None


def state_name(plane: Any, workspace_slug: str, project_id: str, state_ref: Any) -> str | None:
    """Resolve a state UUID or expanded object to its display name."""
    if state_ref is None:
        return None
    if hasattr(state_ref, "name") and state_ref.name:
        return str(state_ref.name)
    if isinstance(state_ref, dict) and state_ref.get("name"):
        return str(state_ref["name"])
    state_id = as_id(state_ref)
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


def state_group(plane: Any, workspace_slug: str, project_id: str, state_ref: Any) -> str | None:
    if state_ref is None:
        return None
    if hasattr(state_ref, "group") and state_ref.group:
        return str(state_ref.group)
    if isinstance(state_ref, dict) and state_ref.get("group"):
        return str(state_ref["group"])
    state_id = as_id(state_ref)
    if not state_id:
        return None
    page = plane.states.list(workspace_slug=workspace_slug, project_id=project_id)
    for s in page.results or []:
        if str(s.id) == str(state_id):
            return getattr(s, "group", None)
    return None


def is_not_found(exc: BaseException) -> bool:
    return isinstance(exc, HttpError) and exc.status_code in (404, 405)


def get_final_text(run: dict[str, Any]) -> str:
    return run.get("final_text") or ""


def count_open_urgent(plane: Any, workspace_slug: str, project_id: str) -> int:
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
            sid = as_id(item.state)
            if sid and str(sid) in closed_ids:
                continue
            n += 1
        if not resp.next_page_results:
            break
        cursor = resp.next_cursor
    return n


__all__ = [
    "TaskSkipped",
    "PromptBindError",
    "format_task_prompt",
    "word_boundary",
    "reports_exact_int",
    "whole_answer_int",
    "reports_contract_int",
    "as_id",
    "ids",
    "find_items_by_name",
    "find_item_by_name",
    "state_name",
    "state_group",
    "is_not_found",
    "get_final_text",
    "count_open_urgent",
]
