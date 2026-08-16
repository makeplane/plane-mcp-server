"""Offline eval tests for table."""

from __future__ import annotations

import json
from typing import Any

import pytest

from evals import report as report_mod
from evals.report import (
    build_multi_surface_table,
    format_surface_cell,
    render_multi_surface_table,
    summarize,
)
from tests.evals.conftest import case_params


def _synth_row(
    tid: str,
    *,
    rep: int = 0,
    success: bool = True,
    num_calls: int = 2,
    server: str = "local",
    skipped: str | None = None,
    error: str | None = None,
    error_class: str | None = None,
    label: str = "local",
    calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "task_id": tid,
        "rep": rep,
        "label": label,
        "success": success,
        "trace_integrity": True,
        "num_calls": num_calls,
        "server": server,
        "skipped": skipped,
        "error": error,
        "error_class": error_class,
        "calls": list(calls or []),
        "tool_manifest_fingerprint": "manifest-a",
    }


def test_print_table_shows_infra_errors(capsys):
    summary = summarize(
        [
            {"task_id": "R1", "success": True, "num_calls": 1, "calls": []},
            {"task_id": "R1", "error": "seed failed", "error_class": "infra_seed"},
            {"task_id": "R1", "error": "CLI failed", "error_class": "infra_cli"},
        ]
    )
    report_mod.print_table(summary, "Summary: test")
    out = capsys.readouterr().out
    assert "infra errors: 2" in out
    assert "i_err" in out
    assert "R1" in out
    # per-task infra_err value rendered next to h_err
    assert "    2" in out  # i_err column value


