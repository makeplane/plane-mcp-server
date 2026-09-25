"""The task facts a report needs, persisted with the run instead of read from the checkout.

Reports derived three things from the live catalog: whether a task mutates Plane, its prompt
text, and the fixtures it needs (which decides whether a plan-gated skip was expected). All
three are properties of *the run that was executed*, so reading them from the working tree
meant a result file could be reinterpreted after the catalog changed — the one thing the
battery fingerprint and identity validation exist to prevent.

The run writes them into its meta header. A file that predates the header has no metadata,
and the reader says so rather than quietly substituting today's catalog.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

TaskMetadata = Mapping[str, Mapping[str, Any]]

METADATA_FIELD = "task_metadata"
MUTATION_TAGS = frozenset({"write"})


def build_task_metadata(tasks: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Capture the report-relevant facts of the tasks this run is about to execute."""
    metadata: dict[str, dict[str, Any]] = {}
    for task in tasks:
        task_id = str(task.get("id") or "")
        if not task_id:
            continue
        metadata[task_id] = {
            "tags": sorted(str(tag) for tag in (task.get("tags") or ())),
            "needs": sorted(str(need) for need in (task.get("needs") or ())),
            "prompt": str(task.get("prompt") or ""),
        }
    return metadata


def normalize_task_metadata(value: Any) -> dict[str, dict[str, Any]]:
    """Validate a persisted metadata map, dropping entries that cannot be trusted."""
    if not isinstance(value, Mapping):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for raw_id, raw_entry in value.items():
        task_id = str(raw_id or "").strip()
        if not task_id or not isinstance(raw_entry, Mapping):
            continue
        normalized[task_id] = {
            "tags": sorted(str(tag) for tag in (raw_entry.get("tags") or ()) if str(tag)),
            "needs": sorted(str(need) for need in (raw_entry.get("needs") or ()) if str(need)),
            "prompt": str(raw_entry.get("prompt") or ""),
        }
    return normalized


def task_metadata_from_rows(rows: Iterable[Any]) -> dict[str, dict[str, Any]]:
    """Merge the metadata declared by every meta header in the loaded rows."""
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or row.get("row_type") != "meta":
            continue
        merged.update(normalize_task_metadata(row.get(METADATA_FIELD)))
    return merged


def entry_requires_mutation(entry: Mapping[str, Any] | None) -> bool:
    """Whether a task was expected to change Plane state, from its persisted tags."""
    if not isinstance(entry, Mapping):
        return False
    return bool(MUTATION_TAGS.intersection(str(tag) for tag in (entry.get("tags") or ())))


def entry_needs(entry: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The fixtures a task declared, from its persisted needs."""
    if not isinstance(entry, Mapping):
        return ()
    return tuple(str(need) for need in (entry.get("needs") or ()) if str(need))


def entry_prompt(entry: Mapping[str, Any] | None) -> str:
    """The prompt text a task ran with, from its persisted metadata."""
    if not isinstance(entry, Mapping):
        return ""
    return str(entry.get("prompt") or "")


__all__ = [
    "METADATA_FIELD",
    "MUTATION_TAGS",
    "TaskMetadata",
    "build_task_metadata",
    "entry_needs",
    "entry_prompt",
    "entry_requires_mutation",
    "normalize_task_metadata",
    "task_metadata_from_rows",
]
