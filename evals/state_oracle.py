"""Neutral project-state normalization shared by seeders and verifiers."""

from __future__ import annotations

from typing import Any


def state_name_group_pairs(rows: list[Any]) -> list[str]:
    """Return exact ``NAME | group: GROUP`` pairs, rejecting incomplete rows."""
    pairs: list[str] = []
    for state in rows:
        name = str(getattr(state, "name", None) or "").strip()
        raw_group = getattr(state, "group", None)
        group = str(getattr(raw_group, "value", raw_group) or "").strip()
        if not name or not group:
            raise RuntimeError(f"project state lacks name or group: {state!r}")
        pairs.append(f"{name} | group: {group}")
    return pairs


__all__ = ["state_name_group_pairs"]
