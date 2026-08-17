"""Offline eval tests for api driver."""

from __future__ import annotations

import copy
from collections import deque
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from evals.drivers.api import (
    KNOWN_API_PROVIDERS,
    AnthropicBackend,
    OpenAIBackend,
    StopReason,
    ToolCall,
    ToolResult,
    ToolSpec,
    Turn,
    UnmappedModelTierError,
    Usage,
    register_backend,
    resolve_backend_model,
    unregister_backend,
)
from evals.drivers.api.driver import ApiDriver
from evals.evidence import TARGET_ENTITY_EVIDENCE
from evals.token_counting import estimate_result_tokens
from evals.tool_manifest import tool_manifest_fingerprint
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
    def __init__(self, results: list[Any] | None = None, tool_pages: list[Any] | None = None) -> None:
        self.results = deque(results or [])
        self.tool_pages = deque(tool_pages or [])
        self.initialized = False
        self.called: list[tuple[str, dict[str, Any]]] = []
        self.list_cursors: list[str | None] = []

    async def initialize(self) -> None:
        self.initialized = True

    async def list_tools(self, cursor: str | None = None) -> Any:
        self.list_cursors.append(cursor)
        if self.tool_pages:
            return self.tool_pages.popleft()
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


def run_driver(
    driver: ApiDriver,
    *,
    max_turns: int = 5,
    evidence_sentinels=None,
    evidence_targets=None,
    evidence_aggregates=None,
):
    return driver.run_task(
        "do it",
        {"SAFE": "1"},
        "fake-requested",
        max_turns,
        system="system",
        evidence_sentinels=evidence_sentinels,
        evidence_targets=evidence_targets,
        evidence_aggregates=evidence_aggregates,
    )


class FakeAnthropicMessages:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = deque(responses)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        return self.responses.popleft()


class FakeOpenAICompletions:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = deque(responses)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        return self.responses.popleft()


def test_registered_third_party_backend_runs_without_driver_changes():
    created: list[FakeBackend] = []

    class DummyBackend(FakeBackend):
        provider = "dummy"

        def __init__(self, model: str, *, max_tokens: int, client: Any | None = None) -> None:
            super().__init__(
                [
                    Turn(
                        text=f"done in {max_tokens}",
                        tool_calls=[],
                        usage=Usage(input_tokens=7, output_tokens=2),
                        stop_reason=StopReason.END_TURN,
                        provider_stop_reason="dummy_complete",
                    )
                ]
            )
            self.model = model
            self.actual_model = f"{model}-actual"
            self.client = client
            created.append(self)

    session = FakeMcpSession()

    @asynccontextmanager
    async def session_factory(_params):
        yield session

    register_backend("dummy", DummyBackend)
    try:
        assert "dummy" in KNOWN_API_PROVIDERS
        with pytest.raises(UnmappedModelTierError, match=r"standard.*explicit model ID"):
            resolve_backend_model("dummy", "standard")
        assert resolve_backend_model("dummy", "dummy-explicit") == "dummy-explicit"
        driver = ApiDriver(
            provider="dummy",
            client=object(),
            mcp_session_factory=session_factory,
            max_tokens=99,
        )
        run = driver.run_task("do it", {"SAFE": "1"}, "dummy-model", 1)
    finally:
        unregister_backend("dummy")

    assert created[0].started is not None
    assert run.final_text == "done in 99"
    assert run.provider == "dummy"
    assert run.model == "dummy-model-actual"
    assert run.stopped_reason == "end_turn"
    assert run.provider_stop_reason == "dummy_complete"
    assert run.usage_per_iteration == [Usage(7, 2, 0, 0)]


