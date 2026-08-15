"""Recording-proxy glue: wrap commands, PYTHONPATH, load/harvest sidecar JSONL."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals import REPO_ROOT
from evals.results import TraceIntegrityReason


@dataclass(slots=True)
class ProxySidecarResult:
    """Harvested calls plus typed integrity and manifest observations."""

    calls: list[dict[str, Any]]
    client_calls: list[dict[str, Any]]
    call_source: str
    trace_integrity: bool
    trace_integrity_reason: TraceIntegrityReason | None
    tool_manifest_fingerprint: str | None
    status: dict[str, Any]

    def __iter__(self) -> Iterator[Any]:
        """Retain the established three-value unpacking API."""
        yield self.calls
        yield self.client_calls
        yield self.call_source


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def proxy_wrap_server_command(
    real_command: list[str],
    *,
    sidecar_path: Path,
    python_bin: str | None = None,
    record_result_payloads: bool = False,
    evidence_path: Path | None = None,
) -> list[str]:
    """Return ``[python, -m, evals.proxy, --log, sidecar, --, *real_command]``."""
    py = python_bin or sys.executable
    command = [py, "-m", "evals.proxy", "--log", str(sidecar_path)]
    if record_result_payloads:
        command.append("--record-result-payloads")
    if evidence_path is not None:
        command.extend(["--evidence-file", str(evidence_path)])
    return [*command, "--", *real_command]


def ensure_proxy_pythonpath(env: dict[str, str]) -> dict[str, str]:
    """Inject the repo root into PYTHONPATH so ``python -m evals.proxy`` works from any cwd.

    ``evals`` is not an installed package (pyproject excludes it); the MCP child
    is often launched from a foreign temp dir (OpenCode project dir, etc.).
    """
    root = str(REPO_ROOT)
    out = dict(env)
    existing = out.get("PYTHONPATH", "")
    parts = [p for p in existing.split(os.pathsep) if p]
    if root not in parts:
        out["PYTHONPATH"] = root + (os.pathsep + existing if existing else "")
    return out


def load_proxy_sidecar(
    path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load sidecar call rows (sorted by seq) plus a status dict.

    Status keys:
      - missing / empty / complete / incomplete
      - torn_line: final line failed to parse
      - skipped_rows: non-final rows that could not produce a call or metadata row
      - meta: proxy_meta row if present
      - pending_left / non_tool_pending_left: unmatched requests from meta
      - sequence_errors: invalid, duplicate, missing, or unexpected call sequence values
      - proxy_meta_count / proxy_meta_not_final: metadata framing integrity
    """
    status: dict[str, Any] = {
        "state": "missing",
        "torn_line": False,
        "skipped_rows": 0,
        "meta": None,
        "pending_left": None,
        "non_tool_pending_left": None,
        "proxy_meta_count": 0,
        "proxy_meta_not_final": False,
        "invalid_seq": 0,
        "duplicate_seq": 0,
        "missing_seq": 0,
        "unexpected_seq": 0,
        "invalid_meta_fields": 0,
    }
    if not path.is_file():
        return [], status
    try:
        raw = path.read_bytes()
    except OSError:
        return [], status
    if not raw:
        status["state"] = "empty"
        return [], status

    # Decode with replacement so invalid UTF-8 does not crash the loader.
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    calls: list[dict[str, Any]] = []
    meta: dict[str, Any] | None = None
    meta_positions: list[int] = []
    nonblank_position = 0
    torn = False
    skipped_rows = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        nonblank_position += 1
        try:
            row = json.loads(s)
        except json.JSONDecodeError:
            # Tolerate a torn final line (crash mid-write); stop there.
            if i == len(lines) - 1:
                torn = True
                break
            skipped_rows += 1
            continue
        if not isinstance(row, dict):
            skipped_rows += 1
            continue
        if row.get("row_type") == "proxy_meta":
            meta = row
            meta_positions.append(nonblank_position)
            continue
        tool = row.get("tool")
        if not tool:
            skipped_rows += 1
            continue
        call = {
            "tool": str(tool),
            "args": row.get("args") if isinstance(row.get("args"), dict) else (row.get("args") or {}),
            "origin": "plane",
            "is_error": bool(row.get("is_error")),
            "result_chars": int(row.get("result_chars") or 0),
            "duration_ms": row.get("duration_ms"),
            "seq": row.get("seq"),
        }
        # Optional in new sidecars; old payload-free rows remain valid.
        if isinstance(row.get("result_text"), str):
            call["result_text"] = row["result_text"]
        if isinstance(row.get("observed_sentinels"), list):
            call["observed_sentinels"] = [str(value) for value in row["observed_sentinels"]]
        calls.append(call)

    valid_sequences = [call["seq"] for call in calls if _nonnegative_int(call.get("seq")) not in (None, 0)]
    invalid_seq = len(calls) - len(valid_sequences)
    duplicate_seq = len(valid_sequences) - len(set(valid_sequences))
    last_seq = _nonnegative_int(meta.get("last_seq")) if meta is not None else None
    tool_request_count = _nonnegative_int(meta.get("tool_request_count")) if meta is not None else None
    invalid_meta_fields = int(meta is not None and last_seq is None) + int(
        meta is not None and tool_request_count is None
    )
    if last_seq is not None and tool_request_count is not None and tool_request_count != last_seq:
        invalid_meta_fields += 1
    expected_sequences = set(range(1, last_seq + 1)) if last_seq is not None else set()
    observed_sequences = set(valid_sequences)
    missing_seq = len(expected_sequences - observed_sequences)
    unexpected_seq = len(observed_sequences - expected_sequences) if last_seq is not None else 0

    # Score order must match request seq, not response-append order. Invalid seq
    # rows remain available for diagnostics but can never make the trace valid.
    calls.sort(
        key=lambda call: (
            _nonnegative_int(call.get("seq")) in (None, 0),
            _nonnegative_int(call.get("seq")) or 0,
        )
    )

    status["torn_line"] = torn
    status["skipped_rows"] = skipped_rows
    status["meta"] = meta
    status["proxy_meta_count"] = len(meta_positions)
    status["proxy_meta_not_final"] = bool(meta_positions and meta_positions[-1] != nonblank_position)
    status["invalid_seq"] = invalid_seq
    status["duplicate_seq"] = duplicate_seq
    status["missing_seq"] = missing_seq
    status["unexpected_seq"] = unexpected_seq
    status["invalid_meta_fields"] = invalid_meta_fields
    if meta is not None:
        counter_keys = (
            "pending_left",
            "non_tool_pending_left",
            "unmatched_responses",
            "unparsed_lines",
            "non_json_lines",
            "malformed_jsonrpc",
            "recorder_errors",
        )
        for key in counter_keys:
            value = _nonnegative_int(meta.get(key))
            if meta.get(key) is not None and value is None:
                status["invalid_meta_fields"] += 1
            status[key] = value
        status["pumps_alive"] = bool(meta.get("pumps_alive"))
        fingerprint = meta.get("tool_manifest_fingerprint")
        status["tool_manifest_fingerprint"] = str(fingerprint) if isinstance(fingerprint, str) else None
    fatal_counts = (
        "pending_left",
        "non_tool_pending_left",
        "unmatched_responses",
        "unparsed_lines",
        "recorder_errors",
        "invalid_seq",
        "duplicate_seq",
        "missing_seq",
        "unexpected_seq",
        "invalid_meta_fields",
    )
    incomplete = bool(
        torn
        or skipped_rows > 0
        or len(meta_positions) != 1
        or status["proxy_meta_not_final"]
        or any((status.get(key) or 0) > 0 for key in fatal_counts)
        or (meta is not None and bool(meta.get("pumps_alive")))
    )
    if not calls and not meta and not torn and skipped_rows == 0:
        status["state"] = "empty"
    elif incomplete:
        status["state"] = "incomplete"
    else:
        status["state"] = "complete"
    return calls, status


