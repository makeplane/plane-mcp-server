"""Live execution, resume bookkeeping, row assembly, and verifier canary."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.drivers import (
    KNOWN_DRIVERS,
    agent_run_to_harness_dict,
    get_driver,
)
from evals.seed import make_plane_client, seed, teardown
from evals.tasks import (
    PromptBindError,
    TaskSkipped,
    battery_fingerprint,
    format_task_prompt,
    resolve_surface_tool_sets,
    task_author,
)

# Surfaces the harness can run. ``full`` = legacy 177-tool stdio (default).
# ``v2`` / ``v2-schema`` set PLANE_MCP_SURFACE in the child env
# (see plane_mcp.v2.choose_stdio_mcp).
KNOWN_SURFACES = frozenset({"full", "v2", "v2-schema"})

MAX_ITERATIONS = 15
MAX_TOKENS = 8192


def _git_sha() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL,
                cwd=Path(__file__).resolve().parent.parent,
            )
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


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


def stdio_server_env(*, surface: str = "full", extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build MCP stdio env from scratch — never inherit os.environ (F6).

    ``surface=v2`` sets ``PLANE_MCP_SURFACE=v2`` so the child process serves the
    v2 tool registry. ``surface=full`` leaves the var unset (legacy default).
    Other surface names (external servers under benchmark) set nothing; their
    selection mechanism comes in via ``extra`` (--server-env) or --server-cmd args.
    """
    env: dict[str, str] = {}
    if path := os.environ.get("PATH"):
        env["PATH"] = path
    if home := os.environ.get("HOME"):
        env["HOME"] = home
    env["PLANE_API_KEY"] = os.environ["EVAL_PLANE_API_KEY"]
    env["PLANE_WORKSPACE_SLUG"] = os.environ["EVAL_PLANE_WORKSPACE_SLUG"]
    env["PLANE_BASE_URL"] = os.environ.get("EVAL_PLANE_BASE_URL", "https://api.plane.so")
    if surface == "v2":
        env["PLANE_MCP_SURFACE"] = "v2"
    elif surface == "v2-schema":
        env["PLANE_MCP_SURFACE"] = "v2-schema"
    if extra:
        env.update(extra)
    return env


def should_skip_resume_row(row: dict[str, Any]) -> bool:
    """Return True if a prior row is a completed result that resume should skip.

    Re-run when ``error_class`` starts with ``infra_`` or when ``error`` is non-null.
    Rows with ``skipped`` set are treated as complete and are not retried (intentional:
    surface/plan skips are stable outcomes, not infra failures).
    Pure function — unit-tested without the live battery.
    """
    ec = row.get("error_class")
    if isinstance(ec, str) and ec.startswith("infra_"):
        return False
    if row.get("error") is not None:
        return False
    return True


def _resume_field_mismatch(
    row: dict[str, Any],
    *,
    field: str,
    expected: str | None,
) -> str | None:
    """Return an error message if row[field] is present and disagrees with expected."""
    if expected is None:
        return None
    raw = row.get(field)
    if raw is None or raw == "":
        return None  # back-compat: older rows without the key pass
    # Surface/driver/provider compare case-insensitively; battery/model are exact.
    if field in ("surface", "driver", "provider"):
        got, want = str(raw).strip().lower(), expected.strip().lower()
    else:
        got, want = str(raw).strip(), expected.strip()
    if got != want:
        return f"error: --resume file {field} {raw!r} does not match current {field} {expected!r}"
    return None


def is_infra_cli_stop_reason(stop_reason: str | None) -> bool:
    """True when a CLI AgentRun stop_reason should be classified as infra_cli.

    ``timeout`` and Claude error subtypes (``error_during_execution``, bare
    ``error``, …) are infrastructure. ``error_max_turns`` is a genuine task
    failure and stays in the success-rate denominator.
    """
    if not stop_reason:
        return False
    sr = str(stop_reason)
    if sr == "timeout":
        return True
    if sr == "error_max_turns":
        return False
    if sr == "error" or sr.startswith("error_"):
        return True
    return False


def _timeout_error_message(agent: dict[str, Any]) -> str:
    """Prefer the driver's recorded timeout note over recomputing MAX_ITERATIONS."""
    for note in agent.get("driver_notes") or []:
        if isinstance(note, str) and note.startswith("timeout after"):
            return note
    return "timeout"


def is_meta_or_non_task_row(row: dict[str, Any]) -> bool:
    """True for run-header meta lines or any row without a task_id."""
    if row.get("row_type") == "meta":
        return True
    return row.get("task_id") is None


