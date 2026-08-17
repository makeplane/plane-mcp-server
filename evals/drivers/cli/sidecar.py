"""Recording-proxy glue: wrap commands, PYTHONPATH, load/harvest sidecar JSONL."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from evals import REPO_ROOT
from evals.results import TraceIntegrityReason

ProxyMetaWaitOutcome = Literal["meta_present", "proxy_exited", "proxy_not_observed", "timeout"]


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


def proxy_session_paths(path: Path) -> list[Path]:
    """Discover the legacy base file and every per-process session derived from it."""
    discovered = list(path.parent.glob(f"{path.name}.*.jsonl"))
    if path.is_file():
        discovered.append(path)
    return sorted(set(discovered), key=lambda item: item.name)


def load_proxy_sidecar(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load, validate, and merge exactly one proxy session from each sidecar file."""
    status: dict[str, Any] = {
        "state": "missing",
        "torn_line": False,
        "skipped_rows": 0,
        "meta": None,
        "metas": [],
        "sessions": [],
        "session_count": 0,
        "session_file_count": 0,
        "session_files": [],
        "all_sessions_finalized": False,
        "unfinalized_sessions": 0,
        "pending_left": None,
        "non_tool_pending_left": None,
        "proxy_meta_count": 0,
        "proxy_meta_not_final": False,
        "invalid_seq": 0,
        "duplicate_seq": 0,
        "missing_seq": 0,
        "unexpected_seq": 0,
        "invalid_meta_fields": 0,
        "tool_manifest_disagreement": False,
        "tool_manifest_fingerprints": [],
        "tool_manifest_missing_sessions": 0,
    }
    session_paths = proxy_session_paths(path)
    if not session_paths:
        return [], status
    status["session_file_count"] = len(session_paths)
    status["session_files"] = [str(session_path) for session_path in session_paths]

    raw_sessions: list[dict[str, Any]] = []
    for session_path in session_paths:
        raw_session: dict[str, Any] = {
            "path": session_path,
            "calls": [],
            "metas": [],
            "torn_line": False,
            "skipped_rows": 0,
            "proxy_meta_not_final": False,
        }
        try:
            raw = session_path.read_bytes()
        except OSError:
            raw_sessions.append(raw_session)
            continue
        lines = raw.decode("utf-8", errors="replace").splitlines()
        last_row_kind: str | None = None
        for index, line in enumerate(lines):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError:
                if index == len(lines) - 1:
                    raw_session["torn_line"] = True
                    raw_session["proxy_meta_not_final"] = bool(raw_session["metas"])
                    break
                raw_session["skipped_rows"] += 1
                last_row_kind = "invalid"
                continue
            if not isinstance(row, dict):
                raw_session["skipped_rows"] += 1
                last_row_kind = "invalid"
                continue
            if row.get("row_type") == "proxy_meta":
                raw_session["metas"].append(row)
                last_row_kind = "meta"
                continue
            tool = row.get("tool")
            if not tool:
                raw_session["skipped_rows"] += 1
                last_row_kind = "invalid"
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
            if isinstance(row.get("result_text"), str):
                call["result_text"] = row["result_text"]
            if isinstance(row.get("observed_sentinels"), list):
                call["observed_sentinels"] = [str(value) for value in row["observed_sentinels"]]
            if isinstance(row.get("observed_aggregates"), list):
                call["observed_aggregates"] = [value for value in row["observed_aggregates"] if isinstance(value, dict)]
            raw_session["calls"].append(call)
            last_row_kind = "call"
        raw_session["proxy_meta_not_final"] = raw_session["proxy_meta_not_final"] or bool(
            raw_session["metas"] and last_row_kind != "meta"
        )
        raw_sessions.append(raw_session)

    counter_keys = (
        "pending_left",
        "non_tool_pending_left",
        "unmatched_responses",
        "unparsed_lines",
        "non_json_lines",
        "malformed_jsonrpc",
        "recorder_errors",
        "undelivered_lines",
    )
    fatal_counts = (
        "pending_left",
        "non_tool_pending_left",
        "unmatched_responses",
        "unparsed_lines",
        "recorder_errors",
        "undelivered_lines",
        "invalid_seq",
        "duplicate_seq",
        "missing_seq",
        "unexpected_seq",
        "invalid_meta_fields",
    )
    calls: list[dict[str, Any]] = []
    session_statuses: list[dict[str, Any]] = []
    manifests: list[str] = []
    for index, raw_session in enumerate(raw_sessions):
        session_calls = raw_session["calls"]
        meta_rows = raw_session["metas"]
        meta = meta_rows[-1] if meta_rows else None
        valid_sequences = [call["seq"] for call in session_calls if _nonnegative_int(call.get("seq")) not in (None, 0)]
        last_seq = _nonnegative_int(meta.get("last_seq")) if meta is not None else None
        tool_request_count = _nonnegative_int(meta.get("tool_request_count")) if meta is not None else None
        segment: dict[str, Any] = {
            "index": index,
            "path": str(raw_session["path"]),
            "meta": meta,
            "meta_count": len(meta_rows),
            "call_count": len(session_calls),
            "torn_line": bool(raw_session["torn_line"]),
            "skipped_rows": int(raw_session["skipped_rows"]),
            "proxy_meta_not_final": bool(raw_session["proxy_meta_not_final"]),
            "finalized": len(meta_rows) == 1 and not raw_session["proxy_meta_not_final"],
            "invalid_seq": len(session_calls) - len(valid_sequences),
            "duplicate_seq": len(valid_sequences) - len(set(valid_sequences)),
            "missing_seq": 0,
            "unexpected_seq": 0,
            "invalid_meta_fields": 0,
            "pumps_alive": bool(meta.get("pumps_alive")) if meta is not None else False,
            "last_seq": last_seq,
            "tool_request_count": tool_request_count,
        }
        if meta is not None:
            segment["invalid_meta_fields"] = (
                abs(len(meta_rows) - 1) + int(last_seq is None) + int(tool_request_count is None)
            )
            if last_seq is not None and tool_request_count is not None and tool_request_count != last_seq:
                segment["invalid_meta_fields"] += 1
            expected_sequences = set(range(1, last_seq + 1)) if last_seq is not None else set()
            observed_sequences = set(valid_sequences)
            segment["missing_seq"] = len(expected_sequences - observed_sequences)
            segment["unexpected_seq"] = len(observed_sequences - expected_sequences) if last_seq is not None else 0
            for key in counter_keys:
                value = _nonnegative_int(meta.get(key))
                if meta.get(key) is not None and value is None:
                    segment["invalid_meta_fields"] += 1
                segment[key] = value
            fingerprint = meta.get("tool_manifest_fingerprint")
            if isinstance(fingerprint, str):
                manifests.append(fingerprint)
        else:
            for key in counter_keys:
                segment[key] = None

        segment["state"] = (
            "incomplete"
            if meta is None
            or not segment["finalized"]
            or segment["torn_line"]
            or segment["skipped_rows"] > 0
            or any((segment.get(key) or 0) > 0 for key in fatal_counts)
            or segment["pumps_alive"]
            else "complete"
        )
        session_calls.sort(
            key=lambda call: (
                _nonnegative_int(call.get("seq")) in (None, 0),
                _nonnegative_int(call.get("seq")) or 0,
            )
        )
        calls.extend(session_calls)
        session_statuses.append(segment)

    metas = [segment["meta"] for segment in session_statuses if segment["meta"] is not None]
    unfinalized_sessions = sum(not segment["finalized"] for segment in session_statuses)
    status["sessions"] = session_statuses
    status["session_count"] = len(session_statuses)
    status["metas"] = metas
    status["meta"] = metas[-1] if metas else None
    status["proxy_meta_count"] = sum(segment["meta_count"] for segment in session_statuses)
    status["unfinalized_sessions"] = unfinalized_sessions
    status["all_sessions_finalized"] = bool(session_statuses) and unfinalized_sessions == 0
    status["proxy_meta_not_final"] = any(segment["proxy_meta_not_final"] for segment in session_statuses)
    status["torn_line"] = any(segment["torn_line"] for segment in session_statuses)
    status["pumps_alive"] = any(segment["pumps_alive"] for segment in session_statuses)
    aggregate_keys = (
        "skipped_rows",
        *counter_keys,
        "invalid_seq",
        "duplicate_seq",
        "missing_seq",
        "unexpected_seq",
        "invalid_meta_fields",
    )
    for key in aggregate_keys:
        status[key] = sum((segment.get(key) or 0) for segment in session_statuses)

    unique_manifests = sorted(set(manifests))
    missing_manifests = len(metas) - len(manifests)
    status["tool_manifest_fingerprints"] = unique_manifests
    status["tool_manifest_missing_sessions"] = missing_manifests
    status["tool_manifest_disagreement"] = len(unique_manifests) > 1 or bool(unique_manifests and missing_manifests)
    status["tool_manifest_fingerprint"] = (
        unique_manifests[0] if len(unique_manifests) == 1 and missing_manifests == 0 else None
    )
    status["evidence_trace_available"] = (
        bool(metas)
        and len(metas) == len(session_statuses)
        and all(bool(meta.get("evidence_trace_available")) for meta in metas)
    )
    if status["meta"] is not None:
        status["finalization_reason"] = status["meta"].get("finalization_reason")
        status["finalization_signal"] = status["meta"].get("finalization_signal")
    if all(segment["call_count"] == 0 and segment["meta_count"] == 0 for segment in session_statuses) and not (
        status["torn_line"] or status["skipped_rows"]
    ):
        status["state"] = "empty"
    else:
        status["state"] = (
            "incomplete" if any(segment["state"] == "incomplete" for segment in session_statuses) else "complete"
        )
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
    if status.get("meta") is None:
        parts.append("no_meta=1")
    else:
        if status.get("proxy_meta_not_final"):
            parts.append("proxy_meta_not_final=1")
        if status.get("unfinalized_sessions"):
            parts.append(f"unfinalized_sessions={int(status['unfinalized_sessions'])}")
    for key in (
        "skipped_rows",
        "pending_left",
        "non_tool_pending_left",
        "unmatched_responses",
        "unparsed_lines",
        "non_json_lines",
        "malformed_jsonrpc",
        "recorder_errors",
        "undelivered_lines",
        "invalid_seq",
        "duplicate_seq",
        "missing_seq",
        "unexpected_seq",
        "invalid_meta_fields",
    ):
        value = status.get(key)
        if value and not (key == "proxy_meta_count" and value == 1):
            parts.append(f"{key}={int(value)}")
    if status.get("pumps_alive"):
        parts.append("pumps_alive=1")
    return ":".join(parts)


