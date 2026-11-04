"""One reading of ``usage_total``, whatever driver produced it.

``usage_total.input_tokens`` does not mean the same thing everywhere, and reading it
naively misprices by roughly 4x on one side of any cross-driver comparison:

  inclusive  the field already contains the cached reads (OpenAI Responses). The
             uncached portion is the remainder.
  exclusive  the field is net of cache, and the cached reads sit beside it
             (Anthropic Messages, and every CLI vendor measured).

Driver family cannot decide this. The same api driver runs both providers, so an
Anthropic arm and an OpenAI arm arrive with identical ``source: "iterations"`` and
opposite meanings. What decides it, in order of authority:

  declared        the driver recorded ``cache_semantics`` outright. Always trusted.
  explicit_total  ``total_input_tokens_including_cache`` is present, which only an
                  exclusive shape carries, and it states the total directly.
  no_cache        nothing was cached, so both readings coincide and no guess is
                  needed -- this covers unknown models safely.
  model_family    inferred from the model name, the last resort for rows recorded
                  before ``cache_semantics`` existed.

When none of those apply the answer is ``None``. Refusing is deliberate: a guess here
is invisible in the output and wrong by a factor that reverses conclusions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

INCLUSIVE = "inclusive"
EXCLUSIVE = "exclusive"

#: Substrings that identify a provider's cache convention from a model name.
#: Only families whose semantics have been verified appear here; an unmatched name
#: with cache activity yields ``None`` rather than a default.
_MODEL_FAMILIES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("claude", "anthropic"), EXCLUSIVE),
    (("gpt", "o1-", "o3-", "o4-", "openai"), INCLUSIVE),
)


@dataclass(frozen=True)
class TokenAccounting:
    """Input split into what was paid for fresh, read from cache, and written to it.

    ``uncached_input + cached_input + cache_creation == total_input`` always holds, so
    a caller can price the three parts at three rates without knowing the source shape.
    """

    uncached_input: int
    cached_input: int
    cache_creation: int
    output: int
    total_input: int
    semantics: str
    semantics_source: str


def _int(usage: Mapping[str, Any], name: str) -> int:
    try:
        return max(0, int(usage.get(name) or 0))
    except (TypeError, ValueError):
        return 0


def _family_semantics(model: str | None) -> str | None:
    lowered = (model or "").strip().lower()
    if not lowered:
        return None
    for markers, semantics in _MODEL_FAMILIES:
        if any(marker in lowered for marker in markers):
            return semantics
    return None


#: The fields that carry an actual measurement, as opposed to describing one.
_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "total_input_tokens_including_cache",
)


def has_token_counts(usage_total: Mapping[str, Any] | None) -> bool:
    """True when this usage carries a real measurement rather than only metadata.

    The api driver writes a ``usage_total`` on every run whether or not any turn
    reported usage, so a dict holding a ``source`` and a ``cache_semantics`` and no
    counts is routine. Pricing that at $0.00 is the "unknown reads as free" failure
    this module exists to prevent, so an all-zero shape counts as no measurement.
    """
    if not usage_total:
        return False
    return any(_int(usage_total, name) for name in _TOKEN_FIELDS)


def cache_semantics_of(usage_total: Mapping[str, Any] | None, *, model: str | None = None) -> str | None:
    """Return how this row's ``input_tokens`` treats cache, or None if undecidable."""
    if not usage_total:
        return None
    declared = usage_total.get("cache_semantics")
    if declared in (INCLUSIVE, EXCLUSIVE):
        return str(declared)
    if usage_total.get("total_input_tokens_including_cache") is not None:
        return EXCLUSIVE
    if _int(usage_total, "cache_read_input_tokens") + _int(usage_total, "cache_creation_input_tokens") == 0:
        # Both readings agree when nothing was cached.
        return INCLUSIVE
    return _family_semantics(model)


def normalize_usage(
    usage_total: Mapping[str, Any] | None,
    *,
    model: str | None = None,
) -> TokenAccounting | None:
    """Normalise one row's usage, or None when it is absent or undecidable.

    A caller distinguishes the two None cases by the input: a falsy ``usage_total``
    means the driver recorded no usage at all (report it as unmeasured), while a
    populated one means the shape could not be read (report it as unpriced).
    """
    if not usage_total or not has_token_counts(usage_total):
        return None

    cached = _int(usage_total, "cache_read_input_tokens")
    creation = _int(usage_total, "cache_creation_input_tokens")
    output = _int(usage_total, "output_tokens")
    reported_input = _int(usage_total, "input_tokens")

    declared = usage_total.get("cache_semantics")
    explicit_total = usage_total.get("total_input_tokens_including_cache")

    if declared in (INCLUSIVE, EXCLUSIVE):
        semantics, source = str(declared), "declared"
    elif explicit_total is not None:
        semantics, source = EXCLUSIVE, "explicit_total"
    elif cached + creation == 0:
        semantics, source = INCLUSIVE, "no_cache"
    else:
        inferred = _family_semantics(model)
        if inferred is None:
            return None
        semantics, source = inferred, "model_family"

    if semantics == EXCLUSIVE:
        uncached = reported_input
        total = uncached + cached + creation
    else:
        total = reported_input
        uncached = total - cached - creation

    if explicit_total is not None and total != _int(usage_total, "total_input_tokens_including_cache"):
        # The vendor states the total as well as the parts. Both agreeing is what makes
        # this shape self-validating; disagreement means the shape changed underneath us,
        # and an unpriced row is a visible failure where a wrong price is not.
        #
        # Checked whenever a total is present, not only when it chose the semantics: a
        # declaration may interpret the parts, but it does not get to overrule
        # arithmetic that contradicts it.
        return None

    if uncached < 0:
        # An inclusive reading whose cache exceeds its total is not a reading at all.
        return None

    return TokenAccounting(
        uncached_input=uncached,
        cached_input=cached,
        cache_creation=creation,
        output=output,
        total_input=total,
        semantics=semantics,
        semantics_source=source,
    )


__all__ = [
    "EXCLUSIVE",
    "INCLUSIVE",
    "TokenAccounting",
    "cache_semantics_of",
    "normalize_usage",
]
