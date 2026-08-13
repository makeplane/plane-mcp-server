"""Anthropic Messages API translation for the provider-neutral eval loop."""

from __future__ import annotations

from typing import Any

from evals.drivers.api.backend import (
    StopReason,
    ToolCall,
    ToolResult,
    ToolSpec,
    Turn,
    Usage,
    register_backend,
)


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _normalize_usage(usage: Any) -> Usage | None:
    if usage is None:
        return None
    return Usage(
        input_tokens=int(_field(usage, "input_tokens", 0) or 0),
        output_tokens=int(_field(usage, "output_tokens", 0) or 0),
        cache_read_input_tokens=int(_field(usage, "cache_read_input_tokens", 0) or 0),
        cache_creation_input_tokens=int(_field(usage, "cache_creation_input_tokens", 0) or 0),
    )


def _normalize_stop_reason(value: Any) -> tuple[StopReason, str | None]:
    raw = str(value) if value is not None else None
    reason = {
        "end_turn": StopReason.END_TURN,
        "tool_use": StopReason.TOOL_USE,
        "max_tokens": StopReason.MAX_TOKENS,
        "refusal": StopReason.REFUSAL,
        "pause_turn": StopReason.PAUSE_TURN,
        "model_context_window_exceeded": StopReason.MODEL_CONTEXT_WINDOW_EXCEEDED,
    }.get(raw, StopReason.UNKNOWN)
    return reason, raw


class AnthropicBackend:
    """Stateful adapter over stable ``client.messages.create`` calls."""

    provider = "anthropic"

    def __init__(self, model: str, *, max_tokens: int, client: Any | None = None) -> None:
        if client is None:
            from anthropic import Anthropic

            client = Anthropic()
        self.client = client
        self.model = model
        self.actual_model = model
        self.max_tokens = max_tokens
        self.system: str | None = None
        self.messages: list[dict[str, Any]] = []
        self.tools: list[dict[str, Any]] = []
        self.started = False

    def start(self, system: str | None, prompt: str, tools: list[ToolSpec]) -> None:
        self.system = system
        self.messages = [{"role": "user", "content": prompt}]
        self.tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in tools
        ]
        self.started = True

    def next_turn(self) -> Turn:
        if not self.started:
            raise RuntimeError("AnthropicBackend.start() must be called before next_turn()")
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": self.messages,
            "tools": self.tools,
        }
        if self.system is not None:
            request["system"] = self.system
        message = self.client.messages.create(**request)
        content = _field(message, "content", None) or []
        # Replay the provider's content objects verbatim, including thinking or
        # other blocks required by later Messages API turns.
        self.messages.append({"role": "assistant", "content": content})

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in content:
            block_type = _field(block, "type")
            if block_type == "text":
                text = _field(block, "text")
                if text:
                    text_parts.append(str(text))
            elif block_type == "refusal":
                explanation = _field(block, "explanation")
                if explanation:
                    text_parts.append(str(explanation))
            elif block_type == "tool_use":
                args = _field(block, "input", {}) or {}
                if not isinstance(args, dict):
                    args = {"_raw": args}
                calls.append(
                    ToolCall(
                        id=str(_field(block, "id", "") or ""),
                        name=str(_field(block, "name", "") or ""),
                        args=args,
                    )
                )

        response_model = _field(message, "model")
        if response_model:
            self.actual_model = str(response_model)
        stop_reason, provider_stop_reason = _normalize_stop_reason(_field(message, "stop_reason"))
        return Turn(
            text="\n".join(text_parts),
            tool_calls=calls,
            usage=_normalize_usage(_field(message, "usage")),
            stop_reason=stop_reason,
            provider_stop_reason=provider_stop_reason,
        )

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": result.call_id,
                        "content": result.text,
                        "is_error": result.is_error,
                    }
                    for result in results
                ],
            }
        )


register_backend(
    AnthropicBackend.provider,
    AnthropicBackend,
    model_aliases={
        "standard": "claude-sonnet-5",
        "fast": "claude-haiku-4-5",
    },
)


__all__ = ["AnthropicBackend"]
