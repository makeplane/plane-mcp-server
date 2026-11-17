"""Cost, input volume, result volume and latency must reach the A/B view.

These are all recorded already. Result tokens even reach ``--table``. They were
absent from the two-file comparison, which is the view a two-arm question is asked
in -- so the arm that made 32% fewer calls while burning 3.1x the input tokens read
as the efficient one for two days.
"""

from __future__ import annotations

from evals.core.pricing import PRICED, UNMEASURED
from evals.report import summarize
from evals.report.compare import ab_compare, print_ab_report

OPENAI_USAGE = {
    "input_tokens": 97813,
    "output_tokens": 1121,
    "cache_read_input_tokens": 83460,
    "cache_creation_input_tokens": 0,
    "source": "iterations",
    "cache_semantics": "inclusive",
}
CODEX_USAGE = {
    "input_tokens": 249983,
    "output_tokens": 682,
    "cache_read_input_tokens": 224768,
    "cache_creation_input_tokens": 0,
    "total_input_tokens_including_cache": 474751,
    "source": "codex_token_count",
}


def row(task_id, *, usage, model, calls=2, wall=10.0, latency=500.0, result_tokens=900):
    return {
        "task_id": task_id,
        "success": True,
        "trace_integrity": True,
        "model": model,
        "usage_total": usage,
        "wall_time_s": wall,
        "num_calls": calls,
        "calls": [{"tool": "workitem", "duration_ms": latency, "result_tokens": result_tokens} for _ in range(calls)],
    }


def test_summary_carries_arm_cost_and_normalised_input():
    economics = summarize([row("R1", usage=OPENAI_USAGE, model="gpt-5.6-luna")]).economics
    assert economics.cost_outcome == PRICED
    assert economics.cost_usd is not None and economics.cost_usd > 0
    assert economics.total_input_tokens == 97813
    assert economics.total_wall_time_s == 10.0
    assert economics.med_call_latency_ms == 500.0


def test_an_arm_with_no_usage_reports_unmeasured_not_zero():
    """antigravity records usage on no row at all; $0.00 would read as free."""
    economics = summarize([row("R1", usage=None, model="gemini-3.6-flash-low")]).economics
    assert economics.cost_outcome == UNMEASURED
    assert economics.cost_usd is None
    assert economics.unmeasured_rows == 1
    assert economics.cost_text == UNMEASURED


def test_ab_block_reports_the_metrics_that_invert_the_call_verdict(capsys):
    """B makes fewer calls and costs far more -- both facts must be visible together."""
    rows_a = [row("R1", usage=OPENAI_USAGE, model="gpt-5.6-luna", calls=7, result_tokens=900)]
    rows_b = [row("R1", usage=CODEX_USAGE, model="gpt-5.6-luna", calls=4, result_tokens=2500)]
    comparison = ab_compare(rows_a, rows_b)

    assert comparison["total_input_a"] == 97813
    assert comparison["total_input_b"] == 474751
    assert comparison["cost_a"] is not None and comparison["cost_b"] is not None
    assert comparison["cost_b"] > comparison["cost_a"]

    print_ab_report(comparison, "A.jsonl", "B.jsonl")
    out = capsys.readouterr().out
    for expected in ("input tokens", "cost", "result tokens", "wall time", "call latency"):
        assert expected in out, f"{expected!r} missing from the A/B block"
    # The call delta says B is better; the cost delta must be right there beside it.
    assert "median call delta" in out


def test_unmeasured_arm_prints_a_word_not_a_zero(capsys):
    rows_a = [row("R1", usage=OPENAI_USAGE, model="gpt-5.6-luna")]
    rows_b = [row("R1", usage=None, model="gemini-3.6-flash-low")]
    print_ab_report(ab_compare(rows_a, rows_b), "A.jsonl", "B.jsonl")
    out = capsys.readouterr().out
    b_lines = [line for line in out.splitlines() if line.startswith("  B economics:")]
    assert b_lines, "arm B has no economics line"
    assert "cost=unmeasured" in b_lines[0]
    assert "input tokens=unmeasured" in b_lines[0]
    # The unknown must never be rendered as a figure of any size, zero included.
    assert "$" not in b_lines[0]
