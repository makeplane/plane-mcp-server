"""Claude Code CLI driver and transcript/JSON parsers."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from evals.drivers.base import (
    REPO_ROOT,
    AgentRun,
    normalize_tool_call,
    split_plane_and_client_calls,
)
from evals.drivers.process import note_timeout_kill, run_cli_subprocess
from evals.drivers.sidecar import (
    apply_proxy_sidecar,
    ensure_proxy_pythonpath,
    harvest_proxy_after_cli_timeout,
    proxy_wrap_server_command,
)


def normalize_claude_usage(data: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Parse Claude print-mode usage into (raw_usage, usage_total).

    Real envelope (probed 2026-08-12, ``claude -p --output-format json``)::

        {
          "usage": {
            "input_tokens": 10,              # uncached NEW input only — NOT run total
            "cache_creation_input_tokens": 17459,
            "cache_read_input_tokens": 18464,
            "output_tokens": 143,
            "iterations": [...],
            ...
          },
          "modelUsage": {
            "<model-id>": {
              "inputTokens": 10,
              "outputTokens": 143,
              "cacheReadInputTokens": 18464,
              "cacheCreationInputTokens": 17459,
              "costUSD": 0.037,
              ...
            }
          },
          "total_cost_usd": 0.037
        }

    ``usage.input_tokens`` alone is misleading for multi-turn cached runs (live
    rows showed 8–10 while cache_read was 180k+). We keep the split fields and
    compute an inclusive total under ``usage_total``; callers must **not** copy
    bare ``input_tokens`` into ``cum_input_tokens``.
    """
    usage = data.get("usage")
    if usage is not None and not isinstance(usage, dict):
        usage = None
    model_usage = data.get("modelUsage") or data.get("model_usage")
    if model_usage is not None and not isinstance(model_usage, dict):
        model_usage = None
    cost = data.get("total_cost_usd")
    if cost is None and isinstance(usage, dict):
        cost = usage.get("total_cost_usd")

    if usage is None and model_usage is None and cost is None:
        return None, None

    raw = dict(usage or {})
    if cost is not None:
        raw["total_cost_usd"] = cost
    if model_usage is not None:
        raw["modelUsage"] = model_usage

    # Prefer summing modelUsage (per-model run totals) when present
    sum_in = sum_out = sum_cr = sum_cc = sum_cost = 0.0
    used_model_usage = False
    if model_usage:
        for _mid, mu in model_usage.items():
            if not isinstance(mu, dict):
                continue
            used_model_usage = True
            sum_in += float(mu.get("inputTokens") or mu.get("input_tokens") or 0)
            sum_out += float(mu.get("outputTokens") or mu.get("output_tokens") or 0)
            sum_cr += float(mu.get("cacheReadInputTokens") or mu.get("cache_read_input_tokens") or 0)
            sum_cc += float(mu.get("cacheCreationInputTokens") or mu.get("cache_creation_input_tokens") or 0)
            sum_cost += float(mu.get("costUSD") or mu.get("cost_usd") or 0)

    if used_model_usage:
        uncached_in = int(sum_in)
        out_tok = int(sum_out)
        cache_read = int(sum_cr)
        cache_write = int(sum_cc)
        total_cost = float(sum_cost) if sum_cost else cost
    else:
        uncached_in = int(raw.get("input_tokens") or 0)
        out_tok = int(raw.get("output_tokens") or 0)
        cache_read = int(raw.get("cache_read_input_tokens") or 0)
        cache_write = int(raw.get("cache_creation_input_tokens") or 0)
        total_cost = cost

    usage_total: dict[str, Any] = {
        "input_tokens": uncached_in,  # uncached / new tokens only
        "output_tokens": out_tok,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
        "total_input_tokens_including_cache": uncached_in + cache_read + cache_write,
        "total_cost_usd": total_cost,
        "modelUsage": model_usage,
        "source": "modelUsage" if used_model_usage else "usage",
    }
    return raw, usage_total


