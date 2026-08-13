"""Evaluation result reports."""

from .command import main
from .compare import ab_compare, print_ab_report
from .load import (
    DedupeMode,
    ResultRow,
    dedupe_rows_latest,
    is_infra_error_row,
    is_meta_row,
    load_rows,
    read_result,
)
from .statistics import iqr, median, percentile, sign_test_pvalue, wilson_interval
from .summary import ResultTokensMode, Summary, TaskSummary, noise_floor_statement, result_tokens_mode, summarize
from .table import (
    build_multi_surface_table,
    format_multi_rep_surface_cell,
    format_number,
    format_result_tokens,
    format_surface_cell,
    print_table,
    prompt_excerpt,
    render_multi_surface_table,
    result_tokens_marker,
    surface_label_for_file,
    task_sort_key,
    warn_if_table_mixes_batteries,
)

__all__ = [
    "DedupeMode",
    "ResultRow",
    "ResultTokensMode",
    "Summary",
    "TaskSummary",
    "ab_compare",
    "build_multi_surface_table",
    "dedupe_rows_latest",
    "format_multi_rep_surface_cell",
    "format_number",
    "format_result_tokens",
    "format_surface_cell",
    "iqr",
    "is_infra_error_row",
    "is_meta_row",
    "load_rows",
    "main",
    "median",
    "noise_floor_statement",
    "percentile",
    "print_ab_report",
    "print_table",
    "prompt_excerpt",
    "read_result",
    "render_multi_surface_table",
    "result_tokens_marker",
    "result_tokens_mode",
    "sign_test_pvalue",
    "summarize",
    "surface_label_for_file",
    "task_sort_key",
    "warn_if_table_mixes_batteries",
    "wilson_interval",
]
