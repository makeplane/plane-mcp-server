"""Both cache semantics must normalise to the same meaning."""

from __future__ import annotations

import pytest

from evals.core.token_accounting import (
    EXCLUSIVE,
    INCLUSIVE,
    TokenAccounting,
    cache_semantics_of,
    normalize_usage,
)

# Shapes taken verbatim from real arms (2026-08-24 battery eaf35e8019aa).
OPENAI_ROW = {
    "input_tokens": 97813,
    "output_tokens": 1121,
    "cache_read_input_tokens": 83460,
    "cache_creation_input_tokens": 0,
    "source": "iterations",
}
CODEX_CLI_ROW = {
    "input_tokens": 249983,
    "output_tokens": 682,
    "cache_read_input_tokens": 224768,
    "cache_creation_input_tokens": 0,
    "total_input_tokens_including_cache": 474751,
    "source": "codex_token_count",
}
CLAUDE_CLI_ROW = {
    "input_tokens": 971,
    "output_tokens": 734,
    "cache_read_input_tokens": 89488,
    "cache_creation_input_tokens": 30899,
    "total_input_tokens_including_cache": 121358,
    "total_cost_usd": 0.0753878,
    "source": "modelUsage",
}


def test_explicit_total_is_treated_as_exclusive():
    """A recorded total means input_tokens excludes cache; the total is authoritative."""
    accounting = normalize_usage(CODEX_CLI_ROW)
    assert accounting == TokenAccounting(
        uncached_input=249983,
        cached_input=224768,
        cache_creation=0,
        output=682,
        total_input=474751,
        semantics=EXCLUSIVE,
        semantics_source="explicit_total",
    )


def test_openai_input_tokens_are_inclusive_of_cache():
    """Responses counts cached reads inside input_tokens; uncached is the remainder."""
    accounting = normalize_usage(OPENAI_ROW, model="gpt-5.6-luna")
    assert accounting is not None
    assert accounting.semantics == INCLUSIVE
    assert accounting.total_input == 97813
    assert accounting.uncached_input == 97813 - 83460


def test_anthropic_api_input_tokens_are_exclusive_of_cache():
    """The Messages API reports input_tokens net of both cache fields.

    The same api driver produces this and the OpenAI shape above, so driver family
    cannot decide the semantics -- this is the case the first plan draft got wrong.
    """
    row = {
        "input_tokens": 1200,
        "output_tokens": 300,
        "cache_read_input_tokens": 50000,
        "cache_creation_input_tokens": 2000,
        "source": "iterations",
    }
    accounting = normalize_usage(row, model="claude-haiku-4-5")
    assert accounting is not None
    assert accounting.semantics == EXCLUSIVE
    assert accounting.uncached_input == 1200
    assert accounting.total_input == 1200 + 50000 + 2000


def test_declared_semantics_beat_inference():
    """A driver that records what it means is trusted over any model-name guess."""
    row = dict(OPENAI_ROW, cache_semantics=EXCLUSIVE)
    accounting = normalize_usage(row, model="gpt-5.6-luna")
    assert accounting is not None
    assert accounting.semantics == EXCLUSIVE
    assert accounting.semantics_source == "declared"
    assert accounting.total_input == 97813 + 83460


def test_uncached_row_needs_no_semantics_at_all():
    """With no cache activity the two readings coincide, so an unknown model is fine."""
    row = {"input_tokens": 500, "output_tokens": 10, "cache_read_input_tokens": 0}
    accounting = normalize_usage(row, model="some-model-nobody-has-heard-of")
    assert accounting is not None
    assert accounting.semantics_source == "no_cache"
    assert accounting.total_input == 500
    assert accounting.uncached_input == 500


def test_cached_row_with_unknown_model_refuses_to_guess():
    """Guessing here misprices by ~4x, which is how two conclusions reversed."""
    row = {"input_tokens": 500, "output_tokens": 10, "cache_read_input_tokens": 400}
    assert normalize_usage(row, model="some-model-nobody-has-heard-of") is None
    assert cache_semantics_of(row, model=None) is None


def test_identity_violation_is_not_silently_priced():
    """input + cache_read + cache_creation == total holds 70/70 on both CLI vendors.

    If a vendor changes shape the sum stops matching, and reporting the row as
    unpriced is the loud outcome; using either number would misprice invisibly.
    """
    broken = dict(CODEX_CLI_ROW, total_input_tokens_including_cache=999999)
    assert normalize_usage(broken) is None


def test_absent_usage_is_none():
    assert normalize_usage(None) is None
    assert normalize_usage({}) is None


@pytest.mark.parametrize("row", [OPENAI_ROW, CODEX_CLI_ROW, CLAUDE_CLI_ROW])
def test_parts_never_exceed_the_total(row):
    """Whatever the shape, the normalised parts must reconstruct the total input."""
    model = "gpt-5.6-luna" if row is OPENAI_ROW else None
    accounting = normalize_usage(row, model=model)
    assert accounting is not None
    assert accounting.uncached_input + accounting.cached_input + accounting.cache_creation == accounting.total_input
    assert accounting.uncached_input >= 0
