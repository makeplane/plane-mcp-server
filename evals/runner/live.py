"""Live evaluation execution and task result assembly."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.drivers import KNOWN_DRIVERS, get_driver
from evals.drivers.api import MODEL_TIERS
from evals.evidence import configured_evidence_labels
from evals.report.load import RunExpectation, dedupe_rows_latest, load_rows, validate_run_keys
from evals.report.off_surface import off_surface_statement
from evals.report.summary import completeness_statement, execution_coverage_statement, summarize
from evals.results import TaskResult, agent_run_to_task_result
from evals.seed import make_plane_client, seed, teardown
from evals.seed.identities import capture_seed_artifacts
from evals.tasks.catalog import battery_fingerprint, task_author, task_fingerprint
from evals.tasks.prompts import PromptBindError, format_task_prompt
from evals.tasks.skip import TaskSkipped

from .meta import make_run_meta_row, maybe_write_run_meta, read_git_revision
from .resume import load_resume_skip_keys

MAX_ITERATIONS = 15
MAX_TOKENS = 8192


def _system_preamble(workspace_slug: str, project_name: str) -> str:
    """Keep under 100 words — part of measured context."""
    return (
        f"You are evaluating Plane project management tools. "
        f"Workspace slug: {workspace_slug}. Project name: {project_name}. "
        f"Complete the task using the available tools, then stop."
    )


def stdio_server_env(*, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build MCP stdio env from scratch — never inherit os.environ (F6)."""
    environment: dict[str, str] = {}
    if path := os.environ.get("PATH"):
        environment["PATH"] = path
    if home := os.environ.get("HOME"):
        environment["HOME"] = home
    environment["PLANE_API_KEY"] = os.environ["EVAL_PLANE_API_KEY"]
    environment["PLANE_WORKSPACE_SLUG"] = os.environ["EVAL_PLANE_WORKSPACE_SLUG"]
    environment["PLANE_BASE_URL"] = os.environ.get("EVAL_PLANE_BASE_URL", "https://api.plane.so")
    if extra:
        environment.update(extra)
    return environment


def is_infra_cli_stop_reason(stop_reason: str | None) -> bool:
    """True when a CLI AgentRun stop_reason should be classified as infra_cli.

    ``timeout`` and Claude error subtypes (``error_during_execution``, bare
    ``error``, …) are infrastructure. ``error_max_turns`` is a genuine task
    failure and stays in the success-rate denominator.
    """
    if not stop_reason:
        return False
    reason = str(stop_reason)
    if reason == "timeout":
        return True
    if reason == "error_max_turns":
        return False
    if reason == "error" or reason.startswith("error_"):
        return True
    return False


def _elapsed(since: float) -> str:
    """Wall time as mm:ss (or h:mm:ss past an hour) for progress lines."""
    seconds = int(time.monotonic() - since)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _timeout_error_message(agent: TaskResult) -> str:
    """Prefer the driver's recorded timeout note over recomputing MAX_ITERATIONS."""
    for note in agent.driver_notes:
        if isinstance(note, str) and note.startswith("timeout after"):
            return note
    return "timeout"


async def run_agent_task_via_driver(
    *,
    driver: Any,
    model_id: str | None,
    task: dict[str, Any],
    ctx: dict[str, Any],
    workspace_slug: str,
    server_env: dict[str, str] | None = None,
) -> TaskResult:
    """Run one task through the selected driver."""
    project_name = ctx["project_name"]
    system = _system_preamble(workspace_slug, project_name)
    prompt = format_task_prompt(task, ctx, strict=True)

    mcp_env = stdio_server_env(extra=server_env)
    # Drivers are sync (CLI subprocess or API loop); keep them off this loop.
    agent_run = await asyncio.to_thread(
        driver.run_task,
        prompt,
        mcp_env,
        model_id,
        MAX_ITERATIONS,
        system=system,
        cwd=Path(__file__).resolve().parent.parent.parent,
        evidence_sentinels=ctx.get("evidence_sentinels"),
        evidence_targets=ctx.get("evidence_targets"),
        evidence_aggregates=ctx.get("evidence_aggregates"),
    )
    return agent_run_to_task_result(agent_run)


