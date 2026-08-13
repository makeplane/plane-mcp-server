"""Plain-text and Markdown tables for evaluation reports."""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from evals.results import TaskResult
from evals.tasks import TASKS_BY_ID

from .load import ResultRow, is_infra_error_row, is_meta_row, read_result
from .statistics import wilson_interval
from .summary import noise_floor_statement


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


def print_table(summary: dict[str, dict[str, Any]], title: str) -> None:
    meta = summary.get("_meta") or {}
    print(title)
    token_mode = str(meta.get("result_tokens_mode") or "unavailable")
    if token_mode == "estimated":
        print("result-token columns marked ~: entirely estimated from result characters")
    elif token_mode == "mixed":
        print("result-token columns marked *: mixed measured and estimated values (~ marks estimated tasks)")
    elif token_mode == "unlabeled":
        print("result-token columns marked ?: include legacy values with unknown measurement status")
    if meta.get("infra_errors"):
        print(f"infra errors: {meta['infra_errors']}")
    aggregate_count = int(meta.get("aggregate_n") or 0)
    if aggregate_count:
        aggregate_passes = int(meta.get("aggregate_k") or 0)
        lower = float(meta.get("aggregate_wilson_lo") or 0.0)
        upper = float(meta.get("aggregate_wilson_hi") or 0.0)
        rate = aggregate_passes / aggregate_count if aggregate_count else 0.0
        print(
            f"aggregate success: {aggregate_passes}/{aggregate_count} ({rate:.1%}) Wilson95 [{lower:.2f},{upper:.2f}]"
        )
    multiple_repetitions = bool(meta.get("multi_rep"))
    if multiple_repetitions:
        print(noise_floor_statement(int(meta.get("unstable_tasks") or 0)))
    # Multi-rep files keep the repetition-aware layout even when errors leave
    # only one completed result in every task's success-rate denominator.
    show_variation = multiple_repetitions or any(
        values.get("n", 0) > 1 for task_id, values in summary.items() if task_id != "_meta"
    )
    token_marker = result_tokens_marker(token_mode)
    median_result_tokens_header = f"med_rtok{token_marker}"
    percentile_result_tokens_header = f"p95_rtok{token_marker}"
    if show_variation:
        unstable_header = f"{'unstable':>8} " if multiple_repetitions else ""
        header = (
            f"{'task':<6} {'n':>3} {'success':>8} {'wilson95':>16} "
            f"{unstable_header}"
            f"{'calls_min':>9} {'med_calls':>9} {'calls_max':>9} {'opt':>4} "
            f"{'IQR':>11} {'mispick':>8} {'err':>4} "
            f"{'capped':>6} {'h_err':>5} {'i_err':>5} "
            f"{median_result_tokens_header:>9} {percentile_result_tokens_header:>9} "
            f"{'med_cum_in':>10}"
        )
    else:
        header = (
            f"{'task':<6} {'n':>3} {'success':>8} {'wilson95':>16} "
            f"{'med_calls':>9} {'opt':>4} {'IQR':>11} {'mispick':>8} {'err':>4} "
            f"{'capped':>6} {'h_err':>5} {'i_err':>5} "
            f"{median_result_tokens_header:>9} {percentile_result_tokens_header:>9} "
            f"{'med_cum_in':>10}"
        )
    print(header)
    print("-" * len(header))
    for task_id, values in summary.items():
        if task_id == "_meta":
            continue
        wilson = f"[{values['wilson_lo']:.2f},{values['wilson_hi']:.2f}]"
        quartiles = f"{format_number(values['calls_q1'])}-{format_number(values['calls_q3'])}"
        optimal = values["optimal_calls"] if values["optimal_calls"] is not None else "-"
        task_token_mode = str(values.get("result_tokens_mode") or "unavailable")
        if show_variation:
            unstable = ("YES" if values.get("unstable") else "no") if multiple_repetitions else ""
            unstable_cell = f"{unstable:>8} " if multiple_repetitions else ""
            print(
                f"{task_id:<6} {values['n']:>3} {values['success']:>8} {wilson:>16} "
                f"{unstable_cell}"
                f"{format_number(values.get('calls_min')):>9} "
                f"{format_number(values['med_calls']):>9} "
                f"{format_number(values.get('calls_max')):>9} "
                f"{optimal!s:>4} {quartiles:>11} {values['mispick_rate']:>7.1%} "
                f"{values['errored_calls']:>4} {values['capped']:>6} "
                f"{values['harness_err']:>5} {values.get('infra_err', 0):>5} "
                f"{format_result_tokens(values['med_result_tokens'], task_token_mode):>9} "
                f"{format_result_tokens(values['p95_result_tokens'], task_token_mode):>9} "
                f"{format_number(values['med_cum_input'], 0):>10}"
            )
        else:
            print(
                f"{task_id:<6} {values['n']:>3} {values['success']:>8} {wilson:>16} "
                f"{format_number(values['med_calls']):>9} {optimal!s:>4} "
                f"{quartiles:>11} {values['mispick_rate']:>7.1%} "
                f"{values['errored_calls']:>4} {values['capped']:>6} "
                f"{values['harness_err']:>5} {values.get('infra_err', 0):>5} "
                f"{format_result_tokens(values['med_result_tokens'], task_token_mode):>9} "
                f"{format_result_tokens(values['p95_result_tokens'], task_token_mode):>9} "
                f"{format_number(values['med_cum_input'], 0):>10}"
            )