def trace_integrity_from_status(
    status: dict[str, Any],
) -> tuple[bool, TraceIntegrityReason | None]:
    """Map sidecar status to the typed result-row integrity fields."""
    if status.get("state") == "complete":
        return True, None
    if (status.get("unparsed_lines") or 0) > 0:
        return False, "protocol_violation"
    return False, "recorder_loss"


def _incompleteness_note(status: dict[str, Any]) -> str:
    parts = ["proxy_sidecar_incomplete"]
    if status.get("torn_line"):
        parts.append("torn_line=1")
    for key in (
        "skipped_rows",
        "proxy_meta_count",
        "proxy_meta_not_final",
        "pending_left",
        "non_tool_pending_left",
        "unmatched_responses",
        "unparsed_lines",
        "non_json_lines",
        "malformed_jsonrpc",
        "recorder_errors",
        "invalid_seq",
        "duplicate_seq",
        "missing_seq",
        "unexpected_seq",
        "invalid_meta_fields",
    ):
        value = status.get(key)
        if value and not (key == "proxy_meta_count" and value == 1):
            parts.append(f"{key}={int(value)}")
    if status.get("meta") is None:
        parts.append("no_meta=1")
    if status.get("pumps_alive"):
        parts.append("pumps_alive=1")
    return ":".join(parts)


