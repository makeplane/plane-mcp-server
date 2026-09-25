"""What a run cost, or an honest statement that we do not know.

Cost is the decision metric for a tool-surface project, and for two days nothing
computed it -- so it was derived by hand and stated wrongly twice. The failure mode
this module is built against is not an absent number but a **zero**: an arm with no
usage recorded, priced at $0.00, reads as free rather than as unmeasured. So there
are three outcomes and they are kept distinct:

  priced      usage present, model known
  unpriced    usage present, but the model is not in the table, or its cache
              semantics could not be resolved
  unmeasured  the driver recorded no usage at all -- true of every antigravity row

Prices go stale, and a wrong price is worse than no price. ``PRICES_AS_OF`` dates the
table, but a date detects nothing on its own. The mechanism that actually detects
staleness is ``vendor_usd``: Claude Code reports ``total_cost_usd`` per run, so the
computed figure can be checked against the vendor's own on every run. That check
earned its keep immediately -- published 5-minute cache-write rates priced a measured
arm at $2.46 against a reported $3.18, and the 1-hour TTL multiplier reproduced the
reported figure exactly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from evals.core.token_accounting import has_token_counts, normalize_usage

#: The day the rates below were last checked against published pricing.
PRICES_AS_OF = "2026-08-24"

PRICED = "priced"
UNPRICED = "unpriced"
UNMEASURED = "unmeasured"

COST_OUTCOMES = (PRICED, UNPRICED, UNMEASURED)


@dataclass(frozen=True)
class ModelPrice:
    """US dollars per million tokens.

    ``cache_creation`` is the rate for tokens *written* to cache. Anthropic charges
    for writes on a multiple of the input rate that depends on the cache TTL -- 1.25x
    at five minutes, 2x at one hour -- and Claude Code uses the one-hour tier, which
    is what reproduces its reported cost. OpenAI does not bill cache writes at all
    and reports zero such tokens, so the value is unexercised there.
    """

    input: float
    cached_input: float
    output: float
    cache_creation: float | None = None

    def cache_creation_rate(self) -> float:
        return self.input if self.cache_creation is None else self.cache_creation


#: Keyed by model family prefix, longest match first. Dated ids such as
#: ``claude-haiku-4-5-20251001`` match their undated family.
PRICES: dict[str, ModelPrice] = {
    "gpt-5.6-luna": ModelPrice(input=0.20, cached_input=0.02, output=1.20),
    "claude-haiku-4-5": ModelPrice(input=1.00, cached_input=0.10, output=5.00, cache_creation=2.00),
    "claude-sonnet-5": ModelPrice(input=3.00, cached_input=0.30, output=15.00, cache_creation=6.00),
    "claude-opus-5": ModelPrice(input=5.00, cached_input=0.50, output=25.00, cache_creation=10.00),
}


@dataclass(frozen=True)
class RowCost:
    """The cost of one row, and how confident that figure is."""

    outcome: str
    usd: float | None
    model_id: str | None
    vendor_usd: float | None = None

    @property
    def billed_usd(self) -> float | None:
        """The vendor's own figure where it exists, else ours.

        A provider that reports what it charged is more authoritative than any table.
        """
        return self.vendor_usd if self.vendor_usd is not None else self.usd


def resolve_model_id(usage_total: Mapping[str, Any] | None, *, model: str | None) -> str | None:
    """Return the most specific model identifier this row carries.

    ``row.model`` is what the driver was asked for, which under a CLI is a tier alias
    -- claude-cli records ``"haiku"``. The real identifier is inside ``modelUsage``,
    so that wins when a single model produced the run.
    """
    if usage_total:
        model_usage = usage_total.get("modelUsage")
        if isinstance(model_usage, Mapping) and model_usage:
            if len(model_usage) > 1:
                # Several models produced this run and the counters are already summed,
                # so no single rate is correct for them. Falling back to the row alias
                # would price Opus tokens at the Haiku rate whenever that alias happened
                # to be priceable.
                return None
            only = next(iter(model_usage))
            if isinstance(only, str) and only:
                return only
    return model or None


def lookup_price(model_id: str | None) -> ModelPrice | None:
    if not model_id:
        return None
    lowered = model_id.strip().lower()
    for prefix in sorted(PRICES, key=len, reverse=True):
        if lowered.startswith(prefix):
            return PRICES[prefix]
    return None


def price_usage(usage_total: Mapping[str, Any] | None, *, model: str | None = None) -> RowCost:
    """Price one row's ``usage_total``."""
    vendor = usage_total.get("total_cost_usd") if usage_total else None
    vendor_usd = float(vendor) if isinstance(vendor, (int, float)) else None

    if not usage_total or not has_token_counts(usage_total):
        # A usage dict with no counts in it is metadata, not a measurement -- but a vendor
        # that stated what it charged still told us something, and discarding that would
        # throw away the most authoritative figure available.
        return RowCost(outcome=UNMEASURED, usd=None, model_id=model or None, vendor_usd=vendor_usd)
    model_id = resolve_model_id(usage_total, model=model)

    accounting = normalize_usage(usage_total, model=model_id)
    price = lookup_price(model_id)
    if accounting is None or price is None:
        return RowCost(outcome=UNPRICED, usd=None, model_id=model_id, vendor_usd=vendor_usd)

    usd = (
        accounting.uncached_input * price.input
        + accounting.cached_input * price.cached_input
        + accounting.cache_creation * price.cache_creation_rate()
        + accounting.output * price.output
    ) / 1e6
    return RowCost(outcome=PRICED, usd=round(usd, 10), model_id=model_id, vendor_usd=vendor_usd)


__all__ = [
    "COST_OUTCOMES",
    "PRICED",
    "PRICES",
    "PRICES_AS_OF",
    "UNMEASURED",
    "UNPRICED",
    "ModelPrice",
    "RowCost",
    "lookup_price",
    "price_usage",
    "resolve_model_id",
]
