"""Claude Code CLI driver and transcript/JSON parsers.

Probed (claude v2.1.228): -p headless; --mcp-config (repeatable) + --strict-mcp-config;
--output-format json|text|stream-json; --max-turns (present but hidden from --help);
--model; --permission-mode; transcript at
~/.claude/projects/<cwd-with-/-as-->/<session_id>.jsonl, assistant rows carrying
tool_use blocks; MCP tools appear as mcp__<server>__<tool>.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from evals.drivers.driver import CliDriver, CliLaunch, CliOutput, CliOutputError
from evals.tool_names import normalize_tool_call, split_plane_and_client_calls


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


class ClaudeCliDriver(CliDriver):
    """Run tasks via ``claude -p`` on the user's Claude Code subscription."""

    name = "claude-cli"
    temp_dir_prefix = "plane-eval-claude-"

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
        self.permission_mode = permission_mode
        self.strict_mcp = strict_mcp
        # Full replacement for the MCP server launch (external surfaces under
        # benchmark): [command, *args]. None → this repo's `-m plane_mcp stdio`.
        super().__init__(
            python_bin=python_bin,
            runner=runner,
            server_command=server_command,
            use_proxy=use_proxy,
            record_result_payloads=record_result_payloads,
        )

    def write_mcp_config(
        self,
        temp_dir: Path,
        *,
        task_cwd: Path,
        server_command: list[str],
        child_env: dict[str, str],
    ) -> CliLaunch:
        mcp_cfg = temp_dir / "mcp.json"
        write_claude_mcp_config(
            mcp_cfg,
            command=server_command[0],
            args=server_command[1:],
            env=child_env,
            server_name="plane",
        )
        return CliLaunch(cwd=task_cwd, config_args=["--mcp-config", str(mcp_cfg)])

    def build_command(
        self,
        prompt: str,
        *,
        model: str | None,
        max_turns: int,
        system: str | None,
        launch: CliLaunch,
    ) -> list[str]:
        command = [
            self.claude_bin,
            "-p",
            "--output-format",
            "json",
            *launch.config_args,
            "--permission-mode",
            self.permission_mode,
            "--max-turns",
            str(max_turns),
        ]
        if self.strict_mcp:
            command.append("--strict-mcp-config")
        if model:
            command.extend(["--model", model])
        if system:
            command.extend(["--append-system-prompt", system])
        # --allowedTools is variadic and would swallow the trailing prompt.
        command.extend(["--allowedTools=mcp__plane__*", prompt])
        return command

    def parse_output(
        self,
        proc: subprocess.CompletedProcess[str],
        *,
        task_cwd: Path,
        max_turns: int,
        notes: list[str],
    ) -> CliOutput:
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        parsed: dict[str, Any] | None = None
        parse_err: str | None = None
        # JSON may be the whole stdout or the last JSON object line.
        for candidate in (stdout.strip(), *(reversed(stdout.strip().splitlines()) if stdout else [])):
            if not candidate or not candidate.lstrip().startswith("{"):
                continue
            try:
                parsed = parse_claude_json_result(candidate)
                break
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                parse_err = str(exc)

        if parsed is None:
            notes.append(f"json_parse_failed: {parse_err or 'no JSON object in stdout'}")
            if proc.returncode != 0:
                notes.append(f"claude_exit={proc.returncode}")
                if stderr.strip():
                    notes.append(stderr.strip()[:500])
            raise CliOutputError("claude cli failed")

        # Parseable JSON can still be a hard CLI failure (exit 1 + is_error subtype).
        if proc.returncode != 0:
            notes.append(f"claude_exit={proc.returncode}")
            if stderr.strip():
                notes.append(stderr.strip()[:500])

        calls = list(parsed.get("calls") or [])
        client_calls = list(parsed.get("client_tool_calls") or [])
        call_source = "json"
        session_id = parsed.get("session_id")
        transcript = find_claude_transcript(session_id, task_cwd)
        if transcript is not None:
            tagged = parse_claude_transcript_calls(transcript)
            transcript_plane, transcript_client = split_plane_and_client_calls(tagged)
            if transcript_plane or transcript_client:
                calls, client_calls = transcript_plane, transcript_client
                call_source = "transcript"
                notes.append(f"calls_from_transcript:{transcript}")
        if not calls and not client_calls:
            notes.append("no_tool_calls_in_json_or_transcript")

        num_turns = parsed.get("num_turns")
        hit_max = bool(num_turns is not None and int(num_turns) >= max_turns)
        stopped = parsed["stopped_reason"]
        if hit_max and stopped in ("end_turn", "completed", ""):
            stopped = "max_turns"

        raw_ref = str(transcript) if transcript else (f"session:{session_id}" if session_id else None)
        return CliOutput(
            calls=calls,
            final_text=parsed["final_text"],
            client_tool_calls=client_calls,
            usage=parsed.get("usage"),
            usage_total=parsed.get("usage_total"),
            stopped_reason=stopped,
            raw_ref=raw_ref,
            call_source=call_source,
            hit_max_turns=hit_max,
        )


__all__ = [
    "ClaudeCliDriver",
    "find_claude_transcript",
    "normalize_claude_usage",
    "parse_claude_json_result",
    "parse_claude_transcript_calls",
    "write_claude_mcp_config",
]
