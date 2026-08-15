"""Offline eval tests for vendors."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from evals.drivers import (
    KNOWN_DRIVERS,
    AntigravityCliDriver,
    ApiDriver,
    ClaudeCliDriver,
    CodexCliDriver,
    OpencodeCliDriver,
    get_driver,
    normalize_claude_usage,
    parse_claude_json_result,
    parse_claude_transcript_calls,
    parse_codex_jsonl_events,
    prepare_antigravity_fake_home,
    write_antigravity_mcp_config,
    write_claude_mcp_config,
    write_opencode_mcp_config,
)
from evals.tool_names import (
    split_plane_and_client_calls,
)
from tests.evals.conftest import case_params

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

REPO = Path(__file__).resolve().parents[3]


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


def _parse_claude_json_result_usage_and_cost(_tmp_path):
    out = parse_claude_json_result(CLAUDE_JSON_RESULT)
    assert out["final_text"] == "The work item is in Todo."
    assert out["session_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert out["num_turns"] == 3
    assert out["usage"]["input_tokens"] == 10
    assert out["usage"]["total_cost_usd"] == 0.291
    assert out["usage_total"]["total_input_tokens_including_cache"] == 10 + 250433 + 33838
    assert out["calls"] == []
    assert out["stopped_reason"] == "end_turn"


def _parse_claude_json_with_embedded_calls_splits_toolsearch(_tmp_path):
    out = parse_claude_json_result(CLAUDE_JSON_WITH_CALLS)
    assert [c["tool"] for c in out["calls"]] == ["find_work_items", "get_work_item"]
    assert all(c["origin"] == "plane" for c in out["calls"])
    assert [c["tool"] for c in out["client_tool_calls"]] == ["ToolSearch"]
    assert out["calls"][0]["args"]["limit"] == 10


def _parse_claude_json_preserves_error_subtype(_tmp_path):
    out = parse_claude_json_result(
        {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "result": "x",
            "session_id": "s",
            "num_turns": 1,
        }
    )
    assert out["stopped_reason"] == "error_during_execution"


def _parse_claude_transcript_calls(tmp_path):
    p = tmp_path / "sess.jsonl"
    p.write_text(_transcript_lines(include_tool_search=True), encoding="utf-8")
    tagged = parse_claude_transcript_calls(p)
    plane, client = split_plane_and_client_calls(tagged)
    assert [c["tool"] for c in plane] == ["list_work_items", "get_work_item"]
    assert [c["tool"] for c in client] == ["ToolSearch"]
    assert plane[0]["args"]["project_id"] == "proj-1"


def _parse_codex_jsonl_events(_tmp_path):
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


def _parse_codex_jsonl_events_v0147_schema(_tmp_path):
    out = parse_codex_jsonl_events(CODEX_V0147_JSONL)
    assert out["session_id"] == "019ff6af-69df-7022-b353-322ffe1ececb"
    assert out["final_text"] == "PING"
    assert out["usage"]["input_tokens"] == 16050
    assert out["usage"]["cache_read_input_tokens"] == 15104
    assert out["usage"]["cache_creation_input_tokens"] == 0
    assert out["usage"]["output_tokens"] == 5


def _parse_codex_jsonl_events_mixed_old_and_new_schema(_tmp_path):
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


@pytest.mark.parametrize(
    "case",
    case_params(
        _parse_claude_json_result_usage_and_cost,
        _parse_claude_json_with_embedded_calls_splits_toolsearch,
        _parse_claude_json_preserves_error_subtype,
        _parse_claude_transcript_calls,
        _parse_codex_jsonl_events,
        _parse_codex_jsonl_events_v0147_schema,
        _parse_codex_jsonl_events_mixed_old_and_new_schema,
    ),
)
def test_parse_behaviours(case, tmp_path):
    case(tmp_path)


def _find_codex_rollout_exact_match_and_unmatched(tmp_path, monkeypatch):
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


def _find_codex_rollout_session_meta_id(tmp_path, monkeypatch):
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


@pytest.mark.parametrize(
    "case",
    case_params(_find_codex_rollout_exact_match_and_unmatched, _find_codex_rollout_session_meta_id),
)
def test_find_behaviours(case, tmp_path, monkeypatch):
    case(tmp_path, monkeypatch)


def _codex_driver_notes_rollout_unmatched_when_no_file(tmp_path, monkeypatch):
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


def _codex_driver_parses_fake_stdout_no_live(_tmp_path, _monkeypatch):
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


def _codex_driver_refuses_live_by_default(_tmp_path, _monkeypatch):
    driver = CodexCliDriver()  # real subprocess.run
    with pytest.raises(RuntimeError, match="refuses live"):
        driver.run_task("x", mcp_env={}, model=None, max_turns=1)


@pytest.mark.parametrize(
    "case",
    case_params(
        _codex_driver_notes_rollout_unmatched_when_no_file,
        _codex_driver_parses_fake_stdout_no_live,
        _codex_driver_refuses_live_by_default,
    ),
)
def test_codex_behaviours(case, tmp_path, monkeypatch):
    case(tmp_path, monkeypatch)


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


def _claude_driver_falls_back_to_transcript(tmp_path, monkeypatch):
    session_id = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
    payload = {
        **CLAUDE_JSON_RESULT,
        "session_id": session_id,
        "tool_calls": [],  # force transcript path
        "result": "from-json",
    }

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    # Keep transcript discovery isolated from the developer's real home.
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    munged = str(tmp_path.resolve()).replace("/", "-")
    proj = Path.home() / ".claude" / "projects" / munged
    proj.mkdir(parents=True, exist_ok=True)
    transcript = proj / f"{session_id}.jsonl"
    transcript.write_text(_transcript_lines(include_tool_search=True), encoding="utf-8")

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


def _claude_driver_writes_mcp_config_and_cmd_flags(tmp_path, _monkeypatch):
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


def _claude_driver_server_command_override(tmp_path, _monkeypatch):
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


def _claude_driver_timeout_returns_agent_run_not_raise(_tmp_path, _monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout") or 120)

    driver = ClaudeCliDriver(runner=fake_run)
    run = driver.run_task(
        "hello",
        mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
        model="sonnet",
        max_turns=2,
        cwd=Path("/tmp"),
    )
    assert run.stopped_reason == "timeout"
    assert run.calls == []
    assert any("timeout after" in n for n in run.notes)


def _claude_driver_json_parse_failure_raises_for_infra_cli(_tmp_path, _monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="not-json", stderr="boom")

    driver = ClaudeCliDriver(runner=fake_run)
    with pytest.raises(RuntimeError, match="claude cli failed"):
        driver.run_task(
            "hello",
            mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
            model=None,
            max_turns=1,
            cwd=Path("/tmp"),
        )


def _claude_driver_uses_proxy_in_mcp_config(tmp_path, _monkeypatch):
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        cfg = Path(cmd[cmd.index("--mcp-config") + 1])
        seen["mcp"] = json.loads(cfg.read_text())
        # Leave empty sidecar (proxy not really run under fake runner).
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "done",
                    "session_id": "s",
                    "num_turns": 1,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            ),
            stderr="",
        )

    driver = ClaudeCliDriver(runner=fake_run, use_proxy=True, python_bin="/venv/bin/python")
    run = driver.run_task(
        "hi",
        mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
        model="sonnet",
        max_turns=3,
        cwd=tmp_path,
    )
    server = seen["mcp"]["mcpServers"]["plane"]
    assert server["command"] == "/venv/bin/python"
    assert server["args"][0:3] == ["-m", "evals.proxy", "--log"]
    assert "--" in server["args"]
    assert "plane_mcp" in server["args"]
    assert "proxy_sidecar_empty" in run.notes


def _claude_driver_proxy_disabled_no_wrap(tmp_path, _monkeypatch):
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        cfg = Path(cmd[cmd.index("--mcp-config") + 1])
        seen["mcp"] = json.loads(cfg.read_text())
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "ok",
                    "session_id": "s",
                    "num_turns": 1,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            ),
            stderr="",
        )

    driver = ClaudeCliDriver(runner=fake_run, use_proxy=False, python_bin="/venv/bin/python")
    driver.run_task(
        "hi",
        mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
        model=None,
        max_turns=1,
        cwd=tmp_path,
    )
    server = seen["mcp"]["mcpServers"]["plane"]
    assert server["args"] == ["-m", "plane_mcp", "stdio"]


def _claude_mcp_env_has_pythonpath_when_proxied(tmp_path, _monkeypatch):
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        cfg = Path(cmd[cmd.index("--mcp-config") + 1])
        seen["env"] = json.loads(cfg.read_text())["mcpServers"]["plane"]["env"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "ok",
                    "session_id": "s",
                    "num_turns": 1,
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            ),
            stderr="",
        )

    ClaudeCliDriver(runner=fake_run, use_proxy=True, python_bin=sys.executable).run_task(
        "hi",
        mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
        model="sonnet",
        max_turns=1,
        cwd=tmp_path,
    )
    assert str(REPO) in seen["env"].get("PYTHONPATH", "")


_CLAUDE_CASES = case_params(
    _claude_driver_falls_back_to_transcript,
    _claude_driver_writes_mcp_config_and_cmd_flags,
    _claude_driver_server_command_override,
    _claude_driver_timeout_returns_agent_run_not_raise,
    _claude_driver_json_parse_failure_raises_for_infra_cli,
    _claude_driver_uses_proxy_in_mcp_config,
    _claude_driver_proxy_disabled_no_wrap,
    _claude_mcp_env_has_pythonpath_when_proxied,
)


@pytest.mark.parametrize("case", _CLAUDE_CASES)
def test_claude_behaviours(case, tmp_path, monkeypatch):
    case(tmp_path, monkeypatch)


def _known_drivers():
    assert KNOWN_DRIVERS == {"api", "claude-cli", "codex-cli", "antigravity-cli", "opencode-cli"}


def _known_drivers_and_get_driver():
    assert "antigravity-cli" in KNOWN_DRIVERS
    assert "opencode-cli" in KNOWN_DRIVERS
    assert isinstance(get_driver("antigravity-cli"), AntigravityCliDriver)
    assert isinstance(get_driver("opencode-cli"), OpencodeCliDriver)


@pytest.mark.parametrize(
    "case",
    case_params(_known_drivers, _known_drivers_and_get_driver),
)
def test_known_drivers_behaviours(case):
    case()


def test_get_driver_api():
    assert isinstance(get_driver("api"), ApiDriver)
    assert isinstance(get_driver("claude-cli"), ClaudeCliDriver)
    assert isinstance(get_driver("codex-cli"), CodexCliDriver)


def _antigravity_driver_writes_mcp_config_under_isolated_home(tmp_path):
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        env = kwargs.get("env") or {}
        seen["env"] = env
        home = env.get("HOME")
        if home:
            cfg = Path(home) / ".gemini" / "config" / "mcp_config.json"
            seen["mcp_cfg"] = json.loads(cfg.read_text()) if cfg.is_file() else None
        return subprocess.CompletedProcess(cmd, 0, stdout='{"result":"hi"}', stderr="")

    driver = AntigravityCliDriver(runner=fake_run, use_proxy=True, python_bin=sys.executable)
    run = driver.run_task(
        "do it",
        mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws", "PATH": "/bin"},
        model="gemini-2.5",
        max_turns=5,
        cwd=tmp_path,
    )
    assert seen["cmd"][0] == "agy"
    assert "-p" in seen["cmd"]
    assert "--output-format" in seen["cmd"]
    assert "json" in seen["cmd"]
    assert "--model" in seen["cmd"] and "gemini-2.5" in seen["cmd"]
    assert "no_turn_cap" in run.notes
    assert seen.get("mcp_cfg") is not None
    assert "mcpServers" in seen["mcp_cfg"]
    assert "evals.proxy" in " ".join(seen["mcp_cfg"]["mcpServers"]["plane"]["args"])


def _antigravity_fallback_runner_timeout_harvests(tmp_path):
    call_row = {
        "tool": "g_tool",
        "args": {},
        "is_error": False,
        "result_chars": 1,
        "duration_ms": 1,
        "seq": 1,
    }
    meta = {
        "row_type": "proxy_meta",
        "relayed_lines": 1,
        "unparsed_lines": 0,
        "unmatched_responses": 0,
        "notifications": 0,
        "pending_left": 0,
        "child_killed": False,
    }

    def fake_run(cmd, **kwargs):
        run_env = kwargs.get("env") or {}
        home = run_env.get("HOME")
        if home:
            # First attempt includes env= — plant sidecar from dual-written mcp config,
            # then reject env so the driver retries without it.
            for rel in (
                Path(home) / ".gemini" / "config" / "mcp_config.json",
                Path(home) / ".gemini" / "antigravity-cli" / "mcp_config.json",
            ):
                if rel.is_file():
                    cfg = json.loads(rel.read_text())
                    args = cfg["mcpServers"]["plane"]["args"]
                    side = Path(args[args.index("--log") + 1])
                    side.write_text(
                        "\n".join(json.dumps(r) for r in (call_row, meta)) + "\n",
                        encoding="utf-8",
                    )
                    break
            raise TypeError("runner does not accept env=")
        # Fallback call (no env) times out — outer except must still harvest.
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

    driver = AntigravityCliDriver(runner=fake_run, use_proxy=True, python_bin=sys.executable)
    run = driver.run_task(
        "hi",
        mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
        model=None,
        max_turns=1,
        cwd=tmp_path,
    )
    assert run.stopped_reason == "timeout"
    assert run.call_source == "proxy"
    assert len(run.calls) == 1
    assert run.calls[0]["tool"] == "g_tool"


@pytest.mark.parametrize(
    "case",
    case_params(
        _antigravity_driver_writes_mcp_config_under_isolated_home,
        _antigravity_fallback_runner_timeout_harvests,
    ),
)
def test_antigravity_behaviours(case, tmp_path):
    case(tmp_path)


def _write_antigravity_mcp_config_shape(tmp_path):
    p = tmp_path / "mcp_config.json"
    write_antigravity_mcp_config(p, command="python", args=["-m", "x"], env={"A": "1"})
    data = json.loads(p.read_text())
    assert data["mcpServers"]["plane"]["command"] == "python"
    assert data["mcpServers"]["plane"]["env"]["A"] == "1"


def _write_opencode_mcp_config_shape(tmp_path):
    p = tmp_path / "opencode.json"
    write_opencode_mcp_config(p, command=["py", "-m", "plane_mcp", "stdio"], env={"K": "V"})
    data = json.loads(p.read_text())
    assert data["mcp"]["plane"]["command"][0] == "py"
    assert data["mcp"]["plane"]["environment"]["K"] == "V"


@pytest.mark.parametrize(
    "case",
    case_params(_write_antigravity_mcp_config_shape, _write_opencode_mcp_config_shape),
)
def test_write_behaviours(case, tmp_path):
    case(tmp_path)


def test_opencode_driver_writes_project_config(tmp_path: Path):
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        cwd = kwargs.get("cwd")
        seen["cwd"] = cwd
        cfg = Path(cwd) / "opencode.json" if cwd else None
        seen["opencode_cfg"] = json.loads(cfg.read_text()) if cfg and cfg.is_file() else None
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    driver = OpencodeCliDriver(runner=fake_run, use_proxy=True, python_bin=sys.executable)
    run = driver.run_task(
        "hello",
        mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
        model="openai/gpt-test",
        max_turns=4,
        cwd=tmp_path,
    )
    assert seen["cmd"][0] == "opencode"
    assert "run" in seen["cmd"]
    assert "--format" in seen["cmd"] and "json" in seen["cmd"]
    assert "-m" in seen["cmd"] and "openai/gpt-test" in seen["cmd"]
    assert "no_turn_cap" in run.notes
    data = seen["opencode_cfg"]
    assert data is not None
    assert data["mcp"]["plane"]["type"] == "local"
    assert "evals.proxy" in " ".join(data["mcp"]["plane"]["command"])


def test_prepare_antigravity_fake_home_dual_write_and_auth_only(tmp_path: Path):
    real_home = tmp_path / "real"
    cli = real_home / ".gemini" / "antigravity-cli"
    cli.mkdir(parents=True)
    token_path = cli / "antigravity-oauth-token"
    token_path.write_text("secret", encoding="utf-8")
    # Snapshot real home before setup — must be byte-identical after.
    before = {p.relative_to(real_home): p.read_bytes() for p in real_home.rglob("*") if p.is_file()}

    fake = tmp_path / "fake"
    prepare_antigravity_fake_home(
        fake,
        command="python",
        args=["-m", "evals.proxy", "--log", "s", "--", "x"],
        env={"PLANE_API_KEY": "k"},
        real_home=real_home,
    )
    p1 = fake / ".gemini" / "config" / "mcp_config.json"
    p2 = fake / ".gemini" / "antigravity-cli" / "mcp_config.json"
    assert p1.is_file() and p2.is_file()
    fake_cli = fake / ".gemini" / "antigravity-cli"
    assert fake_cli.is_dir() and not fake_cli.is_symlink()
    # Auth artifact is a plain COPY — never a symlink (no write-through path).
    token = fake_cli / "antigravity-oauth-token"
    assert token.is_file() and not token.is_symlink()
    assert token.read_text(encoding="utf-8") == "secret"
    # Writing the fake token must not mutate the real one.
    token.write_text("mutated", encoding="utf-8")
    assert token_path.read_text(encoding="utf-8") == "secret"
    # mcp_config is a real file in the fake tree, not inside real home.
    assert not (cli / "mcp_config.json").exists()
    data = json.loads(p1.read_text())
    assert data["mcpServers"]["plane"]["command"] == "python"
    # Real home byte-for-byte untouched (including oauth token).
    after = {p.relative_to(real_home): p.read_bytes() for p in real_home.rglob("*") if p.is_file()}
    assert after == before
