"""Aggregate evaluation results and measure observed task instability."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal

from evals.results import TaskResult
from evals.tasks import TASKS_BY_ID

from .load import ResultRow, is_infra_error_row, is_meta_row, read_result
from .statistics import iqr, median, percentile, wilson_interval

ResultTokensMode = Literal["measured", "estimated", "mixed", "unlabeled", "unavailable"]


def result_tokens_mode(rows: list[ResultRow]) -> ResultTokensMode:
    """Classify token counts without treating unmarked legacy data as measured."""
    labels: set[str] = set()
    for raw_row in rows:
        row = read_result(raw_row)
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


def summarize(rows: list[ResultRow]) -> dict[str, dict[str, Any]]:
    """Aggregate per-task metrics.

    Rows with ``error_class`` starting ``infra_`` are excluded from success-rate
    denominators and counted separately as ``infra_errors`` (total on the returned
    dict under the special key ``_meta``). Other non-null ``error`` rows remain
    harness errors (excluded from success, counted in ``harness_err``).
    """
    by_task: dict[str, list[TaskResult]] = defaultdict(list)
    harness_errors_by_task: dict[str, int] = defaultdict(int)
    infrastructure_errors_by_task: dict[str, int] = defaultdict(int)
    repetitions_by_task: dict[str, set[int]] = defaultdict(set)
    infrastructure_errors = 0
    for raw_row in rows:
        row = read_result(raw_row)
        if is_meta_row(row):
            continue
        task_id = row.task_id
        repetitions_by_task[task_id].add(row.rep)
        if is_infra_error_row(row):
            infrastructure_errors += 1
            infrastructure_errors_by_task[task_id] += 1
            continue  # infra seed/cli — excluded from success aggregates
        if row.error:
            harness_errors_by_task[task_id] += 1
            continue  # harness/API errors excluded from success/medians (F4)
        if row.skipped:
            continue  # skipped rows are excluded from success denominators
        by_task[task_id].append(row)

    # Include tasks that only had harness/infra errors so columns stay visible.
    all_task_ids = sorted(set(by_task) | set(harness_errors_by_task) | set(infrastructure_errors_by_task))

    output: dict[str, dict[str, Any]] = {}
    total_passes = 0
    total_repetitions = 0
    for task_id in all_task_ids:
        task_results = by_task.get(task_id, [])
        repetition_count = len(task_results)
        pass_count = sum(1 for row in task_results if row.success)
        unstable = repetition_count > 1 and 0 < pass_count < repetition_count
        total_passes += pass_count
        total_repetitions += repetition_count
        lower, upper = wilson_interval(pass_count, repetition_count) if repetition_count else (0.0, 0.0)
        calls = [float(row.num_calls) for row in task_results]
        first_quartile, median_calls, third_quartile = iqr(calls)
        minimum_calls = min(calls) if calls else None
        maximum_calls = max(calls) if calls else None
        optimal = TASKS_BY_ID.get(task_id, {}).get("optimal_calls")
        total_calls = 0
        mispicks = 0
        errored_calls = 0
        result_tokens: list[float] = []
        for row in task_results:
            for call in row.calls:
                total_calls += 1
                if call.classification in ("alternate", "out_of_set"):
                    mispicks += 1
                if call.is_error:
                    errored_calls += 1
                if call.result_tokens is not None:
                    result_tokens.append(float(call.result_tokens))
        capped = sum(1 for row in task_results if row.hit_max_iterations or row.stop_reason == "max_tokens")
        cumulative_inputs = [float(row.cum_input_tokens or 0) for row in task_results]
        output[task_id] = {
            "n": repetition_count,
            "k": pass_count,
            "success": f"{pass_count}/{repetition_count}" if repetition_count else "0/0",
            "unstable": unstable,
            "wilson_lo": lower,
            "wilson_hi": upper,
            "med_calls": median_calls,
            "calls_min": minimum_calls,
            "calls_max": maximum_calls,
            "calls_q1": first_quartile,
            "calls_q3": third_quartile,
            "optimal_calls": optimal,
            "mispick_rate": (mispicks / total_calls) if total_calls else 0.0,
            "errored_calls": errored_calls,
            "capped": capped,
            "harness_err": harness_errors_by_task.get(task_id, 0),
            "infra_err": infrastructure_errors_by_task.get(task_id, 0),
            "med_result_tokens": median(result_tokens),
            "p95_result_tokens": percentile(result_tokens, 0.95),
            "result_tokens_mode": result_tokens_mode(task_results),
            "med_cum_input": median(cumulative_inputs),
        }
    aggregate_lower, aggregate_upper = (
        wilson_interval(total_passes, total_repetitions) if total_repetitions else (0.0, 0.0)
    )
    unstable_task_ids = sorted(task_id for task_id, values in output.items() if values.get("unstable"))
    output["_meta"] = {
        "infra_errors": infrastructure_errors,
        "aggregate_k": total_passes,
        "aggregate_n": total_repetitions,
        "aggregate_wilson_lo": aggregate_lower,
        "aggregate_wilson_hi": aggregate_upper,
        "multi_rep": any(len(repetitions) > 1 for repetitions in repetitions_by_task.values()),
        "unstable_task_ids": unstable_task_ids,
        "unstable_tasks": len(unstable_task_ids),
        "result_tokens_mode": result_tokens_mode([row for task_results in by_task.values() for row in task_results]),
    }
    return output


def noise_floor_statement(unstable_tasks: int) -> str:
    """Describe observed pass/fail variance in task-count comparison units."""
    count = max(0, int(unstable_tasks))
    if count == 0:
        return (
            "measured noise floor: 0 tasks flipped at least once; no non-zero "
            "run-to-run variance was observed (minimum meaningful difference "
            "from observed flips: 1 task)"
        )
    noun = "task" if count == 1 else "tasks"
    threshold = count + 1
    threshold_noun = "task" if threshold == 1 else "tasks"
    return (
        f"measured noise floor: {count} {noun} flipped at least once; surface "
        f"differences of {count} {noun} or fewer are within observed run-to-run "
        f"variance (minimum meaningful difference: {threshold} {threshold_noun})"
    )
