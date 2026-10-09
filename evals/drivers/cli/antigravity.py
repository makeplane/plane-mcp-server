"""Antigravity CLI (agy) driver — proxy-first measurement."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

from evals.drivers.cli.base import CliDriver, CliLaunch, CliOutput

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


def prepare_antigravity_gemini_dir(
    gemini_dir: Path,
    *,
    command: str,
    args: list[str],
    env: dict[str, str],
) -> None:
    """Build an isolated ``--gemini_dir`` tree for agy holding only our MCP server.

    Writes mcp_config.json to both paths agy reads under its gemini dir (``config/``
    and ``antigravity-cli/``) since which one wins is unsettled; agy creates an empty
    ``config/mcp_config.json`` itself when none is present.

    This replaces an isolated HOME. agy keeps its OAuth token in the macOS login
    keychain, which Security resolves through ``$HOME/Library/Keychains`` — so
    overriding HOME made the keychain unfindable ("A keychain cannot be found to store
    \"antigravity\"") and every run failed unauthenticated. ``--gemini_dir`` moves only
    agy's own state, leaving HOME real and the credential reachable.
    """
    for rel in (
        Path("config") / "mcp_config.json",
        Path("antigravity-cli") / "mcp_config.json",
    ):
        write_antigravity_mcp_config(
            gemini_dir / rel,
            command=command,
            args=args,
            env=env,
            server_name="plane",
        )


class AntigravityCliDriver(CliDriver):
    """Run tasks via Google Antigravity CLI (``agy``).

    Probed 2026-08-12: -p headless, --output-format text|json|stream-json, --model,
    --dangerously-skip-permissions. No turn-cap flag, so hit_max_turns=False plus a note.
    Tool calls come from the proxy sidecar, not from parsing agy stdout.

    MCP config is not a flag, so the config must be planted somewhere agy will read.
    Re-probed 2026-08-19 on 1.1.15: the undocumented ``--gemini_dir`` relocates agy's
    whole state tree, which isolates the config without touching HOME. It must be an
    absolute path — agy logs "must be an absolute path" and silently falls back to the
    real one otherwise, which would hand the agent the user's own servers.

    Antigravity CLI has no MCP or effective-config introspection command, so server
    exclusivity still cannot be proven by real-binary readback: it rests on the isolated
    gemini dir plus inspection of the generated files.
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
        # Absolute, because agy ignores a relative --gemini_dir and falls back to the
        # real one; temp_dir is already absolute but resolve() makes that a guarantee
        # rather than a caller's promise.
        gemini_dir = (temp_dir / "gemini").resolve()
        prepare_antigravity_gemini_dir(
            gemini_dir,
            command=server_command[0],
            args=server_command[1:],
            env=child_env,
        )
        run_env = None
        if "PATH" in child_env:
            run_env = {**os.environ, "PATH": child_env["PATH"]}
        return CliLaunch(
            cwd=task_cwd,
            config_args=[f"--gemini_dir={gemini_dir}"],
            env=run_env,
        )

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
        full_prompt = prompt if not system else f"{system}\n\n{prompt}"
        # Every string flag takes the ``--flag=value`` form. agy parses with Go's flag
        # package, where a string flag consumes the next argv entry: written as
        # ``-p <prompt>`` with other flags after it, ``-p`` ate ``--output-format``, the
        # real prompt became a stray positional that ended flag parsing, and
        # --dangerously-skip-permissions never took effect. agy then answered a question
        # about its own CLI and denied its own tool calls. Keeping value and flag in one
        # argv entry makes the ordering irrelevant.
        command = [
            self.agy_bin,
            # --gemini_dir chooses the state tree agy reads everything else out of.
            *launch.config_args,
            "--output-format=json",
            "--dangerously-skip-permissions",
        ]
        if model:
            command.append(f"--model={model}")
        # Last, so nothing can be mistaken for its value.
        command.append(f"--print={full_prompt}")
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
        del launch, task_cwd, max_turns
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
    "prepare_antigravity_gemini_dir",
    "write_antigravity_mcp_config",
]
