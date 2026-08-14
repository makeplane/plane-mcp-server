"""Agent drivers: run one task against a tool surface and return an ``AgentRun``.

The ``api`` driver owns a provider-neutral loop; CLI drivers spawn locally installed
agent CLIs on the user's own subscription. Probed CLI details live with each vendor.
"""

from __future__ import annotations

from typing import Any

from evals.drivers.cli.antigravity import (
    AntigravityCliDriver,
    prepare_antigravity_fake_home,
    write_antigravity_mcp_config,
)
from evals.drivers.cli.claude import (
    ClaudeCliDriver,
    find_claude_transcript,
    normalize_claude_usage,
    parse_claude_json_result,
    parse_claude_transcript_calls,
    write_claude_mcp_config,
)
from evals.drivers.cli.codex import (
    CodexCliDriver,
    find_codex_rollout,
    parse_codex_jsonl_events,
    parse_codex_rollout_calls,
    write_codex_mcp_override_args,
)
from evals.drivers.cli.opencode import (
    OpencodeCliDriver,
    write_opencode_mcp_config,
)
from evals.drivers.cli.process import kill_process_group, note_timeout_kill, run_cli_subprocess
from evals.drivers.cli.sidecar import (
    apply_proxy_sidecar,
    ensure_proxy_pythonpath,
    harvest_proxy_after_cli_timeout,
    load_proxy_sidecar,
    load_proxy_sidecar_calls,
    proxy_wrap_server_command,
    wait_for_proxy_meta,
)
from evals.drivers.driver import ApiDriver, CliDriver

# Registry
# ---------------------------------------------------------------------------

KNOWN_DRIVERS = frozenset({"api", "claude-cli", "codex-cli", "antigravity-cli", "opencode-cli"})


def get_driver(name: str, **kwargs: Any) -> ApiDriver | CliDriver:
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
    "AntigravityCliDriver",
    "ApiDriver",
    "ClaudeCliDriver",
    "CliDriver",
    "CodexCliDriver",
    "OpencodeCliDriver",
    "apply_proxy_sidecar",
    "ensure_proxy_pythonpath",
    "find_claude_transcript",
    "find_codex_rollout",
    "get_driver",
    "harvest_proxy_after_cli_timeout",
    "kill_process_group",
    "load_proxy_sidecar",
    "load_proxy_sidecar_calls",
    "normalize_claude_usage",
    "note_timeout_kill",
    "parse_claude_json_result",
    "parse_claude_transcript_calls",
    "parse_codex_jsonl_events",
    "parse_codex_rollout_calls",
    "prepare_antigravity_fake_home",
    "proxy_wrap_server_command",
    "run_cli_subprocess",
    "wait_for_proxy_meta",
    "write_antigravity_mcp_config",
    "write_claude_mcp_config",
    "write_codex_mcp_override_args",
    "write_opencode_mcp_config",
]
