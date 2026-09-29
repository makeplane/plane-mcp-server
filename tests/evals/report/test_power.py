"""At low rep counts the per-task verdicts support nothing; the report must say so.

At 2 reps every mixed task is `1/2 UNSTABLE` with a 95% interval of [0.09, 0.91],
which is compatible with almost any true rate -- while the paired aggregate over 35
tasks is well powered. That asymmetry is easy to misread, and was misread.
"""

from __future__ import annotations

from evals.report import summarize
from evals.report.power import UNDERPOWERED_REPS, power_statement


def rows_at(reps: int, tasks: int = 3) -> list[dict]:
    return [
        {
            "task_id": f"T{task}",
            "rep": rep,
            "success": rep % 2 == 0,
            "trace_integrity": True,
            "num_calls": 1,
            "calls": [],
        }
        for task in range(tasks)
        for rep in range(reps)
    ]


def test_the_guardrail_appears_at_two_reps():
    statement = power_statement(summarize(rows_at(2)))
    assert statement is not None
    assert "per-task" in statement
    assert "aggregate" in statement


def test_the_guardrail_is_absent_at_five_reps():
    assert power_statement(summarize(rows_at(UNDERPOWERED_REPS))) is None


def test_one_deep_task_does_not_silence_the_caveat_for_the_shallow_ones():
    """Keying off the best-covered task left a mixed run's shallow tasks uncaveated.

    That is the opposite of the intended failure, so the line is scoped to the tasks
    that are actually shallow and names how many they are.
    """
    rows = rows_at(2, tasks=2) + [
        {"task_id": "T9", "rep": rep, "success": True, "trace_integrity": True, "num_calls": 1, "calls": []}
        for rep in range(UNDERPOWERED_REPS)
    ]
    statement = power_statement(summarize(rows))
    assert statement is not None
    assert "2 of 3" in statement


def test_the_guardrail_names_the_rep_count_it_saw():
    assert "fewest 2" in (power_statement(summarize(rows_at(2))) or "")


def test_no_evaluated_rows_produces_no_claim():
    assert power_statement(summarize([])) is None


def test_it_reaches_the_printed_report(capsys):
    import evals.report as report_mod

    report_mod.print_table(summarize(rows_at(2)), "Summary: low-power.jsonl")
    assert "per-task" in capsys.readouterr().out
