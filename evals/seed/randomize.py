"""Per-run hidden-truth randomisation for evaluation fixtures."""

from __future__ import annotations

import hashlib
import random
from typing import Any


def random_truth_rng(context: dict[str, Any], namespace: str) -> random.Random:
    """Return a reproducible RNG keyed by the full private run id and a namespace.

    Only the run-id prefix appears in the project name shown to the agent. The full id is
    persisted on the result row, making a failed fixture reproducible without making its
    hidden choices derivable from the prompt.
    """
    run_id = str(context.get("run_id") or "")
    if not run_id:
        raise RuntimeError(f"random truth {namespace}: run_id missing from seed context")
    digest = hashlib.sha256(f"{run_id}:{namespace}".encode()).digest()
    return random.Random(int.from_bytes(digest, "big"))


def random_truth_token(context: dict[str, Any], namespace: str, *, length: int = 10) -> str:
    """Return a reproducible hidden token derived from the full private run id.

    Unlike the visible eight-character project prefix, this token depends on the full
    run id and a task namespace. It gives response evidence a realistically unique value
    without making failed fixture reproduction nondeterministic.
    """
    run_id = str(context.get("run_id") or "")
    if not run_id:
        raise RuntimeError(f"random truth {namespace}: run_id missing from seed context")
    if length < 8:
        raise ValueError("random truth tokens must contain at least 8 hex characters")
    return hashlib.sha256(f"{run_id}:{namespace}:sentinel".encode()).hexdigest()[:length]


def record_randomized_truth(context: dict[str, Any], key: str, value: Any) -> None:
    """Retain the chosen hidden value in seed context for diagnostics."""
    context.setdefault("randomized_truth", {})[key] = value


__all__ = ["random_truth_rng", "random_truth_token", "record_randomized_truth"]
