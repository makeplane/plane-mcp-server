"""Offline eval tests for results."""

from __future__ import annotations

from collections import deque
from contextlib import asynccontextmanager
from dataclasses import fields
from types import SimpleNamespace
from typing import Any

import pytest

from evals.drivers.api import (
    StopReason,
    ToolCall,
    ToolResult,
    ToolSpec,
    Turn,
)
from evals.drivers.api.driver import ApiDriver
from evals.evidence import TARGET_ENTITY_EVIDENCE
from evals.results import (
    AGENT_RESULT_COPY_FIELDS,
    AGENT_RESULT_OPTIONAL_IDENTITY_FIELDS,
    RESULT_SCHEMA_VERSION,
    TASK_RESULT_HARNESS_FIELDS,
    AgentRun,
    CallRecord,
    TaskResult,
    Usage,
    agent_run_to_harness_dict,
)
from evals.token_counting import estimate_result_tokens
from evals.tool_names import (
    normalize_tool_call,
)
from tests.evals.conftest import case_params


class FakeBackend:
    provider = "fake"
    model = "fake-requested"
    actual_model = "fake-actual"

    def __init__(self, turns: list[Turn]) -> None:
        self.turns = deque(turns)
        self.started: tuple[str | None, str, list[ToolSpec]] | None = None
        self.added_results: list[list[ToolResult]] = []
        self.num_turns = 0

    def start(self, system: str | None, prompt: str, tools: list[ToolSpec]) -> None:
        self.started = (system, prompt, tools)

    def next_turn(self) -> Turn:
        self.num_turns += 1
        if not self.turns:
            raise AssertionError("driver requested an unexpected backend turn")
        return self.turns.popleft()

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self.added_results.append(results)


class FakeMcpSession:
    def __init__(self, results: list[Any] | None = None) -> None:
        self.results = deque(results or [])
        self.initialized = False
        self.called: list[tuple[str, dict[str, Any]]] = []

    async def initialize(self) -> None:
        self.initialized = True

    async def list_tools(self) -> Any:
        return SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="lookup",
                    description="Look something up",
                    inputSchema={"type": "object", "properties": {"q": {"type": "string"}}},
                ),
                SimpleNamespace(
                    name="write",
                    description="Write something",
                    inputSchema={"type": "object"},
                ),
            ]
        )

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.called.append((name, arguments))
        if not self.results:
            raise AssertionError(f"no fake result left for {name}")
        return self.results.popleft()


def make_driver(backend: FakeBackend, session: FakeMcpSession) -> ApiDriver:
    @asynccontextmanager
    async def session_factory(_params):
        yield session

    return ApiDriver(
        provider="anthropic",
        backend_factory=lambda _model, _max_tokens: backend,
        mcp_session_factory=session_factory,
    )


def run_driver(driver: ApiDriver, *, max_turns: int = 5):
    return driver.run_task(
        "do it",
        {"SAFE": "1"},
        "fake-requested",
        max_turns,
        system="system",
    )


def test_api_driver_maps_every_current_row_field():
    backend = FakeBackend(
        [
            Turn(
                text="",
                tool_calls=[ToolCall("a", "lookup", {"q": "a"})],
                usage=Usage(4, 1),
                stop_reason=StopReason.TOOL_USE,
            ),
            Turn(
                text="done",
                tool_calls=[],
                usage=None,
                stop_reason=StopReason.END_TURN,
                provider_stop_reason="fake_done",
            ),
        ]
    )
    run = run_driver(make_driver(backend, FakeMcpSession([ToolResult(call_id="a", text="12345")])))
    row = agent_run_to_harness_dict(run)

    required = {
        "final_text",
        "calls",
        "num_calls",
        "errored_calls",
        "total_result_tokens",
        "usage_per_iteration",
        "cum_input_tokens",
        "wall_time_s",
        "stop_reason",
        "provider_stop_reason",
        "hit_max_iterations",
        "result_pair_mismatch",
        "token_count_failures",
    }
    assert required <= row.keys()
    assert {
        "tool",
        "args_chars",
        "result_tokens",
        "result_chars",
        "result_kind",
        "is_error",
    } <= row["calls"][0].keys()
    assert row["calls"][0]["result_chars"] == 5
    assert row["calls"][0]["result_tokens"] == estimate_result_tokens(5) == 2
    assert row["result_tokens_estimated"] is True
    assert row["provider"] == "fake"
    assert row["model"] == "fake-actual"
    assert row["requested_model"] == "fake-requested"
    assert row["provider_stop_reason"] == "fake_done"
    assert row["tool_manifest_fingerprint"]


def _agent_run_dict_keeps_action_arg():
    run = AgentRun(
        calls=[
            {"tool": "work_item", "args": {"action": "create", "name": "x"}, "origin": "plane"},
            {"tool": "get_pql_reference", "args": {}, "origin": "plane"},
        ],
        final_text="done",
        usage=None,
        stopped_reason="end_turn",
    )
    d = agent_run_to_harness_dict(run)
    assert d["calls"][0]["action"] == "create"
    assert "action" not in d["calls"][1]


def _agent_run_to_harness_dict_excludes_toolsearch_from_plane_calls():
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
    out = agent_run_to_harness_dict(run)
    assert out["num_calls"] == 1
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


def _agent_run_hit_max_maps_to_hit_max_iterations():
    run = AgentRun(
        calls=[],
        final_text="",
        usage=None,
        stopped_reason="end_turn",
        hit_max_turns=True,
        call_source="json",
    )
    out = agent_run_to_harness_dict(run)
    assert out["hit_max_iterations"] is True
    assert out["stop_reason"] == "max_turns"


