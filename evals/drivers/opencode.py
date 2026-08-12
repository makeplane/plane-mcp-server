"""OpenCode CLI driver — proxy-first measurement."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from evals.drivers.base import REPO_ROOT, AgentRun
from evals.drivers.process import note_timeout_kill, run_cli_subprocess
from evals.drivers.sidecar import (
    apply_proxy_sidecar,
    ensure_proxy_pythonpath,
    harvest_proxy_after_cli_timeout,
    proxy_wrap_server_command,
)

# OpenCode CLI — proxy-first
# ---------------------------------------------------------------------------


def write_opencode_mcp_config(
    path: Path,
    *,
    command: list[str],
    env: dict[str, str],
    server_name: str = "plane",
) -> None:
    """Write project ``opencode.json`` with a local MCP server entry.

    Schema (opencode.ai docs / probed binary strings, 2026-08-12)::

        {"mcp": {"plane": {"type": "local", "command": [...], "environment": {...}}}}
    """
    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            server_name: {
                "type": "local",
                "command": list(command),
                "environment": env,
                "enabled": True,
            }
        },
    }
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


class OpencodeCliDriver:
    """Run tasks via ``opencode run`` (proxy-first call recording).

    Probed flags (2026-08-12):
      - ``opencode run [message..]`` non-interactive
      - ``--format json|default``, ``-m/--model``
      - MCP via ``opencode.json`` ``mcp`` section (local: type/command/environment)
        written into the task cwd (or a temp project dir).
      - No turn-cap flag → ``hit_max_turns=False`` + note ``no_turn_cap``.
    """

    name = "opencode-cli"

    def __init__(
        self,
        *,
        opencode_bin: str = "opencode",
        python_bin: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        server_command: list[str] | None = None,
        use_proxy: bool = True,
    ) -> None:
        self.opencode_bin = opencode_bin
        self.python_bin = python_bin or sys.executable
        self._runner = runner or run_cli_subprocess
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
        base_cwd = (cwd or REPO_ROOT).resolve()
        notes: list[str] = ["no_turn_cap"]
        t0 = time.perf_counter()
        child_env_plane = {k: v for k, v in mcp_env.items() if k.startswith("PLANE_") or k in ("PATH", "HOME")}

        with tempfile.TemporaryDirectory(prefix="plane-eval-opencode-", dir=str(base_cwd)) as td:
            # Project-local opencode.json so we do not pollute the user's global config.
            proj = Path(td)
            sidecar = proj / "proxy-sidecar.jsonl"
            if self.server_command:
                real_cmd = list(self.server_command)
            else:
                real_cmd = [self.python_bin, "-m", "plane_mcp", "stdio"]
            if self.use_proxy:
                launch = proxy_wrap_server_command(real_cmd, sidecar_path=sidecar, python_bin=self.python_bin)
                child_env_plane = ensure_proxy_pythonpath(child_env_plane)
            else:
                launch = real_cmd
            write_opencode_mcp_config(
                proj / "opencode.json",
                command=launch,
                env=child_env_plane,
                server_name="plane",
            )

            full_prompt = prompt if not system else f"{system}\n\n{prompt}"
            cmd: list[str] = [
                self.opencode_bin,
                "run",
                "--format",
                "json",
            ]
            if model:
                cmd.extend(["-m", model])
            cmd.append(full_prompt)

            timeout_s = max(120, max_turns * 60)
            try:
                proc = self._runner(
                    cmd,
                    cwd=str(proj),
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
                call_source = "json"
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
                    usage_scope="run",
                    call_source=call_source,
                    wall_time_s=round(wall, 3),
                    notes=notes,
                )

            wall = time.perf_counter() - t0
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            calls: list[dict[str, Any]] = []
            client_calls: list[dict[str, Any]] = []
            call_source = "json"
            if self.use_proxy:
                calls, client_calls, call_source = apply_proxy_sidecar(calls, client_calls, sidecar, notes)
            if proc.returncode != 0:
                notes.append(f"opencode_exit={proc.returncode}")
                if stderr.strip():
                    notes.append(stderr.strip()[:500])

            final_text = stdout.strip()
            # JSONL events: concatenate text-ish fields best-effort.
            if final_text and "\n" in final_text:
                parts: list[str] = []
                for line in final_text.splitlines():
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict):
                        for key in ("text", "message", "part", "delta"):
                            v = row.get(key)
                            if isinstance(v, str) and v.strip():
                                parts.append(v)
                        if row.get("type") in ("text", "message") and isinstance(row.get("content"), str):
                            parts.append(row["content"])
                if parts:
                    final_text = "\n".join(parts)

            return AgentRun(
                calls=calls,
                client_tool_calls=client_calls,
                final_text=final_text,
                usage=None,
                stopped_reason="error" if proc.returncode else "end_turn",
                usage_scope="run",
                call_source=call_source,
                hit_max_turns=False,
                wall_time_s=round(wall, 3),
                notes=notes,
            )


__all__ = [
    "OpencodeCliDriver",
    "write_opencode_mcp_config",
]
