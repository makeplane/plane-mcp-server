"""Regressions from the second adversarial review.

Ten findings. The two that mattered most were a metric measuring something other
than what it claimed, and a field documented as "ids and short strings" that had no
size bound at all.
"""

from __future__ import annotations

import json

from evals.core.failure_kind import ABANDONED, MISSING_WRITE, PARTIAL_WRITE, UNPROVEN, WRONG_VALUE, classify_failure
from evals.core.pricing import price_usage
from evals.core.results import AgentRun, Usage, agent_run_to_task_result
from evals.report import summarize
from evals.report.economics import measure_economics
from evals.report.lookup_reuse import measure_lookup_reuse
from evals.report.power import power_statement


def call(tool, action, **args):
    return {"tool": tool, "action": action, "args_json": json.dumps(args)}


def task_row(*calls, **overrides):
    row = {
        "task_id": "R1",
        "success": True,
        "trace_integrity": True,
        "num_calls": len(calls),
        "calls": list(calls),
    }
    row.update(overrides)
    return row


# --- F1: the lookup rule must establish reuse of the same entity ------------------


def test_f1_pagination_is_not_a_redundant_lookup():
    assert (
        measure_lookup_reuse(
            [
                task_row(
                    call("workitem", "list", project_id="p", cursor="a"),
                    call("workitem", "list", project_id="p", cursor="b"),
                )
            ]
        ).total
        == 0
    )


def test_f1_a_list_after_a_create_is_not_a_redundant_lookup():
    assert (
        measure_lookup_reuse(
            [task_row(call("workitem", "create", project_id="p", name="x"), call("workitem", "list", project_id="p"))]
        ).total
        == 0
    )


def test_f1_a_scope_id_does_not_put_the_entity_in_hand():
    """project_id says which project to look in, not which work item is known."""
    assert (
        measure_lookup_reuse(
            [task_row(call("workitem", "retrieve", project_id="p"), call("workitem", "search", query="z"))]
        ).total
        == 0
    )


def test_f1_the_entitys_own_id_still_counts():
    assert (
        measure_lookup_reuse(
            [task_row(call("workitem", "retrieve", workitem_id="wi-1"), call("workitem", "search", query="z"))]
        ).total
        == 1
    )


# --- F2: recorded arguments need a size bound ------------------------------------


def test_f2_a_huge_argument_value_is_bounded():
    run = AgentRun(
        calls=[{"tool": "workitem", "args": {"action": "update", "description_html": "x" * 1_000_000}}],
        final_text="",
        usage=Usage(),
        stopped_reason="end_turn",
        call_source="api",
    )
    args_json = agent_run_to_task_result(run).calls[0].args_json
    assert args_json is not None
    assert len(args_json) < 5_000, f"args_json was {len(args_json)} bytes"
    parsed = json.loads(args_json)
    # Structure and the short discriminating values survive; only the bulk is cut.
    assert parsed["action"] == "update"
    assert parsed["description_html"].endswith("…[truncated]")


def test_f2_short_arguments_are_untouched():
    args = {"action": "retrieve", "workitem_id": "wi-42"}
    run = AgentRun(
        calls=[{"tool": "workitem", "args": args}],
        final_text="",
        usage=Usage(),
        stopped_reason="end_turn",
        call_source="api",
    )
    assert json.loads(agent_run_to_task_result(run).calls[0].args_json) == args


# --- F3: charged rows the runner later marked infrastructure ---------------------


def test_f3_an_infra_terminated_row_that_burned_tokens_is_still_charged():
    usage = {"input_tokens": 500, "output_tokens": 10, "cache_read_input_tokens": 0, "source": "iterations"}
    rows = [task_row(model="gpt-5.6-luna", usage_total=usage, error="infra_cli timeout", error_class="infra_cli")]
    measurement = measure_economics(rows)
    assert measurement.priced_rows == 1
    assert measurement.cost_usd is not None


def test_f3_a_vendor_only_cost_is_not_thrown_away():
    """Tokens unmeasured, but the vendor still said what it charged."""
    cost = price_usage({"source": "modelUsage", "total_cost_usd": 1.25}, model="haiku")
    assert cost.vendor_usd == 1.25
    assert cost.billed_usd == 1.25


# --- F5/F6: classifier precedence -------------------------------------------------


def test_f5_a_wrong_answer_quoting_the_true_marker_is_not_unproven():
    note = "answer_correct=false (values=[\"answer_correct=true\"]; want ['x'])"
    assert classify_failure(note) == WRONG_VALUE


def test_f6_a_capped_run_that_also_wrote_the_wrong_value_is_not_a_non_defect():
    """A cap can coexist with a proven wrong write; abandoned would excuse it."""
    assert classify_failure("answer_correct=false (values=['a']; want ['b'])", hit_max_iterations=True) == WRONG_VALUE


def test_f6_a_capped_run_with_an_unfinished_state_is_still_abandoned():
    assert classify_failure("estimate points missing fib subset", hit_max_iterations=True) == ABANDONED


def test_f6_linked_counts_as_a_present_marker():
    assert classify_failure("request 'x' missing; R1 item ABC-1 linked") == PARTIAL_WRITE


def test_f6_absence_phrasings_from_real_verifiers_are_recognised():
    for note in ("no comments on target item", "no 120-minute work log", "2 module items not archived"):
        assert classify_failure(note) == MISSING_WRITE, note


def test_unproven_still_works():
    assert classify_failure("answer_correct=true (...); provenance=missing (0 evidence-bearing)") == UNPROVEN


# --- F7: the power line must not be silenced by one well-covered task -------------


def test_f7_underpowered_tasks_are_still_named_when_another_task_is_deep():
    rows = [task_row(task_id="T1", rep=rep, success=True, calls=[]) for rep in range(2)] + [
        task_row(task_id="T2", rep=rep, success=True, calls=[]) for rep in range(5)
    ]
    statement = power_statement(summarize(rows))
    assert statement is not None
    assert "1 of 2" in statement


def test_f7_no_line_when_every_task_is_deep_enough():
    rows = [task_row(task_id="T1", rep=rep, success=True, calls=[]) for rep in range(5)]
    assert power_statement(summarize(rows)) is None


# --- F8/F10: the drift cohort and its formatting ---------------------------------


def test_f8_drift_uses_only_rows_carrying_both_figures():
    both = task_row(
        model="haiku",
        usage_total={
            "input_tokens": 971,
            "output_tokens": 734,
            "cache_read_input_tokens": 89488,
            "cache_creation_input_tokens": 30899,
            "total_input_tokens_including_cache": 121358,
            "total_cost_usd": 0.0753878,
            "modelUsage": {"claude-haiku-4-5-20251001": {}},
        },
    )
    computed_only = task_row(
        task_id="R2",
        model="gpt-5.6-luna",
        usage_total={"input_tokens": 900_000, "output_tokens": 5000, "source": "iterations"},
    )
    measurement = measure_economics([both, computed_only])
    assert measurement.drift_rows == 1
    # Without cohort alignment the second row's cost would inflate the drift.
    assert abs(measurement.cost_drift_usd) < 0.01


def test_f10_a_sub_cent_drift_line_does_not_read_as_all_zeroes():
    tiny = task_row(
        model="haiku",
        usage_total={
            "input_tokens": 100,
            "output_tokens": 1,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "total_input_tokens_including_cache": 100,
            "total_cost_usd": 0.00032,
            "modelUsage": {"claude-haiku-4-5-20251001": {}},
        },
    )
    from evals.report.economics import economics_statement

    line = economics_statement(measure_economics([tiny]))
    assert "$0.000;" not in line and "$0.000 " not in line
