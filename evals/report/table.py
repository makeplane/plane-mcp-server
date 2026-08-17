"""Plain-text and Markdown tables for evaluation reports."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from evals.results import TaskResult
from evals.task_metadata import TaskMetadata, entry_prompt, task_metadata_from_rows

from .load import ResultRow, RunKeyValidation, is_infra_error_row, is_meta_row, read_result
from .off_surface import off_surface_statement
from .schema_friction import schema_friction_statement
from .statistics import wilson_interval
from .summary import (
    Summary,
    TaskSummary,
    completeness_statement,
    execution_coverage_statement,
    summarize,
)


def format_number(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def result_tokens_marker(mode: str) -> str:
    return {"estimated": "~", "mixed": "*", "unlabeled": "?"}.get(mode, "")


def format_result_tokens(value: float | None, mode: str) -> str:
    formatted = format_number(value, 0)
    if formatted == "-":
        return formatted
    return f"{result_tokens_marker(mode)}{formatted}"


def format_tool_distribution(task: TaskSummary) -> str:
    """Render success-conditioned tool frequency with its exclusions visible."""
    conditioning = f"success-only n={task.tool_reps}; failed excluded={task.failed_tool_reps}"
    if not task.tool_distribution_available:
        return f"{conditioning}; frequency=—"
    if not task.tool_rep_frequency:
        return f"{conditioning}; no tools"
    core = [
        f"{tool}({task.tool_call_counts[tool]}c)"
        for tool, frequency in task.tool_rep_frequency.items()
        if frequency == 1.0
    ]
    variable = [
        f"{tool}={frequency:.0%}({task.tool_call_counts[tool]}c)"
        for tool, frequency in task.tool_rep_frequency.items()
        if frequency < 1.0
    ]
    groups = []
    if core:
        groups.append(f"core:{','.join(core)}")
    if variable:
        groups.append(f"variable:{','.join(variable)}")
    return f"{conditioning}; {'; '.join(groups)}"


def format_tool_variability(summary: Summary, total_tasks: int | None = None) -> str:
    """Render the fleet count of tasks with variable tool use."""
    if not summary.tool_distribution_available:
        return "—"
    total = summary.total_tasks if total_tasks is None else total_tasks
    return f"{summary.variable_tool_tasks}/{total} tasks"


def print_table(summary: Summary, title: str) -> None:
    print(title)
    token_mode = summary.result_tokens_mode
    if token_mode == "estimated":
        print("result-token columns marked ~: entirely estimated from result characters")
    elif token_mode == "mixed":
        print("result-token columns marked *: mixed measured and estimated values (~ marks estimated tasks)")
    elif token_mode == "unlabeled":
        print("result-token columns marked ?: include legacy values with unknown measurement status")
    aggregate_count = summary.aggregate_n
    if aggregate_count and summary.task_mean_success is not None:
        task_count = sum(task.n > 0 for task in summary.tasks.values())
        print(
            f"task-cluster success: {summary.task_mean_success:.1%} across {task_count} tasks "
            f"cluster-bootstrap95 [{summary.task_cluster_lo:.2f},{summary.task_cluster_hi:.2f}]"
        )
        pooled_rate = summary.aggregate_k / aggregate_count
        print(
            f"pooled repetition success: {summary.aggregate_k}/{aggregate_count} ({pooled_rate:.1%}) "
            f"Wilson95 [{summary.aggregate_wilson_lo:.2f},{summary.aggregate_wilson_hi:.2f}]"
        )
    else:
        print("task-cluster success: n/a (no evaluated tasks)")
        print("pooled repetition success: 0/0 (n/a; no evaluated rows)")
    print(execution_coverage_statement(summary))
    print(off_surface_statement(summary.off_surface))
    print(schema_friction_statement(summary.schema_friction))
    print(completeness_statement(summary))
    if summary.infra_errors:
        print(f"infra errors: {summary.infra_errors}")
    print(f"tool variability: {format_tool_variability(summary)}")
    multiple_repetitions = summary.multi_rep
    # Multi-rep files keep the repetition-aware layout even when errors leave
    # only one completed result in every task's success-rate denominator.
    show_variation = multiple_repetitions or any(task.n > 1 for task in summary.tasks.values())
    token_marker = result_tokens_marker(token_mode)
    median_result_tokens_header = f"med_rtok{token_marker}"
    percentile_result_tokens_header = f"p95_rtok{token_marker}"
    if show_variation:
        unstable_header = f"{'unstable':>8} " if multiple_repetitions else ""
        header = (
            f"{'task':<6} {'n':>3} {'success':>8} {'wilson95':>16} "
            f"{unstable_header}"
            f"{'success_calls_min':>17} {'success_calls_med':>17} "
            f"{'success_calls_max':>17} {'success_calls_q1-q3':>19} {'err':>4} "
            f"{'capped':>6} {'h_err':>5} {'i_err':>5} "
            f"{median_result_tokens_header:>9} {percentile_result_tokens_header:>9} "
            f"{'med_cum_in':>10}  tool distribution"
        )
    else:
        header = (
            f"{'task':<6} {'n':>3} {'success':>8} {'wilson95':>16} "
            f"{'success_calls_med':>17} {'success_calls_min':>17} "
            f"{'success_calls_q1-q3':>19} {'err':>4} "
            f"{'capped':>6} {'h_err':>5} {'i_err':>5} "
            f"{median_result_tokens_header:>9} {percentile_result_tokens_header:>9} "
            f"{'med_cum_in':>10}  tool distribution"
        )
    print(header)
    print("-" * len(header))
    for task_id, values in summary.tasks.items():
        wilson = f"[{values.wilson_lo:.2f},{values.wilson_hi:.2f}]"
        quartiles = f"{format_number(values.calls_q1)}-{format_number(values.calls_q3)}"
        task_token_mode = values.result_tokens_mode
        if show_variation:
            unstable = ("YES" if values.unstable else "no") if multiple_repetitions else ""
            unstable_cell = f"{unstable:>8} " if multiple_repetitions else ""
            print(
                f"{task_id:<6} {values.n:>3} {values.success:>8} {wilson:>16} "
                f"{unstable_cell}"
                f"{format_number(values.calls_min):>17} "
                f"{format_number(values.med_calls):>17} "
                f"{format_number(values.calls_max):>17} "
                f"{quartiles:>19} {values.errored_calls:>4} {values.capped:>6} "
                f"{values.harness_err:>5} {values.infra_err:>5} "
                f"{format_result_tokens(values.med_result_tokens, task_token_mode):>9} "
                f"{format_result_tokens(values.p95_result_tokens, task_token_mode):>9} "
                f"{format_number(values.med_cum_input, 0):>10}  {format_tool_distribution(values)}"
            )
        else:
            print(
                f"{task_id:<6} {values.n:>3} {values.success:>8} {wilson:>16} "
                f"{format_number(values.med_calls):>17} {format_number(values.calls_min):>17} "
                f"{quartiles:>19} {values.errored_calls:>4} {values.capped:>6} "
                f"{values.harness_err:>5} {values.infra_err:>5} "
                f"{format_result_tokens(values.med_result_tokens, task_token_mode):>9} "
                f"{format_result_tokens(values.p95_result_tokens, task_token_mode):>9} "
                f"{format_number(values.med_cum_input, 0):>10}  {format_tool_distribution(values)}"
            )


def task_sort_key(task_id: str) -> tuple[str, int]:
    digits = "".join(character for character in task_id if character.isdigit())
    return (task_id[0] if task_id else "", int(digits) if digits else 0)


def format_surface_cell(row: ResultRow | None) -> str:
    """Cell for a single-repetition surface result."""
    if row is None:
        return "—"
    result = read_result(row)
    if result.skipped:
        return "skip"
    if result.error or is_infra_error_row(result):
        return "ERR"
    passed = "✅" if result.success else "❌"
    call_count = str(result.num_calls)
    return f"{passed} {call_count}c · tools —"


def format_multi_rep_surface_cell(rows: list[ResultRow]) -> str:
    """Aggregate distinct repetitions into one task/surface cell."""
    results = [read_result(row) for row in rows]
    completed = [row for row in results if not is_infra_error_row(row) and not row.error and not row.skipped]
    if completed:
        repetition_count = len(completed)
        pass_count = sum(1 for row in completed if row.success)
        lower, upper = wilson_interval(pass_count, repetition_count)
        if 0 < pass_count < repetition_count:
            marker = "⚠ UNSTABLE"
        elif pass_count == repetition_count:
            marker = "✅"
        else:
            marker = "❌"
        calls = [row.num_calls for row in completed]
        call_span = f"{min(calls)}c" if min(calls) == max(calls) else f"{min(calls)}-{max(calls)}c"
        task_summary = summarize(results).tasks[results[0].task_id]
        tools = format_tool_distribution(task_summary)
        return f"{marker} {pass_count}/{repetition_count} [{lower:.2f},{upper:.2f}] {call_span} · tools {tools}"
    if any(row.error or is_infra_error_row(row) for row in results):
        return "ERR"
    if any(row.skipped for row in results):
        return "skip"
    return "—"


def build_multi_surface_table(
    file_rows: list[tuple[str, list[ResultRow]]],
    *,
    expected_rows_by_column: dict[str, int | None] | None = None,
    run_keys_by_column: dict[str, RunKeyValidation | None] | None = None,
) -> dict[str, Any]:
    """Build a per-task × per-surface grid from labeled row sets.

    ``file_rows`` is a list of ``(column_label, rows)``. Column labels default
    to each file's dominant ``label`` field when the caller passes that label.
    Rows are grouped by task and repetition. Single-rep columns retain the
    historical one-cell rendering; multi-rep columns aggregate all repetitions.
    """
    columns: list[str] = []
    rows_by_column: dict[str, dict[str, list[TaskResult]]] = {}
    multiple_repetitions_by_column: dict[str, bool] = {}
    # Prompt excerpts and mutation intent come from the runs being rendered, so collect the
    # metadata every input file declared before the meta rows are filtered out below.
    task_metadata: dict[str, dict[str, Any]] = {}
    for _label, rows in file_rows:
        task_metadata.update(task_metadata_from_rows(rows))
    for label, rows in file_rows:
        columns.append(label)
        column_rows: dict[str, list[TaskResult]] = defaultdict(list)
        for raw_row in rows:
            if is_meta_row(raw_row):
                continue
            row = read_result(raw_row)
            column_rows[row.task_id].append(row)
        rows_by_column[label] = dict(column_rows)
        multiple_repetitions_by_column[label] = any(
            len({row.rep for row in task_rows}) > 1 for task_rows in column_rows.values()
        )

    multiple_repetitions = any(multiple_repetitions_by_column.values())

    all_tasks = sorted(
        {task_id for column in rows_by_column.values() for task_id in column},
        key=task_sort_key,
    )
    cells: dict[str, dict[str, str]] = {}
    raw: dict[str, dict[str, list[TaskResult]]] = {}
    for task_id in all_tasks:
        cells[task_id] = {}
        raw[task_id] = {}
        for column in columns:
            task_rows = rows_by_column[column].get(task_id, [])
            raw[task_id][column] = task_rows
            if multiple_repetitions:
                cells[task_id][column] = format_multi_rep_surface_cell(task_rows)
            else:
                cells[task_id][column] = format_surface_cell(task_rows[-1] if task_rows else None)

    # Aggregate footer per column.
    footer: dict[str, dict[str, Any]] = {}
    for column in columns:
        successes = repetitions = calls = 0
        infrastructure_errors = 0
        for task_rows in rows_by_column[column].values():
            for row in task_rows:
                if is_infra_error_row(row):
                    infrastructure_errors += 1
                    continue
                if row.error:
                    continue
                if row.skipped:
                    continue
                repetitions += 1
                if row.success:
                    successes += 1
                calls += row.num_calls
        column_summary = summarize(
            [row for task_rows in rows_by_column[column].values() for row in task_rows],
            expected_rows=(expected_rows_by_column or {}).get(column),
            run_keys=(run_keys_by_column or {}).get(column),
            task_metadata=task_metadata,
        )
        footer[column] = {
            "success": successes,
            "n": repetitions,
            "calls": calls,
            "infra_errors": infrastructure_errors,
            "multi_rep": multiple_repetitions_by_column[column],
            "tool_variability": (
                column_summary.variable_tool_tasks if column_summary.tool_distribution_available else None
            ),
            "tasks": len(rows_by_column[column]),
            "task_mean_success": column_summary.task_mean_success,
            "task_cluster_lo": column_summary.task_cluster_lo,
            "task_cluster_hi": column_summary.task_cluster_hi,
            "complete": column_summary.complete,
            "completeness": completeness_statement(column_summary),
            "coverage": execution_coverage_statement(column_summary),
            "off_surface": off_surface_statement(column_summary.off_surface),
            "schema_friction": schema_friction_statement(column_summary.schema_friction),
        }
    return {
        "columns": columns,
        "task_ids": all_tasks,
        "cells": cells,
        "raw": raw,
        "footer": footer,
        "multi_rep": multiple_repetitions,
        "multi_rep_by_col": multiple_repetitions_by_column,
        "task_metadata": task_metadata,
    }


def prompt_excerpt(task_id: str, task_metadata: TaskMetadata | None = None) -> str:
    """Render a short prompt excerpt from the run's own metadata.

    Empty when the file predates the persisted header: an excerpt taken from the current
    checkout can describe a prompt the run never used.
    """
    prompt = entry_prompt((task_metadata or {}).get(task_id)).replace("{project}", "P")
    return (prompt[:32] + "…") if len(prompt) > 32 else prompt


def render_multi_surface_table(table: dict[str, Any], *, markdown: bool = False) -> str:
    """Render multi-surface table as plain text or GitHub markdown."""
    columns: list[str] = table["columns"]
    task_ids: list[str] = table["task_ids"]
    task_metadata: TaskMetadata = table.get("task_metadata") or {}
    cells: dict[str, dict[str, str]] = table["cells"]
    footer: dict[str, dict[str, Any]] = table["footer"]
    multiple_repetitions = bool(table.get("multi_rep"))
    lines: list[str] = []

    if markdown:
        header = "| task | what | " + " | ".join(columns) + " |"
        separator = "| --- | --- | " + " | ".join("---" for _ in columns) + " |"
        lines.append(header)
        lines.append(separator)
        for task_id in task_ids:
            row_cells = " | ".join(cells[task_id].get(column, "—") for column in columns)
            lines.append(f"| {task_id} | {prompt_excerpt(task_id, task_metadata)} | {row_cells} |")
        # Footer
        footer_parts = []
        for column in columns:
            values = footer[column]
            pooled = f"{values['success']}/{values['n']}" if values["n"] else "0/0"
            if values["task_mean_success"] is None:
                rate = f"task-cluster n/a; pooled {pooled}"
            else:
                rate = (
                    f"task-cluster {values['task_mean_success']:.1%} "
                    f"[{values['task_cluster_lo']:.2f},{values['task_cluster_hi']:.2f}]; pooled {pooled}"
                )
            variability = (
                f"{values['tool_variability']}/{values['tasks']} variable"
                if values["tool_variability"] is not None
                else "tools —"
            )
            footer_parts.append(f"{rate} ({values['calls']}c, {variability}, i={values['infra_errors']})")
        lines.append("| **agg** | | " + " | ".join(footer_parts) + " |")
        lines.append(
            "| **execution coverage** | | " + " | ".join(footer[column]["coverage"] for column in columns) + " |"
        )
        lines.append(
            "| **off-surface indicators** | | "
            + " | ".join(footer[column]["off_surface"].replace("\n", "<br>") for column in columns)
            + " |"
        )
        lines.append(
            "| **schema friction** | | "
            + " | ".join(footer[column]["schema_friction"].replace("\n", "<br>") for column in columns)
            + " |"
        )
        lines.append(
            "| **completeness** | | " + " | ".join(footer[column]["completeness"] for column in columns) + " |"
        )
        return "\n".join(lines) + "\n"

    column_width = max(14, max((len(column) for column in columns), default=14))
    if multiple_repetitions:
        column_width = max(
            column_width,
            max(
                (len(value) for task in cells.values() for value in task.values()),
                default=14,
            ),
        )
    heading = f"{'task':5} {'what':34} " + " ".join(f"{column:{column_width}}" for column in columns)
    lines.append(heading)
    lines.append("-" * len(heading))
    for task_id in task_ids:
        line = f"{task_id:5} {prompt_excerpt(task_id, task_metadata):34} "
        for column in columns:
            line += f"{cells[task_id].get(column, '—'):{column_width}} "
        lines.append(line.rstrip())
    lines.append("-" * len(heading))
    for column in columns:
        values = footer[column]
        pooled = f"{values['success']}/{values['n']}" if values["n"] else "0/0"
        if values["task_mean_success"] is None:
            rate = f"task-cluster n/a; pooled {pooled}"
        else:
            rate = (
                f"task-cluster {values['task_mean_success']:.1%} "
                f"[{values['task_cluster_lo']:.2f},{values['task_cluster_hi']:.2f}]; pooled {pooled}"
            )
        variability = (
            f"{values['tool_variability']}/{values['tasks']} tasks" if values["tool_variability"] is not None else "—"
        )
        lines.append(
            f"{column:12} success {rate}  total calls {values['calls']}"
            f"  tool variability {variability}  infra {values['infra_errors']}"
        )
    for column in columns:
        lines.append(f"{column:12} {footer[column]['coverage']}")
    for column in columns:
        for line in footer[column]["off_surface"].splitlines():
            lines.append(f"{column:12} {line}")
    for column in columns:
        for line in footer[column]["schema_friction"].splitlines():
            lines.append(f"{column:12} {line}")
    for column in columns:
        lines.append(f"{column:12} {footer[column]['completeness']}")
    return "\n".join(lines) + "\n"


def surface_label_for_file(path: Path, rows: list[TaskResult]) -> str:
    """Pick a column label from the file's dominant label field, else stem."""
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        label = row.label
        if label:
            counts[str(label)] += 1
    if counts:
        return max(counts, key=counts.get)  # type: ignore[arg-type]
    return path.stem
