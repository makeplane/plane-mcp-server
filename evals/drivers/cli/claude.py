"""Claude Code CLI driver and transcript/JSON parsers.

Probed (claude v2.1.232): -p headless; --mcp-config (repeatable) + --strict-mcp-config;
--output-format json|text|stream-json; --max-turns (present but hidden from --help);
--model; --permission-mode; transcript at
<CLAUDE_CONFIG_DIR>/projects/<cwd-with-/-as-->/<session_id>.jsonl, assistant rows
carrying tool_use blocks; MCP tools appear as mcp__<server>__<tool>. The CLI's own help
defines --strict-mcp-config as ignoring every MCP source except --mcp-config. The harness
passes the same isolated .claude.json to that option and to real-binary ``claude mcp list``
readback, which observes only ``plane``. That readback supports the configuration claim;
exclusion during the evaluated ``claude -p`` invocation rests on the documented strict flag,
not behavioral observation of that invocation.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from evals.core.tool_names import normalize_tool_call, split_plane_and_client_calls
from evals.drivers.cli.base import CliDriver, CliLaunch, CliOutput, CliOutputError


def normalize_claude_usage(data: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Parse Claude print-mode usage into (raw_usage, usage_total).

    The envelope splits input across ``input_tokens`` (uncached new input only),
    ``cache_creation_input_tokens`` and ``cache_read_input_tokens``, mirrored in
    ``modelUsage.<model-id>`` as camelCase plus ``costUSD``/``total_cost_usd``.
    Bare ``input_tokens`` is the trap: live multi-turn rows read 8-10 while
    cache_read was 180k+, so callers must never copy it into ``cum_input_tokens``.
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


def _claude_project_dir(cwd: Path, *, config_dir: Path | None = None) -> Path:
    """Map a cwd to Claude's ``projects/<munged>`` transcript directory."""
    munged = str(cwd.resolve()).replace("/", "-")
    root = config_dir or Path.home() / ".claude"
    return root / "projects" / munged


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
    ``split_plane_and_client_calls`` before counting.
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


def find_claude_transcript(
    session_id: str | None,
    cwd: Path,
    *,
    config_dir: Path | None = None,
) -> Path | None:
    """Locate ``<config-dir>/projects/<munged-cwd>/<session_id>.jsonl``."""
    if not session_id:
        return None
    candidate = _claude_project_dir(cwd, config_dir=config_dir) / f"{session_id}.jsonl"
    if candidate.is_file():
        return candidate
    # Fallback: scan project dir for a file containing the session id
    proj = _claude_project_dir(cwd, config_dir=config_dir)
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


def persist_claude_transcript(
    transcript: Path,
    *,
    artifact_dir: Path,
    session_id: str,
) -> Path:
    """Copy a per-task transcript out of disposable Claude state for row forensics."""
    artifact_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    safe_session = "".join(character for character in session_id if character.isalnum() or character in "-_")
    destination = artifact_dir / f"{safe_session or 'session'}-{uuid.uuid4().hex}.jsonl"
    shutil.copy2(transcript, destination)
    destination.chmod(0o600)
    return destination


def prepare_claude_isolated_environment(
    temp_dir: Path,
    *,
    real_config_dir: Path | None = None,
) -> dict[str, str]:
    """Return isolated HOME/config/XDG roots with only Claude's login artifact copied."""
    fake_home = temp_dir / "home"
    claude_config = temp_dir / "claude-config"
    xdg_roots = {
        name: temp_dir / name.lower().replace("_home", "")
        for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")
    }
    for directory in (fake_home, claude_config, *xdg_roots.values()):
        directory.mkdir(parents=True, exist_ok=True)

    source_config = real_config_dir or Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    source_credentials = source_config / ".credentials.json"
    if source_credentials.is_file():
        try:
            shutil.copy2(source_credentials, claude_config / ".credentials.json")
        except OSError as exc:
            raise RuntimeError(
                f"failed to copy Claude credentials into isolated config from {source_credentials}: {exc}"
            ) from exc

    return {
        **os.environ,
        "HOME": str(fake_home),
        "CLAUDE_CONFIG_DIR": str(claude_config),
        **{name: str(directory) for name, directory in xdg_roots.items()},
    }


