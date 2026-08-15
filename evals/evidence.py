"""Target-bound response-evidence matching without retaining response bodies.

Seeders register hidden, per-run sentinel values under a non-sensitive label. Drivers
compare each Plane response with those values only when the request targets the seeded
entity, then retain only the labels that matched. CLI proxies consume the matching
configuration from a one-shot file before the agent starts; sentinel values never enter
agent-visible argv/config, result rows, or payload-free sidecars.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

EVIDENCE_SENTINELS_ENV = "EVAL_EVIDENCE_SENTINELS_JSON"
TARGET_ENTITY_EVIDENCE = "target-entity-hidden-fact"


def normalize_evidence_sentinels(value: Any) -> dict[str, tuple[str, ...]]:
    """Return a validated label-to-sentinel mapping, dropping empty values."""
    if not isinstance(value, Mapping):
        return {}
    normalized: dict[str, tuple[str, ...]] = {}
    for raw_label, raw_values in value.items():
        label = str(raw_label or "").strip()
        if not label:
            continue
        values: Sequence[Any]
        if isinstance(raw_values, str):
            values = (raw_values,)
        elif isinstance(raw_values, Sequence):
            values = raw_values
        else:
            continue
        clean_values: list[str] = []
        for item in values:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                clean_values.append(text)
        clean = tuple(dict.fromkeys(clean_values))
        if clean:
            normalized[label] = clean
    return normalized


def normalize_evidence_targets(value: Any) -> dict[str, tuple[str, ...]]:
    """Return a validated label-to-target-ID mapping, dropping empty values."""
    return normalize_evidence_sentinels(value)


def configured_evidence_labels(sentinels: Any, targets: Any) -> tuple[str, ...]:
    """Return labels that have both response values and target entity IDs."""
    values_by_label = normalize_evidence_sentinels(sentinels)
    targets_by_label = normalize_evidence_targets(targets)
    return tuple(sorted(values_by_label.keys() & targets_by_label.keys()))


def encode_evidence_sentinels(value: Any) -> str:
    """Serialize a temporary driver/proxy configuration, never a result-row field."""
    normalized = normalize_evidence_sentinels(value)
    return json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))


def decode_evidence_sentinels(value: str | None) -> dict[str, tuple[str, ...]]:
    """Decode a temporary driver/proxy configuration, failing closed on bad input."""
    if not value:
        return {}
    try:
        raw = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return normalize_evidence_sentinels(raw)


def encode_evidence_config(sentinels: Any, targets: Any) -> str:
    """Serialize the proxy-only matching configuration."""
    return json.dumps(
        {
            "sentinels": normalize_evidence_sentinels(sentinels),
            "targets": normalize_evidence_targets(targets),
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


def decode_evidence_config(value: str | None) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    """Decode proxy-only matching configuration, failing closed on malformed input."""
    if not value:
        return {}, {}
    try:
        raw = json.loads(value)
    except (TypeError, ValueError):
        return {}, {}
    if not isinstance(raw, Mapping):
        return {}, {}
    return (
        normalize_evidence_sentinels(raw.get("sentinels")),
        normalize_evidence_targets(raw.get("targets")),
    )


def write_evidence_config(path: Path, sentinels: Any, targets: Any) -> None:
    """Create a private, one-shot proxy configuration outside the agent cwd."""
    payload = encode_evidence_config(sentinels, targets)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(payload)


def consume_evidence_config(path: Path | None) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    """Read and unlink a one-shot proxy configuration, failing closed."""
    if path is None:
        return {}, {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}, {}
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    return decode_evidence_config(raw)


def _request_targets(request_args: Any, target_ids: Sequence[str]) -> bool:
    targets = set(target_ids)

    def contains(value: Any) -> bool:
        if isinstance(value, Mapping):
            return any(contains(item) for item in value.values())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return any(contains(item) for item in value)
        return value is not None and str(value) in targets

    return contains(request_args)


def observed_sentinel_labels(
    response_text: str,
    sentinels: Any,
    *,
    request_args: Any,
    evidence_targets: Any,
) -> list[str]:
    """Return labels whose target request exposed a hidden value in its response."""
    text = str(response_text or "")
    if not text:
        return []
    normalized = normalize_evidence_sentinels(sentinels)
    targets = normalize_evidence_targets(evidence_targets)
    return sorted(
        label
        for label, values in normalized.items()
        if label in targets
        and _request_targets(request_args, targets[label])
        and any(value in text for value in values)
    )


def set_target_evidence(context: dict[str, Any], values: Sequence[Any], *, target_ids: Sequence[Any]) -> None:
    """Register API-confirmed values and the entity IDs whose reads may prove them."""
    clean_values: list[str] = []
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            clean_values.append(text)
    clean = tuple(dict.fromkeys(clean_values))
    if not clean:
        raise RuntimeError("target evidence has no API-confirmed sentinel values")
    clean_targets = tuple(dict.fromkeys(str(value).strip() for value in target_ids if str(value).strip()))
    if not clean_targets:
        raise RuntimeError("target evidence has no seeded target entity ids")
    context["evidence_sentinels"] = {TARGET_ENTITY_EVIDENCE: clean}
    context["evidence_targets"] = {TARGET_ENTITY_EVIDENCE: clean_targets}


__all__ = [
    "EVIDENCE_SENTINELS_ENV",
    "TARGET_ENTITY_EVIDENCE",
    "configured_evidence_labels",
    "consume_evidence_config",
    "decode_evidence_config",
    "decode_evidence_sentinels",
    "encode_evidence_config",
    "encode_evidence_sentinels",
    "normalize_evidence_sentinels",
    "normalize_evidence_targets",
    "observed_sentinel_labels",
    "set_target_evidence",
    "write_evidence_config",
]
