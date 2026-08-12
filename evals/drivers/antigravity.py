"""Antigravity CLI (agy) driver — proxy-first measurement."""

from __future__ import annotations

import json
import os
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

# Antigravity CLI (agy) — proxy-first
# ---------------------------------------------------------------------------


def write_antigravity_mcp_config(
    path: Path,
    *,
    command: str,
    args: list[str],
    env: dict[str, str],
    server_name: str = "plane",
) -> None:
    """Write ``mcpServers`` map JSON (Antigravity / agy mcp_config shape)."""
    cfg = {
        "mcpServers": {
            server_name: {
                "command": command,
                "args": args,
                "env": env,
            }
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def prepare_antigravity_fake_home(
    fake_home: Path,
    *,
    command: str,
    args: list[str],
    env: dict[str, str],
    real_home: Path | None = None,
) -> None:
    """Build an isolated HOME for agy with MCP config + shared auth artifacts.

    Writes mcp_config.json to BOTH documented locations (cheap; live probe
    should settle which path agy actually reads):
      - ~/.gemini/config/mcp_config.json
      - ~/.gemini/antigravity-cli/mcp_config.json

    Creates ``antigravity-cli`` as a **real directory** (never a symlink of the
    whole tree — that would write mcp_config and runtime logs into real user
    state). Auth artifacts (``antigravity-oauth-token``) are plain **copies** —
    never symlinks — so an in-place token refresh cannot write through into the
    real home. Staleness over a single eval run is negligible.
    """
    real_home = real_home or Path.home()
    gemini_root = fake_home / ".gemini"
    gemini_root.mkdir(parents=True, exist_ok=True)

    real_cli = real_home / ".gemini" / "antigravity-cli"
    fake_cli = gemini_root / "antigravity-cli"
    # Always a real directory — never symlink the whole tree.
    if fake_cli.is_symlink() or fake_cli.is_file():
        fake_cli.unlink()
    fake_cli.mkdir(parents=True, exist_ok=True)

    # Share auth via plain COPY only — never symlink (in-place token refresh
    # must not write through into the real home).
    if real_cli.is_dir():
        for name in ("antigravity-oauth-token",):
            src = real_cli / name
            dst = fake_cli / name
            if src.is_file() and not dst.exists():
                try:
                    dst.write_bytes(src.read_bytes())
                except OSError:
                    pass

    # Dual write as real files (not through any symlink).
    for rel in (
        Path(".gemini") / "config" / "mcp_config.json",
        Path(".gemini") / "antigravity-cli" / "mcp_config.json",
    ):
        write_antigravity_mcp_config(
            fake_home / rel,
            command=command,
            args=args,
            env=env,
            server_name="plane",
        )


class AntigravityCliDriver:
    """Run tasks via Google Antigravity CLI (``agy``).

    Probed flags (2026-08-12, ``agy --help``):
      - ``-p`` / ``--print`` headless single-prompt mode
      - ``--output-format`` text|json|stream-json
      - ``--model``, ``--dangerously-skip-permissions``
      - MCP via ``~/.gemini/config/mcp_config.json`` (``mcpServers`` map;
        stdio: command/args/env). No CLI flag for MCP config → HOME isolation.
      - No max-turns / turn-cap flag in help → ``hit_max_turns=False`` + note.

    Tool calls come from the recording proxy sidecar (protocol-layer), not
    agy stdout parsing.
    """

    name = "antigravity-cli"

    def __init__(
        self,
        *,
        agy_bin: str = "agy",
        python_bin: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        server_command: list[str] | None = None,
        use_proxy: bool = True,
    ) -> None:
        self.agy_bin = agy_bin
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
        cwd = (cwd or REPO_ROOT).resolve()
        notes: list[str] = ["no_turn_cap"]
        t0 = time.perf_counter()
        child_env_plane = {k: v for k, v in mcp_env.items() if k.startswith("PLANE_") or k in ("PATH", "HOME")}

        with tempfile.TemporaryDirectory(prefix="plane-eval-antigravity-") as td:
            td_path = Path(td)
            sidecar = td_path / "proxy-sidecar.jsonl"
            fake_home = td_path / "home"
            if self.server_command:
                real_cmd = list(self.server_command)
            else:
                real_cmd = [self.python_bin, "-m", "plane_mcp", "stdio"]
            if self.use_proxy:
                wrapped = proxy_wrap_server_command(real_cmd, sidecar_path=sidecar, python_bin=self.python_bin)
                server_cmd, server_args = wrapped[0], wrapped[1:]
                child_env_plane = ensure_proxy_pythonpath(child_env_plane)
            else:
                server_cmd, server_args = real_cmd[0], real_cmd[1:]
            prepare_antigravity_fake_home(
                fake_home,
                command=server_cmd,
                args=server_args,
                env=child_env_plane,
            )

            full_prompt = prompt if not system else f"{system}\n\n{prompt}"
            cmd: list[str] = [
                self.agy_bin,
                "-p",
                "--output-format",
                "json",
                "--dangerously-skip-permissions",
            ]
            if model:
                cmd.extend(["--model", model])
            cmd.append(full_prompt)

            run_env = {**os.environ, "HOME": str(fake_home)}
            if "PATH" in child_env_plane:
                run_env["PATH"] = child_env_plane["PATH"]

            timeout_s = max(120, max_turns * 60)
            try:
                try:
                    proc = self._runner(
                        cmd,
                        cwd=str(cwd),
                        capture_output=True,
                        text=True,
                        timeout=timeout_s,
                        env=run_env,
                    )
                except TypeError:
                    # Some test runners reject ``env=``; retry without it.
                    # TimeoutExpired from this path must still hit the harvest below.
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
                notes.append(f"agy_exit={proc.returncode}")
                if stderr.strip():
                    notes.append(stderr.strip()[:500])

            final_text = stdout.strip()
            try:
                if final_text.lstrip().startswith("{"):
                    blob = json.loads(final_text)
                    if isinstance(blob, dict):
                        final_text = str(blob.get("result") or blob.get("text") or blob.get("response") or final_text)
            except json.JSONDecodeError:
                pass

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
    "AntigravityCliDriver",
    "prepare_antigravity_fake_home",
    "write_antigravity_mcp_config",
]
