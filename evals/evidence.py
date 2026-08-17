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
from hashlib import sha256
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


def normalize_evidence_aggregates(value: Any) -> dict[str, tuple[dict[str, Any], ...]]:
    """Validate the two narrow aggregate response shapes used by R2 and R6."""
    if not isinstance(value, Mapping):
        return {}
    normalized: dict[str, tuple[dict[str, Any], ...]] = {}
    for raw_label, raw_specs in value.items():
        label = str(raw_label or "").strip()
        if not label or not isinstance(raw_specs, Sequence) or isinstance(raw_specs, (str, bytes, bytearray)):
            continue
        specs: list[dict[str, Any]] = []
        for raw_spec in raw_specs:
            if not isinstance(raw_spec, Mapping):
                continue
            kind = raw_spec.get("kind")
            if kind == "total_count":
                try:
                    specs.append({"kind": kind, "value": int(raw_spec["value"])})
                except (KeyError, TypeError, ValueError):
                    continue
            elif kind == "grouped_counts" and isinstance(raw_spec.get("values"), Mapping):
                try:
                    values = {str(key): int(count) for key, count in raw_spec["values"].items()}
                except (TypeError, ValueError):
                    continue
                if values:
                    specs.append({"kind": kind, "values": values})
        if specs:
            normalized[label] = tuple(specs)
    return normalized


def evidence_aggregate_shapes(value: Any) -> dict[str, tuple[dict[str, str], ...]]:
    """Reduce aggregate truth to the response shapes safe for an agent-visible proxy."""
    return {
        label: tuple({"kind": str(spec["kind"])} for spec in specs)
        for label, specs in normalize_evidence_aggregates(value).items()
    }


def normalize_evidence_aggregate_shapes(value: Any) -> dict[str, tuple[dict[str, str], ...]]:
    """Validate aggregate extraction instructions that contain no expected values."""
    if not isinstance(value, Mapping):
        return {}
    normalized: dict[str, tuple[dict[str, str], ...]] = {}
    for raw_label, raw_specs in value.items():
        label = str(raw_label or "").strip()
        if not label or not isinstance(raw_specs, Sequence) or isinstance(raw_specs, (str, bytes, bytearray)):
            continue
        specs: list[dict[str, str]] = []
        for raw_spec in raw_specs:
            if not isinstance(raw_spec, Mapping) or raw_spec.get("kind") not in {"total_count", "grouped_counts"}:
                continue
            spec = {"kind": str(raw_spec["kind"])}
            if spec not in specs:
                specs.append(spec)
        if specs:
            normalized[label] = tuple(specs)
    return normalized


def configured_evidence_labels(sentinels: Any, targets: Any, aggregates: Any = None) -> tuple[str, ...]:
    """Return labels that have both response values and target entity IDs."""
    values_by_label = normalize_evidence_sentinels(sentinels)
    targets_by_label = normalize_evidence_targets(targets)
    aggregate_labels = normalize_evidence_aggregates(aggregates)
    return tuple(sorted((values_by_label.keys() | aggregate_labels.keys()) & targets_by_label.keys()))


def fingerprint_evidence_sentinels(value: Any) -> dict[str, tuple[tuple[int, str], ...]]:
    """Replace raw values with character lengths and one-way SHA-256 fingerprints."""
    return {
        label: tuple((len(item), sha256(item.encode("utf-8")).hexdigest()) for item in values)
        for label, values in normalize_evidence_sentinels(value).items()
    }


def normalize_evidence_fingerprints(value: Any) -> dict[str, tuple[tuple[int, str], ...]]:
    """Validate serialized response-value fingerprints, dropping malformed entries."""
    if not isinstance(value, Mapping):
        return {}
    normalized: dict[str, tuple[tuple[int, str], ...]] = {}
    for raw_label, raw_specs in value.items():
        label = str(raw_label or "").strip()
        if not label or not isinstance(raw_specs, Sequence) or isinstance(raw_specs, (str, bytes, bytearray)):
            continue
        specs: list[tuple[int, str]] = []
        for raw_spec in raw_specs:
            if isinstance(raw_spec, Mapping):
                raw_length = raw_spec.get("length")
                raw_digest = raw_spec.get("sha256")
            elif (
                isinstance(raw_spec, Sequence)
                and not isinstance(raw_spec, (str, bytes, bytearray))
                and len(raw_spec) == 2
            ):
                raw_length, raw_digest = raw_spec
            else:
                continue
            try:
                length = int(raw_length)
            except (TypeError, ValueError):
                continue
            digest = str(raw_digest or "").strip().lower()
            if length > 0 and len(digest) == 64 and all(char in "0123456789abcdef" for char in digest):
                specs.append((length, digest))
        clean = tuple(dict.fromkeys(specs))
        if clean:
            normalized[label] = clean
    return normalized


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