def load_resume_skip_keys(
    path: Path,
    *,
    surface: str,
    battery: str | None = None,
    model: str | None = None,
    driver: str | None = None,
    provider: str | None = None,
) -> tuple[set[tuple[str, int]], int, int]:
    """Load existing JSONL rows and decide which (task_id, rep) pairs to skip.

    Returns ``(skip_keys, n_skip, n_retry)`` where ``n_retry = len(seen - skip_keys)``
    (keys that still need a re-run). Raises ``SystemExit`` when a row's surface /
    battery / model / driver / provider disagrees with the current run (missing keys pass for
    back-compat). Meta lines (``row_type=meta`` or no task_id) are mismatch-checked
    but not counted as task rows. Truncated/invalid JSON lines are warned and skipped.
    """
    if not path.is_file():
        return set(), 0, 0
    skip_keys: set[tuple[str, int]] = set()
    seen: set[tuple[str, int]] = set()
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                print(
                    f"warning: --resume {path}:{line_no}: skipping invalid JSON ({exc})",
                    file=sys.stderr,
                )
                continue
            if not isinstance(row, dict):
                continue
            for field, expected in (
                ("surface", surface),
                ("battery", battery),
                ("driver", driver),
                ("provider", provider),
            ):
                msg = _resume_field_mismatch(row, field=field, expected=expected)
                if msg:
                    raise SystemExit(msg)
            # New API rows keep both the requested ID (resume identity) and the
            # provider-reported model that actually ran. Older rows only have model.
            model_row = dict(row)
            if model_row.get("requested_model"):
                model_row["model"] = model_row["requested_model"]
            msg = _resume_field_mismatch(model_row, field="model", expected=model)
            if msg:
                raise SystemExit(msg)
            # Meta / header rows: checked above, not part of resume key set.
            if is_meta_or_non_task_row(row):
                continue
            tid = row.get("task_id")
            rep = row.get("rep")
            if tid is None or rep is None:
                continue
            key = (str(tid), int(rep))
            seen.add(key)
            if should_skip_resume_row(row):
                skip_keys.add(key)
            else:
                # Prior infra/error row: do not skip (will re-run). Drop any earlier skip.
                skip_keys.discard(key)
    n_retry = len(seen - skip_keys)
    return skip_keys, len(skip_keys), n_retry


def make_run_meta_row(
    *,
    run_id: str,
    surface: str,
    battery: str,
    model: str | None,
    driver: str,
    git_sha: str,
    provider: str | None = None,
    ts: str | None = None,
) -> dict[str, Any]:
    """Build the single first-line meta record for a new output JSONL."""
    return {
        "row_type": "meta",
        "run_id": run_id,
        "surface": surface,
        "battery": battery,
        "model": model,
        "driver": driver,
        "provider": provider,
        "git_sha": git_sha,
        "ts": ts or datetime.now(timezone.utc).isoformat(),
    }


def maybe_write_run_meta(path: Path, meta: dict[str, Any]) -> bool:
    """Write meta as the first line when the file is missing or empty. Returns True if written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size > 0:
        return False
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps(meta, default=str) + "\n")
    return True


async def run_agent_task_via_driver(
    *,
    driver: Any,
    model_id: str | None,
    task: dict[str, Any],
    ctx: dict[str, Any],
    workspace_slug: str,
    surface: str = "full",
    optimal_tools: set[str] | None = None,
    alternate_tools: set[str] | None = None,
    server_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run one task through the selected AgentDriver."""
    project_name = ctx["project_name"]
    system = _system_preamble(workspace_slug, project_name)
    prompt = format_task_prompt(task, ctx, strict=True)
    optimal = set(optimal_tools) if optimal_tools is not None else set(task["optimal_tools"])
    alternate = set(alternate_tools) if alternate_tools is not None else set(task["alternate_tools"])
    assert optimal.isdisjoint(alternate), f"{task['id']}: optimal/alternate overlap"

    mcp_env = stdio_server_env(surface=surface, extra=server_env)
    # AgentDriver is sync (CLI subprocess or API loop); keep it off this loop.
    agent_run = await asyncio.to_thread(
        driver.run_task,
        prompt,
        mcp_env,
        model_id,
        MAX_ITERATIONS,
        system=system,
        cwd=Path(__file__).resolve().parent.parent,
    )
    return agent_run_to_harness_dict(
        agent_run,
        optimal=optimal,
        alternate=alternate,
        classify=classify_call,
    )