def task_sort_key(task_id: str) -> tuple[str, int]:
    digits = "".join(character for character in task_id if character.isdigit())
    return (task_id[0] if task_id else "", int(digits) if digits else 0)


def format_surface_cell(row: ResultRow | None) -> str:
    """Cell for multi-surface table: '✅ Nc/Mmp', 'skip', 'ERR', or '—'."""
    if row is None:
        return "—"
    result = read_result(row)
    if result.skipped:
        return "skip"
    if result.error or is_infra_error_row(result):
        return "ERR"
    passed = "✅" if result.success else "❌"
    call_count = str(result.num_calls)
    if result.server == "external":
        return f"{passed} {call_count}c"
    alternate = result.alternate_calls
    outside_set = result.out_of_set_calls
    # None counters (external nulling) → omit mispick suffix.
    if alternate is None and outside_set is None:
        return f"{passed} {call_count}c"
    mispicks = int(alternate or 0) + int(outside_set or 0)
    if mispicks:
        return f"{passed} {call_count}c/{mispicks}mp"
    return f"{passed} {call_count}c"


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
        return f"{marker} {pass_count}/{repetition_count} [{lower:.2f},{upper:.2f}] {call_span}"
    if any(row.error or is_infra_error_row(row) for row in results):
        return "ERR"
    if any(row.skipped for row in results):
        return "skip"
    return "—"