def _api_driver_multi_turn_tool_loop_and_usage_accumulation():
    backend = FakeBackend(
        [
            Turn(
                text="",
                tool_calls=[ToolCall("call-1", "lookup", {"q": "one"})],
                usage=Usage(10, 2, 3, 1),
                stop_reason=StopReason.TOOL_USE,
            ),
            Turn(
                text="",
                tool_calls=[ToolCall("call-2", "lookup", {"q": "two"})],
                usage=Usage(20, 4, 6, 0),
                stop_reason=StopReason.TOOL_USE,
            ),
            Turn(
                text="done",
                tool_calls=[],
                usage=Usage(30, 6, 9, 0),
                stop_reason=StopReason.END_TURN,
                provider_stop_reason="fake_done",
            ),
        ]
    )
    session = FakeMcpSession(
        [
            {"content": [{"type": "text", "text": "first result"}], "isError": False},
            {"content": [{"type": "text", "text": "second"}], "isError": True},
        ]
    )

    run = run_driver(make_driver(backend, session))

    assert session.initialized is True
    assert session.called == [("lookup", {"q": "one"}), ("lookup", {"q": "two"})]
    assert backend.started is not None
    assert [tool.name for tool in backend.started[2]] == ["lookup", "write"]
    assert [[result.call_id for result in turn] for turn in backend.added_results] == [["call-1"], ["call-2"]]
    assert run.final_text == "done"
    assert run.stopped_reason == "end_turn"
    assert run.cum_input_tokens == 60
    assert run.usage_per_iteration == [Usage(10, 2, 3, 1), Usage(20, 4, 6, 0), Usage(30, 6, 9, 0)]
    assert [call["result_chars"] for call in run.calls] == [len("first result"), len("second")]
    assert [call["result_tokens"] for call in run.calls] == [
        estimate_result_tokens(len("first result")),
        estimate_result_tokens(len("second")),
    ]
    assert [call["is_error"] for call in run.calls] == [False, True]
    assert run.result_tokens_estimated is True
    assert run.token_count_failures == 0
    assert run.provider == "fake"
    assert run.model == "fake-actual"
    assert run.provider_stop_reason == "fake_done"


def _api_driver_refusal_records_calls_but_executes_nothing():
    backend = FakeBackend(
        [
            Turn(
                text="declined",
                tool_calls=[ToolCall("write-1", "write", {"value": "x"})],
                usage=Usage(1, 1),
                stop_reason=StopReason.REFUSAL,
            )
        ]
    )
    session = FakeMcpSession()

    run = run_driver(make_driver(backend, session))

    assert [call["tool"] for call in run.calls] == ["write"]
    assert session.called == []
    assert backend.added_results == []
    assert run.stopped_reason == "refusal"
    assert run.hit_max_turns is False


def _api_driver_pairs_results_by_id_not_ordinal():
    backend = FakeBackend(
        [
            Turn(
                text="",
                tool_calls=[ToolCall("a", "lookup", {"q": "a"}), ToolCall("b", "lookup", {"q": "b"})],
                usage=None,
                stop_reason=StopReason.TOOL_USE,
            ),
            Turn(text="done", tool_calls=[], usage=None, stop_reason=StopReason.END_TURN),
        ]
    )
    # The fake session deliberately returns tagged results in reverse ID order.
    session = FakeMcpSession(
        [
            ToolResult(call_id="b", text="BBBB"),
            ToolResult(call_id="a", text="A"),
        ]
    )

    run = run_driver(make_driver(backend, session))

    assert [call["result_chars"] for call in run.calls] == [1, 4]
    assert run.result_pair_mismatch is False


def _api_driver_flags_result_id_mismatch():
    backend = FakeBackend(
        [
            Turn(
                text="",
                tool_calls=[ToolCall("a", "lookup", {"q": "a"}), ToolCall("b", "lookup", {"q": "b"})],
                usage=None,
                stop_reason=StopReason.TOOL_USE,
            ),
            Turn(
                text="done",
                tool_calls=[],
                usage=None,
                stop_reason=StopReason.END_TURN,
            ),
        ]
    )
    session = FakeMcpSession(
        [
            ToolResult(call_id="b", text="BBBB"),
            ToolResult(call_id="unknown", text="lost"),
        ]
    )

    run = run_driver(make_driver(backend, session))

    assert run.result_pair_mismatch is True
    assert run.trace_integrity is False
    assert run.trace_integrity_reason == "result_pair_mismatch"
    assert [call["result_chars"] for call in run.calls] == [0, 4]


def test_api_driver_aggregates_every_tools_list_page_before_fingerprinting():
    backend = FakeBackend([Turn(text="done", tool_calls=[], usage=None, stop_reason=StopReason.END_TURN)])
    tools = [
        {"name": "alpha", "inputSchema": {"type": "object"}},
        {"name": "beta", "inputSchema": {"type": "object"}},
    ]
    session = FakeMcpSession(
        tool_pages=[
            {"tools": [tools[0]], "nextCursor": "page-2"},
            {"tools": [tools[1]]},
        ]
    )

    run = run_driver(make_driver(backend, session))

    assert session.list_cursors == [None, "page-2"]
    assert run.tool_manifest_fingerprint == tool_manifest_fingerprint(tools)
    assert backend.started is not None
    assert [tool.name for tool in backend.started[2]] == ["alpha", "beta"]


