"""Re-hunting an entity whose id is already in hand is a surface property.

The sharpest finding of the 2026-08-24 pair was workitem.search 111 vs 26 against
workitem.retrieve 4 vs 20: one arm carried resolved ids across turns and the other
re-searched for them. A surface with stickier identifiers would close that gap with
no change to either agent.
"""

from __future__ import annotations

from evals.report.lookup_reuse import measure_lookup_reuse

WORKITEM_ID = "0cf779b6-9e18-4209-9e09-2264b889be42"


def call(tool, action, **args):
    import json

    return {"tool": tool, "action": action, "args_json": json.dumps(args)}


def row(*calls, task_id="R1", rep=0):
    return {
        "task_id": task_id,
        "rep": rep,
        "success": True,
        "trace_integrity": True,
        "num_calls": len(calls),
        "calls": list(calls),
    }


def test_searching_for_something_already_in_hand_is_flagged():
    measurement = measure_lookup_reuse(
        [row(call("workitem", "retrieve", workitem_id=WORKITEM_ID), call("workitem", "search", query="thing"))]
    )
    assert measurement.total == 1
    assert measurement.by_resource["workitem"] == 1


def test_searching_before_any_id_is_known_is_not_flagged():
    """The first lookup is how the id is obtained; charging for it would be wrong."""
    measurement = measure_lookup_reuse(
        [row(call("workitem", "search", query="thing"), call("workitem", "retrieve", workitem_id=WORKITEM_ID))]
    )
    assert measurement.total == 0


def test_a_search_on_a_different_resource_is_not_flagged():
    measurement = measure_lookup_reuse(
        [row(call("workitem", "retrieve", workitem_id=WORKITEM_ID), call("cycle", "list", project_id="p1"))]
    )
    assert measurement.total == 0


def test_ids_do_not_carry_across_rows():
    """Each repetition is a fresh conversation; nothing is in hand at its start."""
    first = row(call("workitem", "retrieve", workitem_id=WORKITEM_ID), task_id="R1", rep=0)
    second = row(call("workitem", "search", query="thing"), task_id="R1", rep=1)
    assert measure_lookup_reuse([first, second]).total == 0


def test_a_run_without_recorded_arguments_reports_that_it_cannot_tell():
    """Zero must never be reported when the question could not be asked."""
    blind = {
        "task_id": "R1",
        "success": True,
        "trace_integrity": True,
        "num_calls": 2,
        "calls": [{"tool": "workitem", "action": "search"}, {"tool": "workitem", "action": "retrieve"}],
    }
    measurement = measure_lookup_reuse([blind])
    assert measurement.rows_without_args == 1
    assert measurement.measurable is False
    assert "not measured" in measurement.statement()


def test_repeat_searches_after_the_id_is_known_each_count():
    measurement = measure_lookup_reuse(
        [
            row(
                call("workitem", "retrieve", workitem_id=WORKITEM_ID),
                call("workitem", "search", query="a"),
                call("workitem", "search", query="b"),
            )
        ]
    )
    assert measurement.total == 2