def _base_row(
    *,
    run_id: str,
    git_sha: str,
    surface: str,
    driver_name: str,
    provider: str | None,
    model_id: str | None,
    task: dict[str, Any],
    rep: int,
    battery: str,
    classification: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "git_sha": git_sha,
        "battery": battery,
        "surface": surface,
        "driver": driver_name,
        "provider": provider,
        "classification": classification,
        "model": model_id,
        "requested_model": model_id,
        "task_id": task["id"],
        "author": task_author(task),
        "rep": rep,
        "success": False,
        "verify_note": "",
        "skipped": None,
        "error": None,
        "error_class": None,
        "final_text": "",
        "stop_reason": None,
        "hit_max_iterations": False,
        "result_pair_mismatch": False,
        "token_count_failures": 0,
        "result_tokens_estimated": None,
        "calls": [],
        "num_calls": 0,
        "errored_calls": 0,
        "alternate_calls": 0,
        "out_of_set_calls": 0,
        "total_result_tokens": 0,
        "usage_per_iteration": [],
        "cum_input_tokens": 0,
        "wall_time_s": 0.0,
    }


async def run_live(
    tasks: list[dict[str, Any]],
    *,
    model_alias: str,
    reps: int,
    surface: str,
    out_path: Path,
    driver_name: str = "api",
    provider: str = "anthropic",
    server_cmd: list[str] | None = None,
    server_env: dict[str, str] | None = None,
    resume: bool = False,
    record_result_payloads: bool = False,
    resolved_model_id: str | None = None,
) -> int:
    surface = (surface or "full").strip().lower()
    external = server_cmd is not None
    if not external and surface not in KNOWN_SURFACES:
        print(
            f"error: unknown --surface {surface!r}; expected one of {sorted(KNOWN_SURFACES)} "
            "(or pass --server-cmd for an external surface)",
            file=sys.stderr,
        )
        return 2

    driver_name = (driver_name or "api").strip().lower()
    if driver_name not in KNOWN_DRIVERS:
        print(
            f"error: unknown --driver {driver_name!r}; expected one of {sorted(KNOWN_DRIVERS)}",
            file=sys.stderr,
        )
        return 2
    provider = (provider or "anthropic").strip().lower()
    is_api_driver = driver_name in ("api", "sdk")
    provider_id = provider if is_api_driver else None
    model_id = resolved_model_id if resolved_model_id is not None else model_alias

    run_id = uuid.uuid4().hex
    git_sha = _git_sha()
    battery = battery_fingerprint(tasks)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    resume_skip: set[tuple[str, int]] = set()
    if resume:
        try:
            resume_skip, n_skip, n_retry = load_resume_skip_keys(
                out_path,
                surface=surface,
                battery=battery,
                model=model_id,
                driver=driver_name,
                provider=provider_id,
            )
        except SystemExit as e:
            print(e, file=sys.stderr)
            return 2
        print(f"resume: skipping {n_skip} completed rows, retrying {n_retry}")

    # First line of a new/empty file is a meta header (skipped by loaders).
    meta = make_run_meta_row(
        run_id=run_id,
        surface=surface,
        battery=battery,
        model=model_id,
        driver=driver_name,
        provider=provider_id,
        git_sha=git_sha,
    )
    if maybe_write_run_meta(out_path, meta):
        print(f"wrote meta header battery={battery} surface={surface}")

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
        f"run_id={run_id} battery={battery} driver={driver_name} provider={provider_id} model={model_id} "
        f"surface={surface} tasks={[t['id'] for t in tasks]} reps={reps}"
    )
    print(f"writing {out_path}")

    async def _run_tasks() -> None:
        with out_path.open("a", encoding="utf-8") as fh:
            for task in tasks:
                if external:
                    # Foreign tool names have no overlay sets: no skips, no
                    # mispick classification — success/calls/errors only.
                    surface_sets = {
                        "skip": None,
                        "optimal_tools": set(),
                        "alternate_tools": set(),
                        "classification": "external",
                    }
                else:
                    surface_sets = resolve_surface_tool_sets(task, surface)
                for rep in range(reps):
                    if (task["id"], rep) in resume_skip:
                        print(f"  {task['id']} rep={rep} RESUME_SKIP")
                        continue

                    ctx: dict[str, Any] = {}
                    row = _base_row(
                        run_id=run_id,
                        git_sha=git_sha,
                        surface=surface,
                        driver_name=driver_name,
                        provider=provider_id,
                        model_id=model_id,
                        task=task,
                        rep=rep,
                        battery=battery,
                        classification=str(surface_sets["classification"]),
                    )
                    try:
                        # Surface-unsupported tasks: record skip, no seed/agent.
                        if surface_sets.get("skip"):
                            reason = surface_sets["skip"]
                            row["skipped"] = reason
                            row["verify_note"] = reason
                            print(f"  {task['id']} rep={rep} SKIPPED: {reason}")
                        else:
                            task_needs = set(task.get("needs") or set())
                            # Seed wrap: TaskSkipped → skip; other failures → infra_seed.
                            try:
                                seed(plane, run_id=uuid.uuid4().hex, needs=task_needs, ctx=ctx)
                            except TaskSkipped as skip:
                                row["skipped"] = skip.reason
                                row["verify_note"] = skip.reason
                                print(f"  {task['id']} rep={rep} SKIPPED: {skip.reason}")
                            except Exception as exc:
                                row["success"] = False
                                row["error"] = f"{type(exc).__name__}: {exc}"
                                row["error_class"] = "infra_seed"
                                row["verify_note"] = ""
                                print(
                                    f"  {task['id']} rep={rep} ERROR[infra_seed]: {exc}",
                                    file=sys.stderr,
                                )
                                if ctx.get("project_name"):
                                    print(
                                        f"  orphaned project may remain: {ctx['project_name']}",
                                        file=sys.stderr,
                                    )
                            else:
                                if "bug_type" in task_needs and not ctx.get("bug_type"):
                                    reason = ctx.get("bug_type_skip_reason") or "bug_type unavailable"
                                    row["skipped"] = reason
                                    row["verify_note"] = reason
                                    print(f"  {task['id']} rep={rep} SKIPPED: {reason}")
                                else:
                                    agent: dict[str, Any] | None = None
                                    # Agent wrap: API failures and CLI failures are infrastructure.
                                    # Contained CLI stops (timeout / error subtypes) return AgentRun.
                                    try:
                                        agent = await run_agent_task_via_driver(
                                            driver=driver,
                                            model_id=model_id,
                                            task=task,
                                            ctx=ctx,
                                            workspace_slug=workspace_slug,
                                            surface=surface,
                                            optimal_tools=surface_sets["optimal_tools"],
                                            alternate_tools=surface_sets["alternate_tools"],
                                            server_env=server_env,
                                        )
                                    except PromptBindError as exc:
                                        # Empty/missing seed IDs in the prompt — not an agent failure.
                                        row["success"] = False
                                        row["error"] = f"{type(exc).__name__}: {exc}"
                                        row["error_class"] = "infra_seed"
                                        row["verify_note"] = ""
                                        print(
                                            f"  {task['id']} rep={rep} ERROR[infra_seed]: {exc}",
                                            file=sys.stderr,
                                        )
                                        agent = None
                                    except Exception as exc:
                                        if driver_name == "sdk":
                                            agent_err_class = "infra_sdk"
                                        elif is_api_driver:
                                            agent_err_class = "infra_api"
                                        else:
                                            agent_err_class = "infra_cli"
                                        row["success"] = False
                                        row["error"] = f"{type(exc).__name__}: {exc}"
                                        row["error_class"] = agent_err_class
                                        row["verify_note"] = ""
                                        print(
                                            f"  {task['id']} rep={rep} ERROR[{agent_err_class}]: {exc}",
                                            file=sys.stderr,
                                        )
                                        agent = None

                                    if agent is not None:
                                        row.update(agent)
                                        if external:
                                            # Empty overlay sets would classify every call
                                            # out-of-set; null the counters instead.
                                            row["alternate_calls"] = None
                                            row["out_of_set_calls"] = None

                                        # CLI infra stops: timeout + error subtypes except error_max_turns.
                                        stop_reason = agent.get("stop_reason")
                                        if driver_name.endswith("-cli") and is_infra_cli_stop_reason(
                                            str(stop_reason) if stop_reason is not None else None
                                        ):
                                            row["success"] = False
                                            row["error_class"] = "infra_cli"
                                            if stop_reason == "timeout":
                                                row["error"] = _timeout_error_message(agent)
                                            else:
                                                notes = [
                                                    n for n in (agent.get("driver_notes") or []) if isinstance(n, str)
                                                ]
                                                detail = "; ".join(notes) if notes else str(stop_reason)
                                                row["error"] = detail
                                            row["verify_note"] = ""
                                            print(
                                                f"  {task['id']} rep={rep} ERROR[infra_cli]: {row['error']}",
                                                file=sys.stderr,
                                            )
                                        else:
                                            verify = task["verify"]
                                            try:
                                                ok, note = await verify(
                                                    plane,
                                                    ctx,
                                                    {
                                                        "final_text": agent["final_text"],
                                                        "calls": agent["calls"],
                                                    },
                                                )
                                                row["success"] = bool(ok)
                                                row["verify_note"] = note
                                                print(
                                                    f"  {task['id']} rep={rep} success={ok} "
                                                    f"calls={agent['num_calls']} note={note!r}"
                                                )
                                            except TaskSkipped as skip:
                                                row["skipped"] = skip.reason
                                                row["verify_note"] = skip.reason
                                                print(f"  {task['id']} rep={rep} SKIPPED: {skip.reason}")
                                            except Exception as exc:
                                                row["success"] = False
                                                row["error"] = f"{type(exc).__name__}: {exc}"
                                                row["error_class"] = "task"
                                                row["verify_note"] = ""
                                                print(
                                                    f"  {task['id']} rep={rep} ERROR[task]: {exc}",
                                                    file=sys.stderr,
                                                )
                    except Exception as exc:
                        # Anything outside seed/driver/verify wraps.
                        row["success"] = False
                        row["error"] = f"{type(exc).__name__}: {exc}"
                        row["error_class"] = "task"
                        row["verify_note"] = ""
                        print(f"  {task['id']} rep={rep} ERROR[task]: {exc}", file=sys.stderr)
                        if ctx.get("project_name"):
                            print(
                                f"  orphaned project may remain: {ctx['project_name']}",
                                file=sys.stderr,
                            )
                    finally:
                        try:
                            teardown(plane, ctx)
                        except Exception as exc:
                            print(f"  teardown error: {exc}", file=sys.stderr)
                            if ctx.get("project_name"):
                                print(f"  orphaned project: {ctx['project_name']}", file=sys.stderr)

                    fh.write(json.dumps(row, default=str) + "\n")
                    fh.flush()

    await _run_tasks()

    return 0