def test_api_driver_invalidates_manifest_after_tools_list_changed(monkeypatch):
    backend = FakeBackend([Turn(text="done", tool_calls=[], usage=None, stop_reason=StopReason.END_TURN)])
    session = FakeMcpSession()

    @asynccontextmanager
    async def fake_stdio_client(_params):
        yield object(), object()

    class FakeClientSessionContext:
        def __init__(self, _read, _write, *, message_handler):
            self.message_handler = message_handler

        async def __aenter__(self):
            return session

        async def __aexit__(self, *_args):
            await self.message_handler(SimpleNamespace(root=SimpleNamespace(method="notifications/tools/list_changed")))
            return None

    monkeypatch.setattr("evals.drivers.api.driver.stdio_client", fake_stdio_client)
    monkeypatch.setattr("evals.drivers.api.driver.ClientSession", FakeClientSessionContext)
    driver = ApiDriver(
        provider="anthropic",
        backend_factory=lambda _model, _max_tokens: backend,
        server_command=["fake-server"],
    )

    run = run_driver(driver)

    assert run.tool_manifest_fingerprint is None


def _api_driver_iteration_cap_only_flags_mid_tool_loop():
    backend = FakeBackend(
        [
            Turn(
                text="",
                tool_calls=[ToolCall("a", "lookup", {"q": "a"})],
                usage=None,
                stop_reason=StopReason.TOOL_USE,
            ),
            Turn(
                text="must not be read",
                tool_calls=[],
                usage=None,
                stop_reason=StopReason.END_TURN,
            ),
        ]
    )
    session = FakeMcpSession([ToolResult(call_id="a", text="result")])

    run = run_driver(make_driver(backend, session), max_turns=1)

    assert session.called == [("lookup", {"q": "a"})]
    assert len(backend.added_results) == 1
    assert backend.num_turns == 1
    assert run.hit_max_turns is True
    assert run.stopped_reason == "tool_use"


def _api_driver_clean_end_on_last_iteration_is_not_capped():
    backend = FakeBackend([Turn(text="done", tool_calls=[], usage=None, stop_reason=StopReason.END_TURN)])

    run = run_driver(make_driver(backend, FakeMcpSession()), max_turns=1)

    assert run.hit_max_turns is False
    assert run.stopped_reason == "end_turn"


def _api_driver_uses_optional_backend_token_counter():
    backend = FakeBackend(
        [
            Turn(
                text="",
                tool_calls=[ToolCall("a", "lookup", {"q": "a"})],
                usage=None,
                stop_reason=StopReason.TOOL_USE,
            ),
            Turn(text="done", tool_calls=[], usage=None, stop_reason=StopReason.END_TURN),
        ]
    )
    backend.count_tokens = lambda text: len(text) + 10

    run = run_driver(make_driver(backend, FakeMcpSession([ToolResult(call_id="a", text="abc")])))

    assert run.calls[0]["result_tokens"] == 13
    assert run.result_tokens_estimated is False
    assert run.token_count_failures == 0


def _api_driver_records_only_matching_evidence_labels():
    sentinel = "hidden-target-fact-2f81a0cd"
    backend = FakeBackend(
        [
            Turn(
                text="",
                tool_calls=[ToolCall("a", "lookup", {"q": "unrelated"})],
                usage=None,
                stop_reason=StopReason.TOOL_USE,
            ),
            Turn(
                text="",
                tool_calls=[ToolCall("b", "lookup", {"q": "target"})],
                usage=None,
                stop_reason=StopReason.TOOL_USE,
            ),
            Turn(text="done", tool_calls=[], usage=None, stop_reason=StopReason.END_TURN),
        ]
    )
    session = FakeMcpSession(
        [
            ToolResult(call_id="a", text=f"state={sentinel}"),
            ToolResult(call_id="b", text="no seeded value in this response"),
        ]
    )

    run = run_driver(
        make_driver(backend, session),
        evidence_sentinels={TARGET_ENTITY_EVIDENCE: [sentinel]},
    )

    # The sentinel only exists inside Plane, so the response carrying it is proof of
    # surface use even though its request named an unrelated entity. The response without
    # it is not evidence, whatever it was asked about.
    assert run.evidence_trace_available is True
    assert run.calls[0]["observed_sentinels"] == [TARGET_ENTITY_EVIDENCE]
    assert run.calls[1]["observed_sentinels"] == []
    assert "result_text" not in run.calls[1]


