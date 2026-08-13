"""Offline tests for eval agent drivers (no real CLI invocations)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from evals.cli import parse_args
from evals.drivers import (
    KNOWN_DRIVERS,
    ApiDriver,
    ClaudeCliDriver,
    CodexCliDriver,
    get_driver,
    normalize_claude_usage,
    parse_claude_json_result,
    parse_claude_transcript_calls,
    parse_codex_jsonl_events,
    run_cli_subprocess,
    write_claude_mcp_config,
)
from evals.results import AgentRun, agent_run_to_harness_dict
from evals.runner.live import classify_call, stdio_server_env
from evals.token_counting import estimate_result_tokens
from evals.tool_names import (
    is_plane_mcp_tool,
    normalize_tool_call,
    split_plane_and_client_calls,
    strip_mcp_prefix,
)

# ---------------------------------------------------------------------------
# Fixtures (constructed — never captured from live CLIs)
# ---------------------------------------------------------------------------

# Mirrors real claude -p --output-format json (probed): input_tokens is uncached-only;
# mass lives in cache_* + modelUsage.
CLAUDE_JSON_RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "The work item is in Todo.",
    "session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "num_turns": 3,
    "total_cost_usd": 0.291,
    "stop_reason": "end_turn",
    "usage": {
        "input_tokens": 10,
        "output_tokens": 865,
        "cache_read_input_tokens": 250433,
        "cache_creation_input_tokens": 33838,
        "iterations": [
            {
                "input_tokens": 2,
                "output_tokens": 8,
                "cache_read_input_tokens": 57985,
                "cache_creation_input_tokens": 754,
                "type": "message",
            }
        ],
        "speed": "standard",
    },
    "modelUsage": {
        "claude-sonnet-4-20250514": {
            "inputTokens": 10,
            "outputTokens": 865,
            "cacheReadInputTokens": 250433,
            "cacheCreationInputTokens": 33838,
            "costUSD": 0.291,
            "contextWindow": 200000,
        }
    },
}

# JSON result that already embeds tool_calls (rare path)
CLAUDE_JSON_WITH_CALLS = {
    **CLAUDE_JSON_RESULT,
    "tool_calls": [
        {
            "name": "ToolSearch",
            "input": {"query": "work items", "max_results": 5},
        },
        {
            "name": "mcp__plane__find_work_items",
            "input": {"project": "EVAL deadbeef", "limit": 10},
        },
        {
            "name": "mcp__plane__get_work_item",
            "input": {"project_id": "p1", "work_item_id": "w1"},
        },
    ],
}


# Transcript rows (assistant + tool_use) — Claude project JSONL shape
def _transcript_lines(*, include_tool_search: bool = False) -> str:
    content_blocks: list[dict] = []
    if include_tool_search:
        content_blocks.append(
            {
                "type": "tool_use",
                "id": "toolu_0",
                "name": "ToolSearch",
                "input": {"query": "select:find_work_items", "max_results": 1},
            }
        )
    content_blocks.append(
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "mcp__plane__list_work_items",
            "input": {"project_id": "proj-1", "per_page": 25},
        }
    )
    rows = [
        {
            "type": "assistant",
            "sessionId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "message": {
                "role": "assistant",
                "content": content_blocks,
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "[]"}],
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_2",
                        "name": "mcp__plane__get_work_item",
                        "input": {"project_id": "proj-1", "work_item_id": "wi-1"},
                    }
                ],
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Done."}],
                "stop_reason": "end_turn",
            },
        },
    ]
    return "\n".join(json.dumps(r) for r in rows) + "\n"


CODEX_JSONL = "\n".join(
    [
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "sess-codex-1", "cwd": "/tmp", "cli_version": "0.0-test"},
            }
        ),
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "mcp__plane__find_work_items",
                    "arguments": json.dumps({"project": "EVAL x", "limit": 5}),
                    "call_id": "call_1",
                },
            }
        ),
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "arguments": json.dumps({"cmd": "echo hi"}),
                    "call_id": "call_2",
                },
            }
        ),
        json.dumps(
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 5000,
                            "output_tokens": 200,
                            "cached_input_tokens": 1000,
                            "cache_write_input_tokens": 50,
                            "total_tokens": 5200,
                        }
                    },
                },
            }
        ),
        json.dumps(
            {
                "type": "event_msg",
                "payload": {"type": "agent_message", "message": "All set."},
            }
        ),
        json.dumps({"type": "event_msg", "payload": {"type": "task_complete"}}),
    ]
)


# ---------------------------------------------------------------------------
# strip / parse unit tests
# ---------------------------------------------------------------------------


def test_strip_mcp_prefix():
    assert strip_mcp_prefix("mcp__plane__list_work_items") == "list_work_items"
    assert strip_mcp_prefix("mcp__plane-mcp-server__find_work_items") == "find_work_items"
    assert strip_mcp_prefix("list_work_items") == "list_work_items"
    assert strip_mcp_prefix("Bash") == "Bash"


def test_is_plane_mcp_tool():
    assert is_plane_mcp_tool("mcp__plane__find_work_items")
    assert is_plane_mcp_tool("mcp__plane-foo__x")
    assert not is_plane_mcp_tool("ToolSearch")
    assert not is_plane_mcp_tool("Bash")
    assert not is_plane_mcp_tool("mcp__other__tool")
    assert not is_plane_mcp_tool("find_work_items")


def test_normalize_claude_usage_real_shape():
    """F2: uncached input_tokens=10 must not be treated as run total."""
    raw, total = normalize_claude_usage(CLAUDE_JSON_RESULT)
    assert raw is not None
    assert raw["input_tokens"] == 10  # uncached-only
    assert total is not None
    assert total["input_tokens"] == 10
    assert total["cache_read_input_tokens"] == 250433
    assert total["cache_creation_input_tokens"] == 33838
    assert total["output_tokens"] == 865
    assert total["total_input_tokens_including_cache"] == 10 + 250433 + 33838
    assert total["total_cost_usd"] == 0.291
    assert total["source"] == "modelUsage"


def test_parse_claude_json_result_usage_and_cost():
    out = parse_claude_json_result(CLAUDE_JSON_RESULT)
    assert out["final_text"] == "The work item is in Todo."
    assert out["session_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert out["num_turns"] == 3
    assert out["usage"]["input_tokens"] == 10
    assert out["usage"]["total_cost_usd"] == 0.291
    assert out["usage_total"]["total_input_tokens_including_cache"] == 10 + 250433 + 33838
    assert out["calls"] == []
    assert out["stopped_reason"] == "end_turn"


def test_parse_claude_json_with_embedded_calls_splits_toolsearch():
    """F1: ToolSearch is client; only plane MCP tools remain in calls."""
    out = parse_claude_json_result(CLAUDE_JSON_WITH_CALLS)
    assert [c["tool"] for c in out["calls"]] == ["find_work_items", "get_work_item"]
    assert all(c["origin"] == "plane" for c in out["calls"])
    assert [c["tool"] for c in out["client_tool_calls"]] == ["ToolSearch"]
    assert out["calls"][0]["args"]["limit"] == 10


def test_parse_claude_transcript_calls(tmp_path: Path):
    p = tmp_path / "sess.jsonl"
    p.write_text(_transcript_lines(include_tool_search=True), encoding="utf-8")
    tagged = parse_claude_transcript_calls(p)
    plane, client = split_plane_and_client_calls(tagged)
    assert [c["tool"] for c in plane] == ["list_work_items", "get_work_item"]
    assert [c["tool"] for c in client] == ["ToolSearch"]
    assert plane[0]["args"]["project_id"] == "proj-1"


def test_parse_codex_jsonl_events():
    out = parse_codex_jsonl_events(CODEX_JSONL)
    assert out["session_id"] == "sess-codex-1"
    assert out["final_text"] == "All set."
    assert out["usage"]["input_tokens"] == 5000
    assert out["usage"]["cache_read_input_tokens"] == 1000
    # plane only in calls; exec_command is client machinery
    tools = [c["tool"] for c in out["calls"]]
    assert tools == ["find_work_items"]
    assert [c["tool"] for c in out["client_tool_calls"]] == ["exec_command"]
    assert out["stopped_reason"] == "end_turn"


# Live-captured codex v0.147.0 `codex exec --json` shape (exact four lines).
CODEX_V0147_JSONL = "\n".join(
    [
        json.dumps(
            {
                "type": "thread.started",
                "thread_id": "019ff6af-69df-7022-b353-322ffe1ececb",
            }
        ),
        json.dumps({"type": "turn.started"}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "item_0", "type": "agent_message", "text": "PING"},
            }
        ),
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 16050,
                    "cached_input_tokens": 15104,
                    "cache_write_input_tokens": 0,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 0,
                },
            }
        ),
    ]
)


def test_parse_codex_jsonl_events_v0147_schema():
    """Parser fixture: exact four-line v0.147 stream (thread_id, PING, usage)."""
    out = parse_codex_jsonl_events(CODEX_V0147_JSONL)
    assert out["session_id"] == "019ff6af-69df-7022-b353-322ffe1ececb"
    assert out["final_text"] == "PING"
    assert out["usage"]["input_tokens"] == 16050
    assert out["usage"]["cache_read_input_tokens"] == 15104
    assert out["usage"]["cache_creation_input_tokens"] == 0
    assert out["usage"]["output_tokens"] == 5


def test_parse_codex_jsonl_events_mixed_old_and_new_schema():
    """Single parser: new keys + legacy keys in one stream both contribute."""
    mixed = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-new-1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "item_0", "type": "agent_message", "text": "Hello from new"},
                }
            ),
            # Legacy call row still harvested
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "mcp__plane__list_work_items",
                        "arguments": json.dumps({"project_id": "p"}),
                    },
                }
            ),
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 10,
                        "cached_input_tokens": 0,
                        "cache_write_input_tokens": 0,
                        "output_tokens": 2,
                    },
                }
            ),
        ]
    )
    out = parse_codex_jsonl_events(mixed)
    assert out["session_id"] == "thread-new-1"
    assert "Hello from new" in out["final_text"]
    assert [c["tool"] for c in out["calls"]] == ["list_work_items"]
    assert out["usage"]["input_tokens"] == 10


def test_find_codex_rollout_exact_match_and_unmatched(tmp_path: Path, monkeypatch):
    """Exact session id match only; no newest-after-ts substitution."""
    from evals import drivers as drivers_mod

    sessions = tmp_path / ".codex" / "sessions" / "2026" / "04" / "01"
    sessions.mkdir(parents=True)
    tid = "019ff6af-69df-7022-b353-322ffe1ececb"
    # Unrelated newer session (must never be returned when looking for tid)
    other = sessions / "rollout-2026-04-01T12-00-00-other-session-zzzz.jsonl"
    other.write_text(
        json.dumps({"type": "thread.started", "thread_id": "other-session-zzzz"}) + "\n",
        encoding="utf-8",
    )
    # Exact match via filename suffix
    match = sessions / f"rollout-2026-04-01T12-00-01-{tid}.jsonl"
    match.write_text(
        json.dumps({"type": "thread.started", "thread_id": tid}) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    found = drivers_mod.find_codex_rollout(tid)
    assert found is not None
    assert tid in found.name
    # Must not return the other concurrent session
    assert "other-session" not in found.name

    assert drivers_mod.find_codex_rollout("does-not-exist-anywhere") is None
    assert drivers_mod.find_codex_rollout(None) is None


def test_find_codex_rollout_session_meta_id(tmp_path: Path, monkeypatch):
    from evals import drivers as drivers_mod

    sessions = tmp_path / ".codex" / "sessions"
    sessions.mkdir(parents=True)
    p = sessions / "rollout-meta-only.jsonl"
    p.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "sess-meta-42"}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    found = drivers_mod.find_codex_rollout("sess-meta-42")
    assert found is not None
    assert found.name == "rollout-meta-only.jsonl"


def test_codex_driver_notes_rollout_unmatched_when_no_file(tmp_path: Path, monkeypatch):
    """When thread_id is known but no rollout file matches, note codex_rollout_unmatched."""

    # Empty sessions dir under fake home
    (tmp_path / ".codex" / "sessions").mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=CODEX_V0147_JSONL, stderr="")

    driver = CodexCliDriver(runner=fake_run, use_proxy=False)
    run = driver.run_task(
        "ping",
        mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
        model=None,
        max_turns=1,
        cwd=tmp_path,
    )
    # Final text still from stdout (new schema) — never from a wrong rollout
    assert run.final_text == "PING"
    # Unmatched note only when looking for enrichment; with final_text present
    # need_rollout is false for final_text — still may note if no calls.
    # v0147 fixture has no tool calls → need_rollout True → unmatched note.
    assert "codex_rollout_unmatched" in run.notes


def test_max_turns_detection_from_num_turns():
    """When num_turns >= max_turns, driver reports hit_max_turns / max_turns stop."""
    payload = {**CLAUDE_JSON_RESULT, "num_turns": 15, "tool_calls": []}

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    driver = ClaudeCliDriver(runner=fake_run)
    # Avoid looking for a real transcript for empty calls
    run = driver.run_task(
        "do the thing",
        mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
        model="sonnet",
        max_turns=15,
        cwd=Path("/tmp"),
    )
    assert run.hit_max_turns is True
    assert run.stopped_reason == "max_turns"
    assert run.usage_scope == "run"
    assert run.usage is not None
    assert run.usage["input_tokens"] == 10  # uncached-only from real shape
    assert run.usage_total is not None
    assert run.usage_total["total_input_tokens_including_cache"] == 10 + 250433 + 33838


def test_claude_driver_falls_back_to_transcript(tmp_path: Path, monkeypatch):
    session_id = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
    payload = {
        **CLAUDE_JSON_RESULT,
        "session_id": session_id,
        "tool_calls": [],  # force transcript path
        "result": "from-json",
    }

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    # Plant transcript where find_claude_transcript looks
    munged = str(tmp_path.resolve()).replace("/", "-")
    proj = Path.home() / ".claude" / "projects" / munged
    proj.mkdir(parents=True, exist_ok=True)
    transcript = proj / f"{session_id}.jsonl"
    transcript.write_text(_transcript_lines(include_tool_search=True), encoding="utf-8")
    monkeypatch.setenv("HOME", str(Path.home()))  # keep real home for this test path

    driver = ClaudeCliDriver(runner=fake_run)
    run = driver.run_task(
        "prompt",
        mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
        model=None,
        max_turns=10,
        cwd=tmp_path,
    )
    assert run.call_source == "transcript"
    assert [c["tool"] for c in run.calls] == ["list_work_items", "get_work_item"]
    assert [c["tool"] for c in run.client_tool_calls] == ["ToolSearch"]
    assert run.final_text == "from-json"
    # cleanup planted file
    transcript.unlink(missing_ok=True)


def test_claude_driver_writes_mcp_config_and_cmd_flags(tmp_path: Path):
    seen: dict[str, Any] = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["cwd"] = kwargs.get("cwd")
        # Return minimal valid JSON
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({**CLAUDE_JSON_RESULT, "tool_calls": [], "num_turns": 1}),
            stderr="",
        )

    driver = ClaudeCliDriver(runner=fake_run, python_bin="/venv/bin/python")
    driver.run_task(
        "hello",
        mcp_env={
            "PLANE_API_KEY": "key",
            "PLANE_WORKSPACE_SLUG": "slug",
            "PLANE_BASE_URL": "https://api.example",
            "CUSTOM_SETTING": "enabled",
            "PATH": "/usr/bin",
        },
        model="sonnet",
        max_turns=7,
        cwd=tmp_path,
        system="sys",
    )
    cmd = seen["cmd"]
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "--output-format" in cmd and "json" in cmd
    assert "--mcp-config" in cmd
    assert "--max-turns" in cmd and "7" in cmd
    assert "--model" in cmd and "sonnet" in cmd
    assert "--permission-mode" in cmd and "bypassPermissions" in cmd
    assert "--strict-mcp-config" in cmd
    # mcp-config path is a temp file cleaned after run — re-check via write helper
    cfg = tmp_path / "mcp.json"
    write_claude_mcp_config(
        cfg,
        command="/venv/bin/python",
        args=["-m", "plane_mcp", "stdio"],
        env={"PLANE_API_KEY": "key"},
    )
    data = json.loads(cfg.read_text())
    assert "mcpServers" in data
    assert data["mcpServers"]["plane"]["args"] == ["-m", "plane_mcp", "stdio"]


def test_claude_driver_server_command_override(tmp_path: Path):
    """External surfaces: --server-cmd replaces the default `-m plane_mcp stdio` launch."""
    seen: dict[str, Any] = {}

    def fake_run(cmd, **kwargs):
        # Capture the mcp.json content while it still exists (temp dir).
        cfg_path = Path(cmd[cmd.index("--mcp-config") + 1])
        seen["mcp_cfg"] = json.loads(cfg_path.read_text())
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({**CLAUDE_JSON_RESULT, "tool_calls": [], "num_turns": 1}),
            stderr="",
        )

    driver = ClaudeCliDriver(
        runner=fake_run,
        server_command=["/elsewhere/.venv/bin/plane-mcp-server", "stdio", "--mode", "candidate"],
    )
    driver.run_task(
        "hello",
        mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "s", "PLANE_FOREIGN_MODE": "candidate"},
        model="sonnet",
        max_turns=3,
        cwd=tmp_path,
    )
    server = seen["mcp_cfg"]["mcpServers"]["plane"]
    # Default use_proxy=True: command is the proxy; real server follows "--".
    assert server["args"][:3] == ["-m", "evals.proxy", "--log"]
    assert "--" in server["args"]
    dash = server["args"].index("--")
    assert server["args"][dash + 1 :] == [
        "/elsewhere/.venv/bin/plane-mcp-server",
        "stdio",
        "--mode",
        "candidate",
    ]
    # Explicit foreign selection variables pass through to the child.
    assert server["env"]["PLANE_FOREIGN_MODE"] == "candidate"


def test_agent_run_dict_keeps_action_arg():
    run = AgentRun(
        calls=[
            {"tool": "work_item", "args": {"action": "create", "name": "x"}, "origin": "plane"},
            {"tool": "get_pql_reference", "args": {}, "origin": "plane"},
        ],
        final_text="done",
        usage=None,
        stopped_reason="end_turn",
    )
    d = agent_run_to_harness_dict(
        run,
        optimal=set(),
        alternate=set(),
        classify=lambda t, o, a: "out_of_set",
    )
    assert d["calls"][0]["action"] == "create"
    assert "action" not in d["calls"][1]


def test_codex_driver_parses_fake_stdout_no_live():
    def fake_run(cmd, **kwargs):
        assert cmd[0] == "codex"
        assert "exec" in cmd
        assert "--json" in cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=CODEX_JSONL, stderr="")

    driver = CodexCliDriver(runner=fake_run)  # fake runner → no allow_live needed
    run = driver.run_task(
        "do it",
        mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
        model="gpt-test",
        max_turns=5,
        cwd=Path("/tmp"),
    )
    assert run.experimental is True
    assert run.call_source == "stream"
    assert run.calls[0]["tool"] == "find_work_items"
    assert [c["tool"] for c in run.client_tool_calls] == ["exec_command"]
    assert run.usage is not None
    assert run.usage["input_tokens"] == 5000
    assert run.final_text == "All set."


def test_codex_driver_refuses_live_by_default():
    driver = CodexCliDriver()  # real subprocess.run
    with pytest.raises(RuntimeError, match="refuses live"):
        driver.run_task("x", mcp_env={}, model=None, max_turns=1)


def test_agent_run_to_harness_dict_excludes_toolsearch_from_mispicks():
    """F1: ToolSearch must not inflate out_of_set or num_calls."""
    run = AgentRun(
        calls=[
            normalize_tool_call("mcp__plane__find_work_items", {"project": "A"}),
        ],
        client_tool_calls=[
            normalize_tool_call("ToolSearch", {"query": "work items"}),
        ],
        final_text="done",
        usage={
            "input_tokens": 10,
            "output_tokens": 865,
            "cache_read_input_tokens": 250433,
            "cache_creation_input_tokens": 33838,
            "total_cost_usd": 0.29,
            "modelUsage": {
                "claude-sonnet": {
                    "inputTokens": 10,
                    "outputTokens": 865,
                    "cacheReadInputTokens": 250433,
                    "cacheCreationInputTokens": 33838,
                    "costUSD": 0.29,
                }
            },
        },
        usage_total={
            "input_tokens": 10,
            "output_tokens": 865,
            "cache_read_input_tokens": 250433,
            "cache_creation_input_tokens": 33838,
            "total_input_tokens_including_cache": 10 + 250433 + 33838,
            "total_cost_usd": 0.29,
            "source": "modelUsage",
        },
        stopped_reason="end_turn",
        usage_scope="run",
        call_source="transcript",
        hit_max_turns=False,
        wall_time_s=1.5,
    )
    out = agent_run_to_harness_dict(
        run,
        optimal={"find_work_items"},
        alternate={"get_work_item"},
        classify=classify_call,
    )
    assert out["num_calls"] == 1
    assert out["out_of_set_calls"] == 0
    assert out["calls"][0]["class"] == "optimal"
    assert out["client_tool_call_count"] == 1
    assert out["client_tool_calls"][0]["tool"] == "ToolSearch"
    # F2: cum_input_tokens null — not the misleading uncached-only 10
    assert out["cum_input_tokens"] is None
    assert out["cum_input_tokens_reason"]
    assert out["usage_total"]["total_input_tokens_including_cache"] == 10 + 250433 + 33838
    assert out["usage_per_iteration"] == []
    assert out["calls"][0]["result_tokens"] == 0
    assert out["calls"][0]["result_tokens_estimated"] is True
    assert out["result_tokens_estimated"] is True
    assert "result_tokens_skipped_reason" not in out


def test_agent_run_hit_max_maps_to_hit_max_iterations():
    run = AgentRun(
        calls=[],
        final_text="",
        usage=None,
        stopped_reason="end_turn",
        hit_max_turns=True,
        call_source="json",
    )
    out = agent_run_to_harness_dict(run, optimal=set(), alternate=set(), classify=classify_call)
    assert out["hit_max_iterations"] is True
    assert out["stop_reason"] == "max_turns"


def test_agent_run_to_harness_dict_does_not_guess_usage_total():
    """Generic row mapping must not invent usage_total from a vendor usage dict.

    Drivers own normalization (ClaudeCliDriver via normalize_claude_usage,
    CodexCliDriver builds its own). Missing usage_total stays None.
    """
    run = AgentRun(
        calls=[],
        final_text="ok",
        usage={
            "input_tokens": 5000,
            "output_tokens": 200,
            # Codex-ish shape — not Claude modelUsage. A Claude rebuild would
            # silently produce a wrong / empty total if reintroduced.
            "total_token_usage": {"input_tokens": 5000, "output_tokens": 200},
        },
        usage_total=None,
        stopped_reason="completed",
        usage_scope="run",
        call_source="stream",
    )
    out = agent_run_to_harness_dict(
        run,
        optimal=set(),
        alternate=set(),
        classify=classify_call,
    )
    assert out["usage"] == run.usage
    assert out["usage_total"] is None


def test_agent_run_payload_uses_importable_tokenizer(monkeypatch):
    class FakeEncoding:
        def encode(self, text):
            assert text == "serialized workspace result"
            return [10, 20, 30]

    class FakeTiktoken:
        @staticmethod
        def get_encoding(name):
            assert name == "cl100k_base"
            return FakeEncoding()

    monkeypatch.setitem(sys.modules, "tiktoken", FakeTiktoken)
    run = AgentRun(
        calls=[
            {
                "tool": "find_work_items",
                "args": {},
                "origin": "plane",
                "result_chars": len("serialized workspace result"),
                "result_text": "serialized workspace result",
            }
        ],
        final_text="ok",
        usage=None,
        stopped_reason="completed",
        usage_scope="run",
        call_source="proxy",
    )

    out = agent_run_to_harness_dict(
        run,
        optimal={"find_work_items"},
        alternate=set(),
        classify=classify_call,
    )

    assert out["calls"][0]["result_tokens"] == 3
    assert out["calls"][0]["result_tokens_estimated"] is False
    assert out["calls"][0]["result_token_count_method"] == "tiktoken:cl100k_base"
    assert out["result_tokens_estimated"] is False
    assert out["result_tokens_mode"] == "measured"
    assert "result_text" not in out["calls"][0]


def test_agent_run_payload_falls_back_to_shared_estimator_without_tokenizer(monkeypatch):
    monkeypatch.setitem(sys.modules, "tiktoken", None)
    text = "payload without a tokenizer"
    run = AgentRun(
        calls=[
            {
                "tool": "find_work_items",
                "args": {},
                "origin": "plane",
                "result_chars": len(text),
                "result_text": text,
            }
        ],
        final_text="ok",
        usage=None,
        stopped_reason="completed",
        usage_scope="run",
        call_source="proxy",
    )

    out = agent_run_to_harness_dict(
        run,
        optimal={"find_work_items"},
        alternate=set(),
        classify=classify_call,
    )

    assert out["calls"][0]["result_tokens"] == estimate_result_tokens(len(text))
    assert out["calls"][0]["result_tokens_estimated"] is True
    assert out["result_tokens_estimated"] is True


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------


def test_known_drivers():
    assert KNOWN_DRIVERS == {"api", "claude-cli", "codex-cli", "antigravity-cli", "opencode-cli"}


def test_get_driver_api():
    assert isinstance(get_driver("api"), ApiDriver)
    assert isinstance(get_driver("claude-cli"), ClaudeCliDriver)
    assert isinstance(get_driver("codex-cli"), CodexCliDriver)


def test_parse_args_accepts_driver():
    a = parse_args(["--driver", "claude-cli", "--dry-run"])
    assert a.driver == "claude-cli"
    b = parse_args(["--dry-run"])
    assert b.driver == "api"
    assert b.model == "standard"
    assert b.provider == "anthropic"
    assert b.record_result_payloads is False
    c = parse_args(["--driver", "claude-cli", "--record-result-payloads", "--dry-run"])
    assert c.record_result_payloads is True


def test_stdio_env_still_works_for_cli_drivers(monkeypatch):
    monkeypatch.setenv("EVAL_PLANE_API_KEY", "k")
    monkeypatch.setenv("EVAL_PLANE_WORKSPACE_SLUG", "ws")
    monkeypatch.delenv("EVAL_PLANE_BASE_URL", raising=False)
    env = stdio_server_env()
    assert env["PLANE_API_KEY"] == "k"
    assert "ANTHROPIC_API_KEY" not in env


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not owned by us
    return True


def test_run_cli_subprocess_kills_process_group_on_timeout(tmp_path: Path):
    """Timeout kills the whole process group, not just the parent (codex node→native case).

    Sticky CLI: parent spawns a grandchild in the same group that would keep
    stdout open if only the parent were killed. Assert the runner returns
    quickly and both PIDs are dead.
    """
    pidfile = tmp_path / "pids.txt"
    script = tmp_path / "sticky_cli.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import os, subprocess, sys, time
            from pathlib import Path
            pidfile = Path({str(pidfile)!r})
            # Grandchild stays in the same process group (no start_new_session).
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(9999)"],
            )
            pidfile.write_text(f"{{os.getpid()}}\\n{{child.pid}}\\n")
            # Hold our stdout open forever (simulates grandchild pipe hold).
            time.sleep(9999)
            """
        ),
        encoding="utf-8",
    )

    t0 = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as ei:
        run_cli_subprocess(
            [sys.executable, str(script)],
            timeout=1.0,
            capture_output=True,
            text=True,
        )
    elapsed = time.monotonic() - t0
    assert elapsed < 6.0, f"timeout path took {elapsed:.1f}s (unbounded communicate hang?)"
    assert getattr(ei.value, "killed_process_group", False) is True

    # Wait briefly for reaping
    deadline = time.monotonic() + 3.0
    pids: list[int] = []
    while time.monotonic() < deadline:
        if pidfile.is_file():
            pids = [int(x) for x in pidfile.read_text().splitlines() if x.strip()]
            if len(pids) == 2 and not any(_pid_alive(p) for p in pids):
                break
        time.sleep(0.05)
    assert len(pids) == 2, f"pidfile incomplete: {pidfile} {pids}"
    alive = [p for p in pids if _pid_alive(p)]
    assert not alive, f"process group members still alive: {alive}"