async def run_canary(
    tasks: list[dict[str, Any]],
    *,
    surface: str,
) -> int:
    """Seed + verify(empty agent) + teardown per task; no driver/model.

    Passes only when every verifier returns falsy ok on a do-nothing agent.
    Any ok=True is a broken verifier (false positive).
    """
    surface = (surface or "full").strip().lower()
    if surface not in KNOWN_SURFACES:
        print(
            f"error: unknown --surface {surface!r}; expected one of {sorted(KNOWN_SURFACES)}",
            file=sys.stderr,
        )
        return 2

    battery = battery_fingerprint(tasks)
    plane, _workspace_slug = make_plane_client()
    print(f"canary battery={battery} surface={surface} tasks={[t['id'] for t in tasks]}")

    broken: list[str] = []
    verified_count = 0
    empty_agent = {"final_text": "", "calls": []}

    for task in tasks:
        surface_sets = resolve_surface_tool_sets(task, surface)
        if surface_sets.get("skip"):
            print(f"  {task['id']} SKIPPED (surface): {surface_sets['skip']}")
            continue

        ctx: dict[str, Any] = {}
        task_needs = set(task.get("needs") or set())
        try:
            try:
                seed(plane, run_id=uuid.uuid4().hex, needs=task_needs, ctx=ctx)
            except TaskSkipped as skip:
                print(f"  {task['id']} SKIPPED: {skip.reason}")
                continue
            if "bug_type" in task_needs and not ctx.get("bug_type"):
                reason = ctx.get("bug_type_skip_reason") or "bug_type unavailable"
                print(f"  {task['id']} SKIPPED: {reason}")
                continue
            try:
                ok, note = await task["verify"](plane, ctx, empty_agent)
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
                teardown(plane, ctx)
            except Exception as exc:
                print(f"  teardown error: {exc}", file=sys.stderr)

    if broken:
        for tid in broken:
            print(f"BROKEN VERIFIER: {tid}", file=sys.stderr)
        return 1
    if verified_count == 0:
        print(
            "error: canary verified 0 tasks (all skipped by surface/plan gates) — nothing exercised; refusing exit 0",
            file=sys.stderr,
        )
        return 1
    print(f"canary: all verifiers reject empty agent ({verified_count} verified)")
    return 0


__all__ = [
    "KNOWN_SURFACES",
    "MAX_ITERATIONS",
    "MAX_TOKENS",
    "classify_call",
    "is_infra_cli_stop_reason",
    "is_meta_or_non_task_row",
    "load_resume_skip_keys",
    "make_run_meta_row",
    "maybe_write_run_meta",
    "run_agent_task_via_driver",
    "run_canary",
    "run_live",
    "should_skip_resume_row",
    "stdio_server_env",
]
