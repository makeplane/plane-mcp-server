"""OpenCode CLI driver — proxy-first measurement."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from evals.drivers.driver import CliDriver, CliLaunch, CliOutput

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


def prepare_opencode_isolated_environment(temp_dir: Path) -> dict[str, str]:
    """Return an environment whose HOME/XDG roots cannot load user MCP config."""
    fake_home = temp_dir / "home"
    xdg_config = temp_dir / "xdg-config"
    xdg_data = temp_dir / "xdg-data"
    xdg_cache = temp_dir / "xdg-cache"
    xdg_state = temp_dir / "xdg-state"
    for directory in (fake_home, xdg_config, xdg_data, xdg_cache, xdg_state):
        directory.mkdir(parents=True, exist_ok=True)

    real_data_root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    source_auth = real_data_root / "opencode" / "auth.json"
    if source_auth.is_file():
        destination = xdg_data / "opencode" / "auth.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source_auth, destination)
        except OSError:
            pass

    return {
        **os.environ,
        "HOME": str(fake_home),
        "XDG_CONFIG_HOME": str(xdg_config),
        "XDG_DATA_HOME": str(xdg_data),
        "XDG_CACHE_HOME": str(xdg_cache),
        "XDG_STATE_HOME": str(xdg_state),
    }


class OpencodeCliDriver(CliDriver):
    """Run tasks via ``opencode run`` (proxy-first call recording).

    Probed 2026-08-12: ``opencode run [message..]`` non-interactive, --format json|default,
    -m/--model. MCP comes from an ``opencode.json`` mcp section written into the task cwd;
    no turn-cap flag, so hit_max_turns=False plus a ``no_turn_cap`` note.
    """

    name = "opencode-cli"
    run_notes = ("no_turn_cap",)
    temp_dir_prefix = "plane-eval-opencode-"
    temp_dir_in_cwd = True
    exit_note_prefix = "opencode"
    include_stderr_in_exit_note = True

    def __init__(
        self,
        *,
        opencode_bin: str = "opencode",
        python_bin: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        server_command: list[str] | None = None,
        use_proxy: bool = True,
        record_result_payloads: bool = False,
    ) -> None:
        self.opencode_bin = opencode_bin
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
        del task_cwd
        # Project-local config avoids polluting the user's global config.
        write_opencode_mcp_config(
            temp_dir / "opencode.json",
            command=server_command,
            env=child_env,
            server_name="plane",
        )
        run_env = prepare_opencode_isolated_environment(temp_dir)
        if "PATH" in child_env:
            run_env["PATH"] = child_env["PATH"]
        return CliLaunch(cwd=temp_dir, env=run_env)

    def build_command(
        self,
        prompt: str,
        *,
        model: str | None,
        max_turns: int,
        system: str | None,
        launch: CliLaunch,
    ) -> list[str]:
        del max_turns, launch
        full_prompt = prompt if not system else f"{system}\n\n{prompt}"
        command = [self.opencode_bin, "run", "--format", "json"]
        if model:
            command.extend(["-m", model])
        command.append(full_prompt)
        return command

    def parse_output(
        self,
        proc: subprocess.CompletedProcess[str],
        *,
        task_cwd: Path,
        max_turns: int,
        notes: list[str],
    ) -> CliOutput:
        del task_cwd, max_turns
        final_text = (proc.stdout or "").strip()
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
                        value = row.get(key)
                        if isinstance(value, str) and value.strip():
                            parts.append(value)
                    if row.get("type") in ("text", "message") and isinstance(row.get("content"), str):
                        parts.append(row["content"])
            if parts:
                final_text = "\n".join(parts)

        return CliOutput(
            final_text=final_text,
            stopped_reason="error" if proc.returncode else "end_turn",
        )


__all__ = [
    "OpencodeCliDriver",
    "prepare_opencode_isolated_environment",
    "write_opencode_mcp_config",
]