def load_proxy_sidecar_calls(path: Path) -> list[dict[str, Any]]:
    """Convenience: call rows only (sorted by seq)."""
    calls, _status = load_proxy_sidecar(path)
    return calls


def apply_proxy_sidecar(
    calls: list[dict[str, Any]],
    client_calls: list[dict[str, Any]],
    sidecar_path: Path,
    notes: list[str],
) -> ProxySidecarResult:
    """Prefer a complete proxy sidecar; fall back to CLI-parsed when incomplete/empty.

    Incomplete sidecar (torn/skipped row, missing meta, pending_left>0) yields
    to the CLI trace when the CLI has *more* plane calls. Returns
    ``(plane_calls, client_calls, call_source)``.
    """
    proxy_calls, status = load_proxy_sidecar(sidecar_path)
    state = status.get("state")
    trace_integrity, trace_integrity_reason = trace_integrity_from_status(status)
    fingerprint = status.get("tool_manifest_fingerprint") if trace_integrity else None

    def result(
        selected_calls: list[dict[str, Any]],
        selected_client_calls: list[dict[str, Any]],
        source: str,
    ) -> ProxySidecarResult:
        return ProxySidecarResult(
            calls=selected_calls,
            client_calls=selected_client_calls,
            call_source=source,
            trace_integrity=trace_integrity,
            trace_integrity_reason=trace_integrity_reason,
            tool_manifest_fingerprint=str(fingerprint) if isinstance(fingerprint, str) else None,
            status=status,
        )

    if state in ("missing", "empty"):
        notes.append("proxy_sidecar_empty")
        return result(calls, client_calls, "json")
    if state == "incomplete":
        notes.append(_incompleteness_note(status))
        if len(calls) > len(proxy_calls):
            notes.append("proxy_sidecar_deferred_to_cli_trace")
            return result(calls, client_calls, "json")
        if proxy_calls:
            notes.append(f"calls_from_proxy:{sidecar_path}")
            return result(proxy_calls, client_calls, "proxy")
        return result(calls, client_calls, "json")
    # complete
    notes.append(f"calls_from_proxy:{sidecar_path}")
    return result(proxy_calls, client_calls, "proxy")


def wait_for_proxy_meta(
    sidecar_path: Path,
    *,
    poll_s: float = 0.2,
    max_wait_s: float | None = None,
) -> bool:
    """Poll until the sidecar gains a ``proxy_meta`` row, returning True if it appears.

    The proxy is a separate process: after the driver kills the CLI it only then sees stdin
    EOF and needs up to SHUTDOWN_DEADLINE_S to flush. Call before harvesting so the temp
    directory is not deleted mid-finalization.
    """
    # Local import keeps drivers import-light for non-proxy unit tests.
    from evals.proxy import SHUTDOWN_DEADLINE_S

    if max_wait_s is None:
        max_wait_s = SHUTDOWN_DEADLINE_S + 2.0
    deadline = time.monotonic() + max_wait_s
    while True:
        _, status = load_proxy_sidecar(sidecar_path)
        if status.get("meta") is not None:
            return True
        rem = deadline - time.monotonic()
        if rem <= 0:
            break
        time.sleep(min(poll_s, rem))
    _, status = load_proxy_sidecar(sidecar_path)
    return status.get("meta") is not None


def harvest_proxy_after_cli_timeout(
    calls: list[dict[str, Any]],
    client_calls: list[dict[str, Any]],
    sidecar_path: Path,
    notes: list[str],
    *,
    max_wait_s: float | None = None,
) -> ProxySidecarResult:
    """Wait for proxy finalization after CLI kill, then harvest the sidecar.

    If meta never appears within the wait window, harvest anyway (incomplete
    note from ``apply_proxy_sidecar``). ``max_wait_s`` defaults to
    ``SHUTDOWN_DEADLINE_S + 2`` (see ``wait_for_proxy_meta``).
    """
    found = wait_for_proxy_meta(sidecar_path, max_wait_s=max_wait_s)
    if not found:
        notes.append("proxy_meta_wait_timeout")
    return apply_proxy_sidecar(calls, client_calls, sidecar_path, notes)


__all__ = [
    "apply_proxy_sidecar",
    "ensure_proxy_pythonpath",
    "harvest_proxy_after_cli_timeout",
    "load_proxy_sidecar",
    "load_proxy_sidecar_calls",
    "proxy_wrap_server_command",
    "ProxySidecarResult",
    "trace_integrity_from_status",
    "wait_for_proxy_meta",
]
