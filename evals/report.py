"""Summary table, A/B delta, and multi-surface tables for eval JSONL results.

Usage:
  python -m evals.report evals/results/A.jsonl
  python -m evals.report A.jsonl B.jsonl              # A/B delta (sign test + Wilson)
  python -m evals.report --table f1.jsonl f2.jsonl …  # per-task × per-surface
  python -m evals.report --table --markdown f1.jsonl f2.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from evals.tasks import TASKS_BY_ID

DedupeMode = Literal["latest", "none"]


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = p + z2 / (2.0 * n)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    lo = max(0.0, (centre - margin) / denom)
    hi = min(1.0, (centre + margin) / denom)
    return (lo, hi)


def sign_test_pvalue(deltas: list[float]) -> float | None:
    """Two-sided exact binomial sign test on non-zero paired deltas.

    H0: P(delta > 0) = 1/2. Zero deltas are dropped. Returns None when no
    non-zero pairs remain. Uses ``math.comb`` only (no scipy).
    """
    nonzero = [d for d in deltas if d != 0]
    n = len(nonzero)
    if n == 0:
        return None
    k = sum(1 for d in nonzero if d > 0)
    total = 2**n
    # Two-sided: 2 * min(left cdf, right survival), capped at 1.
    left = sum(math.comb(n, i) for i in range(0, k + 1)) / total
    right = sum(math.comb(n, i) for i in range(k, n + 1)) / total
    return min(1.0, 2.0 * min(left, right))


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    if len(s) % 2:
        return float(s[m])
    return (s[m - 1] + s[m]) / 2.0


def _percentile(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * p
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(s[int(k)])
    return float(s[f] + (s[c] - s[f]) * (k - f))


def _iqr(xs: list[float]) -> tuple[float | None, float | None, float | None]:
    return (_percentile(xs, 0.25), _median(xs), _percentile(xs, 0.75))


def is_meta_row(row: dict[str, Any]) -> bool:
    """True for run-header meta lines (or any row without a task_id)."""
    if row.get("row_type") == "meta":
        return True
    return row.get("task_id") is None


def is_infra_error_row(row: dict[str, Any]) -> bool:
    """True when a row failed for infrastructure reasons (seed/cli/sdk), not task verify.

    Any ``error_class`` starting with ``infra_`` (``infra_seed``, ``infra_cli``,
    ``infra_sdk``, …) is excluded from success-rate denominators.
    """
    ec = row.get("error_class")
    return isinstance(ec, str) and ec.startswith("infra_")


def dedupe_rows_latest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the last row per (task_id, rep, surface); preserve key insertion order."""
    latest: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    order: list[tuple[Any, Any, Any]] = []
    for r in rows:
        key = (r.get("task_id"), r.get("rep"), r.get("surface"))
        if key not in latest:
            order.append(key)
        latest[key] = r
    return [latest[k] for k in order]


