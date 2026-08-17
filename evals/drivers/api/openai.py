"""OpenAI Chat Completions translation for the provider-neutral eval loop."""

from __future__ import annotations

import json
from typing import Any

from evals.drivers.api.base import (
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
    prompt_details = _field(usage, "prompt_tokens_details")
    return Usage(
        input_tokens=int(_field(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(_field(usage, "completion_tokens", 0) or 0),
        cache_read_input_tokens=int(_field(prompt_details, "cached_tokens", 0) or 0),
    )


def _normalize_stop_reason(finish_reason: Any, refusal: Any) -> tuple[StopReason, str | None]:
    raw = str(finish_reason) if finish_reason is not None else None
    if refusal or finish_reason == "content_filter":
        return StopReason.REFUSAL, raw
    reason = {
        "stop": StopReason.END_TURN,
        "length": StopReason.MAX_TOKENS,
        "tool_calls": StopReason.TOOL_USE,
        # Retain support for the predecessor of ``tool_calls``.
        "function_call": StopReason.TOOL_USE,
    }.get(raw, StopReason.UNKNOWN)
    return reason, raw


class OpenAIBackend:
    """Stateful adapter over ``client.chat.completions.create``.

    ``openai`` is deliberately imported only when no client was injected, so
    importing this module and all offline tests work without that package.
    """

    provider = "openai"

    def __init__(self, model: str, *, max_tokens: int, client: Any | None = None) -> None:
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "the OpenAI API provider requires the optional 'openai' package; "
                    "install it in the runtime that launches evals"
                ) from exc

            client = OpenAI()
        self.client = client
        self.model = model
        self.actual_model = model
        self.max_tokens = max_tokens
        self.messages: list[dict[str, Any]] = []
        self.tools: list[dict[str, Any]] = []
        self.started = False

    def start(self, system: str | None, prompt: str, tools: list[ToolSpec]) -> None:
        self.messages = []
        if system is not None:
            self.messages.append({"role": "system", "content": system})
        self.messages.append({"role": "user", "content": prompt})
        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in tools
        ]
        self.started = True

    def next_turn(self) -> Turn:
        if not self.started:
            raise RuntimeError("OpenAIBackend.start() must be called before next_turn()")
        completion = self.client.chat.completions.create(
            model=self.model,
            max_completion_tokens=self.max_tokens,
            messages=self.messages,
            tools=self.tools,
        )
        choices = _field(completion, "choices", None) or []
        if not choices:
            raise RuntimeError("OpenAI Chat Completions returned no choices")
        choice = choices[0]
        message = _field(choice, "message")
        raw_calls = _field(message, "tool_calls", None) or []

        calls: list[ToolCall] = []
        wire_calls: list[dict[str, Any]] = []
        for raw_call in raw_calls:
            function = _field(raw_call, "function")
            raw_args = _field(function, "arguments", "{}") or "{}"
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {"_raw": raw_args}
            elif isinstance(raw_args, dict):
                args = raw_args
                raw_args = json.dumps(raw_args, separators=(",", ":"))
            else:
                args = {"_raw": raw_args}
                raw_args = json.dumps(raw_args, default=str)
            if not isinstance(args, dict):
                args = {"_raw": args}
            call_id = str(_field(raw_call, "id", "") or "")
            name = str(_field(function, "name", "") or "")
            calls.append(ToolCall(id=call_id, name=name, args=args))
            wire_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": raw_args},
                }
            )

        content = _field(message, "content")
        refusal = _field(message, "refusal")
        assistant_message: dict[str, Any] = {"role": "assistant", "content": content}
        if wire_calls:
            assistant_message["tool_calls"] = wire_calls
        self.messages.append(assistant_message)

        response_model = _field(completion, "model")
        if response_model:
            self.actual_model = str(response_model)
        stop_reason, provider_stop_reason = _normalize_stop_reason(_field(choice, "finish_reason"), refusal)
        return Turn(
            text=str(content or refusal or ""),
            tool_calls=calls,
            usage=_normalize_usage(_field(completion, "usage")),
            stop_reason=stop_reason,
            provider_stop_reason=provider_stop_reason,
        )

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self.messages.extend(
            {
                "role": "tool",
                "tool_call_id": result.call_id,
                "content": result.text,
            }
            for result in results
        )


register_backend(
    OpenAIBackend.provider,
    OpenAIBackend,
    model_aliases={
        "standard": "gpt-5.6-sol",
        "fast": "gpt-5.6-luna",
    },
)


__all__ = ["OpenAIBackend"]
