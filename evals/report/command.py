"""Command-line behavior for evaluation reports."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evals.results import TaskResult

from .compare import ab_compare, print_ab_report
from .identity import (
    ComparabilityError,
    format_refusal,
    identity_header_lines,
    parse_varied_dimensions,
    validate_persisted_identity,
)
from .load import (
    DedupeMode,
    RunKeyValidation,
    load_rows,
    load_run_expectation,
    load_run_expected_rows,
    validate_run_keys,
)
from .summary import summarize
from .table import (
    build_multi_surface_table,
    print_table,
    render_multi_surface_table,
    surface_label_for_file,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize eval JSONL results")
    parser.add_argument(
        "files",
        nargs="*",
        help="JSONL file(s): one for summary, two for A/B, N with --table",
    )
    parser.add_argument(
        "--table",
        action="store_true",
        help="Multi-surface per-task table (one column per file, using its run label)",
    )
    parser.add_argument(
        "--markdown",
        action="store_true",
        help="With --table, emit a GitHub-flavored markdown table",
    )
    parser.add_argument(
        "--vary",
        action="append",
        default=[],
        metavar="DIM[,DIM]",
        help="Declare treatment dimensions (resolved_model, provider, driver, or server)",
    )
    parser.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Keep all rows (forensics); default is latest-wins per (task_id,rep,label)",
    )
    arguments = parser.parse_args(argv)
    try:
        varied_dimensions = parse_varied_dimensions(arguments.vary)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    dedupe: DedupeMode = "none" if arguments.no_dedupe else "latest"

    if not arguments.files:
        parser.print_help()
        return 2

    paths = [Path(file_name) for file_name in arguments.files]
    for path in paths:
        if not path.exists():
            print(f"error: file not found: {path}", file=sys.stderr)
            return 2

    if not arguments.table and len(paths) not in {1, 2}:
        print(
            "error: pass one JSONL (summary), two (A/B delta), or use --table with N files",
            file=sys.stderr,
        )
        return 2

    try:
        identity = validate_persisted_identity(paths, varied_dimensions=varied_dimensions)
        run_keys_by_path: dict[Path, RunKeyValidation | None] = {}
        for path in paths:
            expectation = load_run_expectation(path)
            run_keys_by_path[path] = (
                validate_run_keys(load_rows(path, dedupe="none"), expectation) if expectation is not None else None
            )
    except ComparabilityError as exc:
        print(format_refusal(exc), file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"error: invalid run expectation: {exc}", file=sys.stderr)
        return 2
    header_lines = identity_header_lines(
        identity,
        warn_missing_manifest=arguments.table or len(paths) == 2,
    )
    for line in header_lines:
        print(line)
    if header_lines and arguments.table and arguments.markdown:
        print()

    if arguments.table:
        if len(paths) < 1:
            print("error: --table requires at least one JSONL", file=sys.stderr)
            return 2
        labeled: list[tuple[str, list[TaskResult]]] = []
        expected_by_label: dict[str, int | None] = {}
        run_keys_by_label: dict[str, RunKeyValidation | None] = {}
        used_labels: set[str] = set()
        for path in paths:
            rows = load_rows(path, dedupe=dedupe)
            label = surface_label_for_file(path, rows)
            # Disambiguate duplicate run labels (e.g. two external files).
            label_root = label
            number = 2
            while label in used_labels:
                label = f"{label_root}-{number}"
                number += 1
            used_labels.add(label)
            labeled.append((label, rows))
            expected_by_label[label] = load_run_expected_rows(path)
            run_keys_by_label[label] = run_keys_by_path[path]
        table = build_multi_surface_table(
            labeled,
            expected_rows_by_column=expected_by_label,
            run_keys_by_column=run_keys_by_label,
        )
        sys.stdout.write(render_multi_surface_table(table, markdown=arguments.markdown))
        return 0 if all(values["complete"] for values in table["footer"].values()) else 1

    if len(paths) == 1:
        path = paths[0]
        rows = load_rows(path, dedupe=dedupe)
        summary = summarize(
            rows,
            expected_rows=load_run_expected_rows(path),
            run_keys=run_keys_by_path[path],
        )
        print_table(summary, f"Summary: {path}")
        return 0 if summary.complete else 1

    if len(paths) == 2:
        rows_a = load_rows(paths[0], dedupe=dedupe)
        rows_b = load_rows(paths[1], dedupe=dedupe)
        comparison = ab_compare(
            rows_a,
            rows_b,
            expected_rows_a=load_run_expected_rows(paths[0]),
            expected_rows_b=load_run_expected_rows(paths[1]),
            run_keys_a=run_keys_by_path[paths[0]],
            run_keys_b=run_keys_by_path[paths[1]],
        )
        print_ab_report(comparison, paths[0], paths[1])
        return 0 if comparison["summary_a"].complete and comparison["summary_b"].complete else 1

    raise AssertionError("validated report arity did not select a command path")