def build_multi_surface_table(
    file_rows: list[tuple[str, list[ResultRow]]],
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
        successes = repetitions = calls = mispicks = 0
        mispicks_comparable = True
        infrastructure_errors = 0
        unstable_tasks = 0
        for task_rows in rows_by_column[column].values():
            completed: list[TaskResult] = []
            for row in task_rows:
                if is_infra_error_row(row):
                    infrastructure_errors += 1
                    continue
                if row.error:
                    continue
                if row.skipped:
                    continue
                completed.append(row)
                repetitions += 1
                if row.success:
                    successes += 1
                calls += row.num_calls
                if row.server == "external":
                    mispicks_comparable = False
                else:
                    alternate = row.alternate_calls
                    outside_set = row.out_of_set_calls
                    if alternate is None and outside_set is None:
                        mispicks_comparable = False
                    else:
                        mispicks += int(alternate or 0) + int(outside_set or 0)
            task_passes = sum(1 for row in completed if row.success)
            if len(completed) > 1 and 0 < task_passes < len(completed):
                unstable_tasks += 1
        footer[column] = {
            "success": successes,
            "n": repetitions,
            "calls": calls,
            "mispicks": mispicks if mispicks_comparable else None,
            "infra_errors": infrastructure_errors,
            "multi_rep": multiple_repetitions_by_column[column],
            "unstable_tasks": unstable_tasks,
        }
    return {
        "columns": columns,
        "task_ids": all_tasks,
        "cells": cells,
        "raw": raw,
        "footer": footer,
        "multi_rep": multiple_repetitions,
        "multi_rep_by_col": multiple_repetitions_by_column,
    }


def prompt_excerpt(task_id: str) -> str:
    prompt = (TASKS_BY_ID.get(task_id, {}).get("prompt") or "").replace("{project}", "P")
    return (prompt[:32] + "…") if len(prompt) > 32 else prompt


def render_multi_surface_table(table: dict[str, Any], *, markdown: bool = False) -> str:
    """Render multi-surface table as plain text or GitHub markdown."""
    columns: list[str] = table["columns"]
    task_ids: list[str] = table["task_ids"]
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
            lines.append(f"| {task_id} | {prompt_excerpt(task_id)} | {row_cells} |")
        # Footer
        footer_parts = []
        for column in columns:
            values = footer[column]
            rate = f"{values['success']}/{values['n']}" if values["n"] else "0/0"
            mispicks = f", {values['mispicks']}mp" if values["mispicks"] is not None else ""
            footer_parts.append(f"{rate} ({values['calls']}c{mispicks}, i={values['infra_errors']})")
        lines.append("| **agg** | | " + " | ".join(footer_parts) + " |")
        if multiple_repetitions:
            noise_parts = [
                noise_floor_statement(int(footer[column].get("unstable_tasks") or 0))
                if footer[column].get("multi_rep")
                else "single repetition"
                for column in columns
            ]
            lines.append("| **noise floor** | | " + " | ".join(noise_parts) + " |")
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
        line = f"{task_id:5} {prompt_excerpt(task_id):34} "
        for column in columns:
            line += f"{cells[task_id].get(column, '—'):{column_width}} "
        lines.append(line.rstrip())
    lines.append("-" * len(heading))
    for column in columns:
        values = footer[column]
        rate = f"{values['success']}/{values['n']}" if values["n"] else "0/0"
        percentage = f" ({100 * values['success'] / values['n']:.0f}%)" if values["n"] else ""
        mispicks = f"  mispicks {values['mispicks']}" if values["mispicks"] is not None else "  mispicks n/a"
        lines.append(
            f"{column:12} success {rate}{percentage}  total calls {values['calls']}"
            f"{mispicks}  infra {values['infra_errors']}"
        )
    if multiple_repetitions:
        for column in columns:
            if footer[column].get("multi_rep"):
                lines.append(f"{column:12} {noise_floor_statement(int(footer[column].get('unstable_tasks') or 0))}")
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


def warn_if_table_mixes_batteries(file_rows: list[tuple[str, list[ResultRow]]]) -> bool:
    """Warn when table columns contain rows from different task batteries."""
    by_label: dict[str, set[str]] = {}
    all_fingerprints: set[str] = set()
    for label, rows in file_rows:
        fingerprints = {read_result(row).battery or "<missing>" for row in rows if not is_meta_row(row)}
        if fingerprints:
            by_label[label] = fingerprints
            all_fingerprints.update(fingerprints)
    if len(all_fingerprints) <= 1:
        return False
    detail = "; ".join(f"{label}={','.join(sorted(values))}" for label, values in by_label.items())
    print(
        "warning: table spans battery fingerprints; these rows were graded on "
        f"different task prompts/questions and are not directly comparable ({detail})",
        file=sys.stderr,
    )
    return True
