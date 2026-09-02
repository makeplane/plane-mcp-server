"""A/B comparison for evaluation result sets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .economics import economics_statement
from .failure_kinds import failure_kind_statement
from .load import ResultRow, RunKeyValidation
from .off_surface import off_surface_statement
from .power import power_statement
from .schema_friction import measure_schema_friction, schema_friction_statement, successful_trace_rows
from .statistics import median, paired_bootstrap_mean_ci, paired_permutation_pvalue
from .summary import completeness_statement, execution_coverage_statement, summarize
from .table import format_number


def _paired_metric(
    economics_a: Any,
    economics_b: Any,
    shared: list[str],
    attribute: str,
) -> dict[str, Any]:
    """Pair one per-task resource metric across arms, skipping tasks either side lacks.

    Same shape as the call delta -- mean, median and a paired bootstrap -- so the
    resource lines read against it directly rather than in different units.
    """
    deltas: list[float] = []
    for task_id in shared:
        task_a = economics_a.tasks.get(task_id)
        task_b = economics_b.tasks.get(task_id)
        if task_a is None or task_b is None:
            continue
        value_a = getattr(task_a, attribute)
        value_b = getattr(task_b, attribute)
        if value_a is None or value_b is None:
            continue
        deltas.append(float(value_b) - float(value_a))
    return {
        "n": len(deltas),
        # Missingness that correlates with expensive tasks can bias a delta, so the
        # count of shared tasks this metric could not use travels with it.
        "dropped": len(shared) - len(deltas),
        "mean_delta": sum(deltas) / len(deltas) if deltas else None,
        "median_delta": median(deltas),
        "ci": paired_bootstrap_mean_ci(deltas),
    }


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
        output: dict[str, list[float]] = {}
        for row in successful_trace_rows(rows):
            output.setdefault(row.task_id, []).append(float(row.num_calls))
        return output

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

    friction_a = measure_schema_friction(rows_a)
    friction_b = measure_schema_friction(rows_b)
    paired_schema_friction: list[dict[str, Any]] = []
    errored_call_deltas: list[float] = []
    errored_call_rate_deltas: list[float] = []
    for task_id in shared:
        task_a = friction_a.tasks[task_id]
        task_b = friction_b.tasks[task_id]
        errored_call_delta = task_b.median_errored_calls - task_a.median_errored_calls
        rate_a = task_a.errored_call_rate
        rate_b = task_b.errored_call_rate
        rate_delta = rate_b - rate_a if rate_a is not None and rate_b is not None else None
        errored_call_deltas.append(errored_call_delta)
        if rate_delta is not None:
            errored_call_rate_deltas.append(rate_delta)
        paired_schema_friction.append(
            {
                "task_id": task_id,
                "errored_calls_a": task_a.median_errored_calls,
                "errored_calls_b": task_b.median_errored_calls,
                "errored_call_delta": errored_call_delta,
                "errored_call_rate_a": rate_a,
                "errored_call_rate_b": rate_b,
                "errored_call_rate_delta": rate_delta,
                "raw_errored_calls_a": task_a.errored_calls,
                "raw_total_calls_a": task_a.total_calls,
                "raw_errored_calls_b": task_b.errored_calls,
                "raw_total_calls_b": task_b.total_calls,
            }
        )

    # Resource deltas over the same shared tasks as the call delta. Fewer calls and
    # less spend are different virtues, and reporting one without the other is what
    # let a 32%-fewer-calls arm read as the cheaper one while burning 3.1x the input.
    economics_a, economics_b = summary_a.economics, summary_b.economics
    paired_resources = {
        name: _paired_metric(economics_a, economics_b, shared, attribute)
        for name, attribute in (
            ("input", "med_total_input"),
            ("result_tokens", "med_result_tokens"),
            ("wall_time", "med_wall_time_s"),
            ("call_latency", "med_call_latency_ms"),
            ("cost", "cost_usd"),
        )
    }

    return {
        "summary_a": summary_a,
        "summary_b": summary_b,
        "paired_tasks": per_task,
        "economics_a": economics_a,
        "economics_b": economics_b,
        "total_input_a": economics_a.total_input_tokens,
        "total_input_b": economics_b.total_input_tokens,
        "cost_a": economics_a.cost_usd,
        "cost_b": economics_b.cost_usd,
        "paired_resources": paired_resources,
        "mean_delta": sum(deltas) / len(deltas) if deltas else None,
        "median_delta": median(deltas),
        "call_permutation_p": paired_permutation_pvalue(deltas),
        "call_zero_deltas": sum(delta == 0 for delta in deltas),
        "n_paired": len(deltas),
        "paired_success_tasks": success_shared,
        "n_paired_success": len(success_deltas),
        "paired_success_delta": paired_success_delta,
        "paired_success_ci": paired_success_ci,
        "paired_schema_friction": paired_schema_friction,
        "mean_errored_call_delta": (
            sum(errored_call_deltas) / len(errored_call_deltas) if errored_call_deltas else None
        ),
        "errored_call_delta_ci": paired_bootstrap_mean_ci(errored_call_deltas),
        "mean_errored_call_rate_delta": (
            sum(errored_call_rate_deltas) / len(errored_call_rate_deltas) if errored_call_rate_deltas else None
        ),
        "errored_call_rate_delta_ci": paired_bootstrap_mean_ci(errored_call_rate_deltas),
        "n_paired_errored_call_rates": len(errored_call_rate_deltas),
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


#: Label, units and precision for each paired resource delta.
_RESOURCE_LINES: tuple[tuple[str, str, str, int], ...] = (
    ("input", "input tokens", "", 0),
    ("result_tokens", "result tokens", "", 0),
    ("cost", "cost per successful rep", "$", 4),
    ("wall_time", "wall time", "s", 1),
    ("call_latency", "call latency", "ms", 0),
)


def _print_resource_deltas(comparison: dict[str, Any]) -> None:
    """Print the resource deltas beside the call delta, in the same paired shape.

    These sit immediately after the call delta on purpose: the call delta alone says
    which arm did less work, which is not the same question as which arm cost less,
    and the two answers pointed opposite ways on the run that motivated this.
    """
    paired = comparison.get("paired_resources") or {}
    for key, label, unit, places in _RESOURCE_LINES:
        metric = paired.get(key)
        if not metric or not metric["n"]:
            print(f"  median {label} delta (B−A): n/a (no paired tasks reporting it)")
            continue
        dropped = metric.get("dropped") or 0
        omitted = f", {dropped} shared task(s) omitted for missing values" if dropped else ""
        low, high = metric["ci"]
        prefix = unit if unit == "$" else ""
        suffix = "" if unit == "$" else unit
        interval = (
            f" paired-bootstrap95 [{prefix}{low:+,.{places}f}{suffix},{prefix}{high:+,.{places}f}{suffix}]"
            if low is not None and high is not None
            else ""
        )
        print(
            f"  median {label} delta (B−A): {prefix}{metric['median_delta']:+,.{places}f}{suffix}"
            f"{interval} (n={metric['n']} tasks{omitted})"
        )
    for label, key in (("A", "economics_a"), ("B", "economics_b")):
        economics = comparison.get(key)
        if economics is None:
            continue
        for line in economics_statement(economics).splitlines():
            print(f"  {label} {line}")


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
    for label, summary in (("A", comparison["summary_a"]), ("B", comparison["summary_b"])):
        for line in off_surface_statement(summary.off_surface).splitlines():
            print(f"  {label} {line}")
        for line in schema_friction_statement(summary.schema_friction).splitlines():
            print(f"  {label} {line}")
        for line in failure_kind_statement(summary.failure_kinds).splitlines():
            print(f"  {label} {line}")
        print(f"  {label} {summary.lookup_reuse.statement()}")
    for label, summary in (("A", comparison["summary_a"]), ("B", comparison["summary_b"])):
        power = power_statement(summary)
        if power:
            print(f"  {label} {power}")
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
    errored_delta = comparison["mean_errored_call_delta"]
    errored_lo, errored_hi = comparison["errored_call_delta_ci"]
    if errored_delta is None or errored_lo is None or errored_hi is None:
        print("  mean errored-call delta (B−A): n/a (no paired successful tasks)")
    else:
        print(
            f"  mean errored-call delta (B−A): {errored_delta:+.1f} "
            f"paired-bootstrap95 [{errored_lo:+.1f},{errored_hi:+.1f}] "
            f"(n={comparison['n_paired']} tasks)"
        )
    rate_delta = comparison["mean_errored_call_rate_delta"]
    rate_lo, rate_hi = comparison["errored_call_rate_delta_ci"]
    if rate_delta is None or rate_lo is None or rate_hi is None:
        print("  mean errored-call-rate delta (B−A): n/a (no paired tasks with calls on both surfaces)")
    else:
        print(
            f"  mean errored-call-rate delta (B−A): {rate_delta * 100:+.1f} percentage points "
            f"paired-bootstrap95 [{rate_lo * 100:+.1f},{rate_hi * 100:+.1f}] "
            f"(n={comparison['n_paired_errored_call_rates']} tasks)"
        )
    _print_resource_deltas(comparison)
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
    if comparison["paired_schema_friction"]:
        print()
        print("schema friction by paired task (raw errors/calls; median errors per successful repetition):")
        for row in comparison["paired_schema_friction"]:
            rate_a = row["errored_call_rate_a"]
            rate_b = row["errored_call_rate_b"]
            rate_a_text = f"{rate_a:.1%}" if rate_a is not None else "n/a"
            rate_b_text = f"{rate_b:.1%}" if rate_b is not None else "n/a"
            print(
                f"  {row['task_id']}: "
                f"A={row['raw_errored_calls_a']}/{row['raw_total_calls_a']} ({rate_a_text}), "
                f"median={row['errored_calls_a']:.1f}; "
                f"B={row['raw_errored_calls_b']}/{row['raw_total_calls_b']} ({rate_b_text}), "
                f"median={row['errored_calls_b']:.1f}"
            )
