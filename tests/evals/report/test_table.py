"""Offline eval tests for table."""

from __future__ import annotations

import json
from typing import Any

from evals import report as report_mod
from evals.report import (
    build_multi_surface_table,
    format_surface_cell,
    render_multi_surface_table,
    summarize,
)


def _synth_row(
    tid: str,
    *,
    rep: int = 0,
    success: bool = True,
    num_calls: int = 2,
    alt: int | None = 0,
    oos: int | None = 0,
    server: str = "local",
    skipped: str | None = None,
    error: str | None = None,
    error_class: str | None = None,
    label: str = "local",
) -> dict[str, Any]:
    return {
        "task_id": tid,
        "rep": rep,
        "label": label,
        "success": success,
        "num_calls": num_calls,
        "alternate_calls": alt,
        "out_of_set_calls": oos,
        "server": server,
        "skipped": skipped,
        "error": error,
        "error_class": error_class,
        "calls": [],
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


def test_report_behaviours(capsys, tmp_path):
    def test_report_marks_entirely_estimated_result_token_columns(capsys):
        rows = [
            {
                "task_id": "R1",
                "rep": 0,
                "success": True,
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

    def test_report_marks_mixed_measured_and_estimated_columns(capsys):
        rows = [
            {
                "task_id": "R1",
                "rep": 0,
                "success": True,
                "num_calls": 1,
                "calls": [{"result_tokens": 8, "result_tokens_estimated": False}],
                "result_tokens_estimated": False,
            },
            {
                "task_id": "R1",
                "rep": 1,
                "success": True,
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

    def test_report_main_table_cli(tmp_path, capsys):
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

    def test_report_main_table_warns_when_battery_fingerprints_differ(tmp_path, capsys):
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

        assert rc == 0
        captured = capsys.readouterr()
        assert "spans battery fingerprints" in captured.err
        assert "different task prompts/questions" in captured.err

    def test_report_main_markdown_flag(tmp_path, capsys):
        f1 = tmp_path / "a.jsonl"
        f1.write_text(json.dumps(_synth_row("R1", label="candidate", num_calls=1)) + "\n", encoding="utf-8")
        rc = report_mod.main(["--table", "--markdown", str(f1)])
        assert rc == 0
        out = capsys.readouterr().out
        assert out.startswith("| task |")
        assert "| R1 |" in out
        assert "---" in out

    def test_report_main_no_dedupe_flag(tmp_path, capsys):
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

    test_report_marks_entirely_estimated_result_token_columns(capsys)
    test_report_marks_mixed_measured_and_estimated_columns(capsys)
    _d2 = tmp_path / "test_report_main_table_cli"
    _d2.mkdir()
    test_report_main_table_cli(_d2, capsys)
    _d3 = tmp_path / "test_report_main_table_warns_when_battery_fingerprints_differ"
    _d3.mkdir()
    test_report_main_table_warns_when_battery_fingerprints_differ(_d3, capsys)
    _d4 = tmp_path / "test_report_main_markdown_flag"
    _d4.mkdir()
    test_report_main_markdown_flag(_d4, capsys)
    _d5 = tmp_path / "test_report_main_no_dedupe_flag"
    _d5.mkdir()
    test_report_main_no_dedupe_flag(_d5, capsys)


def test_format_surface_cell_variants():
    assert format_surface_cell(None) == "—"
    assert format_surface_cell(_synth_row("R1", skipped="nope")) == "skip"
    assert format_surface_cell(_synth_row("R1", error="boom")) == "ERR"
    assert format_surface_cell(_synth_row("R1", error_class="infra_seed", error="x")) == "ERR"
    assert format_surface_cell(_synth_row("R1", success=True, num_calls=3, alt=0, oos=0)) == "✅ 3c"
    assert format_surface_cell(_synth_row("R1", success=False, num_calls=4, alt=1, oos=1)) == "❌ 4c/2mp"
    # external: no mispick suffix
    assert format_surface_cell(_synth_row("R1", server="external", alt=None, oos=None, num_calls=5)) == "✅ 5c"


def test_multi_surface_behaviours():
    def test_multi_surface_table_snapshot_with_external():
        local = [
            _synth_row("R1", label="local", num_calls=4, alt=1, oos=0),
            _synth_row("R2", label="local", success=False, num_calls=2),
        ]
        candidate = [
            _synth_row("R1", label="candidate", num_calls=2, alt=0, oos=0),
            _synth_row("R2", label="candidate", skipped="unsupported", num_calls=0),
        ]
        external = [
            _synth_row("R1", label="akhil", server="external", alt=None, oos=None, num_calls=3),
            _synth_row("R2", label="akhil", server="external", alt=None, oos=None, num_calls=1, success=False),
            _synth_row("R3", label="akhil", server="external", error="timeout", error_class="infra_cli"),
        ]
        table = build_multi_surface_table([("local", local), ("candidate", candidate), ("akhil", external)])
        assert table["columns"] == ["local", "candidate", "akhil"]
        assert "R1" in table["task_ids"] and "R3" in table["task_ids"]
        assert table["cells"]["R1"]["local"] == "✅ 4c/1mp"
        assert table["cells"]["R1"]["candidate"] == "✅ 2c"
        assert table["cells"]["R1"]["akhil"] == "✅ 3c"
        assert table["cells"]["R2"]["candidate"] == "skip"
        assert table["cells"]["R3"]["akhil"] == "ERR"

        text = render_multi_surface_table(table, markdown=False)
        assert "local" in text and "candidate" in text and "akhil" in text
        assert "✅ 3c" in text
        assert "skip" in text
        assert "ERR" in text
        assert "infra 1" in text

        md = render_multi_surface_table(table, markdown=True)
        assert md.startswith("| task |")
        assert "| R1 |" in md
        assert "---" in md
        assert "**agg**" in md

        # Footer: external mispicks n/a
        assert table["footer"]["akhil"]["mispicks"] is None
        assert table["footer"]["local"]["mispicks"] == 1
        assert table["footer"]["akhil"]["infra_errors"] == 1

    def test_multi_surface_table_aggregates_reps_and_flags_unstable():
        rows = [
            _synth_row("R1", rep=0, success=True, num_calls=2, label="local"),
            _synth_row("R1", rep=1, success=True, num_calls=3, label="local"),
            _synth_row("R1", rep=2, success=True, num_calls=2, label="local"),
            _synth_row("R2", rep=0, success=True, num_calls=1, label="local"),
            _synth_row("R2", rep=1, success=False, num_calls=4, label="local"),
            _synth_row("R2", rep=2, success=True, num_calls=2, label="local"),
        ]

        table = build_multi_surface_table([("local", rows)])

        assert table["multi_rep"] is True
        assert table["cells"]["R1"]["local"] == "✅ 3/3 [0.44,1.00] 2-3c"
        assert table["cells"]["R2"]["local"] == "⚠ UNSTABLE 2/3 [0.21,0.94] 1-4c"
        assert table["footer"]["local"]["success"] == 5
        assert table["footer"]["local"]["n"] == 6
        assert table["footer"]["local"]["unstable_tasks"] == 1
        rendered = render_multi_surface_table(table)
        assert "measured noise floor: 1 task flipped at least once" in rendered
        assert "minimum meaningful difference: 2 tasks" in rendered

    test_multi_surface_table_snapshot_with_external()
    test_multi_surface_table_aggregates_reps_and_flags_unstable()


def test_single_rep_multi_surface_rendering_is_unchanged():
    rows = [_synth_row("R1", label="local", success=True, num_calls=2)]

    rendered = render_multi_surface_table(build_multi_surface_table([("local", rows)]))

    assert rendered == (
        "task  what                               local         \n"
        "-------------------------------------------------------\n"
        "R1    In project P, what is the curren…  ✅ 2c\n"
        "-------------------------------------------------------\n"
        "local        success 1/1 (100%)  total calls 2  mispicks 0  infra 0\n"
    )
