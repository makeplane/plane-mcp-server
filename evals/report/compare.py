"""A/B comparison for evaluation result sets."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from .load import ResultRow, is_infra_error_row, is_meta_row, read_result
from .statistics import median, sign_test_pvalue
from .summary import noise_floor_statement, summarize
from .table import format_number


def ab_compare(
    rows_a: list[ResultRow],
    rows_b: list[ResultRow],
) -> dict[str, Any]:
    """Compare two result sets: paired call-count deltas + success rates.

    Paired call deltas only include tasks with at least one successful repetition
    in both A and B. Calls are the median across successful repetitions; this is
    identical to the historical behavior for single-rep files.
    """
    summary_a = summarize(rows_a)
    summary_b = summarize(rows_b)

    def successful_calls_by_task(rows: list[ResultRow]) -> dict[str, list[float]]:
        output: dict[str, list[float]] = defaultdict(list)
        for raw_row in rows:
            row = read_result(raw_row)
            if is_meta_row(row) or is_infra_error_row(row) or row.error or row.skipped:
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

    return {
        "summary_a": summary_a,
        "summary_b": summary_b,
        "paired_tasks": per_task,
        "median_delta": median(deltas),
        "sign_test_p": sign_test_pvalue(deltas),
        "n_paired": len(deltas),
        "multi_rep": summary_a.multi_rep or summary_b.multi_rep,
        "unstable_a": summary_a.unstable_tasks,
        "unstable_b": summary_b.unstable_tasks,
        "success_a": {
            "k": summary_a.aggregate_k,
            "n": summary_a.aggregate_n,
            "wilson": (
                summary_a.aggregate_wilson_lo,
                summary_a.aggregate_wilson_hi,
            ),
        },
        "success_b": {
            "k": summary_b.aggregate_k,
            "n": summary_b.aggregate_n,
            "wilson": (
                summary_b.aggregate_wilson_lo,
                summary_b.aggregate_wilson_hi,
            ),
        },
    }


def print_ab_report(comparison: dict[str, Any], path_a: Path, path_b: Path) -> None:
    print(f"A/B compare: A={path_a}  B={path_b}")
    success_a, success_b = comparison["success_a"], comparison["success_b"]
    rate_a = (success_a["k"] / success_a["n"]) if success_a["n"] else 0.0
    rate_b = (success_b["k"] / success_b["n"]) if success_b["n"] else 0.0
    print(
        f"  success A: {success_a['k']}/{success_a['n']} ({rate_a:.1%}) "
        f"Wilson95 [{success_a['wilson'][0]:.2f},{success_a['wilson'][1]:.2f}]"
    )
    print(
        f"  success B: {success_b['k']}/{success_b['n']} ({rate_b:.1%}) "
        f"Wilson95 [{success_b['wilson'][0]:.2f},{success_b['wilson'][1]:.2f}]"
    )
    print(f"  success rate delta (B−A): {rate_b - rate_a:+.1%}")
    print(f"  paired successful tasks: {comparison['n_paired']}")
    print(f"  median call delta (B−A): {format_number(comparison['median_delta'])}")
    probability = comparison["sign_test_p"]
    print(f"  sign-test p-value (two-sided): {probability if probability is not None else 'n/a'}")
    multiple_repetitions = bool(comparison.get("multi_rep"))
    if multiple_repetitions:
        print(f"  A {noise_floor_statement(int(comparison.get('unstable_a') or 0))}")
        print(f"  B {noise_floor_statement(int(comparison.get('unstable_b') or 0))}")
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