def test_killpg_reaps_grandchild_when_leader_already_dead(tmp_path: Path):
    """killpg(leader_pid) works after the leader is reaped (no getpgid / no proc.kill fallback).

    Simulates: leader already gone, only grandchild remains in the process group.
    """
    import signal
    from types import SimpleNamespace

    from evals.drivers import kill_process_group

    pidfile = tmp_path / "pids.txt"
    script = tmp_path / "sticky_leader.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import os, subprocess, sys, time
            from pathlib import Path
            pidfile = Path({str(pidfile)!r})
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(9999)"],
            )
            pidfile.write_text(f"{{os.getpid()}}\\n{{child.pid}}\\n")
            time.sleep(9999)
            """
        ),
        encoding="utf-8",
    )
    leader = subprocess.Popen(
        [sys.executable, str(script)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 3.0
        pids: list[int] = []
        while time.monotonic() < deadline:
            if pidfile.is_file():
                pids = [int(x) for x in pidfile.read_text().splitlines() if x.strip()]
                if len(pids) == 2:
                    break
            time.sleep(0.02)
        assert len(pids) == 2, pids
        leader_pid, child_pid = pids

        # Kill ONLY the leader (not the group) — grandchild survives in the group.
        os.kill(leader_pid, signal.SIGKILL)
        try:
            leader.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass
        assert not _pid_alive(leader_pid)
        assert _pid_alive(child_pid), "precondition: grandchild must still be alive"

        t0 = time.monotonic()
        # Direct killpg(leader_pid) — pgid == original leader pid under start_new_session.
        ok = kill_process_group(SimpleNamespace(pid=leader_pid))
        assert ok is True
        assert time.monotonic() - t0 < 3.0

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and _pid_alive(child_pid):
            time.sleep(0.05)
        assert not _pid_alive(child_pid), "grandchild survived killpg after leader death"
    finally:
        if leader.poll() is None:
            try:
                os.killpg(leader.pid, signal.SIGKILL)
            except Exception:
                leader.kill()
            try:
                leader.wait(timeout=2.0)
            except Exception:
                pass


def test_run_cli_subprocess_baseexception_kills_group(tmp_path: Path, monkeypatch):
    """Non-TimeoutExpired exceptions mid-communicate must still kill the process group."""
    import evals.drivers as drivers_mod

    pidfile = tmp_path / "pids.txt"
    script = tmp_path / "sticky.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import os, subprocess, sys, time
            from pathlib import Path
            pidfile = Path({str(pidfile)!r})
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(9999)"])
            pidfile.write_text(f"{{os.getpid()}}\\n{{child.pid}}\\n")
            time.sleep(9999)
            """
        ),
        encoding="utf-8",
    )

    real_comm = subprocess.Popen.communicate
    calls = {"n": 0}

    def boom_communicate(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            # Wait until pidfile is written so we can assert both die.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if pidfile.is_file() and len(pidfile.read_text().splitlines()) >= 2:
                    break
                time.sleep(0.02)
            raise KeyboardInterrupt("injected mid-communicate")
        return real_comm(self, *a, **k)

    monkeypatch.setattr(subprocess.Popen, "communicate", boom_communicate)

    t0 = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        run_cli_subprocess(
            [sys.executable, str(script)],
            timeout=30.0,
            capture_output=True,
            text=True,
        )
    assert time.monotonic() - t0 < 6.0

    deadline = time.monotonic() + 3.0
    pids: list[int] = []
    while time.monotonic() < deadline:
        if pidfile.is_file():
            pids = [int(x) for x in pidfile.read_text().splitlines() if x.strip()]
            if len(pids) == 2 and not any(_pid_alive(p) for p in pids):
                break
        time.sleep(0.05)
    assert len(pids) == 2
    alive = [p for p in pids if _pid_alive(p)]
    assert not alive, f"group survived BaseException path: {alive}"
    # silence unused import lint if any
    assert drivers_mod.run_cli_subprocess is run_cli_subprocess


def test_cli_driver_timeout_notes_process_group_kill(tmp_path: Path):
    """ClaudeCliDriver timeout path records timeout_killed_process_group note."""
    script = tmp_path / "slow.py"
    script.write_text(
        textwrap.dedent(
            """
            import time
            time.sleep(9999)
            """
        ),
        encoding="utf-8",
    )

    # Use real run_cli_subprocess with a tiny timeout via fake that wraps it.
    from evals.drivers import run_cli_subprocess as real_runner

    def short_timeout_runner(cmd, **kwargs):
        kwargs = dict(kwargs)
        kwargs["timeout"] = 0.5
        # Replace the CLI binary with our sticky sleeper
        return real_runner([sys.executable, str(script)], **kwargs)

    driver = ClaudeCliDriver(runner=short_timeout_runner, use_proxy=False)
    t0 = time.monotonic()
    run = driver.run_task(
        "hi",
        mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
        model="sonnet",
        max_turns=1,
        cwd=tmp_path,
    )
    assert time.monotonic() - t0 < 6.0
    assert run.stopped_reason == "timeout"
    assert "timeout_killed_process_group" in run.notes
