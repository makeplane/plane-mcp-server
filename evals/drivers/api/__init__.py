"""Provider-generic API driver and registered backend translations."""

# Import built-in adapters for their registrations. Each adapter owns its SDK
# translation and keeps its optional SDK import lazy until construction.
from evals.drivers.api.anthropic import AnthropicBackend
from evals.drivers.api.backend import (
    BACKEND_REGISTRY,
    KNOWN_API_PROVIDERS,
    MODEL_TIERS,
    BackendFactory,
    BackendRegistration,
    BackendRegistry,
    ModelBackend,
    StopReason,
    ToolCall,
    ToolResult,
    ToolSpec,
    Turn,
    UnmappedModelTierError,
    Usage,
    backend_model_aliases,
    create_backend,
    register_backend,
    resolve_backend_model,
    unregister_backend,
)
from evals.drivers.api.driver import ApiDriver
from evals.drivers.api.openai import OpenAIBackend

__all__ = [
    "BACKEND_REGISTRY",
    "KNOWN_API_PROVIDERS",
    "MODEL_TIERS",
    "AnthropicBackend",
    "ApiDriver",
    "BackendFactory",
    "BackendRegistration",
    "BackendRegistry",
    "ModelBackend",
    "OpenAIBackend",
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
