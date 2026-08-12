"""CLI driver for the Plane MCP tool-surface eval harness.

Usage:
  python -m evals.run --list
  python -m evals.run --dry-run --tasks R1
  python -m evals.run --tasks R1,W1,S1 --model sonnet --reps 1 --surface full
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evals.drivers import (
    KNOWN_DRIVERS,
    agent_run_to_harness_dict,
    get_driver,
)
from evals.seed import make_plane_client, seed, seed_plan, teardown
from evals.tasks import (
    TASKS,
    PromptBindError,
    TaskSkipped,
    battery_fingerprint,
    format_task_prompt,
    get_tasks,
    resolve_surface_tool_sets,
    task_author,
)

MODEL_ALIASES: dict[str, str] = {
    "sonnet": "claude-sonnet-5",
    "haiku": "claude-haiku-4-5",
}
# Per-driver resolution of the short harness aliases (sonnet/haiku).
# Drivers that need provider/model form get qualified defaults; unknown
# strings (e.g. ``anthropic/claude-…``) pass through unchanged.
CLI_MODEL_ALIASES: dict[str, dict[str, str]] = {
    "claude-cli": {"sonnet": "sonnet", "haiku": "haiku"},
    "codex-cli": {"sonnet": "sonnet", "haiku": "haiku"},
    "antigravity-cli": {
        "sonnet": "gemini-3.6-flash-high",
        "haiku": "gemini-3.6-flash-low",
    },
    "opencode-cli": {
        "sonnet": "anthropic/claude-sonnet-4-20250514",
        "haiku": "anthropic/claude-haiku-4-5-20251001",
    },
}


def resolve_model_for_driver(driver_name: str, model: str) -> str:
    """Map a harness model token to the string the given driver expects.

    Known short aliases (sonnet/haiku) are looked up per-driver. Any other
    string (including already-qualified ``provider/model``) is passed through.
    """
    key = (driver_name or "sdk").strip().lower()
    if key == "sdk":
        return MODEL_ALIASES.get(model, model)
    table = CLI_MODEL_ALIASES.get(key) or {}
    return table.get(model, model)


# Surfaces the harness can run. ``full`` = legacy 177-tool stdio (default).
# ``v2`` / ``v2-schema`` set PLANE_MCP_SURFACE in the child env
# (see plane_mcp.v2.choose_stdio_mcp).
KNOWN_SURFACES = frozenset({"full", "v2", "v2-schema"})

DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "results"
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


def _tool_result_text(content: Any) -> tuple[str, str]:
    """Return (text_for_counting, result_kind).

    result_kind is 'text' | 'image' | 'mixed'. Mixed keeps text for token counting
    and records that non-text blocks were present (char length of full payload).
    """
    if content is None:
        return "", "text"
    if isinstance(content, str):
        return content, "text"
    if isinstance(content, list):
        texts: list[str] = []
        saw_non_text = False
        for block in content:
            btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
            if btype == "text" or btype is None:
                text = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else None)
                if text is None and isinstance(block, str):
                    text = block
                if text is not None:
                    texts.append(str(text))
            else:
                saw_non_text = True
        joined = "\n".join(texts)
        if texts and saw_non_text:
            return joined, "mixed"
        if texts:
            return joined, "text"
        if saw_non_text:
            return json.dumps(content, default=str), "image"
        return "", "text"
    return str(content), "text"


async def _count_result_tokens(client: Any, model: str, result_text: str) -> int | None:
    if not result_text:
        return 0
    try:
        n = await client.messages.count_tokens(
            model=model,
            messages=[{"role": "user", "content": result_text}],
        )
        return n.input_tokens
    except Exception as exc:
        print(f"count_tokens warning: {exc}", file=sys.stderr)
        return None


def _extract_final_text(message: Any) -> str:
    if message is None:
        return ""
    parts: list[str] = []
    for block in getattr(message, "content", None) or []:
        if getattr(block, "type", None) == "text" and getattr(block, "text", None):
            parts.append(block.text)
    return "\n".join(parts)


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
    # surface/driver compare case-insensitively; battery/model are exact strings.
    if field in ("surface", "driver"):
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
) -> tuple[set[tuple[str, int]], int, int]:
    """Load existing JSONL rows and decide which (task_id, rep) pairs to skip.

    Returns ``(skip_keys, n_skip, n_retry)`` where ``n_retry = len(seen - skip_keys)``
    (keys that still need a re-run). Raises ``SystemExit`` when a row's surface /
    battery / model / driver disagrees with the current run (missing keys pass for
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
                ("model", model),
                ("driver", driver),
            ):
                msg = _resume_field_mismatch(row, field=field, expected=expected)
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Plane MCP tool-surface eval harness")
    p.add_argument("--list", action="store_true", help="Print task table (no network)")
    p.add_argument("--dry-run", action="store_true", help="Print resolved prompts + seed plan (no network)")
    p.add_argument("--tasks", type=str, default=None, help="Comma-separated task ids (default: all)")
    p.add_argument(
        "--model",
        type=str,
        default="sonnet",
        help=(
            "Model alias (sonnet/haiku) or a free-form provider/model id. "
            "Short aliases are remapped per --driver (opencode/antigravity get qualified names)."
        ),
    )
    p.add_argument("--reps", type=int, default=1, help="Repetitions per task")
    p.add_argument(
        "--surface",
        type=str,
        default="full",
        help=(
            "Tool surface: 'full' (legacy 177 tools), 'v2', or 'v2-schema'. "
            "With --server-cmd it is a free-form label for the external surface."
        ),
    )
    p.add_argument(
        "--server-cmd",
        type=str,
        default=None,
        help=(
            "External MCP stdio server launch command (shlex-split), e.g. "
            "'/path/venv/bin/python -m plane_mcp stdio --v2'. Enables external mode: "
            "all tasks run (no surface skips) and mispick classification is disabled "
            "(the foreign tool names have no overlay sets). CLI drivers only."
        ),
    )
    p.add_argument(
        "--server-env",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="Extra env var for the (external) MCP server child; repeatable.",
    )
    p.add_argument(
        "--driver",
        type=str,
        default="sdk",
        choices=sorted(KNOWN_DRIVERS),
        help=(
            "Agent backend: sdk | claude-cli | codex-cli | antigravity-cli | opencode-cli. Not required for --canary."
        ),
    )
    p.add_argument("--out", type=str, default=None, help="JSONL output path")
    p.add_argument(
        "--resume",
        type=str,
        default=None,
        metavar="OUT.jsonl",
        help=(
            "Resume into an existing JSONL (also the --out target). Skip (task_id, rep) "
            "pairs that already completed; re-run rows with infra_ error_class or non-null error."
        ),
    )
    p.add_argument(
        "--canary",
        action="store_true",
        help=(
            "Verifier canary: seed each task, call verify with an empty agent result "
            "(no driver/model), teardown. Exit 1 if any verifier returns ok=True on do-nothing."
        ),
    )
    return p.parse_args(argv)


