"""Regressions from the adversarial review of the pricing and economics change.

Eight findings, all reproduced before being fixed. The three most serious all had
the same shape: something unknown or unverified presenting as a confident number,
which is the exact failure this code was written to prevent.
"""

from __future__ import annotations

from evals.core.pricing import PRICED, UNMEASURED, UNPRICED, price_usage, resolve_model_id
from evals.core.token_accounting import EXCLUSIVE, INCLUSIVE, normalize_usage
from evals.report.economics import measure_economics

CLAUDE_USAGE = {
    "input_tokens": 971,
    "output_tokens": 734,
    "cache_read_input_tokens": 89488,
    "cache_creation_input_tokens": 30899,
    "total_input_tokens_including_cache": 121358,
    "total_cost_usd": 9.0,
    "modelUsage": {"claude-haiku-4-5-20251001": {}},
    "source": "modelUsage",
}


def row(**overrides):
    base = {
        "task_id": "R1",
        "success": True,
        "trace_integrity": True,
        "model": "haiku",
        "num_calls": 1,
        "calls": [],
        "usage_total": dict(CLAUDE_USAGE),
    }
    base.update(overrides)
    return base


def test_f1_usage_present_but_carrying_no_tokens_is_unmeasured():
    """The api driver always writes a usage_total, even when every turn returned None.

    A dict with a source and no counts is not a measurement, and pricing it at
    $0.00 is the precise "unknown reads as free" bug being designed against.
    """
    empty = {"source": "iterations", "cache_semantics": INCLUSIVE}
    assert normalize_usage(empty, model="gpt-5.6-luna") is None
    assert price_usage(empty, model="gpt-5.6-luna").outcome == UNMEASURED


def test_f2_a_charged_row_whose_verifier_crashed_still_counts():
    """The model ran and the tokens were billed; a later crash does not refund them."""
    measurement = measure_economics([row(error="verifier crashed")])
    assert measurement.priced_rows == 1
    assert measurement.cost_usd is not None


def test_f3_drift_compares_the_table_against_the_vendor_not_the_vendor_against_itself():
    """The staleness detector was summing billed_usd, which already prefers vendor cost.

    Reported drift was therefore always $0.000 -- the one check the design leaned on
    was structurally incapable of firing.
    """
    measurement = measure_economics([row()])
    assert measurement.vendor_cost_usd == 9.0
    assert measurement.computed_cost_usd is not None
    # The real table price for this row is cents, so drift against a $9 vendor
    # figure must be large and negative.
    assert measurement.computed_cost_usd < 1.0
    assert measurement.cost_drift_usd is not None and measurement.cost_drift_usd < -8.0


def test_f4_a_partial_input_total_says_it_is_partial():
    priced = row()
    blind = row(task_id="R2", usage_total={"input_tokens": 5, "cache_read_input_tokens": 4}, model="mystery")
    measurement = measure_economics([priced, blind])
    assert measurement.missing_input_rows == 1
    assert "1 row" in measurement.input_text


def test_f5_a_declaration_that_contradicts_an_explicit_total_is_refused():
    """Declared semantics may interpret, but they may not override arithmetic."""
    contradictory = {
        "input_tokens": 100,
        "cache_read_input_tokens": 90,
        "cache_creation_input_tokens": 0,
        "total_input_tokens_including_cache": 190,
        "cache_semantics": INCLUSIVE,
    }
    assert normalize_usage(contradictory) is None
    # Agreement still resolves normally.
    consistent = dict(contradictory, cache_semantics=EXCLUSIVE)
    accounting = normalize_usage(consistent)
    assert accounting is not None and accounting.total_input == 190


def test_f6_multi_model_usage_is_unpriced_rather_than_billed_at_one_rate():
    """Haiku and Opus tokens summed and priced at whichever alias the row carried."""
    usage = dict(CLAUDE_USAGE, modelUsage={"claude-haiku-4-5-20251001": {}, "claude-opus-5": {}})
    assert resolve_model_id(usage, model="claude-haiku-4-5") is None
    assert price_usage(usage, model="claude-haiku-4-5").outcome == UNPRICED


def test_f6b_an_authoritative_vendor_cost_survives_an_unpriced_table_lookup():
    """A real billed figure must not vanish because the table lacks the model."""
    usage = dict(CLAUDE_USAGE, modelUsage={"a": {}, "b": {}})
    measurement = measure_economics([row(usage_total=usage, model="mystery")])
    assert measurement.unpriced_rows == 1
    assert measurement.vendor_cost_usd == 9.0
    assert measurement.cost_usd == 9.0


def test_f7_a_small_real_cost_does_not_render_as_zero():
    tiny = {"input_tokens": 100, "output_tokens": 1, "cache_read_input_tokens": 0, "source": "iterations"}
    measurement = measure_economics([row(usage_total=tiny, model="gpt-5.6-luna")])
    assert measurement.cost_outcome == PRICED
    assert "$0.000 " not in measurement.cost_text
    assert measurement.cost_text.startswith("$") or measurement.cost_text.startswith("<$")


def test_f8_a_backend_that_declares_nothing_is_not_recorded_as_exclusive():
    """getattr(..., False) turned an undeclared backend into a confident claim."""
    from evals.drivers.api.driver import cache_semantics_for

    class Undeclared:
        pass

    class Inclusive:
        input_tokens_include_cache = True

    assert cache_semantics_for(Undeclared()) is None
    assert cache_semantics_for(Inclusive()) == INCLUSIVE
