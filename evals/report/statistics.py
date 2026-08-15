"""Statistical calculations for evaluation reports."""

from __future__ import annotations

import itertools
import math
import random

EXACT_PERMUTATION_LIMIT = 20
MONTE_CARLO_PERMUTATIONS = 100_000
BOOTSTRAP_RESAMPLES = 20_000


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


def paired_permutation_pvalue(
    deltas: list[float],
    *,
    exact_limit: int = EXACT_PERMUTATION_LIMIT,
    permutations: int = MONTE_CARLO_PERMUTATIONS,
    seed: int = 0,
) -> float | None:
    """Two-sided paired sign-flip permutation test on the mean delta.

    The null assumes each pair's A/B labels are exchangeable and pairs are
    independent. The statistic is the absolute mean of *all* paired deltas, so
    zero-delta ties remain in the sample and its denominator. A zero contributes
    the same value under either sign; enumerating its duplicate sign assignments
    once is exactly equivalent to enumerating both.

    Tests with at most ``exact_limit`` non-zero contributions enumerate the exact
    randomization distribution. Larger tests use a deterministic Monte Carlo
    sample and the standard plus-one correction. ``None`` means there were no
    pairs; an all-tie sample returns 1.0.
    """
    if not deltas:
        return None
    pair_count = len(deltas)
    contributions = [float(delta) for delta in deltas if delta != 0]
    observed = abs(sum(deltas) / pair_count)
    tolerance = 1e-12
    if not contributions:
        return 1.0

    def is_extreme(signs: tuple[int, ...] | list[int]) -> bool:
        permuted = abs(sum(sign * delta for sign, delta in zip(signs, contributions, strict=True)) / pair_count)
        return permuted + tolerance >= observed

    if len(contributions) <= exact_limit:
        assignments = itertools.product((-1, 1), repeat=len(contributions))
        extreme = sum(1 for signs in assignments if is_extreme(signs))
        return extreme / (2 ** len(contributions))

    if permutations <= 0:
        raise ValueError("permutations must be positive")
    generator = random.Random(seed)
    extreme = 0
    for _ in range(permutations):
        signs = [generator.choice((-1, 1)) for _ in contributions]
        extreme += is_extreme(signs)
    return (extreme + 1) / (permutations + 1)


def paired_bootstrap_mean_ci(
    deltas: list[float],
    *,
    confidence: float = 0.95,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = 0,
) -> tuple[float | None, float | None]:
    """Percentile paired-bootstrap CI for the mean per-pair delta.

    Resampling whole paired deltas preserves the A/B pairing. The interval treats
    tasks as independent sampling units drawn from a task population and assumes
    the two labels measured comparable task instances. It captures task-sampling
    uncertainty, not dependence between tasks or systematic run/environment drift.

    Small samples are intentionally not narrowed by row-level repetitions: for up
    to five pairs the complete ``n**n`` bootstrap distribution is enumerated.
    Larger samples use a deterministic Monte Carlo bootstrap.
    """
    if not deltas:
        return (None, None)
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between 0 and 1")
    if resamples <= 0:
        raise ValueError("resamples must be positive")

    values = [float(delta) for delta in deltas]
    sample_size = len(values)
    bootstrap_means: list[float] = []
    if sample_size**sample_size <= resamples:
        for sample in itertools.product(values, repeat=sample_size):
            bootstrap_means.append(sum(sample) / sample_size)
    else:
        generator = random.Random(seed)
        for _ in range(resamples):
            bootstrap_means.append(sum(generator.choice(values) for _ in range(sample_size)) / sample_size)

    tail = (1.0 - confidence) / 2.0
    return (percentile(bootstrap_means, tail), percentile(bootstrap_means, 1.0 - tail))


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