def test_api_driver_records_only_exact_target_bound_aggregate_evidence():
    backend = FakeBackend(
        [
            Turn(
                text="",
                tool_calls=[ToolCall("a", "count_work_items", {"pql": 'project = "project-other"'})],
                usage=None,
                stop_reason=StopReason.TOOL_USE,
            ),
            Turn(
                text="",
                tool_calls=[ToolCall("b", "count_work_items", {"pql": 'project = "project-1"'})],
                usage=None,
                stop_reason=StopReason.TOOL_USE,
            ),
            Turn(
                text="",
                tool_calls=[ToolCall("c", "count_work_items", {"pql": 'project = "project-1"'})],
                usage=None,
                stop_reason=StopReason.TOOL_USE,
            ),
            Turn(
                text="",
                tool_calls=[ToolCall("d", "count_work_items", {"group_by": "project_id"})],
                usage=None,
                stop_reason=StopReason.TOOL_USE,
            ),
            Turn(
                text="",
                tool_calls=[
                    ToolCall(
                        "e",
                        "count_work_items",
                        {"group_by": "project_id", "project_ids": ["project-1", "project-2"]},
                    )
                ],
                usage=None,
                stop_reason=StopReason.TOOL_USE,
            ),
            Turn(text="done", tool_calls=[], usage=None, stop_reason=StopReason.END_TURN),
        ]
    )
    session = FakeMcpSession(
        [
            ToolResult(call_id="a", text='{"total_count": 4}'),
            ToolResult(call_id="b", text='{"total_count": 3}'),
            ToolResult(call_id="c", text='{"total_count": 4}'),
            ToolResult(
                call_id="d",
                text=('{"grouped_counts": {"project-1": {"count": 2}, "project-2": {"count": 5}}}'),
            ),
            ToolResult(
                call_id="e",
                text=('{"grouped_counts": {"project-1": {"count": 2}, "project-2": {"count": 5}}}'),
            ),
        ]
    )

    run = run_driver(
        make_driver(backend, session),
        evidence_targets={TARGET_ENTITY_EVIDENCE: ["project-1", "project-2"]},
        evidence_aggregates={
            TARGET_ENTITY_EVIDENCE: [
                {"kind": "total_count", "value": 4},
                {"kind": "grouped_counts", "values": {"project-1": 2, "project-2": 5}},
            ]
        },
        max_turns=6,
    )

    assert run.evidence_trace_available is True
    assert run.calls[0]["observed_sentinels"] == []
    assert run.calls[1]["observed_sentinels"] == []
    assert run.calls[2]["observed_sentinels"] == [TARGET_ENTITY_EVIDENCE]
    assert run.calls[3]["observed_sentinels"] == []
    assert run.calls[4]["observed_sentinels"] == [TARGET_ENTITY_EVIDENCE]


_API_DRIVER_CASES = case_params(
    _api_driver_multi_turn_tool_loop_and_usage_accumulation,
    _api_driver_refusal_records_calls_but_executes_nothing,
    _api_driver_pairs_results_by_id_not_ordinal,
    _api_driver_flags_result_id_mismatch,
    _api_driver_iteration_cap_only_flags_mid_tool_loop,
    _api_driver_clean_end_on_last_iteration_is_not_capped,
    _api_driver_uses_optional_backend_token_counter,
    _api_driver_records_only_matching_evidence_labels,
)


@pytest.mark.parametrize("case", _API_DRIVER_CASES)
def test_api_driver_behaviours(case):
    case()


@pytest.mark.parametrize(
    ("raw_reason", "expected"),
    [
        ("end_turn", StopReason.END_TURN),
        ("tool_use", StopReason.TOOL_USE),
        ("max_tokens", StopReason.MAX_TOKENS),
        ("refusal", StopReason.REFUSAL),
        ("pause_turn", StopReason.PAUSE_TURN),
        ("model_context_window_exceeded", StopReason.MODEL_CONTEXT_WINDOW_EXCEEDED),
        ("future_reason", StopReason.UNKNOWN),
    ],
)
def test_anthropic_backend_normalizes_and_preserves_stop_reason(raw_reason, expected):
    messages = FakeAnthropicMessages([{"model": "claude", "content": [], "usage": None, "stop_reason": raw_reason}])
    backend = AnthropicBackend("claude", max_tokens=10, client=SimpleNamespace(messages=messages))
    backend.start(None, "prompt", [])

    turn = backend.next_turn()

    assert turn.stop_reason is expected
    assert turn.provider_stop_reason == raw_reason


