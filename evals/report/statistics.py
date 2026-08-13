"""Statistical calculations for evaluation reports."""

from __future__ import annotations

import math


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial proportion."""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    centre = p + z2 / (2.0 * n)
    margin = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    lo = max(0.0, (centre - margin) / denom)
    hi = min(1.0, (centre + margin) / denom)
    return (lo, hi)


def sign_test_pvalue(deltas: list[float]) -> float | None:
    """Two-sided exact binomial sign test on non-zero paired deltas.

    H0: P(delta > 0) = 1/2. Zero deltas are dropped. Returns None when no
    non-zero pairs remain. Uses ``math.comb`` only (no scipy).
    """
    nonzero = [d for d in deltas if d != 0]
    n = len(nonzero)
    if n == 0:
        return None
    k = sum(1 for d in nonzero if d > 0)
    total = 2**n
    # Two-sided: 2 * min(left cdf, right survival), capped at 1.
    left = sum(math.comb(n, i) for i in range(0, k + 1)) / total
    right = sum(math.comb(n, i) for i in range(k, n + 1)) / total
    return min(1.0, 2.0 * min(left, right))


def median(values: list[float]) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    middle = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return float(sorted_values[middle])
    return (sorted_values[middle - 1] + sorted_values[middle]) / 2.0


def percentile(values: list[float], proportion: float) -> float | None:
    if not values:
        return None
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = (len(sorted_values) - 1) * proportion
    floor = math.floor(position)
    ceiling = math.ceil(position)
    if floor == ceiling:
        return float(sorted_values[int(position)])
    return float(sorted_values[floor] + (sorted_values[ceiling] - sorted_values[floor]) * (position - floor))


def iqr(values: list[float]) -> tuple[float | None, float | None, float | None]:
    return (percentile(values, 0.25), median(values), percentile(values, 0.75))
