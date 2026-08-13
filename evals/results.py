"""Declared persisted schema for eval task-result JSONL rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RESULT_SCHEMA_VERSION = 1


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
    classification: str | None = None
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


@dataclass(slots=True)
class TaskResult:
    """One task repetition and the complete persisted row schema.

    ``schema_version=0`` identifies rows written before this type existed;
    :meth:`from_row` supplies defaults for every field added since. Version 1
    defines ``wall_time_s`` as CLI invocation time only, excluding harness-owned
    config/temp-directory setup. Pre-versioned Claude, Antigravity, and OpenCode
    rows include a few milliseconds of that setup; Codex and API rows already
    used invocation/agent-loop timing.
    """

    schema_version: int = RESULT_SCHEMA_VERSION
    row_type: str | None = None
    run_id: str = ""
    ts: str = ""
    git_sha: str = ""
    battery: str = ""
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
    success: bool = False
    verify_note: str = ""
    skipped: str | None = None
    error: str | None = None
    error_class: str | None = None
    final_text: str = ""
    stop_reason: str | None = None
    provider_stop_reason: str | None = None
    hit_max_iterations: bool = False
    result_pair_mismatch: bool = False
    token_count_failures: int = 0
    result_tokens_estimated: bool | None = None
    calls: list[CallRecord] = field(default_factory=list)
    num_calls: int = 0
    errored_calls: int = 0
    alternate_calls: int | None = 0
    out_of_set_calls: int | None = 0
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
    driver_raw_ref: str | None = None
    driver_notes: list[str] = field(default_factory=list)
    usage: Usage | dict[str, Any] | None = None
    usage_total: dict[str, Any] | None = None
    result_tokens_skipped_reason: str | None = None

    def apply_agent_result(self, agent: TaskResult) -> None:
        """Copy the driver-owned portion of an agent result onto this task row."""
        self.final_text = agent.final_text
        self.stop_reason = agent.stop_reason
        self.provider_stop_reason = agent.provider_stop_reason
        self.hit_max_iterations = agent.hit_max_iterations
        self.result_pair_mismatch = agent.result_pair_mismatch
        self.token_count_failures = agent.token_count_failures
        self.result_tokens_estimated = agent.result_tokens_estimated
        self.calls = agent.calls
        self.num_calls = agent.num_calls
        self.errored_calls = agent.errored_calls
        self.alternate_calls = agent.alternate_calls
        self.out_of_set_calls = agent.out_of_set_calls
        self.total_result_tokens = agent.total_result_tokens
        self.usage_per_iteration = agent.usage_per_iteration
        self.cum_input_tokens = agent.cum_input_tokens
        self.cum_input_tokens_reason = agent.cum_input_tokens_reason
        self.wall_time_s = agent.wall_time_s
        self.client_tool_calls = agent.client_tool_calls
        self.client_tool_call_count = agent.client_tool_call_count
        self.result_tokens_mode = agent.result_tokens_mode
        self.result_token_count_method = agent.result_token_count_method
        self.usage_scope = agent.usage_scope
        self.call_source = agent.call_source
        self.driver_raw_ref = agent.driver_raw_ref
        self.driver_notes = agent.driver_notes
        self.usage = agent.usage
        self.usage_total = agent.usage_total
        if agent.provider is not None:
            self.provider = agent.provider
        if agent.model is not None:
            self.model = agent.model
        if agent.requested_model is not None:
            self.requested_model = agent.requested_model

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
                "class": call.classification,
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
            "ts": self.ts,
            "git_sha": self.git_sha,
            "battery": self.battery,
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
            "success": self.success,
            "verify_note": self.verify_note,
            "skipped": self.skipped,
            "error": self.error,
            "error_class": self.error_class,
            "final_text": self.final_text,
            "stop_reason": self.stop_reason,
            "provider_stop_reason": self.provider_stop_reason,
            "hit_max_iterations": self.hit_max_iterations,
            "result_pair_mismatch": self.result_pair_mismatch,
            "token_count_failures": self.token_count_failures,
            "result_tokens_estimated": self.result_tokens_estimated,
            "calls": calls,
            "num_calls": self.num_calls,
            "errored_calls": self.errored_calls,
            "alternate_calls": self.alternate_calls,
            "out_of_set_calls": self.out_of_set_calls,
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
        """Read current or pre-versioned persisted rows with stable defaults."""
        raw_calls = row.get("calls") if isinstance(row.get("calls"), list) else []
        calls: list[CallRecord] = []
        for raw in raw_calls:
            if not isinstance(raw, dict):
                continue
            calls.append(
                CallRecord(
                    tool=str(raw.get("tool") or ""),
                    classification=(str(raw["class"]) if raw.get("class") is not None else None),
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

        alternate_default = sum(1 for call in calls if call.classification == "alternate")
        out_of_set_default = sum(1 for call in calls if call.classification == "out_of_set")
        return cls(
            schema_version=int(row.get("schema_version") or 0),
            row_type=(str(row["row_type"]) if row.get("row_type") is not None else None),
            run_id=str(row.get("run_id") or ""),
            ts=str(row.get("ts") or ""),
            git_sha=str(row.get("git_sha") or ""),
            battery=str(row.get("battery") or ""),
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
            success=bool(row.get("success")),
            verify_note=str(row.get("verify_note") or ""),
            skipped=(str(row["skipped"]) if row.get("skipped") is not None else None),
            error=(str(row["error"]) if row.get("error") is not None else None),
            error_class=(str(row["error_class"]) if row.get("error_class") is not None else None),
            final_text=str(row.get("final_text") or ""),
            stop_reason=(str(row["stop_reason"]) if row.get("stop_reason") is not None else None),
            provider_stop_reason=(
                str(row["provider_stop_reason"]) if row.get("provider_stop_reason") is not None else None
            ),
            hit_max_iterations=bool(row.get("hit_max_iterations")),
            result_pair_mismatch=bool(row.get("result_pair_mismatch")),
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
            alternate_calls=(
                int(row["alternate_calls"])
                if row.get("alternate_calls") is not None
                else None
                if "alternate_calls" in row
                else alternate_default
            ),
            out_of_set_calls=(
                int(row["out_of_set_calls"])
                if row.get("out_of_set_calls") is not None
                else None
                if "out_of_set_calls" in row
                else out_of_set_default
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


__all__ = ["RESULT_SCHEMA_VERSION", "CallRecord", "TaskResult", "Usage"]
