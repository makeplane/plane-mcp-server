"""Aggregate evaluation results and measure observed task instability."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from evals.results import TRACE_INTEGRITY_SCHEMA_VERSION, TaskResult
from evals.skip_taxonomy import is_expected_environment_capability_skip, skip_reason_family

from .load import ResultRow, RunKeyValidation, is_infra_error_row, is_meta_row, read_result
from .statistics import cluster_bootstrap_mean_ci, iqr, median, percentile, wilson_interval

ResultTokensMode = Literal["measured", "estimated", "mixed", "unlabeled", "unavailable"]


@dataclass(slots=True)
class TaskSummary:
    task_id: str
    n: int
    k: int
    wilson_lo: float
    wilson_hi: float
    med_calls: float | None
    calls_min: float | None
    calls_max: float | None
    calls_q1: float | None
    calls_q3: float | None
    tool_reps: int
    failed_tool_reps: int
    tool_rep_frequency: dict[str, float]
    tool_call_counts: dict[str, int]
    errored_calls: int
    capped: int
    harness_err: int
    infra_err: int
    med_result_tokens: float | None
    p95_result_tokens: float | None
    result_tokens_mode: ResultTokensMode
    med_cum_input: float | None

    @property
    def unstable(self) -> bool:
        """True when repetitions of this task disagreed on pass/fail."""
        return self.n > 1 and 0 < self.k < self.n

    @property
    def success(self) -> str:
        return f"{self.k}/{self.n}" if self.n else "0/0"

    @property
    def tool_distribution_available(self) -> bool:
        return self.tool_reps >= 2

    @property
    def variable_tool_names(self) -> list[str]:
        return [tool for tool, frequency in self.tool_rep_frequency.items() if frequency < 1.0]


@dataclass(slots=True)
class Summary:
    tasks: dict[str, TaskSummary]
    total_tasks: int
    expected_rows: int
    completed_rows: int
    infra_errors: int
    harness_errors: int
    expected_skips: int
    unexpected_skips: int
    cleanup_errors: int
    trace_invalid_rows: int
    expected_skip_reasons: dict[str, int]
    unexpected_skip_reasons: dict[str, int]
    skipped_task_reasons: dict[str, list[str]]
    aggregate_k: int
    aggregate_n: int
    aggregate_wilson_lo: float
    aggregate_wilson_hi: float
    task_mean_success: float | None
    task_cluster_lo: float | None
    task_cluster_hi: float | None
    missing_run_keys: tuple[str, ...]
    unexpected_run_keys: tuple[str, ...]
    multi_rep: bool
    result_tokens_mode: ResultTokensMode

    @property
    def complete(self) -> bool:
        return (
            self.completed_rows == self.expected_rows
            and not self.missing_run_keys
            and not self.unexpected_run_keys
            and self.infra_errors == 0
            and self.harness_errors == 0
            and self.unexpected_skips == 0
            and self.cleanup_errors == 0
            and self.trace_invalid_rows == 0
        )

    @property
    def unstable_task_ids(self) -> list[str]:
        return [task_id for task_id, task in self.tasks.items() if task.unstable]

    @property
    def unstable_tasks(self) -> int:
        return len(self.unstable_task_ids)

    @property
    def variable_tool_tasks(self) -> int:
        return sum(bool(task.variable_tool_names) for task in self.tasks.values())

    @property
    def tool_distribution_available(self) -> bool:
        return any(task.tool_distribution_available for task in self.tasks.values())


def result_tokens_mode(rows: list[ResultRow]) -> ResultTokensMode:
    """Classify token counts without treating unmarked legacy data as measured."""
    labels: set[str] = set()
    for raw_row in rows:
        row = read_result(raw_row)
        if not row.trace_integrity:
            continue
        for call in row.calls:
            if call.result_tokens is None:
                continue
            estimated = (
                call.result_tokens_estimated
                if call.result_tokens_estimated is not None
                else row.result_tokens_estimated
            )
            if estimated is True:
                labels.add("estimated")
            elif estimated is False:
                labels.add("measured")
            else:
                labels.add("unlabeled")
    if not labels:
        return "unavailable"
    if labels == {"estimated"}:
        return "estimated"
    if labels == {"measured"}:
        return "measured"
    if "unlabeled" in labels:
        return "unlabeled"
    return "mixed"


def _format_reason_counts(reasons: dict[str, int]) -> str:
    return ", ".join(f"{reason}={count}" for reason, count in sorted(reasons.items()))


def completeness_statement(summary: Summary) -> str:
    """Render completeness independently from the model success rate."""
    prefix = "RUN COMPLETE" if summary.complete else "RUN INCOMPLETE"
    parts = [f"{summary.completed_rows}/{summary.expected_rows} rows completed"]
    if summary.infra_errors:
        parts.append(f"infra errors={summary.infra_errors}")
    if summary.harness_errors:
        parts.append(f"harness errors={summary.harness_errors}")
    if summary.unexpected_skips:
        reasons = _format_reason_counts(summary.unexpected_skip_reasons)
        parts.append(f"unexpected skips={summary.unexpected_skips} [{reasons}]")
    if summary.cleanup_errors:
        parts.append(f"cleanup errors={summary.cleanup_errors}")
    if summary.trace_invalid_rows:
        parts.append(f"trace-invalid rows={summary.trace_invalid_rows}")
    if summary.missing_run_keys:
        parts.append(f"missing keys=[{', '.join(summary.missing_run_keys)}]")
    if summary.unexpected_run_keys:
        parts.append(f"unexpected keys=[{', '.join(summary.unexpected_run_keys)}]")
    if summary.expected_skips:
        reasons = _format_reason_counts(summary.expected_skip_reasons)
        parts.append(f"expected skips={summary.expected_skips} [{reasons}]")
    return f"{prefix}: " + "; ".join(parts)


def execution_coverage_statement(summary: Summary) -> str:
    """Render rows actually evaluated, independently from run completeness."""
    if summary.expected_rows:
        rate = summary.aggregate_n / summary.expected_rows
        amount = f"{summary.aggregate_n}/{summary.expected_rows} rows evaluated ({rate:.1%})"
    else:
        amount = f"{summary.aggregate_n}/0 rows evaluated (n/a)"
    parts = [amount]
    if summary.skipped_task_reasons:
        skips = "; ".join(
            f"{','.join(task_ids)} ({reason})" for reason, task_ids in sorted(summary.skipped_task_reasons.items())
        )
        parts.append(f"skipped tasks=[{skips}]")
    return "EXECUTION COVERAGE: " + "; ".join(parts)


def summarize(
    rows: list[ResultRow],
    *,
    expected_rows: int | None = None,
    run_keys: RunKeyValidation | None = None,
) -> Summary:
    """Aggregate per-task metrics.

    Rows with ``error_class`` starting ``infra_`` are excluded from success-rate
    denominators and counted separately as ``infra_errors``. Other non-null
    ``error`` rows remain harness errors (excluded from success, counted in
    ``harness_err``).
    """
    by_task: dict[str, list[TaskResult]] = defaultdict(list)
    harness_errors_by_task: dict[str, int] = defaultdict(int)
    infrastructure_errors_by_task: dict[str, int] = defaultdict(int)
    repetitions_by_task: dict[str, set[int]] = defaultdict(set)
    infrastructure_errors = 0
    harness_errors = 0
    completed_rows = 0
    expected_skips = 0
    unexpected_skips = 0
    cleanup_errors = 0
    trace_invalid_rows = 0
    expected_skip_reasons: dict[str, int] = defaultdict(int)
    unexpected_skip_reasons: dict[str, int] = defaultdict(int)
    skipped_task_reasons: dict[str, set[str]] = defaultdict(set)
    declared_expected_rows = 0
    for raw_row in rows:
        row = read_result(raw_row)
        if is_meta_row(row):
            continue
        declared_expected_rows = max(declared_expected_rows, row.expected_rows)
        task_id = row.task_id
        repetitions_by_task[task_id].add(row.rep)
        if row.trace_integrity is False or (
            row.trace_integrity is None and row.schema_version >= TRACE_INTEGRITY_SCHEMA_VERSION
        ):
            trace_invalid_rows += 1
        if row.cleanup_error:
            cleanup_errors += 1
        if is_infra_error_row(row):
            infrastructure_errors += 1
            infrastructure_errors_by_task[task_id] += 1
            continue  # infra seed/cli — excluded from success aggregates
        if row.error:
            harness_errors += 1
            harness_errors_by_task[task_id] += 1
            continue  # harness/API errors excluded from success/medians (F4)
        if row.skipped:
            family = skip_reason_family(row.skipped)
            skipped_task_reasons[row.skipped].add(task_id)
            if is_expected_environment_capability_skip(row.skipped, task_id=task_id):
                expected_skips += 1
                expected_skip_reasons[family] += 1
                completed_rows += 1
            else:
                unexpected_skips += 1
                unexpected_skip_reasons[family] += 1
            continue  # skipped rows are excluded from success denominators
        completed_rows += 1
        by_task[task_id].append(row)

    # Include tasks that only had harness/infra errors so columns stay visible.
    all_task_ids = sorted(set(by_task) | set(harness_errors_by_task) | set(infrastructure_errors_by_task))

    output: dict[str, TaskSummary] = {}
    total_passes = 0
    total_repetitions = 0
    for task_id in all_task_ids:
        task_results = by_task.get(task_id, [])
        repetition_count = len(task_results)
        pass_count = sum(1 for row in task_results if row.success)
        total_passes += pass_count
        total_repetitions += repetition_count
        lower, upper = wilson_interval(pass_count, repetition_count) if repetition_count else (0.0, 0.0)
        successful_calls = [float(row.num_calls) for row in task_results if row.success and row.trace_integrity]
        first_quartile, median_calls, third_quartile = iqr(successful_calls)
        minimum_calls = min(successful_calls) if successful_calls else None
        maximum_calls = max(successful_calls) if successful_calls else None
        successful_results = [row for row in task_results if row.success and row.trace_integrity]
        tool_reps = len(successful_results)
        failed_tool_reps = (
            repetition_count
            - tool_reps
            + harness_errors_by_task.get(task_id, 0)
            + infrastructure_errors_by_task.get(task_id, 0)
        )
        tool_rep_counts: dict[str, int] = defaultdict(int)
        tool_call_counts: dict[str, int] = defaultdict(int)
        for row in successful_results:
            tools_in_rep: set[str] = set()
            for call in row.calls:
                if not call.tool:
                    continue
                tool_call_counts[call.tool] += 1
                tools_in_rep.add(call.tool)
            for tool in tools_in_rep:
                tool_rep_counts[tool] += 1
        tool_rep_frequency = (
            {tool: tool_rep_counts[tool] / tool_reps for tool in sorted(tool_rep_counts)} if tool_reps >= 2 else {}
        )
        errored_calls = 0
        result_tokens: list[float] = []
        for row in task_results:
            if not row.trace_integrity:
                continue
            for call in row.calls:
                if call.is_error:
                    errored_calls += 1
                if call.result_tokens is not None:
                    result_tokens.append(float(call.result_tokens))
        capped = sum(1 for row in task_results if row.hit_max_iterations or row.stop_reason == "max_tokens")
        cumulative_inputs = [float(row.cum_input_tokens or 0) for row in task_results]
        output[task_id] = TaskSummary(
            task_id=task_id,
            n=repetition_count,
            k=pass_count,
            wilson_lo=lower,
            wilson_hi=upper,
            med_calls=median_calls,
            calls_min=minimum_calls,
            calls_max=maximum_calls,
            calls_q1=first_quartile,
            calls_q3=third_quartile,
            tool_reps=tool_reps,
            failed_tool_reps=failed_tool_reps,
            tool_rep_frequency=tool_rep_frequency,
            tool_call_counts={tool: tool_call_counts[tool] for tool in sorted(tool_call_counts)},
            errored_calls=errored_calls,
            capped=capped,
            harness_err=harness_errors_by_task.get(task_id, 0),
            infra_err=infrastructure_errors_by_task.get(task_id, 0),
            med_result_tokens=median(result_tokens),
            p95_result_tokens=percentile(result_tokens, 0.95),
            result_tokens_mode=result_tokens_mode(task_results),
            med_cum_input=median(cumulative_inputs),
        )
    aggregate_lower, aggregate_upper = (
        wilson_interval(total_passes, total_repetitions) if total_repetitions else (0.0, 0.0)
    )
    task_rates = [task.k / task.n for task in output.values() if task.n]
    task_mean_success = sum(task_rates) / len(task_rates) if task_rates else None
    task_cluster_lower, task_cluster_upper = cluster_bootstrap_mean_ci(task_rates)
    resolved_expected_rows = (
        run_keys.expectation.expected_rows
        if run_keys is not None
        else max(expected_rows, declared_expected_rows)
        if expected_rows is not None
        else declared_expected_rows or sum(1 for row in rows if not is_meta_row(row))
    )
    return Summary(
        tasks=output,
        total_tasks=len(repetitions_by_task),
        expected_rows=resolved_expected_rows,
        completed_rows=completed_rows,
        infra_errors=infrastructure_errors,
        harness_errors=harness_errors,
        expected_skips=expected_skips,
        unexpected_skips=unexpected_skips,
        cleanup_errors=cleanup_errors,
        trace_invalid_rows=trace_invalid_rows,
        expected_skip_reasons=dict(expected_skip_reasons),
        unexpected_skip_reasons=dict(unexpected_skip_reasons),
        skipped_task_reasons={reason: sorted(task_ids) for reason, task_ids in sorted(skipped_task_reasons.items())},
        aggregate_k=total_passes,
        aggregate_n=total_repetitions,
        aggregate_wilson_lo=aggregate_lower,
        aggregate_wilson_hi=aggregate_upper,
        task_mean_success=task_mean_success,
        task_cluster_lo=task_cluster_lower,
        task_cluster_hi=task_cluster_upper,
        missing_run_keys=run_keys.missing if run_keys is not None else (),
        unexpected_run_keys=run_keys.unexpected if run_keys is not None else (),
        multi_rep=any(len(repetitions) > 1 for repetitions in repetitions_by_task.values()),
        result_tokens_mode=result_tokens_mode([row for task_results in by_task.values() for row in task_results]),
    )
