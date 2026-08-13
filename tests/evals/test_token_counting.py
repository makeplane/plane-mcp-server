"""Offline eval tests for token counting."""

from __future__ import annotations

import sys

from evals.results import AgentRun, agent_run_to_harness_dict
from evals.runner.live import classify_call
from evals.token_counting import estimate_result_tokens


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
