"""Owned API and subprocess-backed CLI evaluation drivers."""

from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from evals import REPO_ROOT
from evals.drivers.api.backend import (
    KNOWN_API_PROVIDERS,
    ModelBackend,
    StopReason,
    ToolResult,
    ToolSpec,
    create_backend,
)
from evals.drivers.cli.process import note_timeout_kill, run_cli_subprocess
from evals.drivers.cli.sidecar import (
    ProxySidecarResult,
    apply_proxy_sidecar,
    ensure_proxy_pythonpath,
    harvest_proxy_after_cli_timeout,
    load_proxy_sidecar,
    proxy_wrap_server_command,
)
from evals.evidence import (
    configured_evidence_labels,
    normalize_evidence_aggregates,
    normalize_evidence_sentinels,
    normalize_evidence_targets,
    observed_aggregate_labels,
    observed_sentinel_labels,
    write_evidence_config,
)
from evals.results import AgentRun, Usage
from evals.token_counting import TOKEN_ESTIMATE_METHOD, estimate_result_tokens
from evals.tool_manifest import ToolManifestCapture, tools_page

DEFAULT_MAX_TOKENS = 8192

BackendFactory = Callable[[str, int], ModelBackend]
McpSessionFactory = Callable[[StdioServerParameters], Any]


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
    """Run an owned model/tool loop through a registered API backend."""

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
        backend = create_backend(
            self.provider,
            model,
            max_tokens=self.max_tokens,
            client=self.client,
        )
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
    async def _mcp_session(
        self,
        params: StdioServerParameters,
        *,
        manifest_state: dict[str, bool],
    ):
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

            async def message_handler(message: Any) -> None:
                notification = getattr(message, "root", message)
                if getattr(notification, "method", None) == "notifications/tools/list_changed":
                    manifest_state["stale"] = True

            async with ClientSession(read, write, message_handler=message_handler) as session:
                yield session

    @staticmethod
    async def _list_all_tools(mcp_client: Any) -> tuple[list[Any], str | None]:
        """Aggregate every tools/list page and fingerprint the complete snapshot."""
        capture = ToolManifestCapture()
        tools: list[Any] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            page = await mcp_client.list_tools(cursor=cursor) if cursor is not None else await mcp_client.list_tools()
            capture.observe_page(page, request_cursor=cursor)
            page_tools, next_cursor = tools_page(page)
            tools.extend(page_tools)
            if next_cursor is None:
                return tools, capture.fingerprint
            if next_cursor in seen_cursors:
                raise RuntimeError(f"tools/list pagination repeated cursor {next_cursor!r}")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    def run_task(
        self,
        prompt: str,
        mcp_env: dict[str, str],
        model: str | None,
        max_turns: int,
        *,
        system: str | None = None,
        cwd: Path | None = None,
        evidence_sentinels: dict[str, Any] | None = None,
        evidence_targets: dict[str, Any] | None = None,
        evidence_aggregates: dict[str, Any] | None = None,
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
                evidence_sentinels=evidence_sentinels,
                evidence_targets=evidence_targets,
                evidence_aggregates=evidence_aggregates,
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
        evidence_sentinels: dict[str, Any] | None,
        evidence_targets: dict[str, Any] | None,
        evidence_aggregates: dict[str, Any] | None,
    ) -> AgentRun:
        backend = self._make_backend(model)
        evidence = normalize_evidence_sentinels(evidence_sentinels)
        targets = normalize_evidence_targets(evidence_targets)
        aggregates = normalize_evidence_aggregates(evidence_aggregates)
        evidence_active = bool(configured_evidence_labels(evidence, targets, aggregates))
        calls: list[dict[str, Any]] = []
        pending_results: list[tuple[int, str]] = []
        usage_per_iteration: list[Usage] = []
        total_input_tokens = 0
        total_output_tokens = 0
        total_cache_read_input_tokens = 0
        total_cache_creation_input_tokens = 0
        result_pair_mismatch = False
        hit_max_iterations = False
        iterations = 0
        final_text = ""
        stop_reason: StopReason | None = None
        provider_stop_reason: str | None = None

        params = self._server_params(mcp_env, cwd)
        manifest_state = {"stale": False}
        tool_manifest_fingerprint: str | None = None
        async with self._mcp_session(params, manifest_state=manifest_state) as mcp_client:
            await mcp_client.initialize()
            raw_tools, tool_manifest_fingerprint = await self._list_all_tools(mcp_client)
            backend.start(system, prompt, [tool_spec_from_mcp(tool) for tool in raw_tools])

            # Match the historical metric: model/tool loop only, after list_tools.
            started_at = time.perf_counter()
            try:
                while iterations < max_turns:
                    turn = backend.next_turn()
                    iterations += 1
                    final_text = turn.text
                    stop_reason = turn.stop_reason
                    provider_stop_reason = turn.provider_stop_reason
                    if turn.usage is not None:
                        usage_per_iteration.append(turn.usage)
                        total_input_tokens += turn.usage.input_tokens
                        total_output_tokens += turn.usage.output_tokens
                        total_cache_read_input_tokens += turn.usage.cache_read_input_tokens
                        total_cache_creation_input_tokens += turn.usage.cache_creation_input_tokens

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
                    if stop_reason is StopReason.REFUSAL:
                        break

                    if not turn.tool_calls:
                        if stop_reason is StopReason.PAUSE_TURN and iterations < max_turns:
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
                        if evidence_active:
                            calls[idx]["observed_sentinels"] = sorted(
                                set(
                                    observed_sentinel_labels(
                                        result.text,
                                        evidence,
                                        request_args=calls[idx]["args"],
                                        evidence_targets=targets,
                                    )
                                )
                                | set(
                                    observed_aggregate_labels(
                                        result.text,
                                        aggregates,
                                        request_args=calls[idx]["args"],
                                        evidence_targets=targets,
                                    )
                                )
                            )
                        pending_results.append((idx, result.text))
                    if matched_ids != set(call_indices) or len(call_indices) != len(turn.tool_calls):
                        result_pair_mismatch = True

                    backend.add_tool_results(tool_results)
                    if iterations >= max_turns:
                        hit_max_iterations = stop_reason not in (
                            StopReason.END_TURN,
                            StopReason.MAX_TOKENS,
                        )
                        break
                    if stop_reason is not StopReason.TOOL_USE:
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
            count_estimated = False
            if callable(counter):
                try:
                    raw_count = counter(result_text)
                    if inspect.isawaitable(raw_count):
                        raw_count = await raw_count
                    counted = int(raw_count)
                except Exception:
                    token_count_failures += 1
            if counted is None:
                counted = estimate_result_tokens(len(result_text))
                count_estimated = True
                result_tokens_estimated = True
            calls[idx]["result_tokens"] = counted
            calls[idx]["result_tokens_estimated"] = count_estimated
            calls[idx]["result_token_count_method"] = TOKEN_ESTIMATE_METHOD if count_estimated else "backend"

        usage_total = {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "cache_read_input_tokens": total_cache_read_input_tokens,
            "cache_creation_input_tokens": total_cache_creation_input_tokens,
            "source": "iterations",
        }
        if manifest_state["stale"]:
            tool_manifest_fingerprint = None
        return AgentRun(
            calls=calls,
            final_text=final_text,
            usage=usage_per_iteration[-1] if usage_per_iteration else None,
            usage_total=usage_total,
            usage_scope="iteration",
            stopped_reason=(stop_reason or StopReason.UNKNOWN).value,
            provider_stop_reason=provider_stop_reason,
            call_source="api",
            hit_max_turns=hit_max_iterations,
            wall_time_s=round(wall_time_s, 3),
            usage_per_iteration=usage_per_iteration,
            cum_input_tokens=total_input_tokens,
            result_pair_mismatch=result_pair_mismatch,
            trace_integrity=not result_pair_mismatch,
            trace_integrity_reason="result_pair_mismatch" if result_pair_mismatch else None,
            tool_manifest_fingerprint=tool_manifest_fingerprint,
            token_count_failures=token_count_failures,
            result_tokens_estimated=result_tokens_estimated,
            evidence_trace_available=evidence_active,
            provider=str(backend.provider),
            model=str(backend.actual_model),
            requested_model=model,
        )


