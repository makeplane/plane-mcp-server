"""OpenAI Responses translation for the provider-neutral eval loop.

Responses rather than Chat Completions because Chat Completions cannot run this harness at
all on current models: ``gpt-5.6-luna`` answers a request carrying function tools with
``400 — Function tools with reasoning_effort are not supported for gpt-5.6-luna in
/v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to
'none'``. The harness always sends tools, so every turn failed.

Setting ``reasoning_effort='none'`` would also have satisfied that error, and was rejected:
it would silence reasoning on this path while the vendor CLI drivers keep it, so an arm run
here could not be compared against a CLI arm of the same model — which is the one question
an API arm exists to answer.
"""

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
    """Map Responses usage onto the neutral shape.

    Responses names these ``input_tokens``/``output_tokens``, where Chat Completions said
    ``prompt_tokens``/``completion_tokens``; both spellings are read so an injected fake or a
    future field rename does not silently report zero.
    """
    if usage is None:
        return None
    input_details = _field(usage, "input_tokens_details") or _field(usage, "prompt_tokens_details")
    input_tokens = _field(usage, "input_tokens")
    if input_tokens is None:
        input_tokens = _field(usage, "prompt_tokens", 0)
    output_tokens = _field(usage, "output_tokens")
    if output_tokens is None:
        output_tokens = _field(usage, "completion_tokens", 0)
    return Usage(
        input_tokens=int(input_tokens or 0),
        output_tokens=int(output_tokens or 0),
        cache_read_input_tokens=int(_field(input_details, "cached_tokens", 0) or 0),
    )


def _text_from_content(content: Any) -> str:
    """Concatenate the text parts of one output message's content list."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content or ():
        kind = str(_field(part, "type", "") or "")
        if kind in ("output_text", "text"):
            parts.append(str(_field(part, "text", "") or ""))
        elif kind == "refusal":
            parts.append(str(_field(part, "refusal", "") or ""))
    return "".join(parts)


def _parse_arguments(raw_args: Any) -> tuple[dict[str, Any], str]:
    """Return (args dict, wire string). Malformed JSON is preserved, never dropped."""
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args or "{}")
        except json.JSONDecodeError:
            return {"_raw": raw_args}, raw_args
        if not isinstance(parsed, dict):
            return {"_raw": parsed}, raw_args
        return parsed, raw_args
    if isinstance(raw_args, dict):
        return raw_args, json.dumps(raw_args, separators=(",", ":"))
    return {"_raw": raw_args}, json.dumps(raw_args, default=str)


class OpenAIBackend:
    """Stateful adapter over ``client.responses.create``.

    ``openai`` is deliberately imported only when no client was injected, so
    importing this module and all offline tests work without that package.
    """

    provider = "openai"
    # Responses counts cached reads inside input_tokens; cached_tokens is a subset of it.
    input_tokens_include_cache = True

    def __init__(self, model: str, *, max_tokens: int, client: Any | None = None) -> None:
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "the OpenAI API provider requires the optional 'openai' package; "
                    "install it with the 'evals-openai' extra"
                ) from exc

            client = OpenAI()
        self.client = client
        self.model = model
        self.actual_model = model
        self.max_tokens = max_tokens
        # Responses calls this the input list; it carries user/assistant items plus
        # function_call and function_call_output items, not role-tagged tool messages.
        self.input_items: list[Any] = []
        self.instructions: str | None = None
        self.tools: list[dict[str, Any]] = []
        self.started = False

    def start(self, system: str | None, prompt: str, tools: list[ToolSpec]) -> None:
        # The system prompt is a top-level field here rather than a message in the list.
        self.instructions = system
        self.input_items = [{"role": "user", "content": prompt}]
        # Responses declares a function tool flat; Chat Completions nested it under "function".
        self.tools = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.input_schema,
            }
            for tool in tools
        ]
        self.started = True

    def next_turn(self) -> Turn:
        if not self.started:
            raise RuntimeError("OpenAIBackend.start() must be called before next_turn()")
        request: dict[str, Any] = {
            "model": self.model,
            "max_output_tokens": self.max_tokens,
            "input": self.input_items,
        }
        if self.instructions is not None:
            request["instructions"] = self.instructions
        if self.tools:
            request["tools"] = self.tools
        response = self.client.responses.create(**request)

        output = _field(response, "output", None) or []
        calls: list[ToolCall] = []
        texts: list[str] = []
        refusal_text = ""
        for item in output:
            kind = str(_field(item, "type", "") or "")
            if kind == "function_call":
                args, wire_args = _parse_arguments(_field(item, "arguments", "{}"))
                # call_id is the identifier a function_call_output must echo back; id is the
                # item's own handle. Only call_id closes the loop.
                call_id = str(_field(item, "call_id", "") or _field(item, "id", "") or "")
                calls.append(ToolCall(id=call_id, name=str(_field(item, "name", "") or ""), args=args))
                # Echo the model's own item back verbatim so the provider sees the history it
                # produced, rather than a reconstruction of it.
                self.input_items.append(item)
            elif kind in ("message", ""):
                content = _field(item, "content")
                texts.append(_text_from_content(content))
                self.input_items.append(item)
            else:
                # Reasoning and any future item type: replayed untouched. Dropping a reasoning
                # item breaks the chain these models expect on the next request.
                self.input_items.append(item)

        for item in output:
            if str(_field(item, "type", "") or "") == "message":
                for part in _field(item, "content") or ():
                    if str(_field(part, "type", "") or "") == "refusal":
                        refusal_text = str(_field(part, "refusal", "") or "")

        response_model = _field(response, "model")
        if response_model:
            self.actual_model = str(response_model)

        status = str(_field(response, "status", "") or "")
        incomplete = _field(response, "incomplete_details")
        incomplete_reason = str(_field(incomplete, "reason", "") or "") if incomplete else ""
        stop_reason, provider_stop_reason = self._stop_reason(
            status=status,
            incomplete_reason=incomplete_reason,
            has_calls=bool(calls),
            refusal=refusal_text,
        )
        text = "".join(texts) or refusal_text
        if not text:
            # output_text is the SDK's own concatenation; only consulted as a fallback so a
            # shape this adapter does not model yet still yields the answer.
            text = str(_field(response, "output_text", "") or "")
        return Turn(
            text=text,
            tool_calls=calls,
            usage=_normalize_usage(_field(response, "usage")),
            stop_reason=stop_reason,
            provider_stop_reason=provider_stop_reason,
        )

    @staticmethod
    def _stop_reason(
        *,
        status: str,
        incomplete_reason: str,
        has_calls: bool,
        refusal: str,
    ) -> tuple[StopReason, str | None]:
        """Derive the neutral stop reason.

        Responses has no finish_reason: a turn's outcome is its status plus what it emitted.
        Tool calls win over status because a completed response carrying calls is the loop's
        continue signal, which is what TOOL_USE means to the driver.
        """
        raw = incomplete_reason or status or None
        if refusal:
            return StopReason.REFUSAL, raw
        if has_calls:
            return StopReason.TOOL_USE, raw
        if incomplete_reason == "max_output_tokens":
            return StopReason.MAX_TOKENS, raw
        if incomplete_reason == "content_filter":
            return StopReason.REFUSAL, raw
        if status == "completed":
            return StopReason.END_TURN, raw
        return StopReason.UNKNOWN, raw

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self.input_items.extend(
            {
                "type": "function_call_output",
                "call_id": result.call_id,
                "output": result.text,
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
