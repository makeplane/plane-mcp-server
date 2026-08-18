"""Declared persisted schema for eval task-result JSONL rows."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from evals.core.token_counting import (
    TOKEN_ESTIMATE_METHOD,
    count_result_text_tokens,
    estimate_result_tokens,
)
from evals.core.tool_names import split_plane_and_client_calls

RESULT_SCHEMA_VERSION = 6
TRACE_INTEGRITY_SCHEMA_VERSION = 5

TraceIntegrityReason = Literal["recorder_loss", "protocol_violation", "result_pair_mismatch"]

# ``apply_agent_result`` owns this explicit partition. A reflection test compares
# it with every TaskResult dataclass field so additions cannot disappear silently.
AGENT_RESULT_COPY_FIELDS = (
    "final_text",
    "stop_reason",
    "provider_stop_reason",
    "hit_max_iterations",
    "result_pair_mismatch",
    "trace_integrity",
    "trace_integrity_reason",
    "tool_manifest_fingerprint",
    "token_count_failures",
    "result_tokens_estimated",
    "calls",
    "num_calls",
    "errored_calls",
    "total_result_tokens",
    "usage_per_iteration",
    "cum_input_tokens",
    "cum_input_tokens_reason",
    "wall_time_s",
    "client_tool_calls",
    "client_tool_call_count",
    "result_tokens_mode",
    "result_token_count_method",
    "usage_scope",
    "call_source",
    "evidence_trace_available",
    "driver_raw_ref",
    "driver_notes",
    "usage",
    "usage_total",
    "result_tokens_skipped_reason",
)
AGENT_RESULT_OPTIONAL_IDENTITY_FIELDS = ("provider", "model", "requested_model")
TASK_RESULT_HARNESS_FIELDS = (
    "schema_version",
    "row_type",
    "run_id",
    "fixture_seed_id",
    "ts",
    "git_sha",
    "battery",
    "task_fingerprint",
    "label",
    "driver",
    "server",
    "requested_tier",
    "resolved_model",
    "task_id",
    "author",
    "rep",
    "expected_rows",
    "success",
    "verify_note",
    "skipped",
    "error",
    "error_class",
    "cleanup_error",
    "seeded_entity_kinds",
    "randomized_seed_namespaces",
)


@dataclass(frozen=True, slots=True)
class Usage:
    """Provider-neutral token usage for one model turn."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass(slots=True)
class CallRecord:
    """Persisted metrics for one Plane or client tool call."""

    tool: str
    args_chars: int = 0
    result_tokens: int | None = None
    result_chars: int = 0
    result_kind: str = "text"
    is_error: bool = False
    result_tokens_estimated: bool | None = None
    result_token_count_method: str | None = None
    duration_ms: float | int | None = None
    action: str | None = None
    raw_tool: str | None = None
    result_tokens_skipped: str | None = None
    # None means the response was not checked; [] means checked with no match.
    observed_sentinels: list[str] | None = None


@dataclass
class AgentRun:
    """Normalized result of one agent task execution."""

    # Plane MCP tools only: {tool, args, origin='plane', raw_tool?}
    calls: list[dict[str, Any]]
    final_text: str
    usage: Usage | dict[str, Any] | None
    stopped_reason: str
    raw_ref: str | None = None
    # Client/harness built-ins (ToolSearch, Bash, …) are retained separately.
    client_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # Cache-aware run totals (CLI); do not put uncached-only input_tokens into cum_input_tokens
    usage_total: dict[str, Any] | None = None
    # Harness extras (optional; defaults keep CLI paths simple)
    usage_scope: str = "run"  # 'run' | 'iteration'
    call_source: str = "unknown"  # 'json' | 'transcript' | 'stream' | 'api'
    hit_max_turns: bool = False
    wall_time_s: float = 0.0
    experimental: bool = False
    notes: list[str] = field(default_factory=list)
    usage_per_iteration: list[Usage] = field(default_factory=list)
    cum_input_tokens: int | None = None
    result_pair_mismatch: bool = False
    trace_integrity: bool | None = True
    trace_integrity_reason: TraceIntegrityReason | None = None
    tool_manifest_fingerprint: str | None = None
    token_count_failures: int = 0
    # False means a tokenizer/backend counter was used for every result; True
    # means at least one result used the shared character estimate. None lets
    # the common row mapper determine the status from the recorded calls.
    result_tokens_estimated: bool | None = None
    evidence_trace_available: bool = False
    provider: str | None = None
    model: str | None = None
    requested_model: str | None = None
    # Raw provider finish/stop value. API drivers keep this beside the
    # harness-owned normalized ``stopped_reason`` for diagnostics.
    provider_stop_reason: str | None = None