def _task_ids(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    return [t.strip() for t in raw.split(",") if t.strip()]


def cmd_list() -> int:
    print(f"{'id':<6} {'tags':<18} {'opt':>4}  prompt")
    print("-" * 100)
    for t in TASKS:
        tags = ",".join(sorted(t["tags"]))
        prompt = t["prompt"].replace("\n", " ")
        if len(prompt) > 70:
            prompt = prompt[:67] + "..."
        print(f"{t['id']:<6} {tags:<18} {t['optimal_calls']:>4}  {prompt}")
    return 0


def cmd_dry_run(tasks: list[dict[str, Any]]) -> int:
    needs: set[str] = set()
    for t in tasks:
        needs |= set(t.get("needs") or set())
    print("Seed plan:")
    for line in seed_plan(needs):
        print(f"  {line}")
    print()
    sample_ctx = {"project_name": "EVAL deadbeef"}
    for t in tasks:
        resolved = format_task_prompt(t, sample_ctx, strict=False)
        print(f"=== {t['id']} ===")
        print(f"needs: {sorted(t.get('needs') or [])}")
        print(f"author: {t.get('author') or 'claude'}")
        print(f"optimal_calls: {t['optimal_calls']}")
        print(f"optimal_tools: {sorted(t['optimal_tools'])}")
        print(f"prompt:\n  {resolved}")
        print()
    return 0


async def run_agent_task(
    *,
    client: Any,
    model_id: str,
    task: dict[str, Any],
    ctx: dict[str, Any],
    workspace_slug: str,
    surface: str = "full",
    optimal_tools: set[str] | None = None,
    alternate_tools: set[str] | None = None,
) -> dict[str, Any]:
    """Run one task against a fresh stdio MCP server subprocess."""
    from anthropic.lib.tools.mcp import async_mcp_tool
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    project_name = ctx["project_name"]
    system = _system_preamble(workspace_slug, project_name)
    # strict: empty binder values / exceptions are infra_seed, not blank-ID prompts.
    prompt = format_task_prompt(task, ctx, strict=True)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "plane_mcp", "stdio"],
        env=stdio_server_env(surface=surface),
    )

    optimal = set(optimal_tools) if optimal_tools is not None else set(task["optimal_tools"])
    alternate = set(alternate_tools) if alternate_tools is not None else set(task["alternate_tools"])
    assert optimal.isdisjoint(alternate), f"{task['id']}: optimal/alternate overlap"

    calls: list[dict[str, Any]] = []
    # (call_idx, text, kind, is_error) buffered for post-loop count_tokens
    pending_results: list[tuple[int, str, str, bool]] = []
    usage_per_iteration: list[dict[str, int]] = []
    final_message = None
    iterations = 0
    result_pair_mismatch = False
    wall_time_s = 0.0

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as mcp_client:
            await mcp_client.initialize()
            tools_result = await mcp_client.list_tools()
            runner = client.beta.messages.tool_runner(
                model=model_id,
                max_tokens=MAX_TOKENS,
                max_iterations=MAX_ITERATIONS,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                tools=[async_mcp_tool(t, mcp_client) for t in tools_result.tools],
            )
            # wall_time: agent loop only (after list_tools, before subprocess teardown)
            t0 = time.perf_counter()
            try:
                async for message in runner:
                    iterations += 1
                    final_message = message
                    usage = getattr(message, "usage", None)
                    if usage is not None:
                        usage_per_iteration.append(
                            {
                                "in": getattr(usage, "input_tokens", 0) or 0,
                                "out": getattr(usage, "output_tokens", 0) or 0,
                                "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
                                "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
                            }
                        )

                    # Map tool_use_id → call index for result pairing (not ordinal-only).
                    tool_use_by_id: dict[str, int] = {}
                    for block in message.content or []:
                        if getattr(block, "type", None) == "tool_use":
                            name = block.name
                            args = block.input if hasattr(block, "input") else {}
                            try:
                                args_chars = len(json.dumps(args, default=str))
                            except Exception:
                                args_chars = len(str(args))
                            call_rec = {
                                "tool": name,
                                "class": classify_call(name, optimal, alternate),
                                "args_chars": args_chars,
                                "result_tokens": None,
                                "result_chars": 0,
                                "result_kind": "text",
                                "is_error": False,
                            }
                            idx = len(calls)
                            calls.append(call_rec)
                            use_id = getattr(block, "id", None)
                            if use_id:
                                tool_use_by_id[str(use_id)] = idx

                    # Never execute tools on a refusal-terminated turn (F2 / SDK guard).
                    if getattr(message, "stop_reason", None) == "refusal":
                        continue

                    tool_response = await runner.generate_tool_call_response()
                    if tool_response is not None:
                        if isinstance(tool_response, dict):
                            blocks = tool_response.get("content") or []
                        else:
                            blocks = getattr(tool_response, "content", None) or []
                        result_blocks = [
                            b
                            for b in blocks
                            if getattr(b, "type", None) == "tool_result"
                            or (isinstance(b, dict) and b.get("type") == "tool_result")
                        ]
                        matched_ids: set[str] = set()
                        for block in result_blocks:
                            if isinstance(block, dict):
                                is_error = bool(block.get("is_error"))
                                raw_content = block.get("content")
                                tool_use_id = block.get("tool_use_id")
                            else:
                                is_error = bool(getattr(block, "is_error", False))
                                raw_content = getattr(block, "content", None)
                                tool_use_id = getattr(block, "tool_use_id", None)
                            text, kind = _tool_result_text(raw_content)
                            if tool_use_id is not None and str(tool_use_id) in tool_use_by_id:
                                idx = tool_use_by_id[str(tool_use_id)]
                                matched_ids.add(str(tool_use_id))
                            else:
                                result_pair_mismatch = True
                                continue
                            calls[idx]["is_error"] = is_error
                            calls[idx]["result_kind"] = kind
                            if kind == "text":
                                calls[idx]["result_chars"] = len(text)
                            else:
                                calls[idx]["result_chars"] = len(str(raw_content))
                            pending_results.append((idx, text, kind, is_error))
                        if len(matched_ids) != len(tool_use_by_id):
                            result_pair_mismatch = True
            finally:
                wall_time_s = time.perf_counter() - t0

    # Token-count tool results after the agent loop (must not pollute wall_time).
    token_count_failures = 0
    for idx, text, kind, _is_error in pending_results:
        if kind not in ("text", "mixed"):
            calls[idx]["result_tokens"] = None
            continue
        counted = await _count_result_tokens(client, model_id, text)
        calls[idx]["result_tokens"] = counted
        if counted is None and text:
            token_count_failures += 1

    stop_reason = getattr(final_message, "stop_reason", None) if final_message else None
    # Cap detection is stop_reason-aware only (F0/F3): a clean end_turn (or max_tokens,
    # which the report already counts separately) on the 15th yield is not flagged here.
    # Only runs that exhaust the iteration budget while still mid-tool-loop count.
    hit_max_iterations = iterations >= MAX_ITERATIONS and stop_reason not in (
        "end_turn",
        "max_tokens",
    )

    final_text = _extract_final_text(final_message)
    errored = sum(1 for c in calls if c.get("is_error"))
    alternate_n = sum(1 for c in calls if c["class"] == "alternate")
    out_of_set_n = sum(1 for c in calls if c["class"] == "out_of_set")
    total_result_tokens = sum(c["result_tokens"] or 0 for c in calls if c.get("result_tokens") is not None)
    cum_input = sum(u.get("in", 0) for u in usage_per_iteration)

    return {
        "final_text": final_text,
        "calls": calls,
        "num_calls": len(calls),
        "errored_calls": errored,
        "alternate_calls": alternate_n,
        "out_of_set_calls": out_of_set_n,
        "total_result_tokens": total_result_tokens,
        "usage_per_iteration": usage_per_iteration,
        "cum_input_tokens": cum_input,
        "wall_time_s": round(wall_time_s, 3),
        "stop_reason": stop_reason,
        "hit_max_iterations": hit_max_iterations,
        "result_pair_mismatch": result_pair_mismatch,
        "token_count_failures": token_count_failures,
    }


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
    """Run one task through a CLI (or other) AgentDriver."""
    project_name = ctx["project_name"]
    system = _system_preamble(workspace_slug, project_name)
    prompt = format_task_prompt(task, ctx, strict=True)
    optimal = set(optimal_tools) if optimal_tools is not None else set(task["optimal_tools"])
    alternate = set(alternate_tools) if alternate_tools is not None else set(task["alternate_tools"])
    assert optimal.isdisjoint(alternate), f"{task['id']}: optimal/alternate overlap"

    mcp_env = stdio_server_env(surface=surface, extra=server_env)
    # Drivers are sync (subprocess); run off the event loop thread.
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
        skip_result_tokens=True,
    )


