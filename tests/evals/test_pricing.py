"""Pricing must be right, refuse, or say it has nothing -- never quietly zero."""

from __future__ import annotations

from evals.core.pricing import (
    PRICED,
    PRICES_AS_OF,
    UNMEASURED,
    UNPRICED,
    price_usage,
    resolve_model_id,
)

OPENAI_ROW = {
    "input_tokens": 97813,
    "output_tokens": 1121,
    "cache_read_input_tokens": 83460,
    "cache_creation_input_tokens": 0,
    "source": "iterations",
}
CLAUDE_CLI_ROW = {
    "input_tokens": 971,
    "output_tokens": 734,
    "cache_read_input_tokens": 89488,
    "cache_creation_input_tokens": 30899,
    "total_input_tokens_including_cache": 121358,
    "total_cost_usd": 0.0753878,
    "modelUsage": {"claude-haiku-4-5-20251001": {}},
    "source": "modelUsage",
}


def test_prices_carry_an_as_of_date():
    assert PRICES_AS_OF


def test_a_known_model_is_priced():
    cost = price_usage(OPENAI_ROW, model="gpt-5.6-luna")
    assert cost.outcome == PRICED
    assert cost.usd is not None
    # 1.27M fresh at $0.20, 83460 cached at $0.02, 1121 out at $1.20.
    assert cost.usd == round((14353 * 0.20 + 83460 * 0.02 + 1121 * 1.20) / 1e6, 10)


def test_an_unknown_model_is_unpriced_not_free():
    """A silent zero reads as 'free'. The whole point is that it must read as 'unknown'."""
    cost = price_usage(OPENAI_ROW, model="some-model-nobody-has-heard-of")
    assert cost.outcome == UNPRICED
    assert cost.usd is None


def test_a_row_with_no_usage_at_all_is_unmeasured():
    """antigravity records usage_total on 0 of 70 rows; that is not the same as unknown."""
    for empty in (None, {}):
        cost = price_usage(empty, model="gemini-3.6-flash-low")
        assert cost.outcome == UNMEASURED
        assert cost.usd is None


def test_unmeasured_and_unpriced_are_distinguishable():
    assert UNMEASURED != UNPRICED
    assert price_usage(None, model="gpt-5.6-luna").outcome != price_usage(OPENAI_ROW, model="nope").outcome


def test_model_id_resolves_through_model_usage_before_the_row_alias():
    """claude-cli records model 'haiku' -- an alias, unpriceable as a key."""
    assert resolve_model_id(CLAUDE_CLI_ROW, model="haiku") == "claude-haiku-4-5-20251001"
    assert resolve_model_id(OPENAI_ROW, model="gpt-5.6-luna") == "gpt-5.6-luna"
    assert resolve_model_id(None, model="haiku") == "haiku"


def test_dated_model_ids_match_their_family():
    assert price_usage(CLAUDE_CLI_ROW, model="haiku").outcome == PRICED


def test_computed_cost_agrees_with_the_vendor_reported_cost():
    """The only mechanism that detects a stale price table.

    Published 5-minute cache-write rates give $2.46 against this arm's reported
    $3.18; the 1-hour TTL multiplier reproduces it. Without this check the table
    would have shipped 23% low and looked fine.
    """
    cost = price_usage(CLAUDE_CLI_ROW, model="haiku")
    assert cost.vendor_usd == 0.0753878
    assert cost.usd is not None
    assert abs(cost.usd - cost.vendor_usd) / cost.vendor_usd < 0.01


def test_vendor_cost_is_preferred_when_present():
    cost = price_usage(CLAUDE_CLI_ROW, model="haiku")
    assert cost.billed_usd == cost.vendor_usd
    # ...and falls back to the computed figure when the vendor reports nothing.
    assert price_usage(OPENAI_ROW, model="gpt-5.6-luna").billed_usd == price_usage(OPENAI_ROW, model="gpt-5.6-luna").usd


def test_undecidable_cache_semantics_is_unpriced():
    """A cached row whose model family is unknown cannot be normalised, so it cannot be priced."""
    row = {"input_tokens": 500, "output_tokens": 10, "cache_read_input_tokens": 400}
    assert price_usage(row, model="mystery-model").outcome == UNPRICED