def load_proxy_sidecar_calls(path: Path) -> list[dict[str, Any]]:
    """Convenience: call rows only (sorted by seq)."""
    calls, _status = load_proxy_sidecar(path)
    return calls


def proxy_pid_path(sidecar_path: Path) -> Path:
    """Return the companion lifecycle file written by the recording proxy."""
    return sidecar_path.with_name(f"{sidecar_path.name}.pid")


def _read_proxy_pid(sidecar_path: Path) -> int | None:
    pids = _read_proxy_pids(sidecar_path)
    return pids[-1] if pids else None


def _read_proxy_pids(sidecar_path: Path) -> list[int]:
    lifecycle_paths = [proxy_pid_path(path) for path in proxy_session_paths(sidecar_path)]
    lifecycle_paths.extend(sidecar_path.parent.glob(f"{sidecar_path.name}.*.jsonl.pid"))
    lifecycle_paths.append(proxy_pid_path(sidecar_path))
    pids: set[int] = set()
    for lifecycle_path in set(lifecycle_paths):
        try:
            pid = int(lifecycle_path.read_text(encoding="ascii").strip())
        except (OSError, UnicodeError, ValueError):
            continue
        if pid > 0:
            pids.add(pid)
    return sorted(pids)


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _wait_for_proxy_meta_outcome(
    sidecar_path: Path,
    *,
    poll_s: float = 0.2,
    max_wait_s: float | None = None,
) -> ProxyMetaWaitOutcome:
    """Wait for final metadata and retain why an absent row cannot still arrive."""
    # Local import keeps drivers import-light for non-proxy unit tests.
    from evals.proxy import SHUTDOWN_DEADLINE_S

    if max_wait_s is None:
        max_wait_s = SHUTDOWN_DEADLINE_S + 2.0

    # A completed CLI cannot launch a proxy after the fact. No trace and no
    # lifecycle file therefore means the proxy was never observed, rather than
    # a finalizer that could benefit from waiting for the whole deadline.
    pids = _read_proxy_pids(sidecar_path)
    if not proxy_session_paths(sidecar_path) and not pids:
        return "proxy_not_observed"

    deadline = time.monotonic() + max(0.0, max_wait_s)
    while True:
        _, status = load_proxy_sidecar(sidecar_path)
        if status.get("all_sessions_finalized"):
            return "meta_present"
        pids = _read_proxy_pids(sidecar_path)
        if pids and not any(_process_is_alive(pid) for pid in pids):
            return "proxy_exited"
        rem = deadline - time.monotonic()
        if rem <= 0:
            break
        time.sleep(min(max(0.001, poll_s), rem))

    # Close the boundary races in both directions: metadata may have landed on
    # the final sleep, or the proxy may have exited without writing it.
    _, status = load_proxy_sidecar(sidecar_path)
    if status.get("all_sessions_finalized"):
        return "meta_present"
    pids = _read_proxy_pids(sidecar_path)
    if pids and not any(_process_is_alive(pid) for pid in pids):
        return "proxy_exited"
    return "timeout"