def _agent_run_to_harness_dict_does_not_guess_usage_total():
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
    out = agent_run_to_harness_dict(run)
    assert out["usage"] == run.usage
    assert out["usage_total"] is None


def _agent_run_to_harness_propagates_proxy_fields():
    run = AgentRun(
        calls=[
            {
                "tool": "find_work_items",
                "args": {"q": "a"},
                "origin": "plane",
                "is_error": True,
                "result_chars": 99,
                "duration_ms": 42,
                "observed_sentinels": [TARGET_ENTITY_EVIDENCE],
            }
        ],
        final_text="x",
        usage=None,
        stopped_reason="end_turn",
        call_source="proxy",
        usage_scope="run",
        evidence_trace_available=True,
    )
    d = agent_run_to_harness_dict(run)
    assert d["calls"][0]["is_error"] is True
    assert d["calls"][0]["result_chars"] == 99
    assert d["calls"][0]["result_tokens"] == estimate_result_tokens(99)
    assert d["calls"][0]["result_tokens_estimated"] is True
    assert d["result_tokens_estimated"] is True
    assert d["calls"][0]["duration_ms"] == 42
    assert d["calls"][0]["observed_sentinels"] == [TARGET_ENTITY_EVIDENCE]
    assert d["evidence_trace_available"] is True
    assert d["errored_calls"] == 1


@pytest.mark.parametrize(
    "case",
    case_params(
        _agent_run_dict_keeps_action_arg,
        _agent_run_to_harness_dict_excludes_toolsearch_from_plane_calls,
        _agent_run_hit_max_maps_to_hit_max_iterations,
        _agent_run_to_harness_dict_does_not_guess_usage_total,
        _agent_run_to_harness_propagates_proxy_fields,
    ),
)
def test_agent_run_behaviours(case):
    case()


def test_task_result_schema_round_trip_owns_usage_shape():
    result = TaskResult(
        row_type="result",
        run_id="run-1",
        fixture_seed_id="fixture-seed-1",
        task_id="R1",
        task_fingerprint="taskhash0001",
        label="local",
        server="local",
        expected_rows=35,
        cleanup_error="RuntimeError: teardown failed",
        seeded_entity_kinds=["project", "work_item"],
        randomized_seed_namespaces=["R2.urgent_open_count"],
        calls=[
            CallRecord(
                tool="find_work_items",
                result_tokens=3,
                result_tokens_estimated=False,
                result_token_count_method="backend",
                observed_sentinels=[TARGET_ENTITY_EVIDENCE],
            )
        ],
        num_calls=1,
        evidence_trace_available=True,
        trace_integrity=False,
        trace_integrity_reason="protocol_violation",
        tool_manifest_fingerprint="manifest-sha256",
        usage_per_iteration=[Usage(10, 2, 3, 4)],
    )

    row = result.to_row()
    assert row["schema_version"] == RESULT_SCHEMA_VERSION
    assert row["row_type"] == "result"
    assert row["run_id"] == "run-1"
    assert row["fixture_seed_id"] == "fixture-seed-1"
    assert row["task_fingerprint"] == "taskhash0001"
    assert row["label"] == "local"
    assert row["server"] == "local"
    assert row["expected_rows"] == 35
    assert row["cleanup_error"] == "RuntimeError: teardown failed"
    assert row["seeded_entity_kinds"] == ["project", "work_item"]
    assert row["randomized_seed_namespaces"] == ["R2.urgent_open_count"]
    assert row["usage_per_iteration"] == [{"in": 10, "out": 2, "cache_read": 3, "cache_write": 4}]
    loaded = TaskResult.from_row(row)
    assert loaded.row_type == "result"
    assert loaded.run_id == "run-1"
    assert loaded.fixture_seed_id == "fixture-seed-1"
    assert loaded.task_fingerprint == "taskhash0001"
    assert loaded.calls[0].tool == "find_work_items"
    assert loaded.calls[0].observed_sentinels == [TARGET_ENTITY_EVIDENCE]
    assert loaded.evidence_trace_available is True
    assert loaded.trace_integrity is False
    assert loaded.trace_integrity_reason == "protocol_violation"
    assert loaded.tool_manifest_fingerprint == "manifest-sha256"
    assert loaded.expected_rows == 35
    assert loaded.cleanup_error == "RuntimeError: teardown failed"
    assert loaded.seeded_entity_kinds == result.seeded_entity_kinds
    assert loaded.randomized_seed_namespaces == result.randomized_seed_namespaces
    assert loaded.usage_per_iteration == [Usage(10, 2, 3, 4)]


def test_apply_agent_result_reflection_parity_and_skipped_reason_copy():
    declared = {field.name for field in fields(TaskResult)}
    copied = set(AGENT_RESULT_COPY_FIELDS)
    optional_identity = set(AGENT_RESULT_OPTIONAL_IDENTITY_FIELDS)
    harness_owned = set(TASK_RESULT_HARNESS_FIELDS)

    assert not (copied & optional_identity or copied & harness_owned or optional_identity & harness_owned)
    assert declared == copied | optional_identity | harness_owned

    row = TaskResult(task_id="R1", result_tokens_skipped_reason=None)
    agent = TaskResult(task_id="must-not-replace", result_tokens_skipped_reason="payload recording disabled")
    row.apply_agent_result(agent)

    assert row.task_id == "R1"
    assert row.result_tokens_skipped_reason == "payload recording disabled"
