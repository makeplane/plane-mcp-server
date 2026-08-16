"""Codex CLI driver and JSONL/rollout parsers.

Probed: codex exec --json emits JSONL on stdout; -c key=value overrides config.toml
(including mcp_servers); -m selects the model; rollouts at
~/.codex/sessions/**/rollout-*.jsonl carry response_item/function_call payloads.
Experimental — live runs are opt-in because the quota is metered.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from evals.drivers.cli.process import run_cli_subprocess
from evals.drivers.driver import CliDriver, CliLaunch, CliOutput
from evals.tool_names import normalize_tool_call, split_plane_and_client_calls


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
    """Find the rollout JSONL under ~/.codex/sessions matching *session_id* exactly.

    Matches the filename (which ends with thread_id) or a first-line session_meta id.
    Deliberately no newest-after-ts fallback: under parallel runs that picks another
    task's rollout and corrupts final_text. Callers note codex_rollout_unmatched on None.
    ``after_ts`` is accepted for API compatibility and ignored.
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


def write_codex_mcp_config(
    path: Path,
    *,
    command: str,
    args: list[str],
    env: dict[str, str],
    server_name: str = "plane",
) -> None:
    """Write the complete MCP config for an isolated Codex home.

    ``approvals_reviewer`` is required, not cosmetic. ``codex exec`` is non-interactive, so an
    MCP call that raises an approval request has nobody to answer it and Codex cancels its own
    call with ``user cancelled MCP tool call``. The agent then answers from nothing: a live run
    recorded zero calls on every task while still emitting confident answers. Routing approvals
    through automatic review is what the developer config already does; an isolated home has to
    say so itself, because isolation is exactly what stops it being inherited.
    """
    lines = [
        'approvals_reviewer = "auto_review"',
        f"[mcp_servers.{json.dumps(server_name)}]",
        f"command = {json.dumps(command)}",
        f"args = {json.dumps(args)}",
    ]
    if env:
        lines.append(f"[mcp_servers.{json.dumps(server_name)}.env]")
        lines.extend(f"{json.dumps(key)} = {json.dumps(value)}" for key, value in env.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare_codex_home(
    codex_home: Path,
    *,
    command: str,
    args: list[str],
    env: dict[str, str],
    real_codex_home: Path | None = None,
) -> None:
    """Create an exclusive config root while copying only the CLI login artifact."""
    write_codex_mcp_config(
        codex_home / "config.toml",
        command=command,
        args=args,
        env=env,
    )
    source_home = real_codex_home or Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    source_auth = source_home / "auth.json"
    if source_auth.is_file():
        try:
            shutil.copy2(source_auth, codex_home / "auth.json")
        except OSError:
            pass


# Codex CLI driver (experimental — do not spend live quota from CI)
# ---------------------------------------------------------------------------


class CodexCliDriver(CliDriver):
    """Run tasks via ``codex exec`` (experimental; metered quota).

    Live invocation is supported for the interface, but the eval harness should
    only exercise this driver when the team explicitly opts in. Offline tests
    inject a fake runner and never touch the real binary.
    """

    name = "codex-cli"
    experimental = True
    default_call_source = "stream"
    run_notes = ("experimental:codex-cli",)
    temp_dir_prefix = "plane-eval-codex-"
    exit_note_prefix = "codex"

    def __init__(
        self,
        *,
        codex_bin: str = "codex",
        python_bin: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        allow_live: bool = False,
        server_command: list[str] | None = None,
        use_proxy: bool = True,
        record_result_payloads: bool = False,
    ) -> None:
        self.codex_bin = codex_bin
        self.allow_live = allow_live
        super().__init__(
            python_bin=python_bin,
            runner=runner,
            server_command=server_command,
            use_proxy=use_proxy,
            record_result_payloads=record_result_payloads,
        )

    def validate_run(self) -> None:
        if self._runner is run_cli_subprocess and not self.allow_live:
            raise RuntimeError(
                "CodexCliDriver refuses live runs by default (metered weekly quota). "
                "Pass allow_live=True or inject a fake runner for tests."
            )

    def write_mcp_config(
        self,
        temp_dir: Path,
        *,
        task_cwd: Path,
        server_command: list[str],
        child_env: dict[str, str],
    ) -> CliLaunch:
        codex_home = temp_dir / "codex-home"
        prepare_codex_home(
            codex_home,
            command=server_command[0],
            args=server_command[1:],
            env=child_env,
        )
        run_env = {**os.environ, "CODEX_HOME": str(codex_home)}
        if "PATH" in child_env:
            run_env["PATH"] = child_env["PATH"]
        return CliLaunch(cwd=task_cwd, env=run_env)

    def build_command(
        self,
        prompt: str,
        *,
        model: str | None,
        max_turns: int,
        system: str | None,
        launch: CliLaunch,
    ) -> list[str]:
        del max_turns
        command = [
            self.codex_bin,
            "exec",
            "--json",
            "--skip-git-repo-check",
            *launch.config_args,
        ]
        if model:
            command.extend(["-m", model])
        full_prompt = prompt if not system else f"{system}\n\n{prompt}"
        command.append(full_prompt)
        return command

    def parse_output(
        self,
        proc: subprocess.CompletedProcess[str],
        *,
        launch: CliLaunch,
        task_cwd: Path,
        max_turns: int,
        notes: list[str],
    ) -> CliOutput:
        del launch
        del task_cwd, max_turns
        parsed = parse_codex_jsonl_events(proc.stdout or "")
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
        return CliOutput(
            calls=calls,
            final_text=parsed.get("final_text") or "",
            client_tool_calls=client_calls,
            usage=usage,
            usage_total=usage_total,
            stopped_reason=parsed.get("stopped_reason") or "end_turn",
            raw_ref=raw_ref,
            call_source=call_source,
            hit_max_turns=False,  # codex exec has no max-turns flag in --help
        )


__all__ = [
    "CodexCliDriver",
    "find_codex_rollout",
    "parse_codex_jsonl_events",
    "parse_codex_rollout_calls",
    "prepare_codex_home",
    "write_codex_mcp_config",
    "write_codex_mcp_override_args",
]
