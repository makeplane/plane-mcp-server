"""Offline tests for report stats, multi-surface table, listing tokens, cleanup, meta rows."""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from evals import cleanup as cleanup_mod
from evals import report as report_mod
from evals.listing import count_tool_tokens, tool_payload_model_facing, tool_payload_wire
from evals.report import (
    ab_compare,
    build_multi_surface_table,
    dedupe_rows_latest,
    format_surface_cell,
    is_meta_row,
    load_rows,
    render_multi_surface_table,
    sign_test_pvalue,
    summarize,
    wilson_interval,
)
from evals.results import RESULT_SCHEMA_VERSION, CallRecord, TaskResult, Usage
from evals.run import (
    is_meta_or_non_task_row,
    load_resume_skip_keys,
    make_run_meta_row,
    maybe_write_run_meta,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.delenv("REDIS_PORT", raising=False)
    monkeypatch.setenv("EVAL_PLANE_API_KEY", "test-key")
    monkeypatch.setenv("EVAL_PLANE_WORKSPACE_SLUG", "test-ws")


# ---------------------------------------------------------------------------
# Sign test + Wilson
# ---------------------------------------------------------------------------


def test_sign_test_all_positive_hand_computed():
    """n=5 non-zero, all positive → two-sided p = 2 * (1/32) = 0.0625."""
    deltas = [1.0, 2.0, 3.0, 0.5, 4.0]
    p = sign_test_pvalue(deltas)
    assert p == pytest.approx(2.0 * (1.0 / 32.0))
    assert p == pytest.approx(0.0625)


def test_sign_test_four_of_five_hand_computed():
    """n=5, k=4 positive → right tail (C(5,4)+C(5,5))/32 = 6/32; p=2*6/32=0.375."""
    deltas = [1.0, 1.0, 1.0, 1.0, -1.0]
    p = sign_test_pvalue(deltas)
    right = (math.comb(5, 4) + math.comb(5, 5)) / 32.0
    assert p == pytest.approx(2.0 * right)
    assert p == pytest.approx(0.375)


def test_sign_test_drops_zeros_and_none_when_empty():
    assert sign_test_pvalue([0.0, 0.0]) is None
    assert sign_test_pvalue([]) is None
    # One positive, one zero → n=1, k=1 → p = 2*(1/2) = 1.0
    assert sign_test_pvalue([3.0, 0.0]) == pytest.approx(1.0)


def test_wilson_interval_bounds():
    lo, hi = wilson_interval(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-4)
    assert hi == pytest.approx(0.7634, abs=1e-4)
    lo0, hi0 = wilson_interval(0, 10)
    assert lo0 == 0.0
    assert hi0 == pytest.approx(0.27754, abs=1e-4)
    assert wilson_interval(0, 0) == (0.0, 0.0)


# ---------------------------------------------------------------------------
# load_rows: meta skip + dedupe
# ---------------------------------------------------------------------------


def test_load_rows_skips_meta_and_missing_task_id(tmp_path: Path):
    p = tmp_path / "r.jsonl"
    lines = [
        json.dumps(
            {
                "row_type": "meta",
                "run_id": "abc",
                "surface": "v2",
                "battery": "deadbeef0001",
                "model": "sonnet",
                "driver": "claude-cli",
                "git_sha": "x",
                "ts": "t",
            }
        ),
        json.dumps({"surface": "v2", "rep": 0, "success": True}),  # no task_id
        json.dumps({"task_id": "R1", "rep": 0, "surface": "v2", "success": True, "num_calls": 2}),
    ]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    rows = load_rows(p)
    assert len(rows) == 1
    assert rows[0].task_id == "R1"


def test_task_result_schema_round_trip_owns_usage_shape():
    result = TaskResult(
        task_id="R1",
        calls=[
            CallRecord(
                tool="find_work_items",
                classification="optimal",
                result_tokens=3,
                result_tokens_estimated=False,
                result_token_count_method="backend",
            )
        ],
        num_calls=1,
        usage_per_iteration=[Usage(10, 2, 3, 4)],
    )

    row = result.to_row()
    assert row["schema_version"] == RESULT_SCHEMA_VERSION
    assert row["usage_per_iteration"] == [{"in": 10, "out": 2, "cache_read": 3, "cache_write": 4}]
    loaded = TaskResult.from_row(row)
    assert loaded.calls[0].tool == "find_work_items"
    assert loaded.usage_per_iteration == [Usage(10, 2, 3, 4)]


def test_real_historical_rows_parse_and_report_with_backward_defaults():
    fixture = Path(__file__).parent / "fixtures" / "evals_historical_rows.jsonl"
    rows = load_rows(fixture)

    assert [row.schema_version for row in rows] == [0, 0]
    by_task = {row.task_id: row for row in rows}
    battery4 = by_task["L3"]
    assert battery4.final_text == ""
    assert battery4.result_tokens_estimated is None
    assert battery4.alternate_calls is None
    assert battery4.calls[0].result_tokens is None
    assert battery4.calls[0].action == "create"

    battery5 = by_task["R2"]
    assert battery5.final_text.endswith("\n4")
    assert battery5.result_tokens_estimated is True
    assert [call.result_tokens for call in battery5.calls] == [315, 64]

    summary = summarize(rows)
    assert summary["L3"]["success"] == "1/1"
    assert summary["L3"]["med_calls"] == 1
    assert summary["L3"]["result_tokens_mode"] == "unavailable"
    assert summary["R2"]["success"] == "1/1"
    assert summary["R2"]["med_calls"] == 2
    assert summary["R2"]["result_tokens_mode"] == "estimated"


def test_dedupe_rows_latest_pure():
    rows = [
        {"task_id": "R1", "rep": 0, "surface": "full", "num_calls": 1},
        {"task_id": "R1", "rep": 0, "surface": "full", "num_calls": 5},
        {"task_id": "R2", "rep": 0, "surface": "full", "num_calls": 3},
    ]
    out = dedupe_rows_latest(rows)
    assert len(out) == 2
    by_id = {r.task_id: r for r in out}
    assert by_id["R1"].num_calls == 5
    assert by_id["R2"].num_calls == 3


def test_summarize_aggregate_wilson_and_call_variance():
    rows = [
        {"task_id": "R1", "rep": 0, "success": True, "num_calls": 2, "calls": []},
        {"task_id": "R1", "rep": 1, "success": True, "num_calls": 4, "calls": []},
        {"task_id": "R1", "rep": 2, "success": False, "num_calls": 6, "calls": []},
        {"task_id": "R2", "rep": 0, "success": True, "num_calls": 1, "calls": []},
    ]
    s = summarize(rows)
    assert s["R1"]["n"] == 3
    assert s["R1"]["k"] == 2
    assert s["R1"]["calls_min"] == 2.0
    assert s["R1"]["calls_max"] == 6.0
    assert s["R1"]["med_calls"] == 4.0
    meta = s["_meta"]
    assert meta["aggregate_k"] == 3
    assert meta["aggregate_n"] == 4
    assert 0.0 <= meta["aggregate_wilson_lo"] <= meta["aggregate_wilson_hi"] <= 1.0


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
    assert summary["_meta"]["result_tokens_mode"] == "estimated"
    assert summary["R1"]["result_tokens_mode"] == "estimated"

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
    assert summary["_meta"]["result_tokens_mode"] == "mixed"
    assert summary["R1"]["result_tokens_mode"] == "mixed"

    report_mod.print_table(summary, "mixed")
    output = capsys.readouterr().out
    assert "mixed measured and estimated" in output
    assert "med_rtok*" in output


# ---------------------------------------------------------------------------
# A/B compare
# ---------------------------------------------------------------------------


def test_ab_compare_paired_deltas_and_sign_test():
    rows_a = [
        {"task_id": "R1", "rep": 0, "success": True, "num_calls": 5, "calls": []},
        {"task_id": "R2", "rep": 0, "success": True, "num_calls": 3, "calls": []},
        {"task_id": "R3", "rep": 0, "success": False, "num_calls": 9, "calls": []},  # not paired
    ]
    rows_b = [
        {"task_id": "R1", "rep": 0, "success": True, "num_calls": 2, "calls": []},  # delta -3
        {"task_id": "R2", "rep": 0, "success": True, "num_calls": 4, "calls": []},  # delta +1
        {"task_id": "R3", "rep": 0, "success": True, "num_calls": 1, "calls": []},  # A failed → not paired
    ]
    cmp = ab_compare(rows_a, rows_b)
    assert cmp["n_paired"] == 2
    deltas = {p["task_id"]: p["delta"] for p in cmp["paired_tasks"]}
    assert deltas["R1"] == -3.0
    assert deltas["R2"] == 1.0
    assert cmp["median_delta"] == pytest.approx(-1.0)  # median of [-3, 1]
    assert cmp["sign_test_p"] is not None
    assert cmp["success_a"]["k"] == 2 and cmp["success_a"]["n"] == 3
    assert cmp["success_b"]["k"] == 3 and cmp["success_b"]["n"] == 3


# ---------------------------------------------------------------------------
# Multi-surface table
# ---------------------------------------------------------------------------


def _synth_row(
    tid: str,
    *,
    success: bool = True,
    num_calls: int = 2,
    alt: int | None = 0,
    oos: int | None = 0,
    classification: str = "exact",
    skipped: str | None = None,
    error: str | None = None,
    error_class: str | None = None,
    surface: str = "v2",
) -> dict[str, Any]:
    return {
        "task_id": tid,
        "rep": 0,
        "surface": surface,
        "success": success,
        "num_calls": num_calls,
        "alternate_calls": alt,
        "out_of_set_calls": oos,
        "classification": classification,
        "skipped": skipped,
        "error": error,
        "error_class": error_class,
        "calls": [],
    }


def test_format_surface_cell_variants():
    assert format_surface_cell(None) == "—"
    assert format_surface_cell(_synth_row("R1", skipped="nope")) == "skip"
    assert format_surface_cell(_synth_row("R1", error="boom")) == "ERR"
    assert format_surface_cell(_synth_row("R1", error_class="infra_seed", error="x")) == "ERR"
    assert format_surface_cell(_synth_row("R1", success=True, num_calls=3, alt=0, oos=0)) == "✅ 3c"
    assert format_surface_cell(_synth_row("R1", success=False, num_calls=4, alt=1, oos=1)) == "❌ 4c/2mp"
    # external: no mispick suffix
    assert format_surface_cell(_synth_row("R1", classification="external", alt=None, oos=None, num_calls=5)) == "✅ 5c"


def test_multi_surface_table_snapshot_with_external():
    legacy = [
        _synth_row("R1", surface="full", num_calls=4, alt=1, oos=0),
        _synth_row("R2", surface="full", success=False, num_calls=2),
    ]
    v2 = [
        _synth_row("R1", surface="v2", num_calls=2, alt=0, oos=0),
        _synth_row("R2", surface="v2", skipped="unsupported", num_calls=0),
    ]
    external = [
        _synth_row("R1", surface="akhil", classification="external", alt=None, oos=None, num_calls=3),
        _synth_row("R2", surface="akhil", classification="external", alt=None, oos=None, num_calls=1, success=False),
        _synth_row("R3", surface="akhil", classification="external", error="timeout", error_class="infra_cli"),
    ]
    table = build_multi_surface_table([("full", legacy), ("v2", v2), ("akhil", external)])
    assert table["columns"] == ["full", "v2", "akhil"]
    assert "R1" in table["task_ids"] and "R3" in table["task_ids"]
    assert table["cells"]["R1"]["full"] == "✅ 4c/1mp"
    assert table["cells"]["R1"]["v2"] == "✅ 2c"
    assert table["cells"]["R1"]["akhil"] == "✅ 3c"
    assert table["cells"]["R2"]["v2"] == "skip"
    assert table["cells"]["R3"]["akhil"] == "ERR"

    text = render_multi_surface_table(table, markdown=False)
    assert "full" in text and "v2" in text and "akhil" in text
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
    assert table["footer"]["full"]["mispicks"] == 1
    assert table["footer"]["akhil"]["infra_errors"] == 1


def test_report_main_table_cli(tmp_path: Path, capsys):
    f1 = tmp_path / "a.jsonl"
    f2 = tmp_path / "b.jsonl"
    f1.write_text(
        json.dumps(_synth_row("R1", surface="full", num_calls=2)) + "\n",
        encoding="utf-8",
    )
    f2.write_text(
        json.dumps(_synth_row("R1", surface="v2", num_calls=1)) + "\n",
        encoding="utf-8",
    )
    rc = report_mod.main(["--table", str(f1), str(f2)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "full" in out and "v2" in out
    assert "R1" in out


def test_report_main_markdown_flag(tmp_path: Path, capsys):
    f1 = tmp_path / "a.jsonl"
    f1.write_text(json.dumps(_synth_row("R1", surface="v2", num_calls=1)) + "\n", encoding="utf-8")
    rc = report_mod.main(["--table", "--markdown", str(f1)])
    assert rc == 0
    out = capsys.readouterr().out
    assert out.startswith("| task |")
    assert "| R1 |" in out
    assert "---" in out


def test_report_main_no_dedupe_flag(tmp_path: Path, capsys):
    p = tmp_path / "d.jsonl"
    rows = [
        _synth_row("R1", surface="full", num_calls=1, success=True),
        {**_synth_row("R1", surface="full", num_calls=9, success=False)},
    ]
    # Both rows same (task_id, rep, surface) — latest-wins would keep one.
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    rc = report_mod.main(["--no-dedupe", str(p)])
    assert rc == 0
    # With no-dedupe, both rows enter summarize → n=2 for R1.
    # (dedupe default would leave n=1.)
    out = capsys.readouterr().out
    assert "R1" in out
    assert "2/2" in out or "1/2" in out  # one success of two


# ---------------------------------------------------------------------------
# Meta line (run.py)
# ---------------------------------------------------------------------------


def test_make_run_meta_row_and_write_once(tmp_path: Path):
    path = tmp_path / "out.jsonl"
    meta = make_run_meta_row(
        run_id="rid",
        surface="v2",
        battery="abcd1234ef00",
        model="sonnet",
        driver="claude-cli",
        git_sha="deadbeef",
        ts="2026-01-01T00:00:00+00:00",
    )
    assert meta["row_type"] == "meta"
    assert is_meta_row(meta)
    assert is_meta_or_non_task_row(meta)
    assert maybe_write_run_meta(path, meta) is True
    # Append a data row — a truncating rewrite on the second call would destroy it.
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"task_id": "R1", "rep": 0, "surface": "v2", "success": True}) + "\n")
    assert maybe_write_run_meta(path, meta) is False  # file non-empty
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["row_type"] == "meta"
    assert json.loads(lines[1])["task_id"] == "R1"