def test_anthropic_backend_translates_tools_turns_and_results():
    responses = [
        {
            "model": "claude-actual",
            "content": [
                {"type": "text", "text": "checking"},
                {"type": "tool_use", "id": "toolu-1", "name": "lookup", "input": {"q": "x"}},
            ],
            "usage": {
                "input_tokens": 10,
                "output_tokens": 2,
                "cache_read_input_tokens": 3,
                "cache_creation_input_tokens": 4,
            },
            "stop_reason": "tool_use",
        },
        {
            "model": "claude-actual",
            "content": [{"type": "text", "text": "done"}],
            "usage": {"input_tokens": 20, "output_tokens": 5},
            "stop_reason": "end_turn",
        },
    ]
    messages = FakeAnthropicMessages(responses)
    backend = AnthropicBackend(
        "claude-requested",
        max_tokens=123,
        client=SimpleNamespace(messages=messages),
    )
    tool = ToolSpec("lookup", "Look up", {"type": "object", "required": ["q"]})

    backend.start("system", "prompt", [tool])
    first = backend.next_turn()
    backend.add_tool_results([ToolResult("toolu-1", "value", is_error=True)])
    second = backend.next_turn()

    assert messages.requests[0]["system"] == "system"
    assert messages.requests[0]["max_tokens"] == 123
    assert messages.requests[0]["tools"] == [
        {"name": "lookup", "description": "Look up", "input_schema": {"type": "object", "required": ["q"]}}
    ]
    assert first.tool_calls == [ToolCall("toolu-1", "lookup", {"q": "x"})]
    assert first.usage == Usage(10, 2, 3, 4)
    assert first.stop_reason is StopReason.TOOL_USE
    assert first.provider_stop_reason == "tool_use"
    replay = messages.requests[1]["messages"]
    assert replay[1] == {"role": "assistant", "content": responses[0]["content"]}
    assert replay[2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu-1",
                "content": "value",
                "is_error": True,
            }
        ],
    }
    assert second.text == "done"
    assert backend.actual_model == "claude-actual"


@pytest.mark.parametrize(
    ("raw_reason", "expected"),
    [
        ("stop", StopReason.END_TURN),
        ("tool_calls", StopReason.TOOL_USE),
        ("length", StopReason.MAX_TOKENS),
        ("content_filter", StopReason.REFUSAL),
        ("future_reason", StopReason.UNKNOWN),
    ],
)
def test_openai_backend_normalizes_and_preserves_stop_reason(raw_reason, expected):
    completions = FakeOpenAICompletions(
        [
            {
                "model": "gpt",
                "choices": [
                    {
                        "finish_reason": raw_reason,
                        "message": {"content": "done", "tool_calls": []},
                    }
                ],
                "usage": None,
            }
        ]
    )
    backend = OpenAIBackend(
        "gpt",
        max_tokens=10,
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    backend.start(None, "prompt", [])

    turn = backend.next_turn()

    assert turn.stop_reason is expected
    assert turn.provider_stop_reason == raw_reason


def _openai_backend_translates_tools_calls_and_tool_messages():
    responses = [
        {
            "model": "gpt-actual",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": "lookup", "arguments": '{"q":"x"}'},
                            }
                        ],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 3,
                "prompt_tokens_details": {"cached_tokens": 5},
            },
        },
        {
            "model": "gpt-actual",
            "choices": [{"finish_reason": "stop", "message": {"content": "done", "tool_calls": []}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 4},
        },
    ]
    completions = FakeOpenAICompletions(responses)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    backend = OpenAIBackend("gpt-requested", max_tokens=321, client=client)
    tool = ToolSpec("lookup", "Look up", {"type": "object", "properties": {"q": {"type": "string"}}})

    backend.start("system", "prompt", [tool])
    first = backend.next_turn()
    backend.add_tool_results([ToolResult("call-1", "value")])
    second = backend.next_turn()

    first_request = completions.requests[0]
    assert first_request["max_completion_tokens"] == 321
    assert first_request["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "prompt"},
    ]
    assert first_request["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Look up",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
        }
    ]
    assert first.tool_calls == [ToolCall("call-1", "lookup", {"q": "x"})]
    assert first.stop_reason is StopReason.TOOL_USE
    assert first.provider_stop_reason == "tool_calls"
    assert first.usage == Usage(12, 3, 5, 0)
    second_messages = completions.requests[1]["messages"]
    assert second_messages[2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"q":"x"}'},
            }
        ],
    }
    assert second_messages[3] == {"role": "tool", "tool_call_id": "call-1", "content": "value"}
    assert second.text == "done"
    assert second.stop_reason is StopReason.END_TURN
    assert second.provider_stop_reason == "stop"
    assert backend.actual_model == "gpt-actual"


