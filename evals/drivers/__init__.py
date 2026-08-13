"""Agent-driver abstraction for the Plane MCP eval harness.

Drivers run one task against a tool surface and return a normalized
``AgentRun``. The default ``api`` driver owns a provider-neutral model/tool
loop over an in-process MCP client. CLI drivers (``claude-cli``, ``codex-cli``,
``antigravity-cli``, ``opencode-cli``) spawn locally installed agent CLIs on
the user's subscription — no Anthropic API key required for those paths.

Real CLI surfaces (probed on this machine, 2026-08-12):

Claude Code (``claude`` v2.1.228):
  - ``-p`` / ``--print`` headless
  - ``--mcp-config <file|json>`` (repeatable; ``--strict-mcp-config``)
  - ``--output-format json|text|stream-json`` (print mode)
  - ``--max-turns <n>`` (print mode; *hidden* from ``--help`` but present)
  - ``--model <alias|id>``
  - ``--permission-mode`` choices: acceptEdits, auto, bypassPermissions,
    manual, dontAsk, plan
  - ``--dangerously-skip-permissions``, ``--allowedTools`` / ``--allowed-tools``
  - Transcript: ``~/.claude/projects/<cwd-with-/-as-->/<session_id>.jsonl``
    with ``assistant`` rows whose ``message.content`` holds ``tool_use`` blocks.
  - MCP tools surface as ``mcp__<server>__<tool>`` — strip for classification.

Codex (``codex exec``):
  - ``codex exec --json`` JSONL events on stdout
  - ``-c key=value`` / ``--config`` for config.toml overrides (incl. mcp_servers)
  - ``-m`` / ``--model``
  - Session rollouts: ``~/.codex/sessions/**/rollout-*.jsonl`` with
    ``response_item`` / ``function_call`` payloads (name + arguments JSON string)
  - Marked **experimental**; live runs are opt-in (metered quota).

This package splits that surface into focused modules (base types, subprocess
lifecycle, recording-proxy glue, and per-vendor drivers). Import from
``evals.drivers`` as before — public names are re-exported here.
"""

from __future__ import annotations

from typing import Any

from evals.drivers.antigravity import (
    AntigravityCliDriver,
    prepare_antigravity_fake_home,
    write_antigravity_mcp_config,
)
from evals.drivers.api import ApiDriver
from evals.drivers.base import (
    REPO_ROOT,
    AgentDriver,
    AgentRun,
    agent_run_to_harness_dict,
    agent_run_to_task_result,
    is_plane_mcp_tool,
    normalize_tool_call,
    split_plane_and_client_calls,
    strip_mcp_prefix,
)
from evals.drivers.claude import (
    ClaudeCliDriver,
    find_claude_transcript,
    normalize_claude_usage,
    parse_claude_json_result,
    parse_claude_transcript_calls,
    write_claude_mcp_config,
)
from evals.drivers.cli import CliDriver
from evals.drivers.codex import (
    CodexCliDriver,
    find_codex_rollout,
    parse_codex_jsonl_events,
    parse_codex_rollout_calls,
    write_codex_mcp_override_args,
)
from evals.drivers.opencode import OpencodeCliDriver, write_opencode_mcp_config
from evals.drivers.process import kill_process_group, note_timeout_kill, run_cli_subprocess
from evals.drivers.sidecar import (
    apply_proxy_sidecar,
    ensure_proxy_pythonpath,
    harvest_proxy_after_cli_timeout,
    load_proxy_sidecar,
    load_proxy_sidecar_calls,
    proxy_wrap_server_command,
    wait_for_proxy_meta,
)
from evals.results import CallRecord, TaskResult

# Registry
# ---------------------------------------------------------------------------

KNOWN_DRIVERS = frozenset({"api", "claude-cli", "codex-cli", "antigravity-cli", "opencode-cli"})


def get_driver(name: str, **kwargs: Any) -> AgentDriver:
    """Return a driver instance."""
    key = (name or "api").strip().lower()
    if key == "api":
        return ApiDriver(**kwargs)
    if key == "claude-cli":
        return ClaudeCliDriver(**kwargs)
    if key == "codex-cli":
        return CodexCliDriver(**kwargs)
    if key == "antigravity-cli":
        return AntigravityCliDriver(**kwargs)
    if key == "opencode-cli":
        return OpencodeCliDriver(**kwargs)
    raise ValueError(f"unknown driver {name!r}; expected one of {sorted(KNOWN_DRIVERS)}")


__all__ = [
    "KNOWN_DRIVERS",
    "AgentDriver",
    "AgentRun",
    "AntigravityCliDriver",
    "ApiDriver",
    "ClaudeCliDriver",
    "CallRecord",
    "CliDriver",
    "CodexCliDriver",
    "OpencodeCliDriver",
    "REPO_ROOT",
    "TaskResult",
    "agent_run_to_harness_dict",
    "agent_run_to_task_result",
    "apply_proxy_sidecar",
    "ensure_proxy_pythonpath",
    "find_claude_transcript",
    "find_codex_rollout",
    "get_driver",
    "harvest_proxy_after_cli_timeout",
    "is_plane_mcp_tool",
    "kill_process_group",
    "load_proxy_sidecar",
    "load_proxy_sidecar_calls",
    "normalize_claude_usage",
    "normalize_tool_call",
    "note_timeout_kill",
    "parse_claude_json_result",
    "parse_claude_transcript_calls",
    "parse_codex_jsonl_events",
    "parse_codex_rollout_calls",
    "prepare_antigravity_fake_home",
    "proxy_wrap_server_command",
    "run_cli_subprocess",
    "split_plane_and_client_calls",
    "strip_mcp_prefix",
    "wait_for_proxy_meta",
    "write_antigravity_mcp_config",
    "write_claude_mcp_config",
    "write_codex_mcp_override_args",
    "write_opencode_mcp_config",
]