def load_rows(path: Path, *, dedupe: DedupeMode = "latest") -> list[dict[str, Any]]:
    """Load JSONL data rows (skip meta / missing task_id).

    Default ``dedupe="latest"`` keeps the last row per (task_id, rep, surface)
    so resume appends do not double-count. Pass ``dedupe="none"`` for forensics.
    """
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                print(
                    f"warning: {path}:{line_no}: skipping invalid JSON ({exc})",
                    file=sys.stderr,
                )
                continue
            if not isinstance(row, dict) or is_meta_row(row):
                continue
            rows.append(row)
    if dedupe == "latest":
        return dedupe_rows_latest(rows)
    # Forensics: warn on duplicates but keep all.
    seen_keys: set[tuple[Any, Any, Any]] = set()
    for r in rows:
        key = (r.get("task_id"), r.get("rep"), r.get("surface"))
        if key in seen_keys:
            print(
                f"warning: {path}: duplicate (task_id, rep, surface)={key} "
                f"(--no-dedupe keeps all rows; bare --out reuse double-counts)",
                file=sys.stderr,
            )
        else:
            seen_keys.add(key)
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate per-task metrics.

    Rows with ``error_class`` starting ``infra_`` are excluded from success-rate
    denominators and counted separately as ``infra_errors`` (total on the returned
    dict under the special key ``_meta``). Other non-null ``error`` rows remain
    harness errors (excluded from success, counted in ``harness_err``).
    """
    by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
    harness_err_by_task: dict[str, int] = defaultdict(int)
    infra_err_by_task: dict[str, int] = defaultdict(int)
    infra_errors = 0
    for r in rows:
        if is_meta_row(r):
            continue
        tid = r["task_id"]
        if is_infra_error_row(r):
            infra_errors += 1
            infra_err_by_task[tid] += 1
            continue  # infra seed/cli — excluded from success aggregates
        if r.get("error"):
            harness_err_by_task[tid] += 1
            continue  # harness/API errors excluded from success/medians (F4)
        if r.get("skipped"):
            continue  # skipped rows are excluded from success denominators
        by_task[tid].append(r)

    # Include tasks that only had harness/infra errors so columns stay visible.
    all_task_ids = sorted(set(by_task) | set(harness_err_by_task) | set(infra_err_by_task))

    out: dict[str, dict[str, Any]] = {}
    total_k = 0
    total_n = 0
    for task_id in all_task_ids:
        trs = by_task.get(task_id, [])
        n = len(trs)
        k = sum(1 for r in trs if r.get("success"))
        total_k += k
        total_n += n
        lo, hi = wilson_interval(k, n) if n else (0.0, 0.0)
        calls = [float(r.get("num_calls") or 0) for r in trs]
        q1, med_calls, q3 = _iqr(calls)
        min_calls = min(calls) if calls else None
        max_calls = max(calls) if calls else None
        optimal = TASKS_BY_ID.get(task_id, {}).get("optimal_calls")
        total_calls = 0
        mispick = 0
        errored = 0
        result_tokens: list[float] = []
        for r in trs:
            for c in r.get("calls") or []:
                total_calls += 1
                if c.get("class") in ("alternate", "out_of_set"):
                    mispick += 1
                if c.get("is_error"):
                    errored += 1
                if c.get("result_tokens") is not None:
                    result_tokens.append(float(c["result_tokens"]))
        capped = sum(1 for r in trs if r.get("hit_max_iterations") or r.get("stop_reason") == "max_tokens")
        cum_inputs = [float(r.get("cum_input_tokens") or 0) for r in trs]
        out[task_id] = {
            "n": n,
            "k": k,
            "success": f"{k}/{n}" if n else "0/0",
            "wilson_lo": lo,
            "wilson_hi": hi,
            "med_calls": med_calls,
            "calls_min": min_calls,
            "calls_max": max_calls,
            "calls_q1": q1,
            "calls_q3": q3,
            "optimal_calls": optimal,
            "mispick_rate": (mispick / total_calls) if total_calls else 0.0,
            "errored_calls": errored,
            "capped": capped,
            "harness_err": harness_err_by_task.get(task_id, 0),
            "infra_err": infra_err_by_task.get(task_id, 0),
            "med_result_tokens": _median(result_tokens),
            "p95_result_tokens": _percentile(result_tokens, 0.95),
            "med_cum_input": _median(cum_inputs),
        }
    agg_lo, agg_hi = wilson_interval(total_k, total_n) if total_n else (0.0, 0.0)
    out["_meta"] = {
        "infra_errors": infra_errors,
        "aggregate_k": total_k,
        "aggregate_n": total_n,
        "aggregate_wilson_lo": agg_lo,
        "aggregate_wilson_hi": agg_hi,
    }
    return out


def _fmt(x: float | None, digits: int = 1) -> str:
    if x is None:
        return "-"
    return f"{x:.{digits}f}"


def print_table(summary: dict[str, dict[str, Any]], title: str) -> None:
    meta = summary.get("_meta") or {}
    print(title)
    if meta.get("infra_errors"):
        print(f"infra errors: {meta['infra_errors']}")
    agg_n = int(meta.get("aggregate_n") or 0)
    if agg_n:
        agg_k = int(meta.get("aggregate_k") or 0)
        alo = float(meta.get("aggregate_wilson_lo") or 0.0)
        ahi = float(meta.get("aggregate_wilson_hi") or 0.0)
        rate = agg_k / agg_n if agg_n else 0.0
        print(f"aggregate success: {agg_k}/{agg_n} ({rate:.1%}) Wilson95 [{alo:.2f},{ahi:.2f}]")
    # Show min/med/max call columns when any task has n>1.
    show_var = any(s.get("n", 0) > 1 for tid, s in summary.items() if tid != "_meta")
    if show_var:
        header = (
            f"{'task':<6} {'n':>3} {'success':>8} {'wilson95':>16} "
            f"{'calls_min':>9} {'med_calls':>9} {'calls_max':>9} {'opt':>4} "
            f"{'IQR':>11} {'mispick':>8} {'err':>4} "
            f"{'capped':>6} {'h_err':>5} {'i_err':>5} {'med_rtok':>8} {'p95_rtok':>8} {'med_cum_in':>10}"
        )
    else:
        header = (
            f"{'task':<6} {'n':>3} {'success':>8} {'wilson95':>16} "
            f"{'med_calls':>9} {'opt':>4} {'IQR':>11} {'mispick':>8} {'err':>4} "
            f"{'capped':>6} {'h_err':>5} {'i_err':>5} {'med_rtok':>8} {'p95_rtok':>8} {'med_cum_in':>10}"
        )
    print(header)
    print("-" * len(header))
    for task_id, s in summary.items():
        if task_id == "_meta":
            continue
        wilson = f"[{s['wilson_lo']:.2f},{s['wilson_hi']:.2f}]"
        iqr = f"{_fmt(s['calls_q1'])}-{_fmt(s['calls_q3'])}"
        opt = s["optimal_calls"] if s["optimal_calls"] is not None else "-"
        if show_var:
            print(
                f"{task_id:<6} {s['n']:>3} {s['success']:>8} {wilson:>16} "
                f"{_fmt(s.get('calls_min')):>9} {_fmt(s['med_calls']):>9} {_fmt(s.get('calls_max')):>9} "
                f"{opt!s:>4} {iqr:>11} {s['mispick_rate']:>7.1%} "
                f"{s['errored_calls']:>4} {s['capped']:>6} {s['harness_err']:>5} "
                f"{s.get('infra_err', 0):>5} "
                f"{_fmt(s['med_result_tokens'], 0):>8} {_fmt(s['p95_result_tokens'], 0):>8} "
                f"{_fmt(s['med_cum_input'], 0):>10}"
            )
        else:
            print(
                f"{task_id:<6} {s['n']:>3} {s['success']:>8} {wilson:>16} "
                f"{_fmt(s['med_calls']):>9} {opt!s:>4} {iqr:>11} {s['mispick_rate']:>7.1%} "
                f"{s['errored_calls']:>4} {s['capped']:>6} {s['harness_err']:>5} "
                f"{s.get('infra_err', 0):>5} "
                f"{_fmt(s['med_result_tokens'], 0):>8} {_fmt(s['p95_result_tokens'], 0):>8} "
                f"{_fmt(s['med_cum_input'], 0):>10}"
            )


# ---------------------------------------------------------------------------
# A/B comparison
# ---------------------------------------------------------------------------


def ab_compare(
    rows_a: list[dict[str, Any]],
    rows_b: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare two result sets: paired call-count deltas + success rates.

    Paired call deltas only include tasks that are present and successful in
    both A and B. When multiple success rows exist for a task, the **last** one
    wins (matches load-time ``dedupe="latest"`` semantics).
    """
    sum_a = summarize(rows_a)
    sum_b = summarize(rows_b)

    def _success_rows_by_task(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            if is_meta_row(r) or is_infra_error_row(r) or r.get("error") or r.get("skipped"):
                continue
            if not r.get("success"):
                continue
            tid = str(r["task_id"])
            out[tid] = r  # last wins (dedupe already applied)
        return out

    sa = _success_rows_by_task(rows_a)
    sb = _success_rows_by_task(rows_b)
    shared = sorted(set(sa) & set(sb))
    deltas: list[float] = []
    per_task: list[dict[str, Any]] = []
    for tid in shared:
        ca = float(sa[tid].get("num_calls") or 0)
        cb = float(sb[tid].get("num_calls") or 0)
        d = cb - ca  # B − A (negative = B fewer calls = better if lower is better)
        deltas.append(d)
        per_task.append({"task_id": tid, "calls_a": ca, "calls_b": cb, "delta": d})

    meta_a = sum_a.get("_meta") or {}
    meta_b = sum_b.get("_meta") or {}
    return {
        "summary_a": sum_a,
        "summary_b": sum_b,
        "paired_tasks": per_task,
        "median_delta": _median(deltas),
        "sign_test_p": sign_test_pvalue(deltas),
        "n_paired": len(deltas),
        "success_a": {
            "k": int(meta_a.get("aggregate_k") or 0),
            "n": int(meta_a.get("aggregate_n") or 0),
            "wilson": (
                float(meta_a.get("aggregate_wilson_lo") or 0.0),
                float(meta_a.get("aggregate_wilson_hi") or 0.0),
            ),
        },
        "success_b": {
            "k": int(meta_b.get("aggregate_k") or 0),
            "n": int(meta_b.get("aggregate_n") or 0),
            "wilson": (
                float(meta_b.get("aggregate_wilson_lo") or 0.0),
                float(meta_b.get("aggregate_wilson_hi") or 0.0),
            ),
        },
    }


def print_ab_report(cmp: dict[str, Any], path_a: Path, path_b: Path) -> None:
    print(f"A/B compare: A={path_a}  B={path_b}")
    sa, sb = cmp["success_a"], cmp["success_b"]
    ra = (sa["k"] / sa["n"]) if sa["n"] else 0.0
    rb = (sb["k"] / sb["n"]) if sb["n"] else 0.0
    print(f"  success A: {sa['k']}/{sa['n']} ({ra:.1%}) Wilson95 [{sa['wilson'][0]:.2f},{sa['wilson'][1]:.2f}]")
    print(f"  success B: {sb['k']}/{sb['n']} ({rb:.1%}) Wilson95 [{sb['wilson'][0]:.2f},{sb['wilson'][1]:.2f}]")
    print(f"  success rate delta (B−A): {rb - ra:+.1%}")
    print(f"  paired successful tasks: {cmp['n_paired']}")
    print(f"  median call delta (B−A): {_fmt(cmp['median_delta'])}")
    p = cmp["sign_test_p"]
    print(f"  sign-test p-value (two-sided): {p if p is not None else 'n/a'}")
    if cmp["paired_tasks"]:
        print()
        print(f"{'task':<6} {'calls_A':>8} {'calls_B':>8} {'delta':>8}")
        print("-" * 34)
        for row in cmp["paired_tasks"]:
            print(f"{row['task_id']:<6} {row['calls_a']:>8.0f} {row['calls_b']:>8.0f} {row['delta']:>+8.0f}")


# ---------------------------------------------------------------------------
# Multi-surface table
# ---------------------------------------------------------------------------


def _task_sort_key(tid: str) -> tuple[str, int]:
    digits = "".join(c for c in tid if c.isdigit())
    return (tid[0] if tid else "", int(digits) if digits else 0)


def format_surface_cell(row: dict[str, Any] | None) -> str:
    """Cell for multi-surface table: '✅ Nc/Mmp', 'skip', 'ERR', or '—'."""
    if row is None:
        return "—"
    if row.get("skipped"):
        return "skip"
    if row.get("error") or is_infra_error_row(row):
        return "ERR"
    ok = "✅" if row.get("success") else "❌"
    n_calls = row.get("num_calls")
    n_calls_s = str(n_calls) if n_calls is not None else "?"
    if row.get("classification") == "external":
        return f"{ok} {n_calls_s}c"
    alt = row.get("alternate_calls")
    oos = row.get("out_of_set_calls")
    # None counters (external nulling) → omit mispick suffix.
    if alt is None and oos is None:
        return f"{ok} {n_calls_s}c"
    mp = int(alt or 0) + int(oos or 0)
    if mp:
        return f"{ok} {n_calls_s}c/{mp}mp"
    return f"{ok} {n_calls_s}c"


def build_multi_surface_table(
    file_rows: list[tuple[str, list[dict[str, Any]]]],
) -> dict[str, Any]:
    """Build a per-task × per-surface grid from labeled row sets.

    ``file_rows`` is a list of ``(column_label, rows)``. Column labels default
    to each file's dominant ``surface`` field when the caller passes that label.
    For each column, the latest row per task_id is used (rep-agnostic: last wins).
    """
    columns: list[str] = []
    by_col: dict[str, dict[str, dict[str, Any]]] = {}
    for label, rows in file_rows:
        columns.append(label)
        col_map: dict[str, dict[str, Any]] = {}
        for r in rows:
            if is_meta_row(r):
                continue
            tid = str(r["task_id"])
            col_map[tid] = r  # last wins
        by_col[label] = col_map

    all_tasks = sorted({t for m in by_col.values() for t in m}, key=_task_sort_key)
    cells: dict[str, dict[str, str]] = {}
    raw: dict[str, dict[str, dict[str, Any] | None]] = {}
    for tid in all_tasks:
        cells[tid] = {}
        raw[tid] = {}
        for col in columns:
            r = by_col[col].get(tid)
            raw[tid][col] = r
            cells[tid][col] = format_surface_cell(r)

    # Aggregate footer per column.
    footer: dict[str, dict[str, Any]] = {}
    for col in columns:
        succ = run = calls = mispicks = 0
        mispick_comparable = True
        infra = 0
        for _tid, r in by_col[col].items():
            if is_infra_error_row(r):
                infra += 1
                continue
            if r.get("error"):
                continue
            if r.get("skipped"):
                continue
            run += 1
            if r.get("success"):
                succ += 1
            calls += int(r.get("num_calls") or 0)
            if r.get("classification") == "external":
                mispick_comparable = False
            else:
                alt, oos = r.get("alternate_calls"), r.get("out_of_set_calls")
                if alt is None and oos is None:
                    mispick_comparable = False
                else:
                    mispicks += int(alt or 0) + int(oos or 0)
        footer[col] = {
            "success": succ,
            "n": run,
            "calls": calls,
            "mispicks": mispicks if mispick_comparable else None,
            "infra_errors": infra,
        }
    return {"columns": columns, "task_ids": all_tasks, "cells": cells, "raw": raw, "footer": footer}


def render_multi_surface_table(table: dict[str, Any], *, markdown: bool = False) -> str:
    """Render multi-surface table as plain text or GitHub markdown."""
    cols: list[str] = table["columns"]
    task_ids: list[str] = table["task_ids"]
    cells: dict[str, dict[str, str]] = table["cells"]
    footer: dict[str, dict[str, Any]] = table["footer"]
    lines: list[str] = []

    def _prompt_snip(tid: str) -> str:
        p = (TASKS_BY_ID.get(tid, {}).get("prompt") or "").replace("{project}", "P")
        return (p[:32] + "…") if len(p) > 32 else p

    if markdown:
        header = "| task | what | " + " | ".join(cols) + " |"
        sep = "| --- | --- | " + " | ".join("---" for _ in cols) + " |"
        lines.append(header)
        lines.append(sep)
        for tid in task_ids:
            row_cells = " | ".join(cells[tid].get(c, "—") for c in cols)
            lines.append(f"| {tid} | {_prompt_snip(tid)} | {row_cells} |")
        # Footer
        foot_parts = []
        for c in cols:
            f = footer[c]
            rate = f"{f['success']}/{f['n']}" if f["n"] else "0/0"
            mp = f", {f['mispicks']}mp" if f["mispicks"] is not None else ""
            foot_parts.append(f"{rate} ({f['calls']}c{mp}, i={f['infra_errors']})")
        lines.append("| **agg** | | " + " | ".join(foot_parts) + " |")
        return "\n".join(lines) + "\n"

    col_w = max(14, max((len(c) for c in cols), default=14))
    head = f"{'task':5} {'what':34} " + " ".join(f"{c:{col_w}}" for c in cols)
    lines.append(head)
    lines.append("-" * len(head))
    for tid in task_ids:
        line = f"{tid:5} {_prompt_snip(tid):34} "
        for c in cols:
            line += f"{cells[tid].get(c, '—'):{col_w}} "
        lines.append(line.rstrip())
    lines.append("-" * len(head))
    for c in cols:
        f = footer[c]
        rate = f"{f['success']}/{f['n']}" if f["n"] else "0/0"
        pct = f" ({100 * f['success'] / f['n']:.0f}%)" if f["n"] else ""
        mp = f"  mispicks {f['mispicks']}" if f["mispicks"] is not None else "  mispicks n/a"
        lines.append(f"{c:12} success {rate}{pct}  total calls {f['calls']}{mp}  infra {f['infra_errors']}")
    return "\n".join(lines) + "\n"


def _surface_label_for_file(path: Path, rows: list[dict[str, Any]]) -> str:
    """Pick a column label from the file's dominant surface field, else stem."""
    counts: dict[str, int] = defaultdict(int)
    for r in rows:
        s = r.get("surface")
        if s:
            counts[str(s)] += 1
    if counts:
        return max(counts, key=counts.get)  # type: ignore[arg-type]
    return path.stem


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Summarize eval JSONL results")
    p.add_argument(
        "files",
        nargs="*",
        help="JSONL file(s): one for summary, two for A/B, N with --table",
    )
    p.add_argument(
        "--table",
        action="store_true",
        help="Multi-surface per-task table (one column per file, labeled by surface)",
    )
    p.add_argument(
        "--markdown",
        action="store_true",
        help="With --table, emit a GitHub-flavored markdown table",
    )
    p.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Keep all rows (forensics); default is latest-wins per (task_id,rep,surface)",
    )
    args = p.parse_args(argv)
    dedupe: DedupeMode = "none" if args.no_dedupe else "latest"

    if not args.files:
        p.print_help()
        return 2

    paths = [Path(f) for f in args.files]
    for path in paths:
        if not path.exists():
            print(f"error: file not found: {path}", file=sys.stderr)
            return 2

    if args.table:
        if len(paths) < 1:
            print("error: --table requires at least one JSONL", file=sys.stderr)
            return 2
        labeled: list[tuple[str, list[dict[str, Any]]]] = []
        used_labels: set[str] = set()
        for path in paths:
            rows = load_rows(path, dedupe=dedupe)
            label = _surface_label_for_file(path, rows)
            # Disambiguate duplicate surface labels (e.g. two external files).
            base = label
            n = 2
            while label in used_labels:
                label = f"{base}-{n}"
                n += 1
            used_labels.add(label)
            labeled.append((label, rows))
        table = build_multi_surface_table(labeled)
        sys.stdout.write(render_multi_surface_table(table, markdown=args.markdown))
        return 0

    if len(paths) == 1:
        path = paths[0]
        rows = load_rows(path, dedupe=dedupe)
        summary = summarize(rows)
        task_keys = [k for k in summary if k != "_meta"]
        if not task_keys:
            infra_n = (summary.get("_meta") or {}).get("infra_errors", 0)
            if infra_n:
                print(f"infra errors: {infra_n}")
            print(f"(no non-skipped / non-error rows in {path})")
            return 0
        print_table(summary, f"Summary: {path}")
        return 0

    if len(paths) == 2:
        rows_a = load_rows(paths[0], dedupe=dedupe)
        rows_b = load_rows(paths[1], dedupe=dedupe)
        cmp = ab_compare(rows_a, rows_b)
        print_ab_report(cmp, paths[0], paths[1])
        return 0

    print(
        "error: pass one JSONL (summary), two (A/B delta), or use --table with N files",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