@dataclass(slots=True)
class TaskResult:
    """One task repetition and the complete persisted row schema.

    schema_version 0 marks rows written before this type existed; from_row defaults every
    field added since. Version 1 defines wall_time_s as CLI invocation time only — earlier
    Claude/Antigravity/OpenCode rows also include a few ms of harness setup. Version 2 adds
    run-completeness metadata and cleanup failure recording. Version 3 records only
    response-evidence labels (never Plane response bodies) plus trace availability. Version
    4 adds the task-local question fingerprint used by future intersection comparisons.
    Version 5 adds typed trace integrity and the observed tool-manifest fingerprint.
    Version 6 adds the reproducible per-repetition fixture seed id, non-secret fixture kinds,
    and randomization namespaces. Target entity ids and randomized truth values are
    deliberately excluded.
    """

    schema_version: int = RESULT_SCHEMA_VERSION
    row_type: str | None = None
    run_id: str = ""
    fixture_seed_id: str = ""
    ts: str = ""
    git_sha: str = ""
    battery: str = ""
    task_fingerprint: str = ""
    label: str = ""
    driver: str = ""
    provider: str | None = None
    server: Literal["local", "external"] = "local"
    model: str | None = None
    requested_model: str | None = None
    requested_tier: str | None = None
    resolved_model: str | None = None
    task_id: str = ""
    author: str = ""
    rep: int = 0
    expected_rows: int = 0
    success: bool = False
    verify_note: str = ""
    skipped: str | None = None
    error: str | None = None
    error_class: str | None = None
    cleanup_error: str | None = None
    seeded_entity_kinds: list[str] = field(default_factory=list)
    randomized_seed_namespaces: list[str] = field(default_factory=list)
    final_text: str = ""
    stop_reason: str | None = None
    provider_stop_reason: str | None = None
    hit_max_iterations: bool = False
    result_pair_mismatch: bool = False
    trace_integrity: bool | None = True
    trace_integrity_reason: TraceIntegrityReason | None = None
    tool_manifest_fingerprint: str | None = None
    token_count_failures: int = 0
    result_tokens_estimated: bool | None = None
    calls: list[CallRecord] = field(default_factory=list)
    num_calls: int = 0
    errored_calls: int = 0
    total_result_tokens: int = 0
    usage_per_iteration: list[Usage] = field(default_factory=list)
    cum_input_tokens: int | None = 0
    cum_input_tokens_reason: str | None = None
    wall_time_s: float = 0.0
    client_tool_calls: list[CallRecord] = field(default_factory=list)
    client_tool_call_count: int = 0
    result_tokens_mode: str | None = None
    result_token_count_method: str | None = None
    usage_scope: str | None = None
    call_source: str | None = None
    evidence_trace_available: bool = False
    driver_raw_ref: str | None = None
    driver_notes: list[str] = field(default_factory=list)
    usage: Usage | dict[str, Any] | None = None
    usage_total: dict[str, Any] | None = None
    result_tokens_skipped_reason: str | None = None

    def apply_agent_result(self, agent: TaskResult) -> None:
        """Copy the driver-owned portion of an agent result onto this task row."""
        for field_name in AGENT_RESULT_COPY_FIELDS:
            setattr(self, field_name, getattr(agent, field_name))
        for field_name in AGENT_RESULT_OPTIONAL_IDENTITY_FIELDS:
            value = getattr(agent, field_name)
            if value is not None:
                setattr(self, field_name, value)

    def to_row(self) -> dict[str, Any]:
        """Serialize the versioned persisted JSONL row schema.

        Per-iteration usage deliberately retains the established short keys
        ``in``, ``out``, ``cache_read``, and ``cache_write``. Nothing in this
        repository reads those keys; they are archival data for humans and
        ad-hoc analysis, so the on-disk spelling remains stable here.
        """

        def usage_row(item: Usage) -> dict[str, int]:
            return {
                "in": item.input_tokens,
                "out": item.output_tokens,
                "cache_read": item.cache_read_input_tokens,
                "cache_write": item.cache_creation_input_tokens,
            }

        calls: list[dict[str, Any]] = []
        for call in self.calls:
            item: dict[str, Any] = {
                "tool": call.tool,
                "args_chars": call.args_chars,
                "result_tokens": call.result_tokens,
                "result_chars": call.result_chars,
                "result_kind": call.result_kind,
                "is_error": call.is_error,
                "result_tokens_estimated": call.result_tokens_estimated,
                "result_token_count_method": call.result_token_count_method,
            }
            if call.duration_ms is not None:
                item["duration_ms"] = call.duration_ms
            if call.action is not None:
                item["action"] = call.action
            if call.result_tokens_skipped is not None:
                item["result_tokens_skipped"] = call.result_tokens_skipped
            if call.observed_sentinels is not None:
                item["observed_sentinels"] = list(call.observed_sentinels)
            calls.append(item)

        client_calls = [
            {
                "tool": call.tool,
                "args_chars": call.args_chars,
                "raw_tool": call.raw_tool or call.tool,
            }
            for call in self.client_tool_calls
        ]
        row: dict[str, Any] = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "fixture_seed_id": self.fixture_seed_id,
            "ts": self.ts,
            "git_sha": self.git_sha,
            "battery": self.battery,
            "task_fingerprint": self.task_fingerprint,
            "label": self.label,
            "driver": self.driver,
            "provider": self.provider,
            "server": self.server,
            "model": self.model,
            "requested_model": self.requested_model,
            "requested_tier": self.requested_tier,
            "resolved_model": self.resolved_model,
            "task_id": self.task_id,
            "author": self.author,
            "rep": self.rep,
            "expected_rows": self.expected_rows,
            "success": self.success,
            "verify_note": self.verify_note,
            "skipped": self.skipped,
            "error": self.error,
            "error_class": self.error_class,
            "cleanup_error": self.cleanup_error,
            "seeded_entity_kinds": list(self.seeded_entity_kinds),
            "randomized_seed_namespaces": list(self.randomized_seed_namespaces),
            "final_text": self.final_text,
            "stop_reason": self.stop_reason,
            "provider_stop_reason": self.provider_stop_reason,
            "hit_max_iterations": self.hit_max_iterations,
            "result_pair_mismatch": self.result_pair_mismatch,
            "trace_integrity": self.trace_integrity,
            "trace_integrity_reason": self.trace_integrity_reason,
            "tool_manifest_fingerprint": self.tool_manifest_fingerprint,
            "token_count_failures": self.token_count_failures,
            "result_tokens_estimated": self.result_tokens_estimated,
            "calls": calls,
            "num_calls": self.num_calls,
            "errored_calls": self.errored_calls,
            "total_result_tokens": self.total_result_tokens,
            "usage_per_iteration": [usage_row(item) for item in self.usage_per_iteration],
            "cum_input_tokens": self.cum_input_tokens,
            "cum_input_tokens_reason": self.cum_input_tokens_reason,
            "wall_time_s": self.wall_time_s,
            "client_tool_calls": client_calls,
            "client_tool_call_count": self.client_tool_call_count,
            "result_tokens_mode": self.result_tokens_mode,
            "result_token_count_method": self.result_token_count_method,
            "usage_scope": self.usage_scope,
            "call_source": self.call_source,
            "evidence_trace_available": self.evidence_trace_available,
            "driver_raw_ref": self.driver_raw_ref,
            "driver_notes": list(self.driver_notes),
            "usage": usage_row(self.usage) if isinstance(self.usage, Usage) else self.usage,
            "usage_total": self.usage_total,
        }
        if self.row_type is not None:
            row["row_type"] = self.row_type
        if self.result_tokens_skipped_reason is not None:
            row["result_tokens_skipped_reason"] = self.result_tokens_skipped_reason
        return row

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> TaskResult:
        """Read a persisted row, retaining defaults for unrelated older fields."""
        raw_calls = row.get("calls") if isinstance(row.get("calls"), list) else []
        calls: list[CallRecord] = []
        for raw in raw_calls:
            if not isinstance(raw, dict):
                continue
            calls.append(
                CallRecord(
                    tool=str(raw.get("tool") or ""),
                    args_chars=int(raw.get("args_chars") or 0),
                    result_tokens=(int(raw["result_tokens"]) if raw.get("result_tokens") is not None else None),
                    result_chars=int(raw.get("result_chars") or 0),
                    result_kind=str(raw.get("result_kind") or "text"),
                    is_error=bool(raw.get("is_error")),
                    result_tokens_estimated=(
                        bool(raw["result_tokens_estimated"]) if raw.get("result_tokens_estimated") is not None else None
                    ),
                    result_token_count_method=(
                        str(raw["result_token_count_method"])
                        if raw.get("result_token_count_method") is not None
                        else None
                    ),
                    duration_ms=raw.get("duration_ms"),
                    action=(str(raw["action"]) if raw.get("action") is not None else None),
                    result_tokens_skipped=(
                        str(raw["result_tokens_skipped"]) if raw.get("result_tokens_skipped") is not None else None
                    ),
                    observed_sentinels=(
                        [str(value) for value in raw["observed_sentinels"]]
                        if isinstance(raw.get("observed_sentinels"), list)
                        else None
                    ),
                )
            )

        raw_client_calls = row.get("client_tool_calls") if isinstance(row.get("client_tool_calls"), list) else []
        client_calls: list[CallRecord] = []
        for raw in raw_client_calls:
            if not isinstance(raw, dict):
                continue
            client_calls.append(
                CallRecord(
                    tool=str(raw.get("tool") or raw.get("raw_tool") or ""),
                    args_chars=int(raw.get("args_chars") or 0),
                    raw_tool=str(raw.get("raw_tool") or raw.get("tool") or ""),
                )
            )

        raw_usage = row.get("usage_per_iteration")
        usage_per_iteration: list[Usage] = []
        if isinstance(raw_usage, list):
            for item in raw_usage:
                if not isinstance(item, dict):
                    continue
                usage_per_iteration.append(
                    Usage(
                        input_tokens=int(item.get("in") or 0),
                        output_tokens=int(item.get("out") or 0),
                        cache_read_input_tokens=int(item.get("cache_read") or 0),
                        cache_creation_input_tokens=int(item.get("cache_write") or 0),
                    )
                )

        return cls(
            schema_version=int(row.get("schema_version") or 0),
            row_type=(str(row["row_type"]) if row.get("row_type") is not None else None),
            run_id=str(row.get("run_id") or ""),
            fixture_seed_id=str(row.get("fixture_seed_id") or ""),
            ts=str(row.get("ts") or ""),
            git_sha=str(row.get("git_sha") or ""),
            battery=str(row.get("battery") or ""),
            task_fingerprint=str(row.get("task_fingerprint") or ""),
            label=str(row.get("label") or ""),
            driver=str(row.get("driver") or ""),
            provider=(str(row["provider"]) if row.get("provider") is not None else None),
            server="external" if row.get("server") == "external" else "local",
            model=(str(row["model"]) if row.get("model") is not None else None),
            requested_model=(str(row["requested_model"]) if row.get("requested_model") is not None else None),
            requested_tier=(str(row["requested_tier"]) if row.get("requested_tier") is not None else None),
            resolved_model=(str(row["resolved_model"]) if row.get("resolved_model") is not None else None),
            task_id=str(row.get("task_id") or ""),
            author=str(row.get("author") or ""),
            rep=int(row.get("rep") or 0),
            expected_rows=int(row.get("expected_rows") or 0),
            success=bool(row.get("success")),
            verify_note=str(row.get("verify_note") or ""),
            skipped=(str(row["skipped"]) if row.get("skipped") is not None else None),
            error=(str(row["error"]) if row.get("error") is not None else None),
            error_class=(str(row["error_class"]) if row.get("error_class") is not None else None),
            cleanup_error=(str(row["cleanup_error"]) if row.get("cleanup_error") is not None else None),
            seeded_entity_kinds=(
                [str(kind) for kind in row["seeded_entity_kinds"]]
                if isinstance(row.get("seeded_entity_kinds"), list)
                else []
            ),
            randomized_seed_namespaces=(
                [str(namespace) for namespace in row["randomized_seed_namespaces"]]
                if isinstance(row.get("randomized_seed_namespaces"), list)
                else []
            ),
            final_text=str(row.get("final_text") or ""),
            stop_reason=(str(row["stop_reason"]) if row.get("stop_reason") is not None else None),
            provider_stop_reason=(
                str(row["provider_stop_reason"]) if row.get("provider_stop_reason") is not None else None
            ),
            hit_max_iterations=bool(row.get("hit_max_iterations")),
            result_pair_mismatch=bool(row.get("result_pair_mismatch")),
            trace_integrity=(bool(row["trace_integrity"]) if row.get("trace_integrity") is not None else None),
            trace_integrity_reason=(
                str(row["trace_integrity_reason"])
                if row.get("trace_integrity_reason")
                in {
                    "recorder_loss",
                    "protocol_violation",
                    "result_pair_mismatch",
                }
                else None
            ),
            tool_manifest_fingerprint=(
                str(row["tool_manifest_fingerprint"]) if row.get("tool_manifest_fingerprint") is not None else None
            ),
            token_count_failures=int(row.get("token_count_failures") or 0),
            result_tokens_estimated=(
                bool(row["result_tokens_estimated"]) if row.get("result_tokens_estimated") is not None else None
            ),
            calls=calls,
            num_calls=int(row.get("num_calls") if row.get("num_calls") is not None else len(calls)),
            errored_calls=int(
                row.get("errored_calls")
                if row.get("errored_calls") is not None
                else sum(1 for call in calls if call.is_error)
            ),
            total_result_tokens=int(
                row.get("total_result_tokens")
                if row.get("total_result_tokens") is not None
                else sum(call.result_tokens or 0 for call in calls)
            ),
            usage_per_iteration=usage_per_iteration,
            cum_input_tokens=(int(row["cum_input_tokens"]) if row.get("cum_input_tokens") is not None else None),
            cum_input_tokens_reason=(
                str(row["cum_input_tokens_reason"]) if row.get("cum_input_tokens_reason") is not None else None
            ),
            wall_time_s=float(row.get("wall_time_s") or 0.0),
            client_tool_calls=client_calls,
            client_tool_call_count=int(
                row.get("client_tool_call_count")
                if row.get("client_tool_call_count") is not None
                else len(client_calls)
            ),
            result_tokens_mode=(str(row["result_tokens_mode"]) if row.get("result_tokens_mode") is not None else None),
            result_token_count_method=(
                str(row["result_token_count_method"]) if row.get("result_token_count_method") is not None else None
            ),
            usage_scope=(str(row["usage_scope"]) if row.get("usage_scope") is not None else None),
            call_source=(str(row["call_source"]) if row.get("call_source") is not None else None),
            evidence_trace_available=bool(row.get("evidence_trace_available")),
            driver_raw_ref=(str(row["driver_raw_ref"]) if row.get("driver_raw_ref") is not None else None),
            driver_notes=[str(item) for item in row.get("driver_notes") or []],
            usage=row.get("usage") if isinstance(row.get("usage"), dict) else None,
            usage_total=(row.get("usage_total") if isinstance(row.get("usage_total"), dict) else None),
            result_tokens_skipped_reason=(
                str(row["result_tokens_skipped_reason"])
                if row.get("result_tokens_skipped_reason") is not None
                else None
            ),
        )


