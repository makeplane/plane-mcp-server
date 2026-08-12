"""Codex CLI driver and JSONL/rollout parsers."""

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


def _codex_parse_tool_args(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            return {"_raw": raw_args}
        return args if isinstance(args, dict) else {"_raw": args}
    if isinstance(raw_args, dict):
        return raw_args
    return {"_raw": raw_args}


def parse_codex_jsonl_events(lines: list[str] | str) -> dict[str, Any]:
    """Parse ``codex exec --json`` stdout (JSONL) for function_call + final text + usage.

    Supports both schemas:
    - **v0.147+ streamable**: ``thread.started`` / ``item.completed`` / ``turn.completed``
      (``thread_id`` matches rollout filename suffix).
    - **Legacy**: ``session_meta`` / ``response_item`` / ``event_msg`` payloads.
    New keys are tried first; legacy handling is retained.
    """
    if isinstance(lines, str):
        lines = lines.splitlines()
    calls: list[dict[str, Any]] = []
    final_parts: list[str] = []
    usage: dict[str, Any] | None = None
    stopped = "end_turn"
    session_id: str | None = None

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rtype = row.get("type")

        # --- New schema (codex exec --json v0.147+): try first ---
        if rtype == "thread.started":
            session_id = row.get("thread_id") or session_id
            continue
        if rtype == "turn.started":
            continue
        if rtype == "item.completed":
            item = row.get("item") if isinstance(row.get("item"), dict) else {}
            itype = item.get("type")
            if itype == "agent_message":
                text = item.get("text")
                if text:
                    final_parts.append(str(text))
            elif itype in (
                "function_call",
                "tool_call",
                "mcp_tool_call",
                "command_execution",
                "file_change",
            ):
                # Proxy sidecar is primary for plane calls; harvest best-effort names.
                name = str(item.get("name") or item.get("tool") or item.get("command") or itype)
                args = item.get("arguments") or item.get("args") or item.get("input") or {}
                calls.append(normalize_tool_call(name, _codex_parse_tool_args(args)))
            continue
        if rtype == "turn.completed":
            u = row.get("usage") if isinstance(row.get("usage"), dict) else {}
            if u:
                usage = {
                    "input_tokens": u.get("input_tokens", 0) or 0,
                    "output_tokens": u.get("output_tokens", 0) or 0,
                    "cache_read_input_tokens": u.get("cached_input_tokens", 0) or 0,
                    "cache_creation_input_tokens": u.get("cache_write_input_tokens", 0) or 0,
                    "total_tokens": u.get("total_tokens"),
                }
            continue

        # --- Legacy schema ---
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}

        if rtype == "session_meta":
            session_id = payload.get("id") or session_id
            continue

        if rtype == "response_item":
            pt = payload.get("type")
            if pt == "function_call":
                name = str(payload.get("name") or "")
                calls.append(normalize_tool_call(name, _codex_parse_tool_args(payload.get("arguments") or "{}")))
            elif pt == "message":
                # Assistant final-ish content
                role = payload.get("role")
                content = payload.get("content")
                if role == "assistant" and isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") in ("output_text", "text"):
                            t = c.get("text") or c.get("output_text")
                            if t:
                                final_parts.append(str(t))
            continue

        if rtype == "event_msg":
            pt = payload.get("type")
            if pt == "agent_message":
                msg = payload.get("message") or payload.get("text")
                if msg:
                    final_parts.append(str(msg))
            elif pt == "token_count":
                info = payload.get("info") or {}
                total = info.get("total_token_usage") or info.get("last_token_usage") or {}
                if isinstance(total, dict):
                    usage = {
                        "input_tokens": total.get("input_tokens", 0) or 0,
                        "output_tokens": total.get("output_tokens", 0) or 0,
                        "cache_read_input_tokens": total.get("cached_input_tokens", 0) or 0,
                        "cache_creation_input_tokens": total.get("cache_write_input_tokens", 0) or 0,
                        "total_tokens": total.get("total_tokens"),
                    }
            elif pt == "task_complete":
                stopped = "end_turn"
            elif pt == "turn_aborted":
                stopped = "aborted"
            continue

    plane_calls, client_calls = split_plane_and_client_calls(calls)
    return {
        "calls": plane_calls,
        "client_tool_calls": client_calls,
        "final_text": "\n".join(final_parts).strip(),
        "usage": usage,
        "stopped_reason": stopped,
        "session_id": session_id,
    }


