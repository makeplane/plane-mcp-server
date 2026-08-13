"""Provider-neutral contracts and registry for API-backed eval loops."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Set
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

MODEL_TIERS = frozenset({"standard", "fast"})


class UnmappedModelTierError(ValueError):
    """Raised when a provider has no verified model for a harness tier."""


class StopReason(str, Enum):
    """Harness-owned reasons why a provider turn stopped.

    Values intentionally preserve the strings historically emitted by the
    Anthropic API path so old and new result rows remain comparable. Provider
    adapters retain the provider's original value separately on ``Turn``.

    ``END_TURN`` is a normal completed response; ``TOOL_USE`` requests tool
    execution; ``MAX_TOKENS`` and ``MODEL_CONTEXT_WINDOW_EXCEEDED`` are token
    limits; ``REFUSAL`` is terminal and prevents requested side effects;
    ``PAUSE_TURN`` asks the loop to continue; and ``UNKNOWN`` is the explicit
    fallback for missing or newly introduced provider values.
    """

    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"
    PAUSE_TURN = "pause_turn"
    MODEL_CONTEXT_WINDOW_EXCEEDED = "model_context_window_exceeded"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Usage:
    """Provider-neutral token usage for one model turn."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def to_legacy_dict(self) -> dict[str, int]:
        """Return the established per-iteration JSONL shape."""
        return {
            "in": self.input_tokens,
            "out": self.output_tokens,
            "cache_read": self.cache_read_input_tokens,
            "cache_write": self.cache_creation_input_tokens,
        }


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
    usage: Usage | None
    stop_reason: StopReason
    provider_stop_reason: str | None = None


class ModelBackend(Protocol):
    """Conversation-owning adapter for one model provider.

    Backends retain all provider wire state. The driver sees only normalized
    turns and adds normalized tool results after executing MCP calls.
    """

    provider: str
    model: str
    actual_model: str
    client: Any

    def start(self, system: str | None, prompt: str, tools: list[ToolSpec]) -> None: ...

    def next_turn(self) -> Turn: ...

    def add_tool_results(self, results: list[ToolResult]) -> None: ...


BackendFactory = Callable[..., ModelBackend]


@dataclass(frozen=True)
class BackendRegistration:
    """A registered provider factory and the model aliases it owns."""

    factory: BackendFactory
    model_aliases: Mapping[str, str]


class BackendRegistry:
    """Mutable registry of API providers, populated by backend modules."""

    def __init__(self) -> None:
        self.registrations: dict[str, BackendRegistration] = {}

    @staticmethod
    def normalize_name(provider: str) -> str:
        name = provider.strip().lower()
        if not name:
            raise ValueError("API provider name cannot be empty")
        return name

    def register(
        self,
        provider: str,
        factory: BackendFactory,
        *,
        model_aliases: Mapping[str, str] | None = None,
    ) -> None:
        name = self.normalize_name(provider)
        if name in self.registrations:
            raise ValueError(f"API provider {name!r} is already registered")
        aliases = {str(alias): str(model) for alias, model in (model_aliases or {}).items()}
        self.registrations[name] = BackendRegistration(factory=factory, model_aliases=aliases)

    def unregister(self, provider: str) -> None:
        """Remove a provider registration, primarily for isolated tests."""
        self.registrations.pop(self.normalize_name(provider), None)

    def names(self) -> frozenset[str]:
        return frozenset(self.registrations)

    def resolve(self, provider: str) -> BackendRegistration:
        name = self.normalize_name(provider)
        try:
            return self.registrations[name]
        except KeyError as exc:
            raise ValueError(f"unknown API provider {name!r}; expected one of {sorted(self.registrations)}") from exc

    def create(
        self,
        provider: str,
        model: str,
        *,
        max_tokens: int,
        client: Any | None = None,
    ) -> ModelBackend:
        registration = self.resolve(provider)
        return registration.factory(model, max_tokens=max_tokens, client=client)

    def resolve_model(self, provider: str, model: str) -> str:
        registration = self.resolve(provider)
        if model in MODEL_TIERS and model not in registration.model_aliases:
            raise UnmappedModelTierError(
                f"model tier {model!r} is not mapped for API provider {self.normalize_name(provider)!r}; "
                "pass an explicit model ID with --model"
            )
        return registration.model_aliases.get(model, model)

    def model_aliases(self, provider: str) -> dict[str, str]:
        return dict(self.resolve(provider).model_aliases)


class RegisteredProviderNames(Set[str]):
    """Live set view over the providers in a ``BackendRegistry``."""

    def __init__(self, registry: BackendRegistry) -> None:
        self.registry = registry

    def __contains__(self, value: object) -> bool:
        return value in self.registry.registrations

    def __iter__(self) -> Iterator[str]:
        return iter(self.registry.registrations)

    def __len__(self) -> int:
        return len(self.registry.registrations)


BACKEND_REGISTRY = BackendRegistry()
KNOWN_API_PROVIDERS: Set[str] = RegisteredProviderNames(BACKEND_REGISTRY)


def register_backend(
    provider: str,
    factory: BackendFactory,
    *,
    model_aliases: Mapping[str, str] | None = None,
) -> None:
    """Register a provider factory and any aliases owned by that provider."""
    BACKEND_REGISTRY.register(provider, factory, model_aliases=model_aliases)


def unregister_backend(provider: str) -> None:
    """Remove a provider registration."""
    BACKEND_REGISTRY.unregister(provider)


def create_backend(
    provider: str,
    model: str,
    *,
    max_tokens: int,
    client: Any | None = None,
) -> ModelBackend:
    """Construct the backend registered for ``provider``."""
    return BACKEND_REGISTRY.create(provider, model, max_tokens=max_tokens, client=client)


def resolve_backend_model(provider: str, model: str) -> str:
    """Resolve only aliases declared by the selected provider."""
    return BACKEND_REGISTRY.resolve_model(provider, model)


def backend_model_aliases(provider: str) -> dict[str, str]:
    """Return a copy of one provider's owned alias mapping."""
    return BACKEND_REGISTRY.model_aliases(provider)


__all__ = [
    "BACKEND_REGISTRY",
    "KNOWN_API_PROVIDERS",
    "MODEL_TIERS",
    "BackendFactory",
    "BackendRegistration",
    "BackendRegistry",
    "ModelBackend",
    "RegisteredProviderNames",
    "StopReason",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "Turn",
    "UnmappedModelTierError",
    "Usage",
    "backend_model_aliases",
    "create_backend",
    "register_backend",
    "resolve_backend_model",
    "unregister_backend",
]
