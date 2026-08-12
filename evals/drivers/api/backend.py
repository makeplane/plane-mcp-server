"""Provider-neutral types for API-backed eval agent loops."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolSpec:
    """A model-facing tool definition translated from MCP ``list_tools``."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """A provider-neutral model request to invoke one MCP tool."""

    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """A provider-neutral MCP result paired to its model call ID."""

    call_id: str
    text: str
    is_error: bool = False
    kind: str = "text"


@dataclass(frozen=True)
class Turn:
    """One normalized assistant response from a model provider."""

    text: str
    tool_calls: list[ToolCall]
    usage: dict[str, int] | None
    stop_reason: str | None


class ModelBackend(Protocol):
    """Conversation-owning adapter for one model provider.

    Backends retain all provider wire state. The driver sees only normalized
    turns and adds normalized tool results after executing MCP calls.
    """

    provider: str
    model: str
    actual_model: str

    def start(self, system: str | None, prompt: str, tools: list[ToolSpec]) -> None: ...

    def next_turn(self) -> Turn: ...

    def add_tool_results(self, results: list[ToolResult]) -> None: ...


__all__ = ["ModelBackend", "ToolCall", "ToolResult", "ToolSpec", "Turn"]