def _openai_backend_normalizes_refusal_for_driver_guard():
    completions = FakeOpenAICompletions(
        [
            {
                "model": "gpt",
                "choices": [
                    {
                        "finish_reason": "content_filter",
                        "message": {
                            "content": None,
                            "refusal": "declined",
                            "tool_calls": [
                                {
                                    "id": "danger",
                                    "type": "function",
                                    "function": {"name": "write", "arguments": "{}"},
                                }
                            ],
                        },
                    }
                ],
                "usage": None,
            }
        ]
    )
    backend = OpenAIBackend(
        "gpt",
        max_tokens=10,
        client=SimpleNamespace(chat=SimpleNamespace(completions=completions)),
    )
    backend.start(None, "prompt", [])

    turn = backend.next_turn()

    assert turn.stop_reason is StopReason.REFUSAL
    assert turn.provider_stop_reason == "content_filter"
    assert turn.text == "declined"
    assert turn.tool_calls == [ToolCall("danger", "write", {})]


@pytest.mark.parametrize(
    "case",
    case_params(
        _openai_backend_translates_tools_calls_and_tool_messages,
        _openai_backend_normalizes_refusal_for_driver_guard,
    ),
)
def test_openai_backend_behaviours(case):
    case()


def _tool_spec_from_mcp_reads_dict_and_object_entries():
    from evals.drivers.api.driver import tool_spec_from_mcp

    as_dict = tool_spec_from_mcp(
        {"name": "list_work_items", "description": "List them", "inputSchema": {"type": "object", "x": 1}}
    )
    assert (as_dict.name, as_dict.description) == ("list_work_items", "List them")
    assert as_dict.input_schema == {"type": "object", "x": 1}

    as_object = tool_spec_from_mcp(SimpleNamespace(name="create_cycle", description="", input_schema=None))
    assert as_object.name == "create_cycle"
    # A missing or non-dict schema must still yield a usable object schema.
    assert as_object.input_schema == {"type": "object"}
    assert tool_spec_from_mcp({"name": "x", "inputSchema": "not-a-schema"}).input_schema == {"type": "object"}


def _tool_result_from_mcp_text_only_joins_blocks():
    from evals.drivers.api.driver import tool_result_from_mcp

    result = tool_result_from_mcp(
        "call_1",
        {"content": [{"type": "text", "text": "first"}, {"type": "text", "text": "second"}]},
    )
    assert (result.call_id, result.text, result.kind, result.is_error) == (
        "call_1",
        "first\nsecond",
        "text",
        False,
    )


def _tool_result_from_mcp_serializes_non_text_blocks():
    from evals.drivers.api.driver import tool_result_from_mcp

    mixed = tool_result_from_mcp(
        "call_2",
        {"content": [{"type": "text", "text": "chart:"}, {"type": "image", "data": "AAAA"}]},
    )
    assert mixed.kind == "mixed"
    assert '"image"' in mixed.text and "chart:" in mixed.text

    image_only = tool_result_from_mcp("call_3", {"content": [{"type": "image", "data": "AAAA"}]})
    assert image_only.kind == "image"
    assert '"data":"AAAA"' in image_only.text


def _tool_result_from_mcp_propagates_error_flag_in_both_spellings():
    from evals.drivers.api.driver import tool_result_from_mcp

    assert tool_result_from_mcp("c", {"content": "boom", "isError": True}).is_error is True
    assert tool_result_from_mcp("c", SimpleNamespace(content="boom", is_error=True)).is_error is True


@pytest.mark.parametrize(
    "case",
    case_params(
        _tool_spec_from_mcp_reads_dict_and_object_entries,
        _tool_result_from_mcp_text_only_joins_blocks,
        _tool_result_from_mcp_serializes_non_text_blocks,
        _tool_result_from_mcp_propagates_error_flag_in_both_spellings,
    ),
)
def test_tool_behaviours(case):
    case()
