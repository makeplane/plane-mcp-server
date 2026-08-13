"""Verifier canary execution for evaluation tasks."""

from __future__ import annotations

import sys
import uuid
from typing import Any

from evals.seed import make_plane_client, seed, teardown
from evals.tasks import TaskSkipped, battery_fingerprint


async def run_canary(
    tasks: list[dict[str, Any]],
    *,
    label: str,
) -> int:
    """Seed + verify(empty agent) + teardown per task; no driver/model.

    Passes only when every verifier returns falsy ok on a do-nothing agent.
    Any ok=True is a broken verifier (false positive).
    """
    label = (label or "local").strip() or "local"
    battery = battery_fingerprint(tasks)
    plane, _workspace_slug = make_plane_client()
    print(f"canary battery={battery} label={label} tasks={[task['id'] for task in tasks]}")

    broken: list[str] = []
    verified_count = 0
    empty_agent = {"final_text": "", "calls": []}

    for task in tasks:
        context: dict[str, Any] = {}
        task_needs = set(task.get("needs") or set())
        try:
            try:
                seed(plane, run_id=uuid.uuid4().hex, needs=task_needs, ctx=context)
            except TaskSkipped as skip:
                print(f"  {task['id']} SKIPPED: {skip.reason}")
                continue
            if "bug_type" in task_needs and not context.get("bug_type"):
                reason = context.get("bug_type_skip_reason") or "bug_type unavailable"
                print(f"  {task['id']} SKIPPED: {reason}")
                continue
            try:
                ok, note = await task["verify"](plane, context, empty_agent)
            except TaskSkipped as skip:
                print(f"  {task['id']} SKIPPED: {skip.reason}")
                continue
            verified_count += 1
            if ok:
                broken.append(task["id"])
                print(f"  BROKEN VERIFIER: {task['id']} note={note!r}")
            else:
                print(f"  {task['id']} ok=False note={note!r}")
        except Exception as exc:
            print(f"  {task['id']} canary ERROR: {exc}", file=sys.stderr)
            broken.append(task["id"])
        finally:
            try:
                teardown(plane, context)
            except Exception as exc:
                print(f"  teardown error: {exc}", file=sys.stderr)

    if broken:
        for task_id in broken:
            print(f"BROKEN VERIFIER: {task_id}", file=sys.stderr)
        return 1
    if verified_count == 0:
        print(
            "error: canary verified 0 tasks (all skipped by environment/fixture gates) "
            "— nothing exercised; refusing exit 0",
            file=sys.stderr,
        )
        return 1
    print(f"canary: all verifiers reject empty agent ({verified_count} verified)")
    return 0