def _base_row(
    *,
    run_id: str,
    git_sha: str,
    surface: str,
    driver_name: str,
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
        "classification": classification,
        "model": model_id,
        "task_id": task["id"],
        "author": task_author(task),
        "rep": rep,
        "success": False,
        "verify_note": "",
        "skipped": None,
        "error": None,
        "error_class": None,
        "stop_reason": None,
        "hit_max_iterations": False,
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
    driver_name: str = "sdk",
    server_cmd: list[str] | None = None,
    server_env: dict[str, str] | None = None,
    resume: bool = False,
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

    driver_name = (driver_name or "sdk").strip().lower()
    if driver_name not in KNOWN_DRIVERS:
        print(
            f"error: unknown --driver {driver_name!r}; expected one of {sorted(KNOWN_DRIVERS)}",
            file=sys.stderr,
        )
        return 2
    if external and driver_name == "sdk":
        print(
            f"error: --server-cmd requires a CLI driver (one of {sorted(KNOWN_DRIVERS - {'sdk'})})",
            file=sys.stderr,
        )
        return 2

    use_sdk = driver_name == "sdk"
    model_id = resolve_model_for_driver(driver_name, model_alias)

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
        git_sha=git_sha,
    )
    if maybe_write_run_meta(out_path, meta):
        print(f"wrote meta header battery={battery} surface={surface}")

    plane, workspace_slug = make_plane_client()
    # User chose --driver explicitly: codex live is allowed (they own the quota).
    driver_kwargs: dict[str, Any] = {}
    if driver_name == "codex-cli":
        driver_kwargs["allow_live"] = True
    # --server-cmd must reach every CLI driver (not just Claude); otherwise we
    # silently benchmark the wrong server.
    if server_cmd is not None:
        if use_sdk:
            print("error: --server-cmd is incompatible with --driver sdk", file=sys.stderr)
            return 2
        driver_kwargs["server_command"] = server_cmd
    cli_driver = None if use_sdk else get_driver(driver_name, **driver_kwargs)

    print(
        f"run_id={run_id} battery={battery} driver={driver_name} model={model_id} "
        f"surface={surface} tasks={[t['id'] for t in tasks]} reps={reps}"
    )
    print(f"writing {out_path}")

    async def _one_client_scope(client: Any | None) -> None:
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
                                    # Agent wrap: SDK bugs → infra_sdk; CLI raises → infra_cli.
                                    # Contained CLI stops (timeout / error subtypes) return AgentRun.
                                    try:
                                        if use_sdk:
                                            assert client is not None
                                            agent = await run_agent_task(
                                                client=client,
                                                model_id=model_id,
                                                task=task,
                                                ctx=ctx,
                                                workspace_slug=workspace_slug,
                                                surface=surface,
                                                optimal_tools=surface_sets["optimal_tools"],
                                                alternate_tools=surface_sets["alternate_tools"],
                                            )
                                        else:
                                            assert cli_driver is not None
                                            agent = await run_agent_task_via_driver(
                                                driver=cli_driver,
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
                                        # SDK harness bugs must not look like CLI infra.
                                        agent_err_class = "infra_sdk" if use_sdk else "infra_cli"
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
                                        row.update({k: agent[k] for k in agent if k != "final_text"})
                                        if external:
                                            # Empty overlay sets would classify every call
                                            # out-of-set; null the counters instead.
                                            row["alternate_calls"] = None
                                            row["out_of_set_calls"] = None

                                        # CLI infra stops: timeout + error subtypes except error_max_turns.
                                        stop_reason = agent.get("stop_reason")
                                        if not use_sdk and is_infra_cli_stop_reason(
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

    if use_sdk:
        from anthropic import AsyncAnthropic

        async with AsyncAnthropic() as client:
            await _one_client_scope(client)
    else:
        await _one_client_scope(None)

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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.list:
        return cmd_list()

    ids = _task_ids(args.tasks)
    try:
        tasks = get_tasks(ids)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2

    if args.dry_run:
        return cmd_dry_run(tasks)

    surface = (args.surface or "full").strip().lower()
    server_cmd: list[str] | None = None
    if args.server_cmd:
        import shlex

        server_cmd = shlex.split(args.server_cmd)
        if not server_cmd:
            print("error: --server-cmd is empty", file=sys.stderr)
            return 2
    elif surface not in KNOWN_SURFACES:
        print(
            f"error: unknown --surface {surface!r}; expected one of {sorted(KNOWN_SURFACES)} "
            "(or pass --server-cmd for an external surface)",
            file=sys.stderr,
        )
        return 2

    server_env: dict[str, str] = {}
    for pair in args.server_env:
        key, sep, val = pair.partition("=")
        if not sep or not key:
            print(f"error: --server-env expects KEY=VAL, got {pair!r}", file=sys.stderr)
            return 2
        server_env[key] = val

    # Canary: live env only — no driver/model required.
    if args.canary:
        return asyncio.run(run_canary(tasks, surface=surface))

    driver_name = (getattr(args, "driver", None) or "sdk").strip().lower()
    if driver_name not in KNOWN_DRIVERS:
        print(
            f"error: unknown --driver {driver_name!r}; expected one of {sorted(KNOWN_DRIVERS)}",
            file=sys.stderr,
        )
        return 2

    if args.resume:
        out = Path(args.resume)
    elif args.out:
        out = Path(args.out)
    else:
        out = DEFAULT_OUT_DIR / f"{uuid.uuid4().hex}.jsonl"

    return asyncio.run(
        run_live(
            tasks,
            model_alias=args.model,
            reps=args.reps,
            surface=surface,
            out_path=out,
            driver_name=driver_name,
            server_cmd=server_cmd,
            server_env=server_env or None,
            resume=bool(args.resume),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