def test_resume_skips_meta_and_mismatch_checks_it(tmp_path: Path):
    p = tmp_path / "out.jsonl"
    p.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "row_type": "meta",
                        "surface": "v2",
                        "battery": "bbbbbbbbbbbb",
                        "model": "sonnet",
                        "driver": "claude-cli",
                    }
                ),
                json.dumps(
                    {
                        "task_id": "R1",
                        "rep": 0,
                        "surface": "v2",
                        "error": None,
                        "error_class": None,
                        "success": True,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    skip, n_skip, n_retry = load_resume_skip_keys(
        p, surface="v2", battery="bbbbbbbbbbbb", model="sonnet", driver="claude-cli"
    )
    assert skip == {("R1", 0)}
    assert n_skip == 1 and n_retry == 0

    with pytest.raises(SystemExit, match="battery"):
        load_resume_skip_keys(p, surface="v2", battery="aaaaaaaaaaaa", model="sonnet", driver="claude-cli")


# ---------------------------------------------------------------------------
# Listing token counts (fake tools, no network)
# ---------------------------------------------------------------------------


def test_count_tool_tokens_fake_list():
    class T:
        def __init__(self, name, desc, inp, out=None):
            self.name = name
            self.description = desc
            self.inputSchema = inp
            self.outputSchema = out

    tools = [
        T("alpha", "short", {"type": "object"}),
        T(
            "beta",
            "longer description here",
            {"type": "object", "properties": {"x": {"type": "string"}}},
            out={"type": "object"},
        ),
    ]
    # Fake encode: 1 token per character (deterministic, no tiktoken needed).
    encode = lambda s: list(s)  # noqa: E731
    rows, total_wire, total_model = count_tool_tokens(tools, encode=encode)
    assert len(rows) == 2
    assert total_wire == sum(r.wire_tokens for r in rows)
    assert total_model == sum(r.model_facing_tokens for r in rows)
    # Tool with outputSchema has wire > model-facing.
    beta = next(r for r in rows if r.name == "beta")
    assert beta.has_output_schema is True
    assert beta.wire_tokens > beta.model_facing_tokens
    alpha = next(r for r in rows if r.name == "alpha")
    assert alpha.has_output_schema is False
    assert alpha.wire_tokens == alpha.model_facing_tokens
    # Sorted by wire desc
    assert rows[0].wire_tokens >= rows[1].wire_tokens

    wire = tool_payload_wire(tools[1])
    assert "output_schema" in wire
    model = tool_payload_model_facing(tools[1])
    assert "output_schema" not in model


# ---------------------------------------------------------------------------
# Cleanup dry-run never deletes
# ---------------------------------------------------------------------------


def test_cleanup_dry_run_never_calls_delete(monkeypatch, capsys):
    projects = [
        SimpleNamespace(id="p1", name="EVAL deadbeef", identifier="EVDEAD"),
        SimpleNamespace(id="p2", name="EVAL cafe", identifier="EVCAFE"),
        SimpleNamespace(id="p3", name="Production", identifier="PROD"),
    ]
    delete_calls: list[Any] = []

    class FakeProjects:
        def list(self, workspace_slug=None, params=None):
            return SimpleNamespace(results=projects, next_page_results=False, next_cursor="100:0:0")

        def delete(self, **kwargs):
            delete_calls.append(kwargs)

    plane = MagicMock()
    plane.projects = FakeProjects()
    monkeypatch.setattr("evals.seed.make_plane_client", lambda: (plane, "test-ws"))

    rc = cleanup_mod.main([])  # dry-run
    assert rc == 0
    assert delete_calls == []
    out = capsys.readouterr().out
    assert "EVAL deadbeef" in out
    assert "dry-run" in out
    assert "Production" not in out  # prefix filter


def test_cleanup_yes_deletes(monkeypatch, capsys):
    projects = [SimpleNamespace(id="p1", name="EVAL x", identifier="EVX")]
    delete_calls: list[Any] = []

    class FakeProjects:
        def list(self, workspace_slug=None, params=None):
            return SimpleNamespace(results=projects, next_page_results=False, next_cursor="100:0:0")

        def delete(self, **kwargs):
            delete_calls.append(kwargs)

    plane = MagicMock()
    plane.projects = FakeProjects()
    monkeypatch.setattr("evals.seed.make_plane_client", lambda: (plane, "test-ws"))
    rc = cleanup_mod.main(["--yes"])
    assert rc == 0
    assert len(delete_calls) == 1
    assert delete_calls[0]["project_id"] == "p1"


def test_list_projects_with_prefix_filters():
    projects = [
        SimpleNamespace(id="1", name="EVAL a"),
        SimpleNamespace(id="2", name="Other"),
        SimpleNamespace(id="3", name="EVAL b"),
        SimpleNamespace(id="4", name="EVALUATION"),  # must NOT match "EVAL "
    ]
    calls: list[Any] = []

    class FakeProjects:
        def list(self, workspace_slug=None, params=None):
            calls.append({"workspace_slug": workspace_slug, "params": params})
            assert params is not None
            assert params.per_page == 100
            # SDK always populates next_cursor even on last page.
            return SimpleNamespace(
                results=projects,
                next_page_results=False,
                next_cursor="100:0:0",
            )

    plane = MagicMock()
    plane.projects = FakeProjects()
    got = cleanup_mod.list_projects_with_prefix(plane, "ws", "EVAL ")
    assert [p.id for p in got] == ["1", "3"]
    assert len(calls) == 1  # one page only — no infinite loop on next_cursor
    assert calls[0]["params"].cursor is None


def test_list_projects_two_page_pagination():
    page1 = [SimpleNamespace(id="1", name="EVAL one")]
    page2 = [SimpleNamespace(id="2", name="EVAL two")]
    seen_cursors: list[Any] = []

    class FakeProjects:
        def list(self, workspace_slug=None, params=None):
            seen_cursors.append(getattr(params, "cursor", None))
            if params.cursor is None:
                return SimpleNamespace(
                    results=page1,
                    next_page_results=True,
                    next_cursor="100:0:0",
                )
            assert params.cursor == "100:0:0"
            return SimpleNamespace(
                results=page2,
                next_page_results=False,
                next_cursor="200:0:0",
            )

    plane = MagicMock()
    plane.projects = FakeProjects()
    got = cleanup_mod.list_projects_with_prefix(plane, "ws", "EVAL ")
    assert [p.id for p in got] == ["1", "2"]
    assert seen_cursors == [None, "100:0:0"]
