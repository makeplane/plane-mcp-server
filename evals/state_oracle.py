"""Neutral Plane response normalization shared by seeders and verifiers."""

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


def worklog_summary_item_ids(summary: Any) -> list[str]:
    """Return the distinct work item ids a project worklog summary reports, in order.

    Plane spells the field ``issue_id`` on some payload shapes and ``work_item_id`` on
    others. The seeder builds L1's oracle from this and the verifier compares against it,
    so they must read the payload identically.
    """
    raw = summary if isinstance(summary, list) else (getattr(summary, "results", None) or summary or [])
    item_ids: list[str] = []
    for row in list(raw or []):
        dump = row.model_dump() if hasattr(row, "model_dump") else (row if isinstance(row, dict) else {})
        value = getattr(row, "issue_id", None) or getattr(row, "work_item_id", None)
        if value is None and isinstance(dump, dict):
            value = dump.get("issue_id") or dump.get("work_item_id")
        item_id = str(value or "").strip()
        if item_id and item_id not in item_ids:
            item_ids.append(item_id)
    return item_ids


__all__ = ["state_name_group_pairs", "worklog_summary_item_ids"]