def encode_evidence_config(sentinels: Any, targets: Any, aggregates: Any = None) -> str:
    """Serialize targets and extraction shapes, never raw sentinels or aggregate truth."""
    fingerprints = fingerprint_evidence_sentinels(sentinels)
    return json.dumps(
        {
            "fingerprints": {
                label: [{"length": length, "sha256": digest} for length, digest in specs]
                for label, specs in fingerprints.items()
            },
            "targets": normalize_evidence_targets(targets),
            "aggregates": evidence_aggregate_shapes(aggregates),
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )


def decode_evidence_config(
    value: str | None,
) -> tuple[
    dict[str, tuple[tuple[int, str], ...]],
    dict[str, tuple[str, ...]],
    dict[str, tuple[dict[str, Any], ...]],
]:
    """Decode proxy-only matching configuration, failing closed on malformed input."""
    if not value:
        return {}, {}, {}
    try:
        raw = json.loads(value)
    except (TypeError, ValueError):
        return {}, {}, {}
    if not isinstance(raw, Mapping):
        return {}, {}, {}
    return (
        normalize_evidence_fingerprints(raw.get("fingerprints")),
        normalize_evidence_targets(raw.get("targets")),
        normalize_evidence_aggregate_shapes(raw.get("aggregates")),
    )


def write_evidence_config(path: Path, sentinels: Any, targets: Any, aggregates: Any = None) -> None:
    """Create a private, run-scoped proxy configuration outside the agent cwd."""
    payload = encode_evidence_config(sentinels, targets, aggregates)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(payload)


def consume_evidence_config(
    path: Path | None,
) -> tuple[
    dict[str, tuple[tuple[int, str], ...]],
    dict[str, tuple[str, ...]],
    dict[str, tuple[dict[str, Any], ...]],
]:
    """Read a reusable run-scoped proxy configuration, failing closed.

    The historical name is retained for callers. A CLI may start multiple MCP
    proxy sessions during one task, so the driver's TemporaryDirectory owns
    deletion after every session has exited.
    """
    if path is None:
        return {}, {}, {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}, {}, {}
    return decode_evidence_config(raw)


def _request_targets(request_args: Any, target_ids: Sequence[str]) -> bool:
    targets = set(target_ids)

    def contains(value: Any) -> bool:
        if isinstance(value, Mapping):
            return any(contains(item) for item in value.values())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return any(contains(item) for item in value)
        if value is None:
            return False
        text = str(value)
        return any(target == text or target in text for target in targets)

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


def observed_fingerprint_labels(
    response_text: str,
    fingerprints: Any,
    *,
    request_args: Any,
    evidence_targets: Any,
) -> list[str]:
    """Match target-bound value fingerprints without ever receiving the raw values."""
    text = str(response_text or "")
    if not text:
        return []
    normalized = normalize_evidence_fingerprints(fingerprints)
    targets = normalize_evidence_targets(evidence_targets)
    eligible = {
        label: specs
        for label, specs in normalized.items()
        if label in targets and _request_targets(request_args, targets[label])
    }
    if not eligible:
        return []

    expected_by_length: dict[int, set[str]] = {}
    labels_by_spec: dict[tuple[int, str], set[str]] = {}
    for label, specs in eligible.items():
        for length, digest in specs:
            expected_by_length.setdefault(length, set()).add(digest)
            labels_by_spec.setdefault((length, digest), set()).add(label)

    matched: set[str] = set()
    for length, expected in expected_by_length.items():
        if length > len(text):
            continue
        remaining = set(expected)
        for start in range(len(text) - length + 1):
            digest = sha256(text[start : start + length].encode("utf-8")).hexdigest()
            if digest not in remaining:
                continue
            matched.update(labels_by_spec[(length, digest)])
            remaining.remove(digest)
            if not remaining:
                break
    return sorted(matched)


def _decoded_documents(response_text: str) -> list[Any]:
    """Decode JSON-RPC/MCP wrappers and JSON strings embedded inside them."""
    documents: list[Any] = []
    pending: list[Any] = [response_text]
    seen_strings: set[str] = set()
    while pending:
        value = pending.pop()
        documents.append(value)
        if isinstance(value, Mapping):
            pending.extend(value.values())
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            pending.extend(value)
        elif isinstance(value, str):
            text = value.strip()
            if text in seen_strings or not text or text[0] not in "[{":
                continue
            seen_strings.add(text)
            try:
                pending.append(json.loads(text))
            except (TypeError, ValueError):
                continue
    return documents


def observed_aggregates(
    response_text: str,
    aggregates: Any,
    *,
    request_args: Any,
    evidence_targets: Any,
) -> list[dict[str, Any]]:
    """Extract target-bound aggregate values without receiving expected truth."""
    specs_by_label = normalize_evidence_aggregate_shapes(aggregates)
    targets_by_label = normalize_evidence_targets(evidence_targets)
    documents = _decoded_documents(response_text)
    observations: list[dict[str, Any]] = []
    for label, specs in specs_by_label.items():
        targets = targets_by_label.get(label, ())
        if not targets or not _request_targets(request_args, targets):
            continue
        for spec in specs:
            if spec["kind"] == "total_count":
                for value in documents:
                    if not isinstance(value, Mapping):
                        continue
                    count = value.get("total_count")
                    if isinstance(count, int) and not isinstance(count, bool):
                        observations.append({"label": label, "kind": "total_count", "value": count})
                        break
            elif spec["kind"] == "grouped_counts":
                for value in documents:
                    if not isinstance(value, Mapping) or not isinstance(value.get("grouped_counts"), Mapping):
                        continue
                    grouped = value["grouped_counts"]
                    observed: dict[str, int] = {}
                    for target in targets:
                        entry = grouped.get(target)
                        count = entry.get("count") if isinstance(entry, Mapping) else None
                        if not isinstance(count, int) or isinstance(count, bool):
                            break
                        observed[target] = count
                    if len(observed) == len(targets):
                        observations.append({"label": label, "kind": "grouped_counts", "values": observed})
                        break
    return observations


def observed_aggregate_labels(observations: Any, aggregates: Any) -> list[str]:
    """Compare proxy observations with seed truth inside the post-agent harness."""
    if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes, bytearray)):
        return []
    expected_by_label = normalize_evidence_aggregates(aggregates)
    matched: set[str] = set()
    for observation in observations:
        if not isinstance(observation, Mapping):
            continue
        label = str(observation.get("label") or "")
        for expected in expected_by_label.get(label, ()):
            if expected["kind"] != observation.get("kind"):
                continue
            if expected["kind"] == "total_count" and observation.get("value") == expected["value"]:
                matched.add(label)
            elif expected["kind"] == "grouped_counts" and observation.get("values") == expected["values"]:
                matched.add(label)
    return sorted(matched)