def _make_task_row(
    *,
    run_id: str,
    git_revision: str,
    label: str,
    driver_name: str,
    provider: str | None,
    model_id: str | None,
    model_request: str | None,
    requested_tier: str | None,
    task: dict[str, Any],
    repetition: int,
    expected_rows: int,
    battery: str,
    server: str,
) -> TaskResult:
    return TaskResult(
        run_id=run_id,
        fixture_seed_id=uuid.uuid4().hex,
        ts=datetime.now(timezone.utc).isoformat(),
        git_sha=git_revision,
        battery=battery,
        task_fingerprint=task_fingerprint(task),
        label=label,
        driver=driver_name,
        provider=provider,
        server=server,
        model=model_id,
        requested_model=model_request,
        requested_tier=requested_tier,
        resolved_model=model_id,
        task_id=str(task["id"]),
        author=task_author(task),
        rep=repetition,
        expected_rows=expected_rows,
    )


def _seed_fixtures(
    plane: Any,
    task: dict[str, Any],
    context: dict[str, Any],
    *,
    row: TaskResult,
    repetition: int,
) -> bool:
    """Seed one task and record fixture skips or infrastructure failures."""
    task_needs = set(task.get("needs") or set())
    # Seed wrap: TaskSkipped → skip; other failures → infra_seed.
    try:
        seed(
            plane,
            run_id=row.fixture_seed_id,
            needs=task_needs,
            ctx=context,
            task_id=str(task["id"]),
        )
        if "read" in set(task.get("tags") or set()) and not configured_evidence_labels(
            context.get("evidence_sentinels"), context.get("evidence_targets")
        ):
            raise RuntimeError(f"{task['id']} seed did not register target-bound response evidence")
    except TaskSkipped as skip:
        row.skipped = skip.reason
        row.verify_note = skip.reason
        print(f"  {task['id']} rep={repetition} SKIPPED: {skip.reason}", flush=True)
        return False
    except Exception as exc:
        row.success = False
        row.error = f"{type(exc).__name__}: {exc}"
        row.error_class = "infra_seed"
        row.verify_note = ""
        print(
            f"  {task['id']} rep={repetition} ERROR[infra_seed]: {exc}",
            file=sys.stderr,
            flush=True,
        )
        if context.get("project_name"):
            print(
                f"  orphaned project may remain: {context['project_name']}",
                file=sys.stderr,
                flush=True,
            )
        return False
    if "bug_type" in task_needs and not context.get("bug_type"):
        reason = context.get("bug_type_skip_reason") or "bug_type unavailable"
        row.skipped = reason
        row.verify_note = reason
        print(f"  {task['id']} rep={repetition} SKIPPED: {reason}", flush=True)
        return False
    return True


