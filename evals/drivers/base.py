"""Shared types and tool-name helpers for eval agent drivers."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from evals.results import CallRecord, TaskResult, Usage
from evals.token_counting import (
    TOKEN_ESTIMATE_METHOD,
    count_result_text_tokens,
    estimate_result_tokens,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# mcp__plane__list_work_items  → list_work_items
# mcp__plane-mcp-server__foo  → foo
_MCP_PREFIX_RE = re.compile(r"^mcp__[^_]+(?:_[^_]+)*__(.+)$")
# Alternate: mcp__server__tool with multi-segment server names
_MCP_PREFIX_RE2 = re.compile(r"^mcp__.+?__(.+)$")


@dataclass
class AgentRun:
    """Normalized result of one agent task execution."""

    # Plane MCP tools only for classification: {tool, args, origin='plane', raw_tool?}
    calls: list[dict[str, Any]]
    final_text: str
    usage: Usage | dict[str, Any] | None
    stopped_reason: str
    raw_ref: str | None = None
    # Client/harness built-ins (ToolSearch, Bash, …) — excluded from mispick metrics
    client_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # Cache-aware run totals (CLI); do not put uncached-only input_tokens into cum_input_tokens
    usage_total: dict[str, Any] | None = None
    # Harness extras (optional; defaults keep CLI paths simple)
    usage_scope: str = "run"  # 'run' | 'iteration'
    call_source: str = "unknown"  # 'json' | 'transcript' | 'stream' | 'api'
    hit_max_turns: bool = False
    wall_time_s: float = 0.0
    experimental: bool = False
    notes: list[str] = field(default_factory=list)
    usage_per_iteration: list[Usage] = field(default_factory=list)
    cum_input_tokens: int | None = None
    result_pair_mismatch: bool = False
    token_count_failures: int = 0
    # False means a tokenizer/backend counter was used for every result; True
    # means at least one result used the shared character estimate. None lets
    # the common row mapper determine the status from the recorded calls.
    result_tokens_estimated: bool | None = None
    provider: str | None = None
    model: str | None = None
    requested_model: str | None = None
    # Raw provider finish/stop value. API drivers keep this beside the
    # harness-owned normalized ``stopped_reason`` for diagnostics.
    provider_stop_reason: str | None = None


class AgentDriver(Protocol):
    """Pluggable agent backend for evals.run."""

    name: str

    def run_task(
        self,
        prompt: str,
        mcp_env: dict[str, str],
        model: str | None,
        max_turns: int,
        *,
        system: str | None = None,
        cwd: Path | None = None,
    ) -> AgentRun: ...


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def strip_mcp_prefix(name: str) -> str:
    """Strip Claude/Codex MCP tool name prefixes for classification.

    Examples:
      mcp__plane__list_work_items → list_work_items
      mcp__plane-mcp-server__find_work_items → find_work_items
    """
    if not name:
        return name
    m = _MCP_PREFIX_RE2.match(name)
    if m:
        return m.group(1)
    return name


def is_plane_mcp_tool(name: str) -> bool:
    """True when the raw tool name is from our Plane MCP server (pre-strip).

    Claude surfaces MCP tools as ``mcp__<server>__<tool>``. Our config registers
    the server as ``plane``, so names look like ``mcp__plane__find_work_items``.
    Built-ins (``ToolSearch``, ``Bash``, …) have no ``mcp__`` prefix.
    """
    if not name:
        return False
    # mcp__plane__tool  or  mcp__plane-foo__tool
    return name.startswith("mcp__plane__") or name.startswith("mcp__plane-")


def normalize_tool_call(name: str, args: Any) -> dict[str, Any]:
    """Tag a tool call as plane (classifiable) or client (excluded from mispicks)."""
    raw = str(name or "")
    if not isinstance(args, dict):
        args = {"_raw": args}
    if is_plane_mcp_tool(raw):
        return {
            "tool": strip_mcp_prefix(raw),
            "args": args,
            "origin": "plane",
            "raw_tool": raw,
        }
    return {
        "tool": raw,  # keep built-in name as-is (ToolSearch, Bash, …)
        "args": args,
        "origin": "client",
        "raw_tool": raw,
    }


def split_plane_and_client_calls(
    calls: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition tagged calls into plane vs client lists.

    Prefer explicit ``origin`` from ``normalize_tool_call``. Untagged calls
    (API path) default to plane so existing harness behavior is unchanged.
    """
    plane: list[dict[str, Any]] = []
    client: list[dict[str, Any]] = []
    for c in calls:
        origin = c.get("origin")
        if origin is None:
            raw = str(c.get("raw_tool") or c.get("tool") or "")
            if is_plane_mcp_tool(raw):
                origin = "plane"
            elif raw.startswith("mcp__"):
                origin = "client"  # other MCP server
            else:
                origin = "plane"  # bare name → assume plane (API)
        if origin == "client":
            client.append(c)
        else:
            plane.append(c)
    return plane, client


