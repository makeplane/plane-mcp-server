"""Non-secret seed-shape metadata safe to persist beside evaluation results."""

from __future__ import annotations

from typing import Any


def record_seeded_entity(context: dict[str, Any], kind: str, object_id: Any) -> None:
    """Register that a fixture kind was seeded without retaining its target id."""
    value = str(object_id or "").strip()
    if not value:
        return
    context.setdefault("seeded_entity_kinds", set()).add(str(kind))


def capture_seed_artifacts(context: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Copy only fixture kinds and randomization namespaces, never ids or truth values."""
    entity_kinds = context.get("seeded_entity_kinds")
    randomized_namespaces = context.get("randomized_truth_namespaces")
    return (
        sorted({str(kind) for kind in entity_kinds}) if isinstance(entity_kinds, (list, set, tuple)) else [],
        (
            sorted({str(namespace) for namespace in randomized_namespaces})
            if isinstance(randomized_namespaces, (list, set, tuple))
            else []
        ),
    )


__all__ = ["capture_seed_artifacts", "record_seeded_entity"]
