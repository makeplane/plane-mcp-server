"""Owned provider-neutral agent loop over a stdio MCP session."""

from __future__ import annotations

import asyncio
import inspect
import json
import sys
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from evals.drivers.api.anthropic import AnthropicBackend
from evals.drivers.api.backend import ModelBackend, ToolResult, ToolSpec
from evals.drivers.api.openai import OpenAIBackend
from evals.drivers.base import AgentRun

DEFAULT_MAX_TOKENS = 8192
KNOWN_API_PROVIDERS = frozenset({"anthropic", "openai"})

BackendFactory = Callable[[str, int], ModelBackend]
McpSessionFactory = Callable[[StdioServerParameters], Any]


def estimate_tokens(text: str) -> int:
    """Estimate token count from recorded text without a provider request."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def tool_spec_from_mcp(tool: Any) -> ToolSpec:
    """Translate an MCP list-tools entry into a neutral tool specification."""
    if isinstance(tool, dict):
        name = tool.get("name") or ""
        description = tool.get("description") or ""
        schema = tool.get("inputSchema") or tool.get("input_schema") or {"type": "object"}
    else:
        name = getattr(tool, "name", "") or ""
        description = getattr(tool, "description", "") or ""
        schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
        schema = schema or {"type": "object"}
    if not isinstance(schema, dict):
        schema = {"type": "object"}
    return ToolSpec(name=str(name), description=str(description), input_schema=schema)


def _dump_content_block(block: Any) -> Any:
    if isinstance(block, (dict, str, int, float, bool)) or block is None:
        return block
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        return dump(by_alias=True, exclude_none=True)
    return str(block)


def _content_text_and_kind(content: Any) -> tuple[str, str]:
    if content is None:
        return "", "text"
    if isinstance(content, str):
        return content, "text"
    if not isinstance(content, list):
        return str(content), "text"

    text_parts: list[str] = []
    saw_non_text = False
    for block in content:
        block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if block_type == "text" or block_type is None:
            text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
            if text is None and isinstance(block, str):
                text = block
            if text is not None:
                text_parts.append(str(text))
        else:
            saw_non_text = True

    if not saw_non_text:
        return "\n".join(text_parts), "text"
    payload = json.dumps([_dump_content_block(block) for block in content], default=str, separators=(",", ":"))
    return payload, "mixed" if text_parts else "image"


def tool_result_from_mcp(call_id: str, raw_result: Any) -> ToolResult:
    """Translate an MCP call result, preserving an injected result ID for tests."""
    if isinstance(raw_result, ToolResult):
        return raw_result
    if isinstance(raw_result, dict):
        content = raw_result.get("content")
        is_error = bool(raw_result.get("isError") or raw_result.get("is_error"))
    else:
        content = getattr(raw_result, "content", None)
        is_error = bool(getattr(raw_result, "isError", False) or getattr(raw_result, "is_error", False))
    text, kind = _content_text_and_kind(content)
    return ToolResult(call_id=call_id, text=text, is_error=is_error, kind=kind)


class ApiDriver:
    """Run an owned tool loop against Anthropic or OpenAI and a stdio MCP server."""

    name = "api"

    def __init__(
        self,
        *,
        provider: str = "anthropic",
        client: Any | None = None,
        backend_factory: BackendFactory | None = None,
        mcp_session_factory: McpSessionFactory | None = None,
        server_command: list[str] | None = None,
        python_bin: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        provider = provider.strip().lower()
        if provider not in KNOWN_API_PROVIDERS:
            raise ValueError(f"unknown API provider {provider!r}; expected one of {sorted(KNOWN_API_PROVIDERS)}")
        if server_command is not None and not server_command:
            raise ValueError("server_command cannot be empty")
        self.provider = provider
        self.client = client
        self.backend_factory = backend_factory
        self.mcp_session_factory = mcp_session_factory
        self.server_command = list(server_command) if server_command is not None else None
        self.python_bin = python_bin or sys.executable
        self.max_tokens = max_tokens

    def _make_backend(self, model: str) -> ModelBackend:
        if self.backend_factory is not None:
            return self.backend_factory(model, self.max_tokens)
        if self.provider == "anthropic":
            backend = AnthropicBackend(model, max_tokens=self.max_tokens, client=self.client)
        else:
            backend = OpenAIBackend(model, max_tokens=self.max_tokens, client=self.client)
        # Delay credential-dependent client creation until the first non-skipped
        # task, then reuse the provider's connection pool across the battery.
        if self.client is None:
            self.client = backend.client
        return backend

    def _server_params(self, mcp_env: dict[str, str], cwd: Path | None) -> StdioServerParameters:
        command = self.server_command or [self.python_bin, "-m", "plane_mcp", "stdio"]
        return StdioServerParameters(
            command=command[0],
            args=command[1:],
            env=mcp_env,
            cwd=cwd,
        )

    @asynccontextmanager
    async def _mcp_session(self, params: StdioServerParameters):
        if self.mcp_session_factory is not None:
            context = self.mcp_session_factory(params)
            if inspect.isawaitable(context):
                context = await context
            if hasattr(context, "__aenter__"):
                async with context as session:
                    yield session
            else:
                yield context
            return

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                yield session

    def run_task(
        self,
        prompt: str,
        mcp_env: dict[str, str],
        model: str | None,
        max_turns: int,
        *,
        system: str | None = None,
        cwd: Path | None = None,
    ) -> AgentRun:
        if not model:
            raise ValueError("the API driver requires a model ID")
        if max_turns < 1:
            raise ValueError("max_turns must be at least 1")
        return asyncio.run(
            self._run_task(
                prompt=prompt,
                mcp_env=mcp_env,
                model=model,
                max_turns=max_turns,
                system=system,
                cwd=cwd,
            )
        )

    async def _run_task(
        self,
        *,
        prompt: str,
        mcp_env: dict[str, str],
        model: str,
        max_turns: int,
        system: str | None,
        cwd: Path | None,
    ) -> AgentRun:
        backend = self._make_backend(model)
        calls: list[dict[str, Any]] = []
        pending_results: list[tuple[int, str]] = []
        usage_per_iteration: list[dict[str, int]] = []
        result_pair_mismatch = False
        hit_max_iterations = False
        iterations = 0
        final_text = ""
        stop_reason: str | None = None

        params = self._server_params(mcp_env, cwd)
        async with self._mcp_session(params) as mcp_client:
            await mcp_client.initialize()
            tools_result = await mcp_client.list_tools()
            raw_tools = (
                tools_result.get("tools", []) if isinstance(tools_result, dict) else getattr(tools_result, "tools", [])
            )
            backend.start(system, prompt, [tool_spec_from_mcp(tool) for tool in raw_tools])

            # Match the historical metric: model/tool loop only, after list_tools.
            started_at = time.perf_counter()
            try:
                while iterations < max_turns:
                    turn = backend.next_turn()
                    iterations += 1
                    final_text = turn.text
                    stop_reason = turn.stop_reason
                    if turn.usage is not None:
                        usage_per_iteration.append(dict(turn.usage))

                    call_indices: dict[str, int] = {}
                    for tool_call in turn.tool_calls:
                        idx = len(calls)
                        calls.append(
                            {
                                "tool": tool_call.name,
                                "args": tool_call.args,
                                "result_tokens": None,
                                "result_chars": 0,
                                "result_kind": "text",
                                "is_error": False,
                            }
                        )
                        if not tool_call.id or tool_call.id in call_indices:
                            result_pair_mismatch = True
                        else:
                            call_indices[tool_call.id] = idx

                    # Record the model's calls, but never execute side effects on
                    # a refusal-terminated response.
                    if stop_reason == "refusal":
                        break

                    if not turn.tool_calls:
                        if stop_reason == "pause_turn" and iterations < max_turns:
                            continue
                        break

                    executed: list[tuple[ToolResult, float]] = []
                    for tool_call in turn.tool_calls:
                        call_started = time.perf_counter()
                        raw_result = await mcp_client.call_tool(tool_call.name, arguments=tool_call.args)
                        duration_ms = round((time.perf_counter() - call_started) * 1000, 3)
                        executed.append((tool_result_from_mcp(tool_call.id, raw_result), duration_ms))

                    matched_ids: set[str] = set()
                    tool_results: list[ToolResult] = []
                    for result, duration_ms in executed:
                        tool_results.append(result)
                        idx = call_indices.get(result.call_id)
                        if idx is None or result.call_id in matched_ids:
                            result_pair_mismatch = True
                            continue
                        matched_ids.add(result.call_id)
                        calls[idx]["result_chars"] = len(result.text)
                        calls[idx]["result_kind"] = result.kind
                        calls[idx]["is_error"] = result.is_error
                        calls[idx]["duration_ms"] = duration_ms
                        pending_results.append((idx, result.text))
                    if matched_ids != set(call_indices) or len(call_indices) != len(turn.tool_calls):
                        result_pair_mismatch = True

                    backend.add_tool_results(tool_results)
                    if iterations >= max_turns:
                        hit_max_iterations = stop_reason not in ("end_turn", "max_tokens")
                        break
                    if stop_reason != "tool_use":
                        break
            finally:
                wall_time_s = time.perf_counter() - started_at

        # Token sizing is intentionally outside wall_time. A backend may offer
        # a local/exact counter; absence or failure falls back to recorded text.
        counter = getattr(backend, "count_tokens", None)
        token_count_failures = 0
        result_tokens_estimated = False
        for idx, result_text in pending_results:
            counted: int | None = None
            if callable(counter):
                try:
                    raw_count = counter(result_text)
                    if inspect.isawaitable(raw_count):
                        raw_count = await raw_count
                    counted = int(raw_count)
                except Exception:
                    token_count_failures += 1
            if counted is None:
                counted = estimate_tokens(result_text)
                result_tokens_estimated = True
            calls[idx]["result_tokens"] = counted

        usage_total = {
            "input_tokens": sum(item.get("in", 0) for item in usage_per_iteration),
            "output_tokens": sum(item.get("out", 0) for item in usage_per_iteration),
            "cache_read_input_tokens": sum(item.get("cache_read", 0) for item in usage_per_iteration),
            "cache_creation_input_tokens": sum(item.get("cache_write", 0) for item in usage_per_iteration),
            "source": "iterations",
        }
        return AgentRun(
            calls=calls,
            final_text=final_text,
            usage=usage_per_iteration[-1] if usage_per_iteration else None,
            usage_total=usage_total,
            usage_scope="iteration",
            stopped_reason=stop_reason or "end_turn",
            call_source="api",
            hit_max_turns=hit_max_iterations,
            wall_time_s=round(wall_time_s, 3),
            usage_per_iteration=usage_per_iteration,
            cum_input_tokens=sum(item.get("in", 0) for item in usage_per_iteration),
            result_pair_mismatch=result_pair_mismatch,
            token_count_failures=token_count_failures,
            result_tokens_estimated=result_tokens_estimated,
            provider=str(getattr(backend, "provider", self.provider)),
            model=str(getattr(backend, "actual_model", getattr(backend, "model", model))),
            requested_model=model,
        )


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "KNOWN_API_PROVIDERS",
    "ApiDriver",
    "BackendFactory",
    "McpSessionFactory",
    "estimate_tokens",
    "tool_result_from_mcp",
    "tool_spec_from_mcp",
]