def _claude_project_dir(cwd: Path) -> Path:
    """Map a cwd to ``~/.claude/projects/<munged>`` (``/`` → ``-``)."""
    munged = str(cwd.resolve()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / munged


def parse_claude_json_result(payload: dict[str, Any] | str) -> dict[str, Any]:
    """Extract final text, usage, session id, num_turns from ``claude -p --output-format json``.

    The print-mode JSON envelope is a single object (``type=result``) with
    ``result``, ``session_id``, ``num_turns``, ``total_cost_usd``, ``usage``,
    and ``modelUsage``. Per-call tool detail is usually **absent** — callers
    should fall back to the session transcript.
    """
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object from claude, got {type(payload)}")

    data = payload

    final = data.get("result")
    if final is None:
        final = data.get("final_text") or data.get("text") or ""
    if not isinstance(final, str):
        final = json.dumps(final, default=str)

    usage, usage_total = normalize_claude_usage(data)

    session_id = data.get("session_id") or data.get("sessionId")
    num_turns = data.get("num_turns")
    if num_turns is None:
        num_turns = data.get("numTurns")
    is_error = bool(data.get("is_error") or data.get("isError"))
    subtype = data.get("subtype") or ""
    stop_reason = data.get("stop_reason") or data.get("terminal_reason") or ""

    # Tool calls rarely present in the result envelope; collect if present.
    calls: list[dict[str, Any]] = []
    for key in ("tool_calls", "tools", "calls"):
        raw = data.get(key)
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("tool") or ""
                args = item.get("input") or item.get("arguments") or item.get("args") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"_raw": args}
                calls.append(normalize_tool_call(str(name), args))

    # Preserve Claude error subtypes (e.g. error_during_execution, error_max_turns).
    # is_error alone collapses to "error" and loses the subtype run.py uses for infra_cli.
    if is_error and subtype and str(subtype) not in ("success", ""):
        stopped = str(subtype)
    elif is_error:
        stopped = "error"
    else:
        stopped = str(stop_reason) if stop_reason else "end_turn"
        if subtype and subtype not in ("success", "") and stopped == "end_turn":
            stopped = str(subtype)

    plane_calls, client_calls = split_plane_and_client_calls(calls)

    return {
        "final_text": final,
        "usage": usage,
        "usage_total": usage_total,
        "session_id": session_id,
        "num_turns": int(num_turns) if num_turns is not None else None,
        "calls": plane_calls,
        "client_tool_calls": client_calls,
        "stopped_reason": stopped,
        "raw": data,
    }


def parse_claude_transcript_calls(transcript_path: Path) -> list[dict[str, Any]]:
    """Parse ``tool_use`` blocks from a Claude Code session JSONL transcript.

    Returns tagged calls (``origin`` plane|client). Use
    ``split_plane_and_client_calls`` before classification.
    """
    calls: list[dict[str, Any]] = []
    if not transcript_path.is_file():
        return calls
    with transcript_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = row.get("message") if isinstance(row, dict) else None
            if not isinstance(msg, dict):
                if row.get("type") == "assistant" and isinstance(row.get("content"), list):
                    content = row["content"]
                else:
                    continue
            else:
                content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue
                name = str(block.get("name") or "")
                args = block.get("input") or {}
                if not isinstance(args, dict):
                    args = {"_raw": args}
                calls.append(normalize_tool_call(name, args))
    return calls


def find_claude_transcript(session_id: str | None, cwd: Path) -> Path | None:
    """Locate ``~/.claude/projects/<munged-cwd>/<session_id>.jsonl``."""
    if not session_id:
        return None
    candidate = _claude_project_dir(cwd) / f"{session_id}.jsonl"
    if candidate.is_file():
        return candidate
    # Fallback: scan project dir for a file containing the session id
    proj = _claude_project_dir(cwd)
    if not proj.is_dir():
        return None
    direct = proj / f"{session_id}.jsonl"
    if direct.is_file():
        return direct
    for p in proj.glob("*.jsonl"):
        if session_id in p.name:
            return p
    return None