def _note_proxy_meta_wait(outcome: ProxyMetaWaitOutcome, sidecar_path: Path, notes: list[str]) -> None:
    if outcome == "proxy_exited":
        notes.append("proxy_meta_missing_after_proxy_exit")
    elif outcome == "proxy_not_observed":
        notes.append("proxy_meta_missing:proxy_not_observed")
    elif outcome == "timeout":
        pid = _read_proxy_pid(sidecar_path)
        state = "proxy_alive=1" if pid is not None and _process_is_alive(pid) else "proxy_state=unknown"
        notes.append(f"proxy_meta_wait_timeout:{state}")


def apply_proxy_sidecar(
    calls: list[dict[str, Any]],
    client_calls: list[dict[str, Any]],
    sidecar_path: Path,
    notes: list[str],
    *,
    poll_s: float = 0.2,
    max_wait_s: float | None = None,
) -> ProxySidecarResult:
    """Wait for and prefer a complete sidecar; fall back when incomplete/empty.

    Incomplete sidecar (torn/skipped row, missing meta, pending_left>0) yields
    to the CLI trace when the CLI has *more* plane calls. Returns
    ``(plane_calls, client_calls, call_source)``.
    """
    wait_outcome = _wait_for_proxy_meta_outcome(
        sidecar_path,
        poll_s=poll_s,
        max_wait_s=max_wait_s,
    )
    _note_proxy_meta_wait(wait_outcome, sidecar_path, notes)
    proxy_calls, status = load_proxy_sidecar(sidecar_path)
    state = status.get("state")
    trace_integrity, trace_integrity_reason = trace_integrity_from_status(status)
    fingerprint = status.get("tool_manifest_fingerprint") if trace_integrity else None
    if status.get("tool_manifest_disagreement"):
        manifest_values = list(status.get("tool_manifest_fingerprints") or [])
        if status.get("tool_manifest_missing_sessions"):
            manifest_values.append("<missing>")
        manifests = ",".join(manifest_values)
        notes.append(f"proxy_tool_manifest_disagreement:{manifests}")

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
    return (
        _wait_for_proxy_meta_outcome(
            sidecar_path,
            poll_s=poll_s,
            max_wait_s=max_wait_s,
        )
        == "meta_present"
    )


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
    return apply_proxy_sidecar(
        calls,
        client_calls,
        sidecar_path,
        notes,
        max_wait_s=max_wait_s,
    )


__all__ = [
    "apply_proxy_sidecar",
    "ensure_proxy_pythonpath",
    "harvest_proxy_after_cli_timeout",
    "load_proxy_sidecar",
    "load_proxy_sidecar_calls",
    "proxy_pid_path",
    "proxy_session_paths",
    "proxy_wrap_server_command",
    "ProxySidecarResult",
    "trace_integrity_from_status",
    "wait_for_proxy_meta",
]
