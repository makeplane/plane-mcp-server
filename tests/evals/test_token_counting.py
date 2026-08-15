"""Offline eval tests for token counting."""

from __future__ import annotations

import sys

import pytest

from evals.results import AgentRun, agent_run_to_harness_dict
from evals.token_counting import estimate_result_tokens


@pytest.mark.parametrize("has_tokenizer", [True, False], ids=["importable-tokenizer", "estimator-fallback"])
def test_agent_behaviours(monkeypatch, has_tokenizer):
    text = "serialized workspace result"

    if has_tokenizer:

        class FakeEncoding:
            def encode(self, encoded_text):
                assert encoded_text == text
                return [10, 20, 30]

        class FakeTiktoken:
            @staticmethod
            def get_encoding(name):
                assert name == "cl100k_base"
                return FakeEncoding()

        monkeypatch.setitem(sys.modules, "tiktoken", FakeTiktoken)
    else:
        monkeypatch.setitem(sys.modules, "tiktoken", None)

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

    out = agent_run_to_harness_dict(run)

    expected_tokens = 3 if has_tokenizer else estimate_result_tokens(len(text))
    assert out["calls"][0]["result_tokens"] == expected_tokens
    assert out["calls"][0]["result_tokens_estimated"] is not has_tokenizer
    assert out["result_tokens_estimated"] is not has_tokenizer
    assert "result_text" not in out["calls"][0]
    if has_tokenizer:
        assert out["calls"][0]["result_token_count_method"] == "tiktoken:cl100k_base"
        assert out["result_tokens_mode"] == "measured"
