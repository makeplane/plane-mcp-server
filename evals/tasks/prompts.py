"""Task prompt binding."""

from __future__ import annotations

import string
from typing import Any


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


__all__ = ["PromptBindError", "format_task_prompt"]