# ---------------------------------------------------------------------------
# Claude CLI driver
# ---------------------------------------------------------------------------


class ClaudeCliDriver(CliDriver):
    """Run Claude Code with isolated state and readback-supported strict MCP config.

    ``--strict-mcp-config`` makes the launch-scoped file exclusive by the Claude CLI's
    documented contract, not a behavioral probe of the evaluated ``claude -p`` invocation.
    HOME, CLAUDE_CONFIG_DIR, and every XDG root are isolated so ambient user-home state is
    not inherited. A real ``claude mcp list`` reads the same temporary .claude.json and
    observes exactly the ``plane`` server.

    Known limitation: a refreshed file-based credential is discarded with the per-task
    config. It is not copied into user state because doing so would mutate the user's auth
    and introduce cross-task/concurrent refresh races; later tasks re-copy the durable source.
    """

    name = "claude-cli"
    run_notes = (
        "known_limitation:claude_file_credentials_refresh_discarded:per-task config is deleted; "
        "refresh is not copied to user auth to avoid mutation and cross-task races",
    )
    temp_dir_prefix = "plane-eval-claude-"

    def __init__(
        self,
        *,
        claude_bin: str = "claude",
        python_bin: str | None = None,
        permission_mode: str = "bypassPermissions",
        strict_mcp: bool = True,
        builtin_tools: str | None = "",
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        server_command: list[str] | None = None,
        use_proxy: bool = True,
        record_result_payloads: bool = False,
    ) -> None:
        self.claude_bin = claude_bin
        self.permission_mode = permission_mode
        self.strict_mcp = strict_mcp
        # Which of Claude Code's own tools the agent keeps. Empty means none, which is
        # what an eval of a *tool surface* wants: with Bash and Read in hand a model that
        # cannot work the surface out reads the repo it is standing in, harvests the API
        # key and calls Plane's REST API directly — measured, not hypothesised. Removing
        # the built-ins also drops the total tool count under the threshold that defers
        # MCP tools behind ToolSearch, so the surface arrives directly, as it does for
        # every other driver. None keeps Claude Code's default set.
        self.builtin_tools = builtin_tools
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
        run_env = prepare_claude_isolated_environment(temp_dir)
        if "PATH" in child_env:
            run_env["PATH"] = child_env["PATH"]
        transcript_config_dir = Path(run_env["CLAUDE_CONFIG_DIR"])
        # Claude's management readback consumes this location, while the session receives
        # the exact same physical file through --mcp-config.
        mcp_cfg = transcript_config_dir / ".claude.json"
        write_claude_mcp_config(
            mcp_cfg,
            command=server_command[0],
            args=server_command[1:],
            env=child_env,
            server_name="plane",
        )
        return CliLaunch(
            cwd=task_cwd,
            config_args=["--mcp-config", str(mcp_cfg)],
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
        if self.builtin_tools is not None:
            # `=` form: --tools is variadic and would otherwise swallow the trailing prompt.
            command.append(f"--tools={self.builtin_tools}")
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
        launch: CliLaunch,
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
        config_value = (launch.env or {}).get("CLAUDE_CONFIG_DIR")
        config_dir = Path(config_value) if config_value else None
        transcript = find_claude_transcript(
            session_id,
            task_cwd,
            config_dir=config_dir,
        )
        if transcript is not None:
            if launch.artifact_dir is None:
                raise CliOutputError("Claude transcript found without a durable artifact directory")
            try:
                transcript = persist_claude_transcript(
                    transcript,
                    artifact_dir=launch.artifact_dir,
                    session_id=str(session_id or transcript.stem),
                )
            except OSError as exc:
                raise CliOutputError(f"failed to persist Claude transcript: {exc}") from exc
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
    "persist_claude_transcript",
    "prepare_claude_isolated_environment",
    "write_claude_mcp_config",
]
