"""OpenAI Chat Completions translation for the provider-neutral eval loop."""

from __future__ import annotations

import json
from typing import Any

from evals.drivers.api.backend import ToolCall, ToolResult, ToolSpec, Turn


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _usage_dict(usage: Any) -> dict[str, int] | None:
    if usage is None:
        return None
    prompt_details = _field(usage, "prompt_tokens_details")
    return {
        "in": int(_field(usage, "prompt_tokens", 0) or 0),
        "out": int(_field(usage, "completion_tokens", 0) or 0),
        "cache_read": int(_field(prompt_details, "cached_tokens", 0) or 0),
        "cache_write": 0,
    }


def _normalize_stop_reason(finish_reason: str | None, refusal: Any) -> str | None:
    if refusal or finish_reason == "content_filter":
        return "refusal"
    return {
        "stop": "end_turn",
        "length": "max_tokens",
        "tool_calls": "tool_use",
        "function_call": "tool_use",
    }.get(str(finish_reason), finish_reason)


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
        return Turn(
            text=str(content or refusal or ""),
            tool_calls=calls,
            usage=_usage_dict(_field(completion, "usage")),
            stop_reason=_normalize_stop_reason(_field(choice, "finish_reason"), refusal),
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


__all__ = ["OpenAIBackend"]
