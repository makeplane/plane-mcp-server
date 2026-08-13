"""CLI agent drivers and their shared execution machinery."""

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
from evals.drivers.cli.opencode import OpencodeCliDriver, write_opencode_mcp_config
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
from evals.drivers.cli.template import CliDriver

__all__ = [
    "AntigravityCliDriver",
    "ClaudeCliDriver",
    "CliDriver",
    "CodexCliDriver",
    "OpencodeCliDriver",
    "apply_proxy_sidecar",
    "ensure_proxy_pythonpath",
    "find_claude_transcript",
    "find_codex_rollout",
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