def agent_run_to_task_result(
    run: AgentRun,
    *,
    optimal: set[str],
    alternate: set[str],
    classify: Callable[[str, set[str], set[str]], str],
) -> TaskResult:
    """Map an ``AgentRun`` onto the typed driver-owned portion of a task result.

    Only **plane** MCP tools are classified and counted in ``num_calls`` /
    mispick metrics. Client built-ins (``ToolSearch``, …) go to
    ``client_tool_calls`` and are excluded.

    CLI drivers never populate ``cum_input_tokens`` from bare
    ``usage.input_tokens`` (that field is uncached-only under Claude Code and
    misreads multi-turn cached runs as ~10 tokens). Use ``usage_total`` instead.
    """
    # Re-split in case callers passed a mixed list
    plane_src, client_extra = split_plane_and_client_calls(list(run.calls))
    client_src = list(run.client_tool_calls) + client_extra

    is_cli = run.call_source in ("json", "transcript", "stream", "proxy") or run.usage_scope == "run"
    calls: list[CallRecord] = []
    local_token_count_failures = 0
    for c in plane_src:
        tool = c.get("tool") or ""
        args = c.get("args") or {}
        try:
            args_chars = len(json.dumps(args, default=str))
        except Exception:
            args_chars = len(str(args))
        result_chars = int(c["result_chars"]) if c.get("result_chars") is not None else 0
        result_tokens = c.get("result_tokens")
        estimated = c.get("result_tokens_estimated")
        count_method = c.get("result_token_count_method")
        if result_tokens is not None:
            result_tokens = int(result_tokens)
            if estimated is None:
                estimated = bool(run.result_tokens_estimated)
            if count_method is None:
                count_method = TOKEN_ESTIMATE_METHOD if estimated else "backend"
        elif isinstance(c.get("result_text"), str):
            count = count_result_text_tokens(c["result_text"])
            result_tokens = count.value
            estimated = count.estimated
            count_method = count.method
            local_token_count_failures += int(count.tokenizer_failed)
        else:
            result_tokens = estimate_result_tokens(result_chars)
            estimated = True
            count_method = TOKEN_ESTIMATE_METHOD

        rec = CallRecord(
            tool=str(tool),
            classification=classify(str(tool), optimal, alternate),
            args_chars=args_chars,
            result_tokens=result_tokens,
            result_chars=result_chars,
            result_kind=str(c.get("result_kind") or "text"),
            is_error=bool(c.get("is_error")),
            result_tokens_estimated=bool(estimated),
            result_token_count_method=str(count_method),
            duration_ms=c.get("duration_ms"),
        )
        # Action-dispatch surfaces: the action arg IS the second half of the
        # tool choice — keep it (args content is otherwise not persisted).
        if isinstance(args, dict) and isinstance(args.get("action"), str):
            rec.action = args["action"]
        calls.append(rec)

    client_tool_calls: list[CallRecord] = []
    for c in client_src:
        tool = c.get("tool") or c.get("raw_tool") or ""
        args = c.get("args") or {}
        try:
            args_chars = len(json.dumps(args, default=str))
        except Exception:
            args_chars = len(str(args))
        client_tool_calls.append(
            CallRecord(
                tool=str(tool),
                args_chars=args_chars,
                raw_tool=str(c.get("raw_tool") or tool),
            )
        )

    stop_reason = run.stopped_reason
    hit_max = run.hit_max_turns
    if hit_max:
        stop_reason = stop_reason if stop_reason not in ("end_turn", "completed", None, "") else "max_turns"

    errored = sum(1 for c in calls if c.is_error)
    alternate_n = sum(1 for c in calls if c.classification == "alternate")
    out_of_set_n = sum(1 for c in calls if c.classification == "out_of_set")

    # CLI path: never write misleading cum_input_tokens from uncached-only field.
    # usage_total is driver-owned — do not re-derive it here (Claude vs Codex
    # shapes differ; a generic Claude rebuild mislabels other vendors).
    usage_total = run.usage_total

    if run.usage_per_iteration:
        usage_per_iteration = list(run.usage_per_iteration)
        cum_input = (
            run.cum_input_tokens
            if run.cum_input_tokens is not None
            else sum(item.input_tokens for item in usage_per_iteration)
        )
        cum_reason = None
    elif is_cli:
        cum_input: int | None = None
        cum_reason: str | None = (
            "CLI driver: Claude usage.input_tokens is uncached-only; "
            "see usage_total (cache_read/cache_creation/output/cost) for run accounting"
        )
        usage_per_iteration: list[Usage] = []
    else:
        cum_input = 0
        cum_reason = None
        usage_per_iteration = []
        if run.usage and run.usage_scope == "iteration":
            pass

    estimated_states = [bool(c.result_tokens_estimated) for c in calls]
    if estimated_states:
        result_tokens_estimated = any(estimated_states)
        result_tokens_mode = (
            "estimated" if all(estimated_states) else "measured" if not any(estimated_states) else "mixed"
        )
    else:
        result_tokens_estimated = (
            bool(run.result_tokens_estimated) if run.result_tokens_estimated is not None else is_cli
        )
        result_tokens_mode = "estimated" if result_tokens_estimated else "measured"

    count_methods = {str(c.result_token_count_method) for c in calls}
    if not count_methods:
        result_token_count_method = "none"
    elif len(count_methods) == 1:
        result_token_count_method = next(iter(count_methods))
    else:
        result_token_count_method = "mixed"
    return TaskResult(
        final_text=run.final_text,
        calls=calls,
        num_calls=len(calls),
        client_tool_calls=client_tool_calls,
        client_tool_call_count=len(client_tool_calls),
        errored_calls=errored,
        alternate_calls=alternate_n,
        out_of_set_calls=out_of_set_n,
        total_result_tokens=sum(int(c.result_tokens or 0) for c in calls),
        usage_per_iteration=usage_per_iteration,
        cum_input_tokens=cum_input,
        cum_input_tokens_reason=cum_reason,
        wall_time_s=run.wall_time_s,
        stop_reason=stop_reason,
        provider_stop_reason=run.provider_stop_reason,
        hit_max_iterations=hit_max,
        result_pair_mismatch=run.result_pair_mismatch,
        token_count_failures=run.token_count_failures + local_token_count_failures,
        result_tokens_estimated=result_tokens_estimated,
        result_tokens_mode=result_tokens_mode,
        result_token_count_method=result_token_count_method,
        usage_scope=run.usage_scope,
        call_source=run.call_source,
        driver_raw_ref=run.raw_ref,
        driver_notes=list(run.notes),
        usage=run.usage,
        usage_total=usage_total,
        provider=run.provider,
        model=run.model,
        requested_model=run.requested_model,
    )


def agent_run_to_harness_dict(
    run: AgentRun,
    *,
    optimal: set[str],
    alternate: set[str],
    classify: Callable[[str, set[str], set[str]], str],
) -> dict[str, Any]:
    """Compatibility wrapper returning the public persisted-row dictionary."""
    return agent_run_to_task_result(
        run,
        optimal=optimal,
        alternate=alternate,
        classify=classify,
    ).to_row()


__all__ = [
    "REPO_ROOT",
    "AgentRun",
    "AgentDriver",
    "agent_run_to_harness_dict",
    "agent_run_to_task_result",
    "is_plane_mcp_tool",
    "normalize_tool_call",
    "split_plane_and_client_calls",
    "strip_mcp_prefix",
]