def _register_targets(context: dict[str, Any], target_ids: Sequence[Any], *, what: str) -> None:
    """Add seeded entity IDs to the label's target set, keeping any already registered."""
    clean = tuple(dict.fromkeys(str(value).strip() for value in target_ids if value is not None and str(value).strip()))
    if not clean:
        raise RuntimeError(f"{what} has no seeded target entity ids")
    targets = context.setdefault("evidence_targets", {})
    current = targets.get(TARGET_ENTITY_EVIDENCE, ())
    targets[TARGET_ENTITY_EVIDENCE] = tuple(dict.fromkeys((*current, *clean)))


def _add_aggregate_specs(context: dict[str, Any], specs: Sequence[dict[str, Any]]) -> None:
    """Append acceptable aggregate shapes rather than replacing the registered ones.

    A task may reach its answer by more than one honest call shape — R6's winner is
    provable by two per-project counts or by one count grouped by project. Replacing
    here privileged whichever seeder ran last, and scored every other path unproven.
    """
    aggregates = context.setdefault("evidence_aggregates", {})
    registered = list(aggregates.get(TARGET_ENTITY_EVIDENCE, ()))
    for spec in specs:
        if spec not in registered:
            registered.append(spec)
    aggregates[TARGET_ENTITY_EVIDENCE] = tuple(registered)


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
    _register_targets(context, target_ids, what="target evidence")
    context["evidence_sentinels"] = {TARGET_ENTITY_EVIDENCE: clean}


def set_target_count_evidence(context: dict[str, Any], *counts: int, target_ids: Sequence[Any]) -> None:
    """Allow an exact ``total_count`` response whose request names a seeded target."""
    if not counts:
        raise RuntimeError("target count evidence has no API-confirmed counts")
    _register_targets(context, target_ids, what="target count evidence")
    _add_aggregate_specs(context, [{"kind": "total_count", "value": int(count)} for count in counts])


def set_target_grouped_count_evidence(context: dict[str, Any], values: Mapping[Any, int]) -> None:
    """Allow grouped counts only when every seeded target id has its exact count."""
    clean = {str(target): int(count) for target, count in values.items() if str(target).strip()}
    if not clean:
        raise RuntimeError("target grouped-count evidence has no seeded targets")
    _register_targets(context, clean, what="target grouped-count evidence")
    _add_aggregate_specs(context, [{"kind": "grouped_counts", "values": clean}])


__all__ = [
    "EVIDENCE_SENTINELS_ENV",
    "TARGET_ENTITY_EVIDENCE",
    "configured_evidence_labels",
    "consume_evidence_config",
    "decode_evidence_config",
    "decode_evidence_sentinels",
    "encode_evidence_config",
    "encode_evidence_sentinels",
    "evidence_aggregate_shapes",
    "fingerprint_evidence_sentinels",
    "normalize_evidence_fingerprints",
    "normalize_evidence_aggregate_shapes",
    "normalize_evidence_aggregates",
    "normalize_evidence_sentinels",
    "normalize_evidence_targets",
    "observed_sentinel_labels",
    "observed_fingerprint_labels",
    "observed_aggregate_labels",
    "observed_aggregates",
    "set_target_count_evidence",
    "set_target_evidence",
    "set_target_grouped_count_evidence",
    "write_evidence_config",
]
