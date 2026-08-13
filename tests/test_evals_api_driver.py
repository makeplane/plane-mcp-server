"""Offline tests for the provider-generic API eval driver."""

from __future__ import annotations

import copy
from collections import deque
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from evals.drivers import agent_run_to_harness_dict
from evals.drivers.api import (
    KNOWN_API_PROVIDERS,
    AnthropicBackend,
    ApiDriver,
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
from evals.token_counting import estimate_result_tokens


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
    assert run.usage_per_iteration == [{"in": 7, "out": 2, "cache_read": 0, "cache_write": 0}]


def test_api_driver_multi_turn_tool_loop_and_usage_accumulation():
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
    assert run.usage_per_iteration == [
        {"in": 10, "out": 2, "cache_read": 3, "cache_write": 1},
        {"in": 20, "out": 4, "cache_read": 6, "cache_write": 0},
        {"in": 30, "out": 6, "cache_read": 9, "cache_write": 0},
    ]
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


def test_api_driver_refusal_records_calls_but_executes_nothing():
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


def test_api_driver_pairs_results_by_id_not_ordinal():
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


def test_api_driver_flags_result_id_mismatch():
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
    assert [call["result_chars"] for call in run.calls] == [0, 4]


def test_api_driver_iteration_cap_only_flags_mid_tool_loop():
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


def test_api_driver_clean_end_on_last_iteration_is_not_capped():
    backend = FakeBackend([Turn(text="done", tool_calls=[], usage=None, stop_reason=StopReason.END_TURN)])

    run = run_driver(make_driver(backend, FakeMcpSession()), max_turns=1)

    assert run.hit_max_turns is False
    assert run.stopped_reason == "end_turn"


def test_api_driver_uses_optional_backend_token_counter():
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


def test_api_driver_maps_every_legacy_row_field():
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
    row = agent_run_to_harness_dict(
        run,
        optimal={"lookup"},
        alternate=set(),
        classify=lambda tool, optimal, alternate: (
            "optimal" if tool in optimal else "alternate" if tool in alternate else "out_of_set"
        ),
    )

    required = {
        "final_text",
        "calls",
        "num_calls",
        "errored_calls",
        "alternate_calls",
        "out_of_set_calls",
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
        "class",
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


class FakeAnthropicMessages:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = deque(responses)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        return self.responses.popleft()


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


class FakeOpenAICompletions:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = deque(responses)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs):
        self.requests.append(copy.deepcopy(kwargs))
        return self.responses.popleft()


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


def test_openai_backend_translates_tools_calls_and_tool_messages():
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


def test_openai_backend_normalizes_refusal_for_driver_guard():
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
