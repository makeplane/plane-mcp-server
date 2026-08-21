"""Per-run hidden-truth randomisation for evaluation fixtures."""

from __future__ import annotations

import hashlib
import random
from typing import Any


def random_truth_rng(context: dict[str, Any], namespace: str) -> random.Random:
    """Return a reproducible RNG keyed by the per-repetition fixture seed and a namespace.

    The caller supplies the repetition's private fixture seed as ``context["run_id"]``.
    Its full value is persisted explicitly as ``TaskResult.fixture_seed_id``, making a
    failed fixture reproducible without making later repetitions' independent choices
    derivable from this one.
    """
    run_id = str(context.get("run_id") or "")
    if not run_id:
        raise RuntimeError(f"random truth {namespace}: run_id missing from seed context")
    digest = hashlib.sha256(f"{run_id}:{namespace}".encode()).digest()
    return random.Random(int.from_bytes(digest, "big"))


def random_truth_token(context: dict[str, Any], namespace: str, *, length: int = 10) -> str:
    """Return a reproducible hidden token derived from the per-repetition fixture seed.

    Unlike the visible eight-character project prefix, this token depends on the full
    fixture seed id and a task namespace. It gives response evidence a realistically unique
    value without making failed fixture reproduction nondeterministic.
    """
    run_id = str(context.get("run_id") or "")
    if not run_id:
        raise RuntimeError(f"random truth {namespace}: run_id missing from seed context")
    if length < 8:
        raise ValueError("random truth tokens must contain at least 8 hex characters")
    return hashlib.sha256(f"{run_id}:{namespace}:sentinel".encode()).hexdigest()[:length]


def record_randomized_truth(context: dict[str, Any], key: str, value: Any) -> None:
    """Retain hidden truth in memory and separately register its persistable namespace."""
    context.setdefault("randomized_truth", {})[key] = value
    context.setdefault("randomized_truth_namespaces", set()).add(str(key))


__all__ = ["random_truth_rng", "random_truth_token", "record_randomized_truth"]
