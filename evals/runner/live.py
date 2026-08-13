"""Live evaluation execution and task result assembly."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.drivers import KNOWN_DRIVERS, get_driver
from evals.drivers.api import MODEL_TIERS
from evals.results import TaskResult, agent_run_to_task_result
from evals.seed import make_plane_client, seed, teardown
from evals.tasks.catalog import battery_fingerprint, task_author
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


def classify_call(tool: str, optimal: set[str], alternate: set[str]) -> str:
    if tool in optimal:
        return "optimal"
    if tool in alternate:
        return "alternate"
    return "out_of_set"


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
    optimal_tools: set[str] | None = None,
    alternate_tools: set[str] | None = None,
    server_env: dict[str, str] | None = None,
) -> TaskResult:
    """Run one task through the selected driver."""
    project_name = ctx["project_name"]
    system = _system_preamble(workspace_slug, project_name)
    prompt = format_task_prompt(task, ctx, strict=True)
    optimal = set(optimal_tools) if optimal_tools is not None else set(task["optimal_tools"])
    alternate = set(alternate_tools) if alternate_tools is not None else set(task["alternate_tools"])
    assert optimal.isdisjoint(alternate), f"{task['id']}: optimal/alternate overlap"

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
    )
    return agent_run_to_task_result(
        agent_run,
        optimal=optimal,
        alternate=alternate,
        classify=classify_call,
    )


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
    battery: str,
    server: str,
) -> TaskResult:
    return TaskResult(
        run_id=run_id,
        ts=datetime.now(timezone.utc).isoformat(),
        git_sha=git_revision,
        battery=battery,
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
    )


async def _run_task_repetition(
    *,
    plane: Any,
    driver: Any,
    workspace_slug: str,
    task: dict[str, Any],
    repetition: int,
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
        battery=battery,
        server="external" if external else "local",
    )
    try:
        task_needs = set(task.get("needs") or set())
        # Seed wrap: TaskSkipped → skip; other failures → infra_seed.
        try:
            seed(plane, run_id=uuid.uuid4().hex, needs=task_needs, ctx=context)
        except TaskSkipped as skip:
            row.skipped = skip.reason
            row.verify_note = skip.reason
            print(f"  {task['id']} rep={repetition} SKIPPED: {skip.reason}")
        except Exception as exc:
            row.success = False
            row.error = f"{type(exc).__name__}: {exc}"
            row.error_class = "infra_seed"
            row.verify_note = ""
            print(
                f"  {task['id']} rep={repetition} ERROR[infra_seed]: {exc}",
                file=sys.stderr,
            )
            if context.get("project_name"):
                print(
                    f"  orphaned project may remain: {context['project_name']}",
                    file=sys.stderr,
                )
        else:
            if "bug_type" in task_needs and not context.get("bug_type"):
                reason = context.get("bug_type_skip_reason") or "bug_type unavailable"
                row.skipped = reason
                row.verify_note = reason
                print(f"  {task['id']} rep={repetition} SKIPPED: {reason}")
            else:
                agent: TaskResult | None = None
                # Agent wrap: API failures and CLI failures are infrastructure.
                # Contained CLI stops (timeout / error subtypes) return AgentRun.
                try:
                    agent = await run_agent_task_via_driver(
                        driver=driver,
                        model_id=model_id,
                        task=task,
                        ctx=context,
                        workspace_slug=workspace_slug,
                        optimal_tools=set(task["optimal_tools"]),
                        alternate_tools=set(task["alternate_tools"]),
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
                    )
                    agent = None
                except Exception as exc:
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
                    agent = None
                if agent is not None:
                    row.apply_agent_result(agent)
                    # Driver-level requested_model is the resolved ID.
                    # Restore run-level intent and retain both identities.
                    row.requested_model = model_alias
                    row.requested_tier = requested_tier
                    row.resolved_model = model_id
                    if external:
                        # Foreign tool names are not comparable to our catalog.
                        row.alternate_calls = None
                        row.out_of_set_calls = None
                    # CLI infra stops: timeout + error subtypes except error_max_turns.
                    stop_reason = agent.stop_reason
                    if driver_name.endswith("-cli") and is_infra_cli_stop_reason(
                        str(stop_reason) if stop_reason is not None else None
                    ):
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
                    else:
                        verify = task["verify"]
                        try:
                            agent_row = agent.to_row()
                            ok, note = await verify(
                                plane,
                                context,
                                {
                                    "final_text": agent.final_text,
                                    "calls": agent_row["calls"],
                                },
                            )
                            row.success = bool(ok)
                            row.verify_note = note
                            print(f"  {task['id']} rep={repetition} success={ok} calls={agent.num_calls} note={note!r}")
                        except TaskSkipped as skip:
                            row.skipped = skip.reason
                            row.verify_note = skip.reason
                            print(f"  {task['id']} rep={repetition} SKIPPED: {skip.reason}")
                        except Exception as exc:
                            row.success = False
                            row.error = f"{type(exc).__name__}: {exc}"
                            row.error_class = "task"
                            row.verify_note = ""
                            print(
                                f"  {task['id']} rep={repetition} ERROR[task]: {exc}",
                                file=sys.stderr,
                            )
    except Exception as exc:
        # Anything outside seed/driver/verify wraps.
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
    finally:
        try:
            teardown(plane, context)
        except Exception as exc:
            print(f"  teardown error: {exc}", file=sys.stderr)
            if context.get("project_name"):
                print(f"  orphaned project: {context['project_name']}", file=sys.stderr)
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
        print(f"resume: skipping {skip_count} completed rows, retrying {retry_count}")

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
    )
    if maybe_write_run_meta(out_path, meta):
        print(f"wrote meta header battery={battery} label={label}")

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
    print(f"writing {out_path}")

    with out_path.open("a", encoding="utf-8") as file:
        for task in tasks:
            for repetition in range(reps):
                if (task["id"], repetition, label) in resume_skip:
                    print(f"  {task['id']} rep={repetition} RESUME_SKIP")
                    continue
                row = await _run_task_repetition(
                    plane=plane,
                    driver=driver,
                    workspace_slug=workspace_slug,
                    task=task,
                    repetition=repetition,
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

    return 0