def test_report_separates_success_from_completeness_and_sets_exit_status(tmp_path, capsys):
    collision = tmp_path / "collision.jsonl"
    collision.write_text(
        "\n".join(
            [
                json.dumps({"row_type": "meta", "expected_rows": 1, "server": "local"}),
                json.dumps(_synth_row("R1", skipped="env:fixture-collision:customers:Acme")),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert report_mod.main([str(collision)]) == 1
    collision_output = capsys.readouterr().out
    assert "pooled repetition success: 0/0" in collision_output
    assert "EXECUTION COVERAGE: 0/1 rows evaluated (0.0%)" in collision_output
    assert "R1 (env:fixture-collision:customers:Acme)" in collision_output
    assert "RUN INCOMPLETE:" in collision_output
    assert "unexpected skips=1 [fixture-collision=1]" in collision_output

    plan_gated = tmp_path / "plan-gated.jsonl"
    plan_gated.write_text(
        "\n".join(
            [
                json.dumps({"row_type": "meta", "expected_rows": 1, "server": "local"}),
                json.dumps(_synth_row("L4", skipped="env:plan-gated:customers")),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert report_mod.main([str(plan_gated)]) == 0
    plan_output = capsys.readouterr().out
    assert "pooled repetition success: 0/0" in plan_output
    assert "EXECUTION COVERAGE: 0/1 rows evaluated (0.0%)" in plan_output
    assert "L4 (env:plan-gated:customers)" in plan_output
    assert "RUN COMPLETE:" in plan_output
    assert "expected skips=1 [plan-gated=1]" in plan_output


def _report_marks_entirely_estimated_result_token_columns(_tmp_path, capsys):
    rows = [
        {
            "task_id": "R1",
            "rep": 0,
            "success": True,
            "trace_integrity": True,
            "num_calls": 1,
            "calls": [{"result_tokens": 12, "result_tokens_estimated": True}],
            "result_tokens_estimated": True,
        }
    ]
    summary = summarize(rows)
    assert summary.result_tokens_mode == "estimated"
    assert summary.tasks["R1"].result_tokens_mode == "estimated"

    report_mod.print_table(summary, "estimated")
    output = capsys.readouterr().out
    assert "entirely estimated" in output
    assert "med_rtok~" in output
    assert "~12" in output


def _report_marks_mixed_measured_and_estimated_columns(_tmp_path, capsys):
    rows = [
        {
            "task_id": "R1",
            "rep": 0,
            "success": True,
            "trace_integrity": True,
            "num_calls": 1,
            "calls": [{"result_tokens": 8, "result_tokens_estimated": False}],
            "result_tokens_estimated": False,
        },
        {
            "task_id": "R1",
            "rep": 1,
            "success": True,
            "trace_integrity": True,
            "num_calls": 1,
            "calls": [{"result_tokens": 10, "result_tokens_estimated": True}],
            "result_tokens_estimated": True,
        },
    ]
    summary = summarize(rows)
    assert summary.result_tokens_mode == "mixed"
    assert summary.tasks["R1"].result_tokens_mode == "mixed"

    report_mod.print_table(summary, "mixed")
    output = capsys.readouterr().out
    assert "mixed measured and estimated" in output
    assert "med_rtok*" in output


def _report_main_table_cli(tmp_path, capsys):
    f1 = tmp_path / "a.jsonl"
    f2 = tmp_path / "b.jsonl"
    f1.write_text(
        json.dumps(_synth_row("R1", label="local", num_calls=2)) + "\n",
        encoding="utf-8",
    )
    f2.write_text(
        json.dumps(_synth_row("R1", label="candidate", num_calls=1)) + "\n",
        encoding="utf-8",
    )
    rc = report_mod.main(["--table", str(f1), str(f2)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "local" in out and "candidate" in out
    assert "R1" in out


def _report_main_table_refuses_when_battery_fingerprints_differ(tmp_path, capsys):
    f1 = tmp_path / "old.jsonl"
    f2 = tmp_path / "new.jsonl"
    f1.write_text(
        json.dumps({**_synth_row("R1", label="local"), "battery": "6425dcc64404"}) + "\n",
        encoding="utf-8",
    )
    f2.write_text(
        json.dumps({**_synth_row("R1", label="candidate"), "battery": "newfinger001"}) + "\n",
        encoding="utf-8",
    )

    rc = report_mod.main(["--table", str(f1), str(f2)])

    assert rc == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "comparability cannot be established from the persisted identity" in captured.err
    assert "battery differs across files" in captured.err


def _report_main_markdown_flag(tmp_path, capsys):
    f1 = tmp_path / "a.jsonl"
    row = {**_synth_row("R1", label="candidate", num_calls=1), "tool_manifest_fingerprint": None}
    f1.write_text(json.dumps(row) + "\n", encoding="utf-8")
    rc = report_mod.main(["--table", "--markdown", str(f1)])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("WARNING: TOOL MANIFEST ABSENT")
    assert "\n\n| task |" in out
    assert "| R1 |" in out
    assert "---" in out


def _report_main_no_dedupe_flag(tmp_path, capsys):
    p = tmp_path / "d.jsonl"
    rows = [
        _synth_row("R1", label="local", num_calls=1, success=True),
        {**_synth_row("R1", label="local", num_calls=9, success=False)},
    ]
    # Both rows have the same (task_id, rep, label), so latest-wins keeps one.
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    rc = report_mod.main(["--no-dedupe", str(p)])
    assert rc == 0
    # With no-dedupe, both rows enter summarize → n=2 for R1.
    # (dedupe default would leave n=1.)
    out = capsys.readouterr().out
    assert "R1" in out
    assert "2/2" in out or "1/2" in out  # one success of two


@pytest.mark.parametrize(
    "case",
    case_params(
        _report_marks_entirely_estimated_result_token_columns,
        _report_marks_mixed_measured_and_estimated_columns,
        _report_main_table_cli,
        _report_main_table_refuses_when_battery_fingerprints_differ,
        _report_main_markdown_flag,
        _report_main_no_dedupe_flag,
    ),
)
def test_report_behaviours(case, tmp_path, capsys):
    case(tmp_path, capsys)


def test_format_surface_cell_variants():
    assert format_surface_cell(None) == "—"
    assert format_surface_cell(_synth_row("R1", skipped="nope")) == "skip"
    assert format_surface_cell(_synth_row("R1", error="boom")) == "ERR"
    assert format_surface_cell(_synth_row("R1", error_class="infra_seed", error="x")) == "ERR"
    assert format_surface_cell(_synth_row("R1", success=True, num_calls=3)) == "✅ 3c · tools —"
    assert format_surface_cell(_synth_row("R1", success=False, num_calls=4)) == "❌ 4c · tools —"
    assert format_surface_cell(_synth_row("R1", server="external", num_calls=5)) == "✅ 5c · tools —"


def _multi_surface_table_snapshot_with_external():
    local = [
        _synth_row("R1", label="local", num_calls=4),
        _synth_row("R2", label="local", success=False, num_calls=2),
    ]
    candidate = [
        _synth_row("R1", label="candidate", num_calls=2),
        _synth_row("R2", label="candidate", skipped="unsupported", num_calls=0),
    ]
    external = [
        _synth_row("R1", label="akhil", server="external", num_calls=3),
        _synth_row("R2", label="akhil", server="external", num_calls=1, success=False),
        _synth_row("R3", label="akhil", server="external", error="timeout", error_class="infra_cli"),
    ]
    table = build_multi_surface_table([("local", local), ("candidate", candidate), ("akhil", external)])
    assert table["columns"] == ["local", "candidate", "akhil"]
    assert "R1" in table["task_ids"] and "R3" in table["task_ids"]
    assert table["cells"]["R1"]["local"] == "✅ 4c · tools —"
    assert table["cells"]["R1"]["candidate"] == "✅ 2c · tools —"
    assert table["cells"]["R1"]["akhil"] == "✅ 3c · tools —"
    assert table["cells"]["R2"]["candidate"] == "skip"
    assert table["cells"]["R3"]["akhil"] == "ERR"

    text = render_multi_surface_table(table, markdown=False)
    assert "local" in text and "candidate" in text and "akhil" in text
    assert "✅ 3c · tools —" in text
    assert "skip" in text
    assert "ERR" in text
    assert "infra 1" in text

    md = render_multi_surface_table(table, markdown=True)
    assert md.startswith("| task |")
    assert "| R1 |" in md
    assert "---" in md
    assert "**agg**" in md

    assert table["footer"]["akhil"]["tool_variability"] is None
    assert table["footer"]["local"]["tool_variability"] is None
    assert table["footer"]["akhil"]["infra_errors"] == 1


def _multi_surface_table_aggregates_reps_and_flags_unstable():
    rows = [
        _synth_row("R1", rep=0, success=True, num_calls=2, label="local", calls=[{"tool": "a"}, {"tool": "b"}]),
        _synth_row("R1", rep=1, success=True, num_calls=3, label="local", calls=[{"tool": "a"}]),
        _synth_row("R1", rep=2, success=True, num_calls=2, label="local", calls=[{"tool": "a"}]),
        _synth_row("R2", rep=0, success=True, num_calls=1, label="local", calls=[{"tool": "c"}]),
        _synth_row("R2", rep=1, success=False, num_calls=4, label="local", calls=[{"tool": "failed_only"}]),
        _synth_row("R2", rep=2, success=True, num_calls=2, label="local", calls=[{"tool": "c"}]),
    ]

    table = build_multi_surface_table([("local", rows)])

    assert table["multi_rep"] is True
    assert table["cells"]["R1"]["local"] == (
        "✅ 3/3 [0.44,1.00] 2-3c · tools success-only n=3; failed excluded=0; core:a(3c); variable:b=33%(1c)"
    )
    assert table["cells"]["R2"]["local"] == (
        "⚠ UNSTABLE 2/3 [0.21,0.94] 1-4c · tools success-only n=2; failed excluded=1; core:c(2c)"
    )
    assert table["footer"]["local"]["success"] == 5
    assert table["footer"]["local"]["n"] == 6
    assert table["footer"]["local"]["tool_variability"] == 1
    rendered = render_multi_surface_table(table)
    assert "tool variability 1/2 tasks" in rendered
    assert "noise floor" not in rendered


@pytest.mark.parametrize(
    "case",
    case_params(_multi_surface_table_snapshot_with_external, _multi_surface_table_aggregates_reps_and_flags_unstable),
)
def test_multi_surface_behaviours(case):
    case()


def test_single_rep_multi_surface_renders_tool_distribution_unavailable():
    rows = [_synth_row("R1", label="local", success=True, num_calls=2)]

    rendered = render_multi_surface_table(build_multi_surface_table([("local", rows)]))

    assert rendered == (
        "task  what                               local         \n"
        "-------------------------------------------------------\n"
        "R1    In project P, what is the curren…  ✅ 2c · tools —\n"
        "-------------------------------------------------------\n"
        "local        success task-cluster 100.0% [1.00,1.00]; pooled 1/1  total calls 2  "
        "tool variability —  infra 0\n"
        "local        EXECUTION COVERAGE: 1/1 rows evaluated (100.0%)\n"
        "local        off-surface indicators: 0\n"
        "local          zero-call success: 0\n"
        "local          write without a write call: 0\n"
        "local          answer without provenance: 0\n"
        "local          implausibly few calls: 0; rule: among at least 5 successful trace-usable repetitions for "
        "the same task, calls < Q1 - 3×IQR and calls ≤ half the task median\n"
        "local          limitation: detects off-surface work only when it leaves a trace signature; it cannot detect "
        "an agent that performs the work off-surface and also makes convincing surface calls\n"
        "local        schema friction (same successful, trace-intact rows as call deltas): task-mean median errored "
        "calls=0.0 across 1 tasks; task-mean errored-call rate=0.0% across 1 tasks with calls\n"
        "local          errored-call tasks: 0/1 []\n"
        "local          limitation: is_error is the MCP-level error flag, so this counts tool-reported failures; an "
        "error that is the correct task outcome still contributes, while calling the wrong tool successfully does not\n"
        "local        RUN COMPLETE: 1/1 rows completed\n"
    )


def test_multi_surface_table_reports_off_surface_indicators_per_column_in_plain_and_markdown():
    clean = [_synth_row("R1", label="clean", num_calls=1, calls=[{"tool": "list_work_items"}])]
    bypass = [_synth_row("W1", label="bypass", num_calls=0, calls=[])]
    table = build_multi_surface_table([("clean", clean), ("bypass", bypass)])

    plain = render_multi_surface_table(table)
    assert "clean        off-surface indicators: 0" in plain
    assert "bypass       off-surface indicators: 1 flagged rows (2 indicator hits)" in plain
    assert "bypass         zero-call success: 1 [W1[rep=0]]" in plain
    assert "bypass         write without a write call: 1 [W1[rep=0]]" in plain

    markdown = render_multi_surface_table(table, markdown=True)
    assert "| **off-surface indicators** | |" in markdown
    assert "off-surface indicators: 0<br>" in markdown
    assert "off-surface indicators: 1 flagged rows (2 indicator hits)<br>" in markdown


def test_multi_surface_table_reports_schema_friction_per_column_in_plain_and_markdown():
    clean = [_synth_row("R1", label="clean", num_calls=4, calls=[{"tool": "get_work_item"}])]
    friction = [
        _synth_row(
            "R1",
            label="friction",
            num_calls=4,
            calls=[{"tool": "get_work_item", "is_error": True}],
        )
    ]
    table = build_multi_surface_table([("clean", clean), ("friction", friction)])

    plain = render_multi_surface_table(table)
    assert "clean        schema friction" in plain
    assert "clean          errored-call tasks: 0/1 []" in plain
    assert "friction       errored-call tasks: 1/1 [R1=1/4 (25.0%)]" in plain
    assert "friction       limitation: is_error is the MCP-level error flag" in plain

    markdown = render_multi_surface_table(table, markdown=True)
    assert "| **schema friction** | |" in markdown
    assert "errored-call tasks: 0/1 []" in markdown
    assert "errored-call tasks: 1/1 [R1=1/4 (25.0%)]" in markdown
