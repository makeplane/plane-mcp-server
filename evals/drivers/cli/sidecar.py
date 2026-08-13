"""Recording-proxy glue: wrap commands, PYTHONPATH, load/harvest sidecar JSONL."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from evals.drivers.protocol import REPO_ROOT


def proxy_wrap_server_command(
    real_command: list[str],
    *,
    sidecar_path: Path,
    python_bin: str | None = None,
    record_result_payloads: bool = False,
) -> list[str]:
    """Return ``[python, -m, evals.proxy, --log, sidecar, --, *real_command]``."""
    py = python_bin or sys.executable
    command = [py, "-m", "evals.proxy", "--log", str(sidecar_path)]
    if record_result_payloads:
        command.append("--record-result-payloads")
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
      - meta: proxy_meta row if present
      - pending_left: from meta when present
    """
    status: dict[str, Any] = {
        "state": "missing",
        "torn_line": False,
        "meta": None,
        "pending_left": None,
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
    torn = False
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        try:
            row = json.loads(s)
        except json.JSONDecodeError:
            # Tolerate a torn final line (crash mid-write); stop there.
            if i == len(lines) - 1:
                torn = True
                break
            continue
        if not isinstance(row, dict):
            continue
        if row.get("row_type") == "proxy_meta":
            meta = row
            continue
        tool = row.get("tool")
        if not tool:
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
        calls.append(call)

    # Score order must match request seq, not response-append order.
    calls.sort(key=lambda c: (c.get("seq") is None, c.get("seq") if c.get("seq") is not None else 0))

    status["torn_line"] = torn
    status["meta"] = meta
    if meta is not None:
        status["pending_left"] = meta.get("pending_left")
        status["pumps_alive"] = bool(meta.get("pumps_alive"))
    incomplete = bool(
        torn
        or meta is None
        or (meta is not None and int(meta.get("pending_left") or 0) > 0)
        or (meta is not None and bool(meta.get("pumps_alive")))
    )
    if not calls and not meta and not torn:
        status["state"] = "empty"
    elif incomplete:
        status["state"] = "incomplete"
    else:
        status["state"] = "complete"
    return calls, status


def load_proxy_sidecar_calls(path: Path) -> list[dict[str, Any]]:
    """Convenience: call rows only (sorted by seq)."""
    calls, _status = load_proxy_sidecar(path)
    return calls


def apply_proxy_sidecar(
    calls: list[dict[str, Any]],
    client_calls: list[dict[str, Any]],
    sidecar_path: Path,
    notes: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    """Prefer a complete proxy sidecar; fall back to CLI-parsed when incomplete/empty.

    Incomplete sidecar (torn line, missing meta, pending_left>0) yields to the
    CLI trace when the CLI has *more* plane calls. Returns
    ``(plane_calls, client_calls, call_source)``.
    """
    proxy_calls, status = load_proxy_sidecar(sidecar_path)
    state = status.get("state")
    if state in ("missing", "empty"):
        notes.append("proxy_sidecar_empty")
        return calls, client_calls, "json"
    if state == "incomplete":
        notes.append(
            "proxy_sidecar_incomplete"
            + (":torn" if status.get("torn_line") else "")
            + (":no_meta" if status.get("meta") is None else "")
            + (f":pending_left={status.get('pending_left')}" if status.get("pending_left") else "")
            + (":pumps_alive" if status.get("pumps_alive") else "")
        )
        if len(calls) > len(proxy_calls):
            notes.append("proxy_sidecar_deferred_to_cli_trace")
            return calls, client_calls, "json"
        if proxy_calls:
            notes.append(f"calls_from_proxy:{sidecar_path}")
            return proxy_calls, client_calls, "proxy"
        return calls, client_calls, "json"
    # complete
    notes.append(f"calls_from_proxy:{sidecar_path}")
    return proxy_calls, client_calls, "proxy"


def wait_for_proxy_meta(
    sidecar_path: Path,
    *,
    poll_s: float = 0.2,
    max_wait_s: float | None = None,
) -> bool:
    """Poll until the sidecar gains a ``proxy_meta`` row (or the wait expires).

    After a CLI timeout the driver kills the CLI; the proxy is a *separate*
    process that only then sees stdin EOF and needs up to
    ``SHUTDOWN_DEADLINE_S`` to flush call rows + meta. Call this **before**
    harvesting so the temp dir is not deleted mid-finalization.

    Returns True if meta was observed.
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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
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
    "wait_for_proxy_meta",
]
