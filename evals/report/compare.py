"""A/B comparison for evaluation result sets."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .load import ResultRow, RunKeyValidation, is_infra_error_row, is_meta_row, read_result
from .statistics import median, paired_bootstrap_mean_ci, paired_permutation_pvalue
from .summary import completeness_statement, execution_coverage_statement, summarize
from .table import format_number


def ab_compare(
    rows_a: list[ResultRow],
    rows_b: list[ResultRow],
    *,
    expected_rows_a: int | None = None,
    expected_rows_b: int | None = None,
    run_keys_a: RunKeyValidation | None = None,
    run_keys_b: RunKeyValidation | None = None,
) -> dict[str, Any]:
    """Compare two result sets with task-paired call and success deltas.

    Paired call deltas only include tasks with at least one successful repetition
    in both A and B. Calls are the median across successful repetitions; this is
    identical to the historical behavior for single-rep files.

    Success-rate differences pair each task's completed-repetition rate across
    labels, then bootstrap whole task pairs. This treats tasks as independent
    sampling units and assumes the labels cover comparable task instances.
    """
    summary_a = summarize(rows_a, expected_rows=expected_rows_a, run_keys=run_keys_a)
    summary_b = summarize(rows_b, expected_rows=expected_rows_b, run_keys=run_keys_b)

    def successful_calls_by_task(rows: list[ResultRow]) -> dict[str, list[float]]:
        output: dict[str, list[float]] = defaultdict(list)
        for raw_row in rows:
            row = read_result(raw_row)
            if is_meta_row(row) or is_infra_error_row(row) or row.error or row.skipped or not row.trace_integrity:
                continue
            if not row.success:
                continue
            output[row.task_id].append(float(row.num_calls))
        return dict(output)

    calls_a = successful_calls_by_task(rows_a)
    calls_b = successful_calls_by_task(rows_b)
    shared = sorted(set(calls_a) & set(calls_b))
    deltas: list[float] = []
    per_task: list[dict[str, Any]] = []
    for task_id in shared:
        count_a = float(median(calls_a[task_id]) or 0.0)
        count_b = float(median(calls_b[task_id]) or 0.0)
        delta = count_b - count_a  # B − A (negative = B fewer calls = better if lower is better)
        deltas.append(delta)
        per_task.append(
            {
                "task_id": task_id,
                "calls_a": count_a,
                "calls_b": count_b,
                "delta": delta,
            }
        )

    success_shared = sorted(
        task_id
        for task_id in set(summary_a.tasks) & set(summary_b.tasks)
        if summary_a.tasks[task_id].n and summary_b.tasks[task_id].n
    )
    success_deltas = [
        (summary_b.tasks[task_id].k / summary_b.tasks[task_id].n)
        - (summary_a.tasks[task_id].k / summary_a.tasks[task_id].n)
        for task_id in success_shared
    ]
    paired_success_delta = sum(success_deltas) / len(success_deltas) if success_deltas else None
    paired_success_ci = paired_bootstrap_mean_ci(success_deltas)

    return {
        "summary_a": summary_a,
        "summary_b": summary_b,
        "paired_tasks": per_task,
        "mean_delta": sum(deltas) / len(deltas) if deltas else None,
        "median_delta": median(deltas),
        "call_permutation_p": paired_permutation_pvalue(deltas),
        "call_zero_deltas": sum(delta == 0 for delta in deltas),
        "n_paired": len(deltas),
        "paired_success_tasks": success_shared,
        "n_paired_success": len(success_deltas),
        "paired_success_delta": paired_success_delta,
        "paired_success_ci": paired_success_ci,
        "multi_rep": summary_a.multi_rep or summary_b.multi_rep,
        "success_a": {
            "k": summary_a.aggregate_k,
            "n": summary_a.aggregate_n,
            "wilson": (
                summary_a.aggregate_wilson_lo,
                summary_a.aggregate_wilson_hi,
            ),
            "task_mean": summary_a.task_mean_success,
            "task_cluster": (summary_a.task_cluster_lo, summary_a.task_cluster_hi),
            "task_n": sum(task.n > 0 for task in summary_a.tasks.values()),
        },
        "success_b": {
            "k": summary_b.aggregate_k,
            "n": summary_b.aggregate_n,
            "wilson": (
                summary_b.aggregate_wilson_lo,
                summary_b.aggregate_wilson_hi,
            ),
            "task_mean": summary_b.task_mean_success,
            "task_cluster": (summary_b.task_cluster_lo, summary_b.task_cluster_hi),
            "task_n": sum(task.n > 0 for task in summary_b.tasks.values()),
        },
    }


def print_ab_report(comparison: dict[str, Any], path_a: Path, path_b: Path) -> None:
    print(f"A/B compare: A={path_a}  B={path_b}")
    success_a, success_b = comparison["success_a"], comparison["success_b"]
    rate_a = (success_a["k"] / success_a["n"]) if success_a["n"] else 0.0
    rate_b = (success_b["k"] / success_b["n"]) if success_b["n"] else 0.0
    for label, success, pooled_rate in (("A", success_a, rate_a), ("B", success_b, rate_b)):
        task_mean = success["task_mean"]
        task_lo, task_hi = success["task_cluster"]
        if task_mean is None or task_lo is None or task_hi is None:
            print(f"  success {label} task-cluster: n/a (no evaluated tasks)")
        else:
            print(
                f"  success {label} task-cluster: {task_mean:.1%} "
                f"cluster-bootstrap95 [{task_lo:.2f},{task_hi:.2f}] (n={success['task_n']} tasks)"
            )
        print(
            f"  success {label} pooled repetitions: {success['k']}/{success['n']} ({pooled_rate:.1%}) "
            f"Wilson95 [{success['wilson'][0]:.2f},{success['wilson'][1]:.2f}]"
        )
    print(f"  A {execution_coverage_statement(comparison['summary_a'])}")
    print(f"  B {execution_coverage_statement(comparison['summary_b'])}")
    print(f"  A {completeness_statement(comparison['summary_a'])}")
    print(f"  B {completeness_statement(comparison['summary_b'])}")
    print(f"  success rate delta (B−A): {rate_b - rate_a:+.1%}")
    paired_success_delta = comparison["paired_success_delta"]
    paired_success_lo, paired_success_hi = comparison["paired_success_ci"]
    if paired_success_delta is None or paired_success_lo is None or paired_success_hi is None:
        print("  paired task success delta (B−A): n/a (no shared evaluated tasks)")
    else:
        print(
            f"  paired task success delta (B−A): {paired_success_delta:+.1%} "
            f"paired-bootstrap95 [{paired_success_lo:+.1%},{paired_success_hi:+.1%}] "
            f"(n={comparison['n_paired_success']} tasks)"
        )
    print(f"  paired successful tasks: {comparison['n_paired']}")
    print(f"  mean call delta (B−A): {format_number(comparison['mean_delta'])}")
    print(f"  median call delta (B−A): {format_number(comparison['median_delta'])}")
    probability = comparison["call_permutation_p"]
    tie_count = comparison["call_zero_deltas"]
    print(
        "  paired permutation p-value for mean call delta (two-sided): "
        f"{probability if probability is not None else 'n/a'} "
        f"({tie_count} zero-delta ties retained)"
    )
    multiple_repetitions = bool(comparison.get("multi_rep"))
    if comparison["paired_tasks"]:
        print()
        print(f"{'task':<6} {'calls_A':>8} {'calls_B':>8} {'delta':>8}")
        print("-" * 34)
        for row in comparison["paired_tasks"]:
            if multiple_repetitions:
                print(
                    f"{row['task_id']:<6} {format_number(row['calls_a']):>8} "
                    f"{format_number(row['calls_b']):>8} {row['delta']:>+8.1f}"
                )
            else:
                print(f"{row['task_id']:<6} {row['calls_a']:>8.0f} {row['calls_b']:>8.0f} {row['delta']:>+8.0f}")