def agent_run_to_task_result(
    run: AgentRun,
) -> TaskResult:
    """Map an ``AgentRun`` onto the typed driver-owned portion of a task result.

    Only Plane MCP tools count toward num_calls; client built-ins go to client_tool_calls.
    CLI drivers never fill cum_input_tokens from bare usage.input_tokens — under Claude
    Code that is uncached-only and misreads cached runs as ~10 tokens.
    """
    # Re-split in case callers passed a mixed list
    plane_src, client_extra = split_plane_and_client_calls(list(run.calls))
    client_src = list(run.client_tool_calls) + client_extra

    is_cli = run.call_source in ("json", "transcript", "stream", "proxy") or run.usage_scope == "run"
    calls: list[CallRecord] = []
    local_token_count_failures = 0
    for c in plane_src:
        tool = c.get("tool") or ""
        args = c.get("args") or {}
        try:
            args_chars = len(json.dumps(args, default=str))
        except Exception:
            args_chars = len(str(args))
        result_chars = int(c["result_chars"]) if c.get("result_chars") is not None else 0
        result_tokens = c.get("result_tokens")
        estimated = c.get("result_tokens_estimated")
        count_method = c.get("result_token_count_method")
        if result_tokens is not None:
            result_tokens = int(result_tokens)
            if estimated is None:
                estimated = bool(run.result_tokens_estimated)
            if count_method is None:
                count_method = TOKEN_ESTIMATE_METHOD if estimated else "backend"
        elif isinstance(c.get("result_text"), str):
            count = count_result_text_tokens(c["result_text"])
            result_tokens = count.value
            estimated = count.estimated
            count_method = count.method
            local_token_count_failures += int(count.tokenizer_failed)
        else:
            result_tokens = estimate_result_tokens(result_chars)
            estimated = True
            count_method = TOKEN_ESTIMATE_METHOD

        rec = CallRecord(
            tool=str(tool),
            args_chars=args_chars,
            result_tokens=result_tokens,
            result_chars=result_chars,
            result_kind=str(c.get("result_kind") or "text"),
            is_error=bool(c.get("is_error")),
            result_tokens_estimated=bool(estimated),
            result_token_count_method=str(count_method),
            duration_ms=c.get("duration_ms"),
            observed_sentinels=(
                [str(value) for value in c["observed_sentinels"]]
                if isinstance(c.get("observed_sentinels"), list)
                else None
            ),
        )
        # Action-dispatch surfaces: the action arg IS the second half of the
        # tool choice — keep it (args content is otherwise not persisted).
        if isinstance(args, dict) and isinstance(args.get("action"), str):
            rec.action = args["action"]
        calls.append(rec)

    client_tool_calls: list[CallRecord] = []
    for c in client_src:
        tool = c.get("tool") or c.get("raw_tool") or ""
        args = c.get("args") or {}
        try:
            args_chars = len(json.dumps(args, default=str))
        except Exception:
            args_chars = len(str(args))
        client_tool_calls.append(
            CallRecord(
                tool=str(tool),
                args_chars=args_chars,
                raw_tool=str(c.get("raw_tool") or tool),
            )
        )

    stop_reason = run.stopped_reason
    hit_max = run.hit_max_turns
    if hit_max:
        stop_reason = stop_reason if stop_reason not in ("end_turn", "completed", None, "") else "max_turns"

    errored = sum(1 for c in calls if c.is_error)

    # CLI path: never write misleading cum_input_tokens from uncached-only field.
    # usage_total is driver-owned — do not re-derive it here (Claude vs Codex
    # shapes differ; a generic Claude rebuild mislabels other vendors).
    usage_total = run.usage_total

    if run.usage_per_iteration:
        usage_per_iteration = list(run.usage_per_iteration)
        cum_input = (
            run.cum_input_tokens
            if run.cum_input_tokens is not None
            else sum(item.input_tokens for item in usage_per_iteration)
        )
        cum_reason = None
    elif is_cli:
        cum_input: int | None = None
        cum_reason: str | None = (
            "CLI driver: Claude usage.input_tokens is uncached-only; "
            "see usage_total (cache_read/cache_creation/output/cost) for run accounting"
        )
        usage_per_iteration: list[Usage] = []
    else:
        cum_input = 0
        cum_reason = None
        usage_per_iteration = []

    estimated_states = [bool(c.result_tokens_estimated) for c in calls]
    if estimated_states:
        result_tokens_estimated = any(estimated_states)
        result_tokens_mode = (
            "estimated" if all(estimated_states) else "measured" if not any(estimated_states) else "mixed"
        )
    else:
        result_tokens_estimated = (
            bool(run.result_tokens_estimated) if run.result_tokens_estimated is not None else is_cli
        )
        result_tokens_mode = "estimated" if result_tokens_estimated else "measured"

    count_methods = {str(c.result_token_count_method) for c in calls}
    if not count_methods:
        result_token_count_method = "none"
    elif len(count_methods) == 1:
        result_token_count_method = next(iter(count_methods))
    else:
        result_token_count_method = "mixed"
    return TaskResult(
        final_text=run.final_text,
        calls=calls,
        num_calls=len(calls),
        client_tool_calls=client_tool_calls,
        client_tool_call_count=len(client_tool_calls),
        errored_calls=errored,
        total_result_tokens=sum(int(c.result_tokens or 0) for c in calls),
        usage_per_iteration=usage_per_iteration,
        cum_input_tokens=cum_input,
        cum_input_tokens_reason=cum_reason,
        wall_time_s=run.wall_time_s,
        stop_reason=stop_reason,
        provider_stop_reason=run.provider_stop_reason,
        hit_max_iterations=hit_max,
        result_pair_mismatch=run.result_pair_mismatch,
        trace_integrity=run.trace_integrity,
        trace_integrity_reason=run.trace_integrity_reason,
        tool_manifest_fingerprint=run.tool_manifest_fingerprint,
        token_count_failures=run.token_count_failures + local_token_count_failures,
        result_tokens_estimated=result_tokens_estimated,
        result_tokens_mode=result_tokens_mode,
        result_token_count_method=result_token_count_method,
        usage_scope=run.usage_scope,
        call_source=run.call_source,
        evidence_trace_available=run.evidence_trace_available,
        driver_raw_ref=run.raw_ref,
        driver_notes=list(run.notes),
        usage=run.usage,
        usage_total=usage_total,
        provider=run.provider,
        model=run.model,
        requested_model=run.requested_model,
    )


def agent_run_to_harness_dict(
    run: AgentRun,
) -> dict[str, Any]:
    """Map an agent run to the public persisted-row dictionary."""
    return agent_run_to_task_result(run).to_row()


__all__ = [
    "AGENT_RESULT_COPY_FIELDS",
    "AGENT_RESULT_OPTIONAL_IDENTITY_FIELDS",
    "RESULT_SCHEMA_VERSION",
    "TRACE_INTEGRITY_SCHEMA_VERSION",
    "TASK_RESULT_HARNESS_FIELDS",
    "AgentRun",
    "CallRecord",
    "TaskResult",
    "TraceIntegrityReason",
    "Usage",
    "agent_run_to_harness_dict",
    "agent_run_to_task_result",
]