def parse_codex_rollout_calls(rollout_path: Path) -> list[dict[str, Any]]:
    """Parse function_call records from a Codex session rollout JSONL (plane only)."""
    if not rollout_path.is_file():
        return []
    lines = rollout_path.read_text(encoding="utf-8").splitlines()
    return parse_codex_jsonl_events(lines)["calls"]


def find_codex_rollout(session_id: str | None, *, after_ts: float | None = None) -> Path | None:
    """Find a rollout JSONL under ``~/.codex/sessions`` matching *session_id* exactly.

    Matches filename containing the id (rollout filenames end with ``thread_id``)
    or a first-line ``session_meta`` / ``thread.started`` id field.

    **No newest-after-ts fallback**: under parallel runs that would pick another
    task's rollout and corrupt final_text. Callers should note
    ``codex_rollout_unmatched`` when this returns None.

    ``after_ts`` is accepted for API compatibility but ignored.
    """
    del after_ts  # intentionally unused — see docstring
    if not session_id:
        return None
    root = Path.home() / ".codex" / "sessions"
    if not root.is_dir():
        return None
    sid = str(session_id)
    for p in root.rglob("*.jsonl"):
        if sid in p.name:
            return p
    for p in root.rglob("rollout-*.jsonl"):
        try:
            with p.open(encoding="utf-8") as fh:
                first = fh.readline()
            row = json.loads(first)
        except Exception:
            continue
        # Legacy session_meta
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if payload.get("id") == sid:
            return p
        # New thread.started on first line (unusual but possible)
        if row.get("type") == "thread.started" and row.get("thread_id") == sid:
            return p
        if row.get("thread_id") == sid or row.get("session_id") == sid:
            return p
    return None


def write_codex_mcp_override_args(
    *,
    command: str,
    args: list[str],
    env: dict[str, str],
    server_name: str = "plane",
) -> list[str]:
    """Build ``codex exec -c ...`` overrides for a stdio MCP server.

    Codex stores MCP under ``[mcp_servers.<name>]`` with ``command``, ``args``,
    and ``env`` (see user config.toml). Overrides use dotted ``-c`` paths.
    """
    out: list[str] = [
        "-c",
        f"mcp_servers.{server_name}.command={json.dumps(command)}",
        "-c",
        f"mcp_servers.{server_name}.args={json.dumps(args)}",
    ]
    # env table — pass each key
    for k, v in env.items():
        out.extend(["-c", f"mcp_servers.{server_name}.env.{k}={json.dumps(v)}"])
    return out


# Codex CLI driver (experimental — do not spend live quota from CI)
# ---------------------------------------------------------------------------


