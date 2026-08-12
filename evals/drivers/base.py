"""Shared types and tool-name helpers for eval agent drivers."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

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
    usage: dict[str, Any] | None
    stopped_reason: str
    raw_ref: str | None = None
    # Client/harness built-ins (ToolSearch, Bash, …) — excluded from mispick metrics
    client_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # Cache-aware run totals (CLI); do not put uncached-only input_tokens into cum_input_tokens
    usage_total: dict[str, Any] | None = None
    # Harness extras (optional; defaults keep SDK path simple)
    usage_scope: str = "run"  # 'run' | 'iteration'
    call_source: str = "unknown"  # 'json' | 'transcript' | 'stream' | 'sdk'
    hit_max_turns: bool = False
    wall_time_s: float = 0.0
    experimental: bool = False
    notes: list[str] = field(default_factory=list)


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
    (SDK path) default to plane so existing harness behavior is unchanged.
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
                origin = "plane"  # bare name → assume plane (SDK)
        if origin == "client":
            client.append(c)
        else:
            plane.append(c)
    return plane, client


def agent_run_to_harness_dict(
    run: AgentRun,
    *,
    optimal: set[str],
    alternate: set[str],
    classify: Callable[[str, set[str], set[str]], str],
    skip_result_tokens: bool = True,
) -> dict[str, Any]:
    """Map an ``AgentRun`` onto the dict shape expected by ``run_live`` rows.

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

    calls: list[dict[str, Any]] = []
    for c in plane_src:
        tool = c.get("tool") or ""
        args = c.get("args") or {}
        try:
            args_chars = len(json.dumps(args, default=str))
        except Exception:
            args_chars = len(str(args))
        rec: dict[str, Any] = {
            "tool": tool,
            "class": classify(str(tool), optimal, alternate),
            "args_chars": args_chars,
            "result_tokens": None,
            "result_chars": int(c["result_chars"]) if c.get("result_chars") is not None else 0,
            "result_kind": "text",
            "is_error": bool(c.get("is_error")),
        }
        if c.get("duration_ms") is not None:
            rec["duration_ms"] = c["duration_ms"]
        # Action-dispatch surfaces: the action arg IS the second half of the
        # tool choice — keep it (args content is otherwise not persisted).
        if isinstance(args, dict) and isinstance(args.get("action"), str):
            rec["action"] = args["action"]
        if skip_result_tokens:
            rec["result_tokens_skipped"] = "no API key / CLI driver has no count_tokens"
        calls.append(rec)

    client_tool_calls: list[dict[str, Any]] = []
    for c in client_src:
        tool = c.get("tool") or c.get("raw_tool") or ""
        args = c.get("args") or {}
        try:
            args_chars = len(json.dumps(args, default=str))
        except Exception:
            args_chars = len(str(args))
        client_tool_calls.append(
            {
                "tool": tool,
                "args_chars": args_chars,
                "raw_tool": c.get("raw_tool") or tool,
            }
        )

    stop_reason = run.stopped_reason
    hit_max = run.hit_max_turns
    if hit_max:
        stop_reason = stop_reason if stop_reason not in ("end_turn", "completed", None, "") else "max_turns"

    errored = sum(1 for c in calls if c.get("is_error"))
    alternate_n = sum(1 for c in calls if c["class"] == "alternate")
    out_of_set_n = sum(1 for c in calls if c["class"] == "out_of_set")

    # CLI path: never write misleading cum_input_tokens from uncached-only field.
    # usage_total is driver-owned — do not re-derive it here (Claude vs Codex
    # shapes differ; a generic Claude rebuild mislabels other vendors).
    is_cli = run.call_source in ("json", "transcript", "stream", "proxy") or run.usage_scope == "run"
    usage_total = run.usage_total

    if is_cli and skip_result_tokens:
        cum_input: int | None = None
        cum_reason: str | None = (
            "CLI driver: Claude usage.input_tokens is uncached-only; "
            "see usage_total (cache_read/cache_creation/output/cost) for run accounting"
        )
        usage_per_iteration: list[dict[str, int]] = []
    else:
        cum_input = 0
        cum_reason = None
        usage_per_iteration = []
        if run.usage and run.usage_scope == "iteration":
            pass  # SDK fills this separately

    return {
        "final_text": run.final_text,
        "calls": calls,
        "num_calls": len(calls),
        "client_tool_calls": client_tool_calls,
        "client_tool_call_count": len(client_tool_calls),
        "errored_calls": errored,
        "alternate_calls": alternate_n,
        "out_of_set_calls": out_of_set_n,
        "total_result_tokens": 0
        if skip_result_tokens
        else sum(c["result_tokens"] or 0 for c in calls if c.get("result_tokens") is not None),
        "usage_per_iteration": usage_per_iteration,
        "cum_input_tokens": cum_input,
        "cum_input_tokens_reason": cum_reason,
        "wall_time_s": run.wall_time_s,
        "stop_reason": stop_reason,
        "hit_max_iterations": hit_max,
        "result_pair_mismatch": False,
        "token_count_failures": 0,
        "usage_scope": run.usage_scope,
        "call_source": run.call_source,
        "driver_raw_ref": run.raw_ref,
        "driver_notes": list(run.notes),
        "result_tokens_skipped_reason": (
            "CLI driver: count_tokens requires Anthropic API key; skipped" if skip_result_tokens else None
        ),
        "usage": run.usage,
        "usage_total": usage_total,
    }


__all__ = [
    "REPO_ROOT",
    "AgentRun",
    "AgentDriver",
    "agent_run_to_harness_dict",
    "is_plane_mcp_tool",
    "normalize_tool_call",
    "split_plane_and_client_calls",
    "strip_mcp_prefix",
]