@dataclass
class CliLaunch:
    """Vendor-prepared CLI launch details."""

    cwd: Path
    config_args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None


@dataclass
class CliOutput:
    """Normalized vendor output consumed by the shared ``AgentRun`` assembly."""

    final_text: str
    calls: list[dict[str, Any]] = field(default_factory=list)
    client_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    usage_total: dict[str, Any] | None = None
    stopped_reason: str = "end_turn"
    raw_ref: str | None = None
    call_source: str = "json"
    hit_max_turns: bool = False


class CliOutputError(RuntimeError):
    """Signal that vendor output could not produce a valid ``AgentRun``."""


class CliRunError(RuntimeError):
    """CLI failure retaining typed sidecar observations for the result row."""

    def __init__(self, message: str, sidecar: ProxySidecarResult | None = None) -> None:
        super().__init__(message)
        self.trace_integrity = sidecar.trace_integrity if sidecar is not None else True
        self.trace_integrity_reason = sidecar.trace_integrity_reason if sidecar is not None else None
        self.tool_manifest_fingerprint = sidecar.tool_manifest_fingerprint if sidecar is not None else None


class CliDriver(ABC):
    """Template for CLI drivers that run one MCP-backed subprocess task."""

    name: str
    experimental = False
    default_call_source = "json"
    run_notes: tuple[str, ...] = ()
    temp_dir_prefix = "plane-eval-cli-"
    temp_dir_in_cwd = False
    exit_note_prefix: str | None = None
    include_stderr_in_exit_note = False

    def __init__(
        self,
        *,
        python_bin: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        server_command: list[str] | None = None,
        use_proxy: bool = True,
        record_result_payloads: bool = False,
    ) -> None:
        self.python_bin = python_bin or sys.executable
        self._runner = runner or run_cli_subprocess
        self.server_command = list(server_command) if server_command else None
        self.use_proxy = use_proxy
        self.record_result_payloads = record_result_payloads

    def validate_run(self) -> None:
        """Reject a launch before any temporary state is created, if needed."""
        return None

    @abstractmethod
    def write_mcp_config(
        self,
        temp_dir: Path,
        *,
        task_cwd: Path,
        server_command: list[str],
        child_env: dict[str, str],
    ) -> CliLaunch:
        """Write vendor MCP configuration and return launch settings."""

    @abstractmethod
    def build_command(
        self,
        prompt: str,
        *,
        model: str | None,
        max_turns: int,
        system: str | None,
        launch: CliLaunch,
    ) -> list[str]:
        """Build the vendor CLI command."""

    def invoke_cli(
        self,
        command: list[str],
        *,
        launch: CliLaunch,
        timeout_s: int,
    ) -> subprocess.CompletedProcess[str]:
        """Invoke the configured runner with the shared subprocess contract."""
        kwargs: dict[str, Any] = {
            "cwd": str(launch.cwd),
            "capture_output": True,
            "text": True,
            "timeout": timeout_s,
        }
        if launch.env is not None:
            kwargs["env"] = launch.env
        return self._runner(command, **kwargs)

    @abstractmethod
    def parse_output(
        self,
        proc: subprocess.CompletedProcess[str],
        *,
        task_cwd: Path,
        max_turns: int,
        notes: list[str],
    ) -> CliOutput:
        """Parse vendor output into the normalized CLI result shape."""

    def finalize_run(
        self,
        proc: subprocess.CompletedProcess[str],
        *,
        output: CliOutput,
        notes: list[str],
    ) -> None:
        """Apply vendor handling that must occur after proxy reconciliation."""
        del output
        if proc.returncode != 0 and self.exit_note_prefix:
            notes.append(f"{self.exit_note_prefix}_exit={proc.returncode}")
            stderr = proc.stderr or ""
            if self.include_stderr_in_exit_note and stderr.strip():
                notes.append(stderr.strip()[:500])

    def run_task(
        self,
        prompt: str,
        mcp_env: dict[str, str],
        model: str | None,
        max_turns: int,
        *,
        system: str | None = None,
        cwd: Path | None = None,
        evidence_sentinels: dict[str, Any] | None = None,
        evidence_targets: dict[str, Any] | None = None,
        evidence_aggregates: dict[str, Any] | None = None,
    ) -> AgentRun:
        """Run one CLI task using the shared configuration/proxy/timeout flow."""
        task_cwd = (cwd or REPO_ROOT).resolve()
        notes = list(self.run_notes)
        self.validate_run()
        temp_parent = str(task_cwd) if self.temp_dir_in_cwd else None

        with (
            tempfile.TemporaryDirectory(prefix=self.temp_dir_prefix, dir=temp_parent) as td,
            tempfile.TemporaryDirectory(prefix="plane-eval-evidence-") as evidence_td,
        ):
            temp_dir = Path(td)
            sidecar = temp_dir / "proxy-sidecar.jsonl"
            child_env = {
                key: value for key, value in mcp_env.items() if key.startswith("PLANE_") or key in ("PATH", "HOME")
            }
            evidence = normalize_evidence_sentinels(evidence_sentinels)
            targets = normalize_evidence_targets(evidence_targets)
            aggregates = normalize_evidence_aggregates(evidence_aggregates)
            evidence_active = bool(configured_evidence_labels(evidence, targets, aggregates))
            real_command = (
                list(self.server_command) if self.server_command else [self.python_bin, "-m", "plane_mcp", "stdio"]
            )
            server_command = real_command
            if self.use_proxy:
                evidence_path = None
                if evidence_active:
                    evidence_path = Path(evidence_td) / "proxy-evidence.json"
                    write_evidence_config(evidence_path, evidence, targets, aggregates)
                server_command = proxy_wrap_server_command(
                    real_command,
                    sidecar_path=sidecar,
                    python_bin=self.python_bin,
                    record_result_payloads=self.record_result_payloads,
                    evidence_path=evidence_path,
                )
                child_env = ensure_proxy_pythonpath(child_env)

            launch = self.write_mcp_config(
                temp_dir,
                task_cwd=task_cwd,
                server_command=server_command,
                child_env=child_env,
            )
            command = self.build_command(
                prompt,
                model=model,
                max_turns=max_turns,
                system=system,
                launch=launch,
            )
            timeout_s = max(120, max_turns * 60)
            # Persisted schema v1 defines wall time as the CLI invocation only.
            started_at = time.perf_counter()

            try:
                proc = self.invoke_cli(command, launch=launch, timeout_s=timeout_s)
            except subprocess.TimeoutExpired as exc:
                wall = time.perf_counter() - started_at
                notes.append(f"timeout after {timeout_s}s")
                note_timeout_kill(notes, exc)
                calls: list[dict[str, Any]] = []
                client_calls: list[dict[str, Any]] = []
                call_source = self.default_call_source
                trace_integrity = True
                trace_integrity_reason = None
                tool_manifest_fingerprint = None
                if self.use_proxy:
                    sidecar_result = harvest_proxy_after_cli_timeout(
                        calls,
                        client_calls,
                        sidecar,
                        notes,
                    )
                    calls, client_calls, call_source = sidecar_result
                    trace_integrity = sidecar_result.trace_integrity
                    trace_integrity_reason = sidecar_result.trace_integrity_reason
                    tool_manifest_fingerprint = sidecar_result.tool_manifest_fingerprint
                evidence_available = False
                if evidence_active and call_source == "proxy":
                    _proxy_calls, status = load_proxy_sidecar(sidecar)
                    meta = status.get("meta") if isinstance(status, dict) else None
                    evidence_available = bool(
                        status.get("state") == "complete"
                        and isinstance(meta, dict)
                        and meta.get("evidence_trace_available")
                    )
                    if not evidence_available:
                        notes.append("proxy_response_evidence_unavailable")
                return AgentRun(
                    calls=calls,
                    client_tool_calls=client_calls,
                    final_text="",
                    usage=None,
                    stopped_reason="timeout",
                    raw_ref=None,
                    usage_scope="run",
                    call_source=call_source,
                    hit_max_turns=False,
                    wall_time_s=round(wall, 3),
                    evidence_trace_available=evidence_available,
                    trace_integrity=trace_integrity,
                    trace_integrity_reason=trace_integrity_reason,
                    tool_manifest_fingerprint=tool_manifest_fingerprint,
                    experimental=self.experimental,
                    notes=notes,
                )

            wall = time.perf_counter() - started_at
            try:
                output = self.parse_output(
                    proc,
                    task_cwd=task_cwd,
                    max_turns=max_turns,
                    notes=notes,
                )
            except CliOutputError as exc:
                sidecar_result = None
                if self.use_proxy:
                    sidecar_result = apply_proxy_sidecar([], [], sidecar, notes)
                detail = "; ".join(notes)
                raise CliRunError(f"{exc}: {detail}", sidecar_result) from None

            trace_integrity = True
            trace_integrity_reason = None
            tool_manifest_fingerprint = None
            if self.use_proxy:
                sidecar_result = apply_proxy_sidecar(
                    output.calls,
                    output.client_tool_calls,
                    sidecar,
                    notes,
                )
                calls, client_calls, proxy_source = sidecar_result
                output.calls = calls
                output.client_tool_calls = client_calls
                trace_integrity = sidecar_result.trace_integrity
                trace_integrity_reason = sidecar_result.trace_integrity_reason
                tool_manifest_fingerprint = sidecar_result.tool_manifest_fingerprint
                if proxy_source == "proxy":
                    output.call_source = "proxy"

            evidence_available = False
            if evidence_active and output.call_source == "proxy":
                _proxy_calls, status = load_proxy_sidecar(sidecar)
                meta = status.get("meta") if isinstance(status, dict) else None
                evidence_available = bool(
                    status.get("state") == "complete"
                    and isinstance(meta, dict)
                    and meta.get("evidence_trace_available")
                )
                if not evidence_available:
                    notes.append("proxy_response_evidence_unavailable")

            self.finalize_run(proc, output=output, notes=notes)
            return AgentRun(
                calls=output.calls,
                client_tool_calls=output.client_tool_calls,
                final_text=output.final_text,
                usage=output.usage,
                usage_total=output.usage_total,
                stopped_reason=output.stopped_reason,
                raw_ref=output.raw_ref,
                usage_scope="run",
                call_source=output.call_source,
                hit_max_turns=output.hit_max_turns,
                wall_time_s=round(wall, 3),
                evidence_trace_available=evidence_available,
                trace_integrity=trace_integrity,
                trace_integrity_reason=trace_integrity_reason,
                tool_manifest_fingerprint=tool_manifest_fingerprint,
                experimental=self.experimental,
                notes=notes,
            )


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "KNOWN_API_PROVIDERS",
    "ApiDriver",
    "BackendFactory",
    "CliDriver",
    "CliLaunch",
    "CliOutput",
    "CliOutputError",
    "CliRunError",
    "McpSessionFactory",
    "tool_result_from_mcp",
    "tool_spec_from_mcp",
]