async def _drive_agent(
    *,
    driver: Any,
    model_id: str | None,
    task: dict[str, Any],
    context: dict[str, Any],
    workspace_slug: str,
    server_env: dict[str, str] | None,
    row: TaskResult,
    repetition: int,
    is_api_driver: bool,
) -> TaskResult | None:
    """Run the agent and classify launch or prompt failures."""
    # Agent wrap: API failures and CLI failures are infrastructure.
    # Contained CLI stops (timeout / error subtypes) return AgentRun.
    try:
        return await run_agent_task_via_driver(
            driver=driver,
            model_id=model_id,
            task=task,
            ctx=context,
            workspace_slug=workspace_slug,
            server_env=server_env,
        )
    except PromptBindError as exc:
        # Empty/missing seed IDs in the prompt — not an agent failure.
        row.success = False
        row.error = f"{type(exc).__name__}: {exc}"
        row.error_class = "infra_seed"
        row.verify_note = ""
        print(
            f"  {task['id']} rep={repetition} ERROR[infra_seed]: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return None
    except Exception as exc:
        if hasattr(exc, "trace_integrity"):
            row.trace_integrity = bool(exc.trace_integrity)
            row.trace_integrity_reason = getattr(exc, "trace_integrity_reason", None)
            row.tool_manifest_fingerprint = getattr(exc, "tool_manifest_fingerprint", None)
        if is_api_driver:
            agent_error_class = "infra_api"
        else:
            agent_error_class = "infra_cli"
        row.success = False
        row.error = f"{type(exc).__name__}: {exc}"
        row.error_class = agent_error_class
        row.verify_note = ""
        print(
            f"  {task['id']} rep={repetition} ERROR[{agent_error_class}]: {exc}",
            file=sys.stderr,
        )
        return None


def _apply_agent_run(
    row: TaskResult,
    agent: TaskResult,
    *,
    model_alias: str,
    requested_tier: str | None,
    model_id: str | None,
) -> None:
    """Copy agent metrics and restore the run-level model and server identity."""
    row.apply_agent_result(agent)
    # Driver-level requested_model is the resolved ID.
    # Restore run-level intent and retain both identities.
    row.requested_model = model_alias
    row.requested_tier = requested_tier
    row.resolved_model = model_id


def _record_cli_infra_stop(
    row: TaskResult,
    agent: TaskResult,
    *,
    task: dict[str, Any],
    repetition: int,
    driver_name: str,
) -> bool:
    """Record contained CLI infrastructure stops and block verification."""
    # CLI infra stops: timeout + error subtypes except error_max_turns.
    stop_reason = agent.stop_reason
    if not driver_name.endswith("-cli") or not is_infra_cli_stop_reason(
        str(stop_reason) if stop_reason is not None else None
    ):
        return False
    row.success = False
    row.error_class = "infra_cli"
    if stop_reason == "timeout":
        row.error = _timeout_error_message(agent)
    else:
        notes = [note for note in agent.driver_notes if isinstance(note, str)]
        detail = "; ".join(notes) if notes else str(stop_reason)
        row.error = detail
    row.verify_note = ""
    print(
        f"  {task['id']} rep={repetition} ERROR[infra_cli]: {row.error}",
        file=sys.stderr,
    )
    return True


def _record_trace_infra(
    row: TaskResult,
    agent: TaskResult,
    *,
    task: dict[str, Any],
    repetition: int,
) -> bool:
    """Make recorder/protocol trace loss completeness-visible infrastructure."""
    if agent.trace_integrity or agent.trace_integrity_reason == "result_pair_mismatch":
        return False
    error_class = "infra_protocol" if agent.trace_integrity_reason == "protocol_violation" else "infra_trace"
    detail = next(
        (
            note
            for note in agent.driver_notes
            if isinstance(note, str) and note.startswith(("proxy_sidecar_incomplete", "proxy_sidecar_empty"))
        ),
        f"trace_integrity={agent.trace_integrity_reason or 'recorder_loss'}",
    )
    row.success = False
    row.error_class = error_class
    row.error = detail
    row.verify_note = ""
    print(
        f"  {task['id']} rep={repetition} ERROR[{error_class}]: {detail}",
        file=sys.stderr,
    )
    return True


async def _verify_task(
    plane: Any,
    task: dict[str, Any],
    context: dict[str, Any],
    agent: TaskResult,
    *,
    row: TaskResult,
    repetition: int,
) -> None:
    """Run one verifier and record task outcomes or verifier failures."""
    verify = task["verify"]
    try:
        agent_row = agent.to_row()
        ok, note = await verify(
            plane,
            context,
            {
                "final_text": agent.final_text,
                "calls": agent_row["calls"],
                "call_source": agent.call_source,
                "evidence_trace_available": agent.evidence_trace_available,
                "driver_notes": list(agent.driver_notes),
                "result_pair_mismatch": agent.result_pair_mismatch,
                "trace_integrity": agent.trace_integrity,
                "trace_integrity_reason": agent.trace_integrity_reason,
            },
        )
        row.success = bool(ok)
        row.verify_note = note
        print(
            f"  {task['id']} rep={repetition} success={ok} calls={agent.num_calls} note={note!r}",
            flush=True,
        )
    except TaskSkipped as skip:
        row.skipped = skip.reason
        row.verify_note = skip.reason
        print(f"  {task['id']} rep={repetition} SKIPPED: {skip.reason}", flush=True)
    except Exception as exc:
        row.success = False
        row.error = f"{type(exc).__name__}: {exc}"
        row.error_class = "task"
        row.verify_note = ""
        print(
            f"  {task['id']} rep={repetition} ERROR[task]: {exc}",
            file=sys.stderr,
        )


def _record_unexpected(
    row: TaskResult,
    exc: Exception,
    *,
    task: dict[str, Any],
    repetition: int,
    context: dict[str, Any],
) -> None:
    """Record failures outside the seed, driver, and verifier boundaries."""
    row.success = False
    row.error = f"{type(exc).__name__}: {exc}"
    row.error_class = "task"
    row.verify_note = ""
    print(f"  {task['id']} rep={repetition} ERROR[task]: {exc}", file=sys.stderr)
    if context.get("project_name"):
        print(
            f"  orphaned project may remain: {context['project_name']}",
            file=sys.stderr,
        )


def _remove_fixtures(plane: Any, context: dict[str, Any], row: TaskResult) -> None:
    """Remove task fixtures and retain the historical teardown diagnostics."""
    try:
        teardown(plane, context)
    except Exception as exc:
        row.cleanup_error = f"{type(exc).__name__}: {exc}"
        print(f"  teardown error: {exc}", file=sys.stderr)
        if context.get("project_name"):
            print(f"  orphaned project: {context['project_name']}", file=sys.stderr)


async def _run_task_repetition(
    *,
    plane: Any,
    driver: Any,
    workspace_slug: str,
    task: dict[str, Any],
    repetition: int,
    expected_rows: int,
    run_id: str,
    git_revision: str,
    label: str,
    driver_name: str,
    provider_id: str | None,
    model_id: str | None,
    model_alias: str,
    requested_tier: str | None,
    battery: str,
    is_api_driver: bool,
    external: bool,
    server_env: dict[str, str] | None,
) -> TaskResult:
    """Seed, drive, verify, assemble, and remove one task repetition."""
    context: dict[str, Any] = {}
    row = _make_task_row(
        run_id=run_id,
        git_revision=git_revision,
        label=label,
        driver_name=driver_name,
        provider=provider_id,
        model_id=model_id,
        model_request=model_alias,
        requested_tier=requested_tier,
        task=task,
        repetition=repetition,
        expected_rows=expected_rows,
        battery=battery,
        server="external" if external else "local",
    )
    try:
        if _seed_fixtures(plane, task, context, row=row, repetition=repetition):
            agent = await _drive_agent(
                driver=driver,
                model_id=model_id,
                task=task,
                context=context,
                workspace_slug=workspace_slug,
                server_env=server_env,
                row=row,
                repetition=repetition,
                is_api_driver=is_api_driver,
            )
            if agent is not None:
                _apply_agent_run(
                    row,
                    agent,
                    model_alias=model_alias,
                    requested_tier=requested_tier,
                    model_id=model_id,
                )
                infra_stop = _record_cli_infra_stop(
                    row,
                    agent,
                    task=task,
                    repetition=repetition,
                    driver_name=driver_name,
                )
                trace_infra = False
                if not infra_stop:
                    trace_infra = _record_trace_infra(
                        row,
                        agent,
                        task=task,
                        repetition=repetition,
                    )
                if not infra_stop and not trace_infra:
                    await _verify_task(plane, task, context, agent, row=row, repetition=repetition)
    except Exception as exc:
        # Anything outside seed/driver/verify wraps.
        _record_unexpected(row, exc, task=task, repetition=repetition, context=context)
    finally:
        try:
            row.seeded_entity_kinds, row.randomized_seed_namespaces = capture_seed_artifacts(context)
        except Exception as exc:
            _record_unexpected(row, exc, task=task, repetition=repetition, context=context)
        _remove_fixtures(plane, context, row)
    return row


async def run_live(
    tasks: list[dict[str, Any]],
    *,
    model_alias: str,
    reps: int,
    label: str,
    out_path: Path,
    driver_name: str = "api",
    provider: str = "anthropic",
    server_cmd: list[str] | None = None,
    server_env: dict[str, str] | None = None,
    resume: bool = False,
    record_result_payloads: bool = False,
    resolved_model_id: str | None = None,
) -> int:
    label = (label or "local").strip() or "local"
    external = server_cmd is not None

    driver_name = (driver_name or "api").strip().lower()
    if driver_name not in KNOWN_DRIVERS:
        print(
            f"error: unknown --driver {driver_name!r}; expected one of {sorted(KNOWN_DRIVERS)}",
            file=sys.stderr,
        )
        return 2
    provider = (provider or "anthropic").strip().lower()
    is_api_driver = driver_name == "api"
    provider_id = provider if is_api_driver else None
    model_id = resolved_model_id if resolved_model_id is not None else model_alias
    requested_tier = model_alias if model_alias in MODEL_TIERS else None

    run_id = uuid.uuid4().hex
    git_revision = read_git_revision()
    battery = battery_fingerprint(tasks)
    total_runs = len(tasks) * reps
    out_path.parent.mkdir(parents=True, exist_ok=True)

    resume_skip: set[tuple[str, int, str]] = set()
    if resume:
        try:
            resume_skip, skip_count, retry_count = load_resume_skip_keys(
                out_path,
                label=label,
                battery=battery,
                model=model_id,
                driver=driver_name,
                provider=provider_id,
            )
        except SystemExit as exc:
            print(exc, file=sys.stderr)
            return 2
        print(f"resume: skipping {skip_count} completed rows, retrying {retry_count}", flush=True)

    # First line of a new/empty file is a meta header (skipped by loaders).
    meta = make_run_meta_row(
        run_id=run_id,
        label=label,
        server="external" if external else "local",
        battery=battery,
        model=model_id,
        requested_model=model_alias,
        requested_tier=requested_tier,
        resolved_model=model_id,
        driver=driver_name,
        provider=provider_id,
        git_sha=git_revision,
        expected_rows=total_runs,
        expected_task_ids=[str(task["id"]) for task in tasks],
        expected_reps=reps,
    )
    if maybe_write_run_meta(out_path, meta):
        print(f"wrote meta header battery={battery} label={label}", flush=True)

    plane, workspace_slug = make_plane_client()
    # User chose --driver explicitly: codex live is allowed (they own the quota).
    driver_kwargs: dict[str, Any] = {}
    if is_api_driver:
        driver_kwargs.update({"provider": provider, "max_tokens": MAX_TOKENS})
    if driver_name == "codex-cli":
        driver_kwargs["allow_live"] = True
    if not is_api_driver:
        driver_kwargs["record_result_payloads"] = record_result_payloads
    # --server-cmd must reach every driver; otherwise we
    # silently benchmark the wrong server.
    if server_cmd is not None:
        driver_kwargs["server_command"] = server_cmd
    driver = get_driver(driver_name, **driver_kwargs)

    print(
        f"run_id={run_id} battery={battery} driver={driver_name} provider={provider_id} "
        f"requested_model={model_alias} resolved_model={model_id} "
        f"label={label} tasks={[task['id'] for task in tasks]} reps={reps}"
    )
    print(f"writing {out_path}", flush=True)

    # A battery is tens of minutes of silence otherwise: one line before each
    # repetition says what is running now, and one after says where the run is.
    started_at = time.monotonic()
    finished = 0
    passed = 0
    skipped = 0
    failed = 0

    with out_path.open("a", encoding="utf-8") as file:
        for task in tasks:
            for repetition in range(reps):
                if (task["id"], repetition, label) in resume_skip:
                    finished += 1
                    print(f"  {task['id']} rep={repetition} RESUME_SKIP", flush=True)
                    continue
                print(
                    f"[{finished + 1:>2}/{total_runs}] {task['id']} rep={repetition} "
                    f"running ({_elapsed(started_at)} elapsed)",
                    flush=True,
                )
                row = await _run_task_repetition(
                    plane=plane,
                    driver=driver,
                    workspace_slug=workspace_slug,
                    task=task,
                    repetition=repetition,
                    expected_rows=total_runs,
                    run_id=run_id,
                    git_revision=git_revision,
                    label=label,
                    driver_name=driver_name,
                    provider_id=provider_id,
                    model_id=model_id,
                    model_alias=model_alias,
                    requested_tier=requested_tier,
                    battery=battery,
                    is_api_driver=is_api_driver,
                    external=external,
                    server_env=server_env,
                )
                file.write(json.dumps(row.to_row(), default=str) + "\n")
                file.flush()

                finished += 1
                if row.skipped:
                    skipped += 1
                elif row.success:
                    passed += 1
                else:
                    failed += 1
                print(
                    f"          {finished}/{total_runs} done · {passed} pass · "
                    f"{failed} fail · {skipped} skip · {_elapsed(started_at)} elapsed",
                    flush=True,
                )

    print(
        f"finished {finished}/{total_runs} in {_elapsed(started_at)}: {passed} pass, {failed} fail, {skipped} skip",
        flush=True,
    )
    selected_task_ids = {str(task["id"]) for task in tasks}
    raw_result_rows = load_rows(out_path, dedupe="none")
    run_keys = validate_run_keys(
        raw_result_rows,
        RunExpectation(tuple(str(task["id"]) for task in tasks), reps, label),
    )
    result_rows = [
        row
        for row in dedupe_rows_latest(raw_result_rows)
        if row.label == label
        and (not row.battery or row.battery == battery)
        and row.task_id in selected_task_ids
        and 0 <= row.rep < reps
    ]
    summary = summarize(result_rows, expected_rows=total_runs, run_keys=run_keys)
    if summary.task_mean_success is not None:
        task_count = sum(task.n > 0 for task in summary.tasks.values())
        print(
            f"success: {summary.task_mean_success:.1%} across {task_count} tasks "
            f"(cluster-bootstrap95 [{summary.task_cluster_lo:.2f},{summary.task_cluster_hi:.2f}]; "
            f"pooled repetitions {summary.aggregate_k}/{summary.aggregate_n})",
            flush=True,
        )
    else:
        print("success: 0/0 (n/a; no evaluated rows)", flush=True)
    print(execution_coverage_statement(summary), flush=True)
    print(off_surface_statement(summary.off_surface), flush=True)
    print(completeness_statement(summary), flush=True)
    return 0 if summary.complete else 1
