"""Offline eval tests for listing."""

from __future__ import annotations

from evals.listing import count_tool_tokens, tool_payload_model_facing, tool_payload_wire


def test_count_tool_tokens_fake_list():
    class T:
        def __init__(self, name, desc, inp, out=None):
            self.name = name
            self.description = desc
            self.inputSchema = inp
            self.outputSchema = out

    tools = [
        T("alpha", "short", {"type": "object"}),
        T(
            "beta",
            "longer description here",
            {"type": "object", "properties": {"x": {"type": "string"}}},
            out={"type": "object"},
        ),
    ]
    # Fake encode: 1 token per character (deterministic, no tiktoken needed).
    encode = lambda s: list(s)  # noqa: E731
    rows, total_wire, total_model = count_tool_tokens(tools, encode=encode)
    assert len(rows) == 2
    assert total_wire == sum(r.wire_tokens for r in rows)
    assert total_model == sum(r.model_facing_tokens for r in rows)
    # Tool with outputSchema has wire > model-facing.
    beta = next(r for r in rows if r.name == "beta")
    assert beta.has_output_schema is True
    assert beta.wire_tokens > beta.model_facing_tokens
    alpha = next(r for r in rows if r.name == "alpha")
    assert alpha.has_output_schema is False
    assert alpha.wire_tokens == alpha.model_facing_tokens
    # Sorted by wire desc
    assert rows[0].wire_tokens >= rows[1].wire_tokens

    wire = tool_payload_wire(tools[1])
    assert "output_schema" in wire
    model = tool_payload_model_facing(tools[1])
    assert "output_schema" not in model