class CodexCliDriver:
    """Run tasks via ``codex exec`` (experimental; metered quota).

    Live invocation is supported for the interface, but the eval harness should
    only exercise this driver when the team explicitly opts in. Offline tests
    inject a fake runner and never touch the real binary.
    """

    name = "codex-cli"
    experimental = True

    def __init__(
        self,
        *,
        codex_bin: str = "codex",
        python_bin: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        allow_live: bool = False,
        server_command: list[str] | None = None,
        use_proxy: bool = True,
    ) -> None:
        self.codex_bin = codex_bin
        self.python_bin = python_bin or sys.executable
        self._runner = runner or run_cli_subprocess
        self.allow_live = allow_live
        self.server_command = list(server_command) if server_command else None
        self.use_proxy = use_proxy

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
        notes = ["experimental:codex-cli"]
        if self._runner is run_cli_subprocess and not self.allow_live:
            raise RuntimeError(
                "CodexCliDriver refuses live runs by default (metered weekly quota). "
                "Pass allow_live=True or inject a fake runner for tests."
            )

        child_env = {k: v for k, v in mcp_env.items() if k.startswith("PLANE_") or k in ("PATH", "HOME")}
        with tempfile.TemporaryDirectory(prefix="plane-eval-codex-") as td:
            td_path = Path(td)
            sidecar = td_path / "proxy-sidecar.jsonl"
            if self.server_command:
                real_cmd = list(self.server_command)
            else:
                real_cmd = [self.python_bin, "-m", "plane_mcp", "stdio"]
            if self.use_proxy:
                wrapped = proxy_wrap_server_command(real_cmd, sidecar_path=sidecar, python_bin=self.python_bin)
                server_cmd, server_args = wrapped[0], wrapped[1:]
                child_env = ensure_proxy_pythonpath(child_env)
            else:
                server_cmd, server_args = real_cmd[0], real_cmd[1:]
            mcp_args = write_codex_mcp_override_args(
                command=server_cmd,
                args=server_args,
                env=child_env,
                server_name="plane",
            )
            cmd: list[str] = [
                self.codex_bin,
                "exec",
                "--json",
                "--skip-git-repo-check",
                *mcp_args,
            ]
            if model:
                cmd.extend(["-m", model])
            full_prompt = prompt if not system else f"{system}\n\n{prompt}"
            cmd.append(full_prompt)

            t0 = time.perf_counter()
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
                calls_to: list[dict[str, Any]] = []
                client_to: list[dict[str, Any]] = []
                call_source = "stream"
                if self.use_proxy:
                    calls_to, client_to, call_source = harvest_proxy_after_cli_timeout(
                        calls_to, client_to, sidecar, notes
                    )
                return AgentRun(
                    calls=calls_to,
                    client_tool_calls=client_to,
                    final_text="",
                    usage=None,
                    stopped_reason="timeout",
                    raw_ref=None,
                    usage_scope="run",
                    call_source=call_source,
                    hit_max_turns=False,
                    wall_time_s=round(wall, 3),
                    experimental=True,
                    notes=notes,
                )
            wall = time.perf_counter() - t0
            stdout = proc.stdout or ""
            parsed = parse_codex_jsonl_events(stdout)
            calls = list(parsed.get("calls") or [])
            client_calls = list(parsed.get("client_tool_calls") or [])
            call_source = "stream"
            session_id = parsed.get("session_id")

            # Exact-match rollout only — never steal another parallel task's file.
            need_rollout = (not calls and not client_calls) or not parsed.get("final_text")
            if need_rollout and session_id:
                rollout = find_codex_rollout(session_id)
                if rollout is not None:
                    full = parse_codex_jsonl_events(rollout.read_text(encoding="utf-8").splitlines())
                    if not calls and not client_calls:
                        calls = list(full.get("calls") or [])
                        client_calls = list(full.get("client_tool_calls") or [])
                        if calls or client_calls:
                            call_source = "transcript"
                            notes.append(f"calls_from_rollout:{rollout}")
                    if full.get("final_text") and not parsed.get("final_text"):
                        parsed["final_text"] = full["final_text"]
                        notes.append(f"final_text_from_rollout:{rollout}")
                    if full.get("usage") and not parsed.get("usage"):
                        parsed["usage"] = full["usage"]
                else:
                    notes.append("codex_rollout_unmatched")
            elif need_rollout and not session_id:
                notes.append("codex_rollout_unmatched")

            if self.use_proxy:
                calls, client_calls, proxy_src = apply_proxy_sidecar(calls, client_calls, sidecar, notes)
                if proxy_src == "proxy":
                    call_source = "proxy"

            if proc.returncode != 0:
                notes.append(f"codex_exit={proc.returncode}")

            usage = parsed.get("usage")
            usage_total = None
            if isinstance(usage, dict):
                usage_total = {
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                    "total_input_tokens_including_cache": (
                        int(usage.get("input_tokens") or 0)
                        + int(usage.get("cache_read_input_tokens") or 0)
                        + int(usage.get("cache_creation_input_tokens") or 0)
                    ),
                    "source": "codex_token_count",
                }

            raw_ref = f"session:{session_id}" if session_id else None
            return AgentRun(
                calls=calls,
                client_tool_calls=client_calls,
                final_text=parsed.get("final_text") or "",
                usage=usage,
                usage_total=usage_total,
                stopped_reason=parsed.get("stopped_reason") or "end_turn",
                raw_ref=raw_ref,
                usage_scope="run",
                call_source=call_source,
                hit_max_turns=False,  # codex exec has no max-turns flag in --help
                wall_time_s=round(wall, 3),
                experimental=True,
                notes=notes,
            )


__all__ = [
    "CodexCliDriver",
    "find_codex_rollout",
    "parse_codex_jsonl_events",
    "parse_codex_rollout_calls",
    "write_codex_mcp_override_args",
]
