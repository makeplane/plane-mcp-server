"""Verifier canary execution for evaluation tasks."""

from __future__ import annotations

import sys
import uuid
from typing import Any

from evals.errors import TaskSkipped
from evals.seed import make_plane_client, seed, teardown
from evals.tasks.catalog import battery_fingerprint

CANARY_CANNED_OUTPUTS: dict[str, tuple[str, ...]] = {
    "R1": ("state: In Progress",),
    "R2": ("count: 0",),
    "R3": ("item: Example work item",),
    "R4": ("cycle: Sprint 13\nitem: Example work item\noverdue: none",),
    "R5": ("comment: looks good",),
    "R6": ("project: EVAL deadbeef B",),
    "R7": ("state: Backlog | group: backlog",),
    "C2": ("release: 1.2.0\nshipped: guessed change",),
    "I2": ("state: Backlog",),
    "L1": ("logged-minutes: 90\nsummary-work-item-id: guessed-id",),
    "L2": ("count: 0",),
    "L5": ("count: 0",),
}


def canary_probe_texts(task_id: str) -> tuple[str, ...]:
    """Return empty plus plausible zero-call answers for a verifier canary."""
    values = ("", "count: 0", *CANARY_CANNED_OUTPUTS.get(task_id, ()))
    return tuple(dict.fromkeys(values))


async def run_canary(
    tasks: list[dict[str, Any]],
    *,
    label: str,
    required_task_ids: set[str] | frozenset[str] | None = None,
) -> int:
    """Seed + verify zero-call probes + teardown per task; no driver/model.

    ``required_task_ids`` enables strict coverage for an explicit environment capability
    set. Legitimate skips outside that set remain non-fatal but are always reported.
    """
    label = (label or "local").strip() or "local"
    battery = battery_fingerprint(tasks)
    plane, _workspace_slug = make_plane_client()
    print(f"canary battery={battery} label={label} tasks={[task['id'] for task in tasks]}")

    broken_ids: list[str] = []
    verified_ids: list[str] = []
    skipped_reasons: dict[str, str] = {}
    errored_reasons: dict[str, list[str]] = {}

    def record_error(task_id: str, reason: str) -> None:
        errored_reasons.setdefault(task_id, []).append(reason)

    for task in tasks:
        task_id = str(task["id"])
        context: dict[str, Any] = {}
        task_needs = set(task.get("needs") or set())
        verifier_exercised = False
        try:
            try:
                seed(
                    plane,
                    run_id=uuid.uuid4().hex,
                    needs=task_needs,
                    ctx=context,
                    task_id=task_id,
                )
            except TaskSkipped as skip:
                skipped_reasons[task_id] = skip.reason
                print(f"  {task_id} SKIPPED: {skip.reason}")
                continue
            except Exception as exc:
                reason = f"infra_seed {type(exc).__name__}: {exc}"
                record_error(task_id, reason)
                print(f"  {task_id} canary ERROR[infra_seed]: {exc}", file=sys.stderr)
                continue
            if "bug_type" in task_needs and not context.get("bug_type"):
                reason = context.get("bug_type_skip_reason") or "bug_type unavailable"
                skipped_reasons[task_id] = str(reason)
                print(f"  {task_id} SKIPPED: {reason}")
                continue
            for probe_index, final_text in enumerate(canary_probe_texts(task_id)):
                probe = {
                    "final_text": final_text,
                    "calls": [],
                    "call_source": "canary",
                }
                try:
                    ok, note = await task["verify"](plane, context, probe)
                except TaskSkipped as skip:
                    skipped_reasons[task_id] = skip.reason
                    print(f"  {task_id} SKIPPED during verifier: {skip.reason}")
                    break
                verifier_exercised = True
                if ok:
                    broken_ids.append(task_id)
                    print(
                        f"  BROKEN VERIFIER: {task_id} accepted canary probe "
                        f"{probe_index} final_text={final_text!r} note={note!r}"
                    )
                    break
                print(f"  {task_id} probe={probe_index} ok=False note={note!r}")
            if task_id not in skipped_reasons:
                verified_ids.append(task_id)
        except Exception as exc:
            record_error(task_id, f"{type(exc).__name__}: {exc}")
            print(f"  {task_id} canary ERROR: {exc}", file=sys.stderr)
        finally:
            try:
                teardown(plane, context)
            except Exception as exc:
                record_error(task_id, f"teardown {type(exc).__name__}: {exc}")
                print(f"  {task_id} teardown ERROR: {exc}", file=sys.stderr)

        if (
            verifier_exercised
            and task_id not in verified_ids
            and task_id not in skipped_reasons
            and task_id not in errored_reasons
        ):
            verified_ids.append(task_id)

    skipped_ids = list(skipped_reasons)
    errored_ids = list(errored_reasons)
    total = len(tasks)
    print(f"canary coverage: verified={len(verified_ids)}/{total} ids={verified_ids}")
    print(f"canary coverage: skipped={len(skipped_ids)} ids={skipped_ids} reasons={skipped_reasons}")
    print(f"canary coverage: errored={len(errored_ids)} ids={errored_ids} reasons={errored_reasons}")

    required = set(required_task_ids or ())
    missing_required = sorted(required - set(verified_ids))
    if missing_required:
        print(
            f"canary strict coverage FAILED: missing required ids={missing_required}",
            file=sys.stderr,
        )

    if broken_ids:
        for task_id in broken_ids:
            print(f"BROKEN VERIFIER: {task_id}", file=sys.stderr)
    if not verified_ids:
        print(
            "error: canary verified 0 tasks — nothing exercised; refusing exit 0",
            file=sys.stderr,
        )
    if broken_ids or errored_ids or missing_required or not verified_ids:
        return 1
    print(f"canary: verified zero-call probes rejected ({len(verified_ids)} verifier(s))")
    return 0


__all__ = ["CANARY_CANNED_OUTPUTS", "canary_probe_texts", "run_canary"]
