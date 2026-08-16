"""Persisted run-identity validation shared by every report command path."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.report.load import is_infra_error_row

MISSING = "<missing>"
IDENTITY_FIELDS = ("battery", "resolved_model", "provider", "driver", "server")
VARYABLE_DIMENSIONS = ("resolved_model", "provider", "driver", "server")
TOOL_MANIFEST_FIELD = "tool_manifest_fingerprint"


class ComparabilityError(ValueError):
    """The persisted records do not establish the requested comparison."""

    def __init__(self, details: Iterable[str]) -> None:
        self.details = tuple(details)
        super().__init__("; ".join(self.details))


@dataclass(frozen=True, slots=True)
class FileIdentity:
    """Validated canonical identity and realized-model observations for one file."""

    path: Path
    values: dict[str, str]
    realized_models: tuple[str, ...]

    @property
    def realized_model_changed(self) -> bool:
        return len(self.realized_models) > 1


@dataclass(frozen=True, slots=True)
class IdentityReport:
    """Identity evidence safe to print beside report measurements."""

    files: tuple[FileIdentity, ...]
    varied_dimensions: tuple[str, ...]


def persisted_value(value: Any) -> str:
    """Normalize absent and empty persisted values to an explicit, non-wildcard value."""
    if value is None or value == "":
        return MISSING
    return str(value)


def parse_varied_dimensions(raw_values: Iterable[str]) -> tuple[str, ...]:
    """Parse repeatable comma-separated --vary declarations."""
    requested: list[str] = []
    for raw_value in raw_values:
        requested.extend(part.strip() for part in raw_value.split(","))
    if not requested:
        return ()
    if any(not dimension for dimension in requested):
        raise ValueError("--vary requires a dimension name")
    if "all" in requested:
        raise ValueError("--vary has no 'all'; name each treatment dimension individually")
    if "battery" in requested:
        raise ValueError("--vary battery is not allowed; the measurement universe cannot be waived")
    unknown = sorted(set(requested) - set(VARYABLE_DIMENSIONS))
    if unknown:
        valid = ", ".join(VARYABLE_DIMENSIONS)
        raise ValueError(f"unknown --vary dimension(s): {', '.join(unknown)}; choose from: {valid}")
    requested_set = set(requested)
    return tuple(dimension for dimension in VARYABLE_DIMENSIONS if dimension in requested_set)


def _read_records(path: Path) -> tuple[list[tuple[int, dict[str, Any]]], list[tuple[int, dict[str, Any]]]]:
    headers: list[tuple[int, dict[str, Any]]] = []
    rows: list[tuple[int, dict[str, Any]]] = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                record = {}
            if not isinstance(record, dict):
                record = {}
            target = headers if record.get("row_type") == "meta" else rows
            target.append((line_number, record))
    return headers, rows


def _record_values(records: list[tuple[int, dict[str, Any]]], field: str) -> dict[str, list[int]]:
    values: dict[str, list[int]] = {}
    for line_number, record in records:
        values.setdefault(persisted_value(record.get(field)), []).append(line_number)
    return values


def _format_values(values: dict[str, list[int]]) -> str:
    return ", ".join(f"{value} (line(s) {','.join(str(line) for line in lines)})" for value, lines in values.items())


def _validate_file(path: Path) -> tuple[FileIdentity, list[str]]:
    headers, rows = _read_records(path)
    issues: list[str] = []
    values: dict[str, str] = {}

    for field in IDENTITY_FIELDS:
        header_values = _record_values(headers, field)
        row_values = _record_values(rows, field)
        if len(header_values) > 1:
            issues.append(f"{path}: conflicting meta headers for {field}: {_format_values(header_values)}")
        if len(row_values) > 1:
            issues.append(f"{path}: rows disagree on {field}: {_format_values(row_values)}")
        if header_values and row_values and set(header_values) != set(row_values):
            issues.append(
                f"{path}: meta header disagrees with raw rows on {field}: "
                f"header={_format_values(header_values)}; rows={_format_values(row_values)}"
            )
        source = row_values or header_values or {MISSING: []}
        values[field] = next(iter(source))

    # A row that failed in seeding never launched an agent, so no tools/list was ever
    # observed and there is no manifest to record. Demanding one from those rows refuses
    # comparisons that are perfectly sound: the first live A/B was blocked by six
    # infra_seed rows that every statistic already excludes.
    surface_rows = [(line, record) for line, record in rows if not is_infra_error_row(record)]
    manifest_values = _record_values(surface_rows, TOOL_MANIFEST_FIELD)
    if len(manifest_values) > 1:
        issues.append(f"{path}: rows disagree on {TOOL_MANIFEST_FIELD}: {_format_values(manifest_values)}")
    values[TOOL_MANIFEST_FIELD] = next(iter(manifest_values), MISSING)

    realized_models = tuple(sorted(_record_values(rows, "model")))
    return FileIdentity(path=path, values=values, realized_models=realized_models), issues


def validate_persisted_identity(
    paths: Iterable[Path],
    *,
    varied_dimensions: Iterable[str] = (),
) -> IdentityReport:
    """Validate files internally and against each other before any dedupe or statistics."""
    varied = tuple(varied_dimensions)
    identities: list[FileIdentity] = []
    issues: list[str] = []
    for path in paths:
        identity, file_issues = _validate_file(path)
        identities.append(identity)
        issues.extend(file_issues)

    if len(identities) > 1:
        for field in IDENTITY_FIELDS:
            by_path = {str(identity.path): identity.values[field] for identity in identities}
            if len(set(by_path.values())) <= 1:
                continue
            if field != "battery" and field in varied:
                continue
            detail = "; ".join(f"{path}={value}" for path, value in by_path.items())
            issues.append(f"{field} differs across files: {detail}")
        manifest_values = {identity.values[TOOL_MANIFEST_FIELD] for identity in identities}
        if MISSING in manifest_values:
            unidentified = [
                str(identity.path) for identity in identities if identity.values[TOOL_MANIFEST_FIELD] == MISSING
            ]
            issues.append(
                f"{TOOL_MANIFEST_FIELD} is missing for comparison input(s): {', '.join(unidentified)}; "
                "every compared surface must be identified"
            )

    if issues:
        raise ComparabilityError(issues)
    return IdentityReport(files=tuple(identities), varied_dimensions=varied)


def format_refusal(error: ComparabilityError) -> str:
    """Render an exit-2 refusal without any report measurements."""
    lines = ["error: comparability cannot be established from the persisted identity"]
    lines.extend(f"  - {detail}" for detail in error.details)
    return "\n".join(lines)


def identity_header_lines(report: IdentityReport, *, warn_missing_manifest: bool = False) -> list[str]:
    """Render treatment declarations and non-canonical realized-model evidence."""
    lines: list[str] = []
    varied = report.varied_dimensions
    if varied:
        treatment = ", ".join(varied)
        if len(varied) > 1:
            treatment += " — end-to-end comparison; effect not attributable to any single dimension"
        elif varied == ("driver",):
            treatment += " — end-to-end driver question; cannot support a surface-only claim"
        lines.append(f"Treatment: {treatment}")
        if "driver" in varied and len(varied) > 1:
            lines.append("Driver interpretation: end-to-end driver question; cannot support a surface-only claim")
        for dimension in varied:
            values = "; ".join(f"{identity.path}={identity.values[dimension]}" for identity in report.files)
            lines.append(f"Treatment values ({dimension}): {values}")

    evidence = [
        identity for identity in report.files if identity.realized_models and identity.realized_models != (MISSING,)
    ]
    if evidence:
        detail = "; ".join(f"{identity.path}={','.join(identity.realized_models)}" for identity in evidence)
        lines.append(f"Realized model evidence: {detail}")
    for identity in evidence:
        if identity.realized_model_changed:
            lines.append(
                f"WARNING: realized model changed within {identity.path}: {','.join(identity.realized_models)}"
            )
    if any(identity.values[TOOL_MANIFEST_FIELD] != MISSING for identity in report.files):
        manifests = "; ".join(f"{identity.path}={identity.values[TOOL_MANIFEST_FIELD]}" for identity in report.files)
        lines.append(f"Tool manifest evidence: {manifests}")
    missing_manifests = [
        str(identity.path) for identity in report.files if identity.values[TOOL_MANIFEST_FIELD] == MISSING
    ]
    if warn_missing_manifest and missing_manifests:
        lines.append("WARNING: TOOL MANIFEST ABSENT — tool surface is unidentified for " + ", ".join(missing_manifests))
    return lines


__all__ = [
    "ComparabilityError",
    "FileIdentity",
    "IDENTITY_FIELDS",
    "IdentityReport",
    "MISSING",
    "TOOL_MANIFEST_FIELD",
    "VARYABLE_DIMENSIONS",
    "format_refusal",
    "identity_header_lines",
    "parse_varied_dimensions",
    "persisted_value",
    "validate_persisted_identity",
]