def write_claude_mcp_config(
    path: Path,
    *,
    command: str,
    args: list[str],
    env: dict[str, str],
    server_name: str = "plane",
) -> None:
    """Write a Claude-compatible mcp-config JSON file."""
    cfg = {
        "mcpServers": {
            server_name: {
                "command": command,
                "args": args,
                "env": env,
            }
        }
    }
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Claude CLI driver
# ---------------------------------------------------------------------------


class ClaudeCliDriver:
    """Run tasks via ``claude -p`` on the user's Claude Code subscription."""

    name = "claude-cli"

    def __init__(
        self,
        *,
        claude_bin: str = "claude",
        python_bin: str | None = None,
        permission_mode: str = "bypassPermissions",
        strict_mcp: bool = True,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        server_command: list[str] | None = None,
        use_proxy: bool = True,
        record_result_payloads: bool = False,
    ) -> None:
        self.claude_bin = claude_bin
        self.python_bin = python_bin or sys.executable
        self.permission_mode = permission_mode
        self.strict_mcp = strict_mcp
        self._runner = runner or run_cli_subprocess
        # Full replacement for the MCP server launch (external surfaces under
        # benchmark): [command, *args]. None → this repo's `-m plane_mcp stdio`.
        self.server_command = list(server_command) if server_command else None
        self.use_proxy = use_proxy
        self.record_result_payloads = record_result_payloads

    def run_task(
        self,
        prompt: str,
        mcp_env: dict[str, str],
        model: str | None,
        max_turns: int,
        *,
        system: str | None = None,
        cwd: Path | None = None,
    ) -> AgentRun:
        cwd = (cwd or REPO_ROOT).resolve()
        notes: list[str] = []
        t0 = time.perf_counter()

        with tempfile.TemporaryDirectory(prefix="plane-eval-claude-") as td:
            td_path = Path(td)
            mcp_cfg = td_path / "mcp.json"
            sidecar = td_path / "proxy-sidecar.jsonl"
            # Only pass Plane-related env into the MCP child (plus PATH/HOME if present).
            child_env = {k: v for k, v in mcp_env.items() if k.startswith("PLANE_") or k in ("PATH", "HOME")}
            if self.server_command:
                real_cmd = list(self.server_command)
            else:
                real_cmd = [self.python_bin, "-m", "plane_mcp", "stdio"]
            if self.use_proxy:
                wrapped = proxy_wrap_server_command(
                    real_cmd,
                    sidecar_path=sidecar,
                    python_bin=self.python_bin,
                    record_result_payloads=self.record_result_payloads,
                )
                server_cmd, server_args = wrapped[0], wrapped[1:]
                child_env = ensure_proxy_pythonpath(child_env)
            else:
                server_cmd, server_args = real_cmd[0], real_cmd[1:]
            write_claude_mcp_config(
                mcp_cfg,
                command=server_cmd,
                args=server_args,
                env=child_env,
                server_name="plane",
            )

            cmd: list[str] = [
                self.claude_bin,
                "-p",
                "--output-format",
                "json",
                "--mcp-config",
                str(mcp_cfg),
                "--permission-mode",
                self.permission_mode,
                "--max-turns",
                str(max_turns),
            ]
            if self.strict_mcp:
                cmd.append("--strict-mcp-config")
            if model:
                cmd.extend(["--model", model])
            if system:
                cmd.extend(["--append-system-prompt", system])
            # Allow MCP tools from our server without interactive prompts
            # --allowedTools is variadic and would swallow the trailing prompt; use = form.
            cmd.append("--allowedTools=mcp__plane__*")
            cmd.append(prompt)

            timeout_s = max(120, max_turns * 60)
            try:
                proc = self._runner(
                    cmd,
                    cwd=str(cwd),
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                wall = time.perf_counter() - t0
                notes.append(f"timeout after {timeout_s}s")
                note_timeout_kill(notes, exc)
                # Wait for proxy finalization before harvesting / temp dir teardown.
                calls: list[dict[str, Any]] = []
                client_calls: list[dict[str, Any]] = []
                call_source = "json"
                if self.use_proxy:
                    calls, client_calls, call_source = harvest_proxy_after_cli_timeout(
                        calls, client_calls, sidecar, notes
                    )
                return AgentRun(
                    calls=calls,
                    client_tool_calls=client_calls,
                    final_text="",
                    usage=None,
                    stopped_reason="timeout",
                    raw_ref=None,
                    usage_scope="run",
                    call_source=call_source,
                    hit_max_turns=False,
                    wall_time_s=round(wall, 3),
                    notes=notes,
                )

            wall = time.perf_counter() - t0
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""

            parsed: dict[str, Any] | None = None
            parse_err: str | None = None
            # JSON may be the whole stdout or the last JSON object line
            for candidate in (stdout.strip(), *(reversed(stdout.strip().splitlines()) if stdout else [])):
                if not candidate or not candidate.lstrip().startswith("{"):
                    continue
                try:
                    parsed = parse_claude_json_result(candidate)
                    break
                except (json.JSONDecodeError, ValueError, TypeError) as exc:
                    parse_err = str(exc)
                    continue

            if parsed is None:
                notes.append(f"json_parse_failed: {parse_err or 'no JSON object in stdout'}")
                if proc.returncode != 0:
                    notes.append(f"claude_exit={proc.returncode}")
                    if stderr.strip():
                        notes.append(stderr.strip()[:500])
                if self.use_proxy:
                    apply_proxy_sidecar([], [], sidecar, notes)
                detail = "; ".join(notes)
                raise RuntimeError(f"claude cli failed: {detail}")

            # Parseable JSON can still be a hard CLI failure (exit 1 + is_error subtype).
            if proc.returncode != 0:
                notes.append(f"claude_exit={proc.returncode}")
                if stderr.strip():
                    notes.append(stderr.strip()[:500])

            # JSON rarely embeds per-call tool detail — prefer transcript when present.
            calls = list(parsed.get("calls") or [])
            client_calls = list(parsed.get("client_tool_calls") or [])
            call_source = "json" if (calls or client_calls) else "json"
            session_id = parsed.get("session_id")
            transcript = find_claude_transcript(session_id, cwd)
            if transcript is not None:
                tagged = parse_claude_transcript_calls(transcript)
                t_plane, t_client = split_plane_and_client_calls(tagged)
                if t_plane or t_client:
                    calls, client_calls = t_plane, t_client
                    call_source = "transcript"
                    notes.append(f"calls_from_transcript:{transcript}")
            if not calls and not client_calls:
                notes.append("no_tool_calls_in_json_or_transcript")

            # Proxy sidecar (when enabled) replaces CLI-parsed plane calls.
            if self.use_proxy:
                calls, client_calls, proxy_src = apply_proxy_sidecar(calls, client_calls, sidecar, notes)
                if proxy_src == "proxy":
                    call_source = "proxy"

            num_turns = parsed.get("num_turns")
            hit_max = bool(num_turns is not None and int(num_turns) >= max_turns)
            stopped = parsed["stopped_reason"]
            if hit_max and stopped in ("end_turn", "completed", ""):
                stopped = "max_turns"

            raw_ref = str(transcript) if transcript else (f"session:{session_id}" if session_id else None)
            return AgentRun(
                calls=calls,
                client_tool_calls=client_calls,
                final_text=parsed["final_text"],
                usage=parsed.get("usage"),
                usage_total=parsed.get("usage_total"),
                stopped_reason=stopped,
                raw_ref=raw_ref,
                usage_scope="run",
                call_source=call_source,
                hit_max_turns=hit_max,
                wall_time_s=round(wall, 3),
                notes=notes,
            )


__all__ = [
    "ClaudeCliDriver",
    "find_claude_transcript",
    "normalize_claude_usage",
    "parse_claude_json_result",
    "parse_claude_transcript_calls",
    "write_claude_mcp_config",
]
