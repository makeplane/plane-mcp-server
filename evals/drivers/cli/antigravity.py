"""Antigravity CLI (agy) driver — proxy-first measurement."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from evals.drivers.driver import CliDriver, CliLaunch, CliOutput

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
    """Build an isolated HOME for agy with MCP config plus copied auth artifacts.

    Writes mcp_config.json to both documented paths (~/.gemini/config/ and
    ~/.gemini/antigravity-cli/) since which one agy reads is unsettled. antigravity-cli is
    a real directory and the oauth token a plain copy, never symlinks — otherwise a token
    refresh or a runtime log would write through into the user's real home.
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


class AntigravityCliDriver(CliDriver):
    """Run tasks via Google Antigravity CLI (``agy``).

    Probed 2026-08-12: -p headless, --output-format text|json|stream-json, --model,
    --dangerously-skip-permissions. MCP only via ~/.gemini/config/mcp_config.json with no
    CLI flag, hence HOME isolation; no turn-cap flag, so hit_max_turns=False plus a note.
    Tool calls come from the proxy sidecar, not from parsing agy stdout.
    """

    name = "antigravity-cli"
    run_notes = ("no_turn_cap",)
    temp_dir_prefix = "plane-eval-antigravity-"
    exit_note_prefix = "agy"
    include_stderr_in_exit_note = True

    def __init__(
        self,
        *,
        agy_bin: str = "agy",
        python_bin: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        server_command: list[str] | None = None,
        use_proxy: bool = True,
        record_result_payloads: bool = False,
    ) -> None:
        self.agy_bin = agy_bin
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
        fake_home = temp_dir / "home"
        prepare_antigravity_fake_home(
            fake_home,
            command=server_command[0],
            args=server_command[1:],
            env=child_env,
        )
        xdg_roots = {
            name: temp_dir / name.lower().replace("_home", "")
            for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")
        }
        for directory in xdg_roots.values():
            directory.mkdir(parents=True, exist_ok=True)
        run_env = {
            **os.environ,
            "HOME": str(fake_home),
            **{name: str(directory) for name, directory in xdg_roots.items()},
        }
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
        del max_turns, launch
        full_prompt = prompt if not system else f"{system}\n\n{prompt}"
        command = [
            self.agy_bin,
            "-p",
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
        ]
        if model:
            command.extend(["--model", model])
        command.append(full_prompt)
        return command

    def invoke_cli(
        self,
        command: list[str],
        *,
        launch: CliLaunch,
        timeout_s: int,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return super().invoke_cli(command, launch=launch, timeout_s=timeout_s)
        except TypeError:
            # Some test runners reject ``env=``; retry without it. A timeout
            # from this fallback still reaches the template's harvest path.
            fallback = CliLaunch(cwd=launch.cwd, config_args=launch.config_args)
            return super().invoke_cli(command, launch=fallback, timeout_s=timeout_s)

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
        try:
            if final_text.lstrip().startswith("{"):
                blob = json.loads(final_text)
                if isinstance(blob, dict):
                    final_text = str(blob.get("result") or blob.get("text") or blob.get("response") or final_text)
        except json.JSONDecodeError:
            pass

        return CliOutput(
            final_text=final_text,
            stopped_reason="error" if proc.returncode else "end_turn",
        )


__all__ = [
    "AntigravityCliDriver",
    "prepare_antigravity_fake_home",
    "write_antigravity_mcp_config",
]
