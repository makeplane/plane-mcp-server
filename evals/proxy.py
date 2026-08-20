"""Stdio MCP recording proxy — byte-faithful JSON-RPC relay with a sidecar call log.

``python -m evals.proxy --log SIDECAR.jsonl [--record-result-payloads] -- <server cmd...>``
Relays raw bytes both ways and parses a *copy* to log tools/call pairs. Uses ``os.read`` on
raw fds, never select + buffered readline: partial lines hang and prefetch stalls multi-line
clients. Child stderr is forwarded; exit code matches the child (signals as 128+signum).
"""

from __future__ import annotations

import argparse
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from evals.core.error_class import classify_error
from evals.core.evidence import (
    EVIDENCE_SENTINELS_ENV,
    consume_evidence_config,
    fingerprint_evidence_sentinels,
    normalize_evidence_aggregate_shapes,
    normalize_evidence_fingerprints,
    normalize_evidence_sentinels,
    normalize_evidence_targets,
    observed_aggregates,
    observed_fingerprint_labels,
)
from evals.core.tool_manifest import ToolManifestCapture

# Single post-EOF / child-exit deadline for the whole shutdown sequence.
SHUTDOWN_DEADLINE_S = 10.0
READ_CHUNK = 65536

# Repo root for PYTHONPATH scrubbing (parent of evals/). Deliberately computed
# here rather than imported from ``evals``: this module runs inside the MCP
# server's process tree with the repo scrubbed off PYTHONPATH, so it cannot
# import its own package.
REPO_ROOT = Path(__file__).resolve().parent.parent


def proxy_session_log_path(configured_path: Path, *, pid: int | None = None) -> Path:
    """Derive the one sidecar owned by this proxy process from the configured base."""
    process_id = os.getpid() if pid is None else pid
    return configured_path.with_name(f"{configured_path.name}.{process_id}.jsonl")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Stdio MCP recording proxy (tools/call → sidecar JSONL)",
    )
    p.add_argument(
        "--log",
        required=True,
        type=Path,
        help="Sidecar JSONL path for recorded tool calls + proxy_meta summary",
    )
    p.add_argument(
        "--record-result-payloads",
        action="store_true",
        help="Also store serialized tool-result text (off by default; may contain workspace data)",
    )
    p.add_argument(
        "--evidence-file",
        type=Path,
        help="Run-scoped target-evidence configuration loaded before the MCP child starts",
    )
    p.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Target MCP server command after --",
    )
    args = p.parse_args(argv)
    cmd = list(args.command or [])
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        p.error("target command required after --")
    args.command = cmd
    return args


def map_child_returncode(rc: int | None) -> int:
    """Map subprocess returncode to a conventional shell exit status.

    Negative codes mean killed by signal N (``-N``); return ``128 + N``.
    """
    if rc is None:
        return 1
    if rc < 0:
        return 128 + (-rc)
    return int(rc)


def write_all_fd(fd: int, data: bytes) -> None:
    """Write ``data`` fully to a raw fd, looping on short writes."""
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        n = os.write(fd, view[offset:])
        if n == 0:
            raise BrokenPipeError("os.write returned 0")
        offset += n


def scrub_child_pythonpath(env: dict[str, str] | None = None) -> dict[str, str]:
    """Return a copy of ``env`` with this repo's root removed from PYTHONPATH.

    The proxy may be launched via ``python -m evals.proxy`` with PYTHONPATH set
    to the monorepo root so ``evals`` is importable from a foreign cwd. That
    entry must not leak into the *real* MCP server child (which may resolve
    ``plane_mcp`` from its own venv).
    """
    base = dict(env if env is not None else os.environ)
    # Matching configuration belongs only to the recorder. The real Plane MCP server
    # neither needs nor receives hidden sentinel values.
    base.pop(EVIDENCE_SENTINELS_ENV, None)
    root = str(REPO_ROOT)
    raw = base.get("PYTHONPATH", "")
    if not raw:
        return base
    parts = [p for p in raw.split(os.pathsep) if p and Path(p).resolve() != REPO_ROOT.resolve()]
    # Also drop exact string matches that may not resolve the same way.
    parts = [p for p in parts if p != root]
    if parts:
        base["PYTHONPATH"] = os.pathsep.join(parts)
    else:
        base.pop("PYTHONPATH", None)
    return base


class SidecarRecorder:
    """Thread-safe recorder for tools/call pairs into a JSONL sidecar.

    Finalization is atomic under ``_lock``: once ``write_meta`` sets
    ``finalized``, further row appends no-op so ``proxy_meta`` is always the
    last sidecar line even if daemon pumps keep running briefly.
    """

    def __init__(
        self,
        log_path: Path,
        *,
        record_result_payloads: bool = False,
        evidence_sentinels: dict[str, Any] | None = None,
        evidence_fingerprints: dict[str, Any] | None = None,
        evidence_targets: dict[str, Any] | None = None,
        evidence_aggregates: dict[str, Any] | None = None,
    ) -> None:
        self.log_path = log_path
        self.record_result_payloads = record_result_payloads
        raw_sentinels = normalize_evidence_sentinels(evidence_sentinels)
        self.evidence_fingerprints = normalize_evidence_fingerprints(evidence_fingerprints)
        if not self.evidence_fingerprints and raw_sentinels:
            self.evidence_fingerprints = fingerprint_evidence_sentinels(raw_sentinels)
        self.evidence_targets = normalize_evidence_targets(evidence_targets)
        self.evidence_aggregates = normalize_evidence_aggregate_shapes(evidence_aggregates)
        self.evidence_active = bool(
            self.evidence_fingerprints or (self.evidence_aggregates.keys() & self.evidence_targets.keys())
        )
        self._lock = threading.Lock()
        self._error_lock = threading.Lock()
        self._pending: dict[Any, dict[str, Any]] = {}
        self._non_tool_pending: dict[Any, dict[str, Any]] = {}
        self._tool_manifest = ToolManifestCapture()
        self._seq = 0
        self.relayed_lines = 0
        self.unparsed_lines = 0
        self.non_json_lines = 0
        self.malformed_jsonrpc = 0
        self.recorder_errors = 0
        self.undelivered_lines = 0
        self.unmatched_responses = 0
        self.non_tool_responses = 0
        self.notifications = 0
        self.server_requests = 0
        self.child_killed = False
        self.pumps_alive = False
        # Which streams were still pumping at finalization. A bare boolean cannot
        # distinguish "the server may still have been talking to us" from "the client
        # went away while our stdin read was parked", which are different facts.
        self.pumps_alive_streams: set[str] = set()
        self.finalization_reason = "direct"
        self.finalization_signal: str | None = None
        self.finalized = False
        # Post-finalize append attempts (not written; for tests / diagnostics).
        self.post_finalize_appends = 0
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)

    def _append(self, row: dict[str, Any]) -> None:
        line = json.dumps(row, default=str, ensure_ascii=False) + "\n"
        with self._lock:
            if self.finalized:
                self.post_finalize_appends += 1
                return
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    def note_relayed(self) -> None:
        with self._lock:
            self.relayed_lines += 1

    def note_unparsed(self, *, malformed_jsonrpc: bool = False) -> None:
        with self._lock:
            self.unparsed_lines += 1
            if malformed_jsonrpc:
                self.malformed_jsonrpc += 1
            else:
                self.non_json_lines += 1
            self.relayed_lines += 1

    def note_recorder_error(self) -> None:
        """Count a swallowed callback failure without depending on recorder state."""
        with self._error_lock:
            self.recorder_errors += 1

    def note_undelivered(self) -> None:
        """Count a line recorded here that never reached the other endpoint.

        Recording happens before forwarding so a fast child cannot race an unregistered
        pending id. The cost is that a broken pipe leaves a match in the sidecar for a
        response the agent never saw, which would prove surface use it never had. Counting
        it makes the sidecar non-authoritative instead of quietly wrong.
        """
        with self._error_lock:
            self.undelivered_lines += 1

    def on_client_message(self, obj: dict[str, Any]) -> None:
        """Handle a parsed JSON-RPC message from the client (parent → child)."""
        has_method = "method" in obj
        has_id = "id" in obj
        if has_method and not has_id:
            with self._lock:
                self.notifications += 1
            return
        if not has_method:
            return
        method = obj.get("method")
        req_id = obj.get("id")
        if method != "tools/call":
            params = obj.get("params")
            cursor = params.get("cursor") if isinstance(params, dict) else None
            with self._lock:
                self._non_tool_pending[req_id] = {
                    "method": str(method),
                    "cursor": str(cursor) if cursor is not None else None,
                }
            return
        params = obj.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        name = params.get("name") or ""
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        with self._lock:
            self._seq += 1
            self._pending[req_id] = {
                "tool": str(name),
                "args": arguments,
                "t_start": time.perf_counter(),
                "seq": self._seq,
            }

    def on_server_message(self, obj: dict[str, Any]) -> None:
        """Handle a parsed JSON-RPC message from the server (child → parent)."""
        has_method = "method" in obj
        has_id = "id" in obj

        if has_method and not has_id:
            with self._lock:
                self.notifications += 1
                if obj.get("method") == "notifications/tools/list_changed":
                    self._tool_manifest.invalidate()
            return
        if has_method and has_id:
            with self._lock:
                self.server_requests += 1
            return
        if not has_id:
            return

        req_id = obj.get("id")
        with self._lock:
            pending = self._pending.pop(req_id, None)
            non_tool_pending = self._non_tool_pending.pop(req_id, None) if pending is None else None
        if non_tool_pending is not None:
            with self._lock:
                self.non_tool_responses += 1
                if non_tool_pending["method"] == "tools/list" and isinstance(obj.get("result"), dict):
                    self._tool_manifest.observe_page(
                        obj["result"],
                        request_cursor=non_tool_pending["cursor"],
                    )
            return
        if pending is None:
            with self._lock:
                self.unmatched_responses += 1
            return
        duration_ms = int(round((time.perf_counter() - pending["t_start"]) * 1000))
        if "error" in obj:
            is_error = True
            result_payload = obj.get("error")
        else:
            result = obj.get("result")
            is_error = False
            if isinstance(result, dict):
                is_error = bool(result.get("isError") or result.get("is_error"))
            result_payload = result
        try:
            result_text = json.dumps(result_payload, default=str, ensure_ascii=False)
        except Exception:
            result_text = str(result_payload)
        row = {
            "tool": pending["tool"],
            "args": pending["args"],
            "is_error": is_error,
            "result_chars": len(result_text),
            "duration_ms": duration_ms,
            "seq": pending["seq"],
        }
        if is_error:
            # Classified here because this is the last place the payload exists:
            # rows keep result_chars, not the text. Only the category is stored.
            row["error_class"] = classify_error(result_text)
        if self.evidence_active:
            # Persist only labels matched from non-enumerable sentinels and
            # target-bound aggregate values the agent already received. The
            # expected aggregate truth and complete result body never enter
            # the proxy process.
            row["observed_sentinels"] = observed_fingerprint_labels(result_text, self.evidence_fingerprints)
            row["observed_aggregates"] = observed_aggregates(
                result_text,
                self.evidence_aggregates,
                request_args=pending["args"],
                evidence_targets=self.evidence_targets,
            )
        if self.record_result_payloads:
            row["result_text"] = result_text
        self._append(row)

    def write_meta(self) -> None:
        """Write proxy_meta as the last row and seal the sidecar (atomic under lock)."""
        with self._lock:
            if self.finalized:
                self.post_finalize_appends += 1
                return
            with self._error_lock:
                recorder_errors = self.recorder_errors
                undelivered_lines = self.undelivered_lines
            row = {
                "row_type": "proxy_meta",
                "relayed_lines": self.relayed_lines,
                "unparsed_lines": self.unparsed_lines,
                "non_json_lines": self.non_json_lines,
                "malformed_jsonrpc": self.malformed_jsonrpc,
                "recorder_errors": recorder_errors,
                "undelivered_lines": undelivered_lines,
                "unmatched_responses": self.unmatched_responses,
                "non_tool_responses": self.non_tool_responses,
                "notifications": self.notifications,
                "server_requests": self.server_requests,
                "pending_left": len(self._pending),
                "non_tool_pending_left": len(self._non_tool_pending),
                "last_seq": self._seq,
                "tool_request_count": self._seq,
                "child_killed": self.child_killed,
                "pumps_alive": self.pumps_alive,
                "pumps_alive_streams": sorted(self.pumps_alive_streams),
                "finalization_reason": self.finalization_reason,
                "finalization_signal": self.finalization_signal,
                "evidence_trace_available": self.evidence_active,
                "tool_manifest_fingerprint": self._tool_manifest.fingerprint,
            }
            line = json.dumps(row, default=str, ensure_ascii=False) + "\n"
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            self.finalized = True


def _valid_jsonrpc_object(obj: dict[str, Any]) -> bool:
    """Validate the JSON-RPC 2.0 message envelope used by MCP stdio."""
    if obj.get("jsonrpc") != "2.0":
        return False
    has_method = "method" in obj
    has_id = "id" in obj
    if has_id and (isinstance(obj.get("id"), bool) or not isinstance(obj.get("id"), (str, int, float, type(None)))):
        return False
    if has_method:
        if not isinstance(obj.get("method"), str) or "result" in obj or "error" in obj:
            return False
        params = obj.get("params")
        return params is None or isinstance(params, (dict, list))
    if not has_id or ("result" in obj) == ("error" in obj):
        return False
    if "error" not in obj:
        return True
    error = obj.get("error")
    return bool(
        isinstance(error, dict)
        and isinstance(error.get("code"), int)
        and not isinstance(error.get("code"), bool)
        and isinstance(error.get("message"), str)
    )


def try_parse_json_line(line: bytes) -> dict[str, Any] | None:
    """Parse a valid JSON-RPC object line; return None on failure (never raises)."""
    try:
        text = line.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    if not text or not text.startswith("{"):
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) and _valid_jsonrpc_object(obj) else None


def classify_jsonrpc_line(line: bytes) -> tuple[str, dict[str, Any] | None]:
    """Classify a framed line as blank, non-JSON, malformed JSON-RPC, or valid."""
    try:
        text = line.decode("utf-8").strip()
    except UnicodeDecodeError:
        return "non_json", None
    if not text:
        return "blank", None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return "non_json", None
    if not isinstance(obj, dict) or not _valid_jsonrpc_object(obj):
        return "malformed_jsonrpc", None
    return "valid", obj


def process_buffer_lines(
    buf: bytearray,
    *,
    forward_fd: int,
    recorder: SidecarRecorder | None,
    is_client: bool,
    record_jsonrpc: bool,
) -> None:
    """Split complete lines from ``buf``, record then forward, leave incomplete tail.

    **Record-before-forward**: for JSON-RPC directions, update the sidecar /
    pending map *before* the line becomes visible to the opposite endpoint.
    A fast child responding on stdout must never race past an unregistered
    pending tools/call id; a failed parent write must not lose a completed
    response that was already matched.
    """
    while True:
        idx = buf.find(b"\n")
        if idx < 0:
            break
        line = bytes(buf[: idx + 1])
        del buf[: idx + 1]
        if record_jsonrpc and recorder is not None:
            classification, obj = classify_jsonrpc_line(line)
            if classification == "blank":
                recorder.note_relayed()
            elif obj is None:
                recorder.note_unparsed(malformed_jsonrpc=classification == "malformed_jsonrpc")
            else:
                recorder.note_relayed()
                try:
                    if is_client:
                        recorder.on_client_message(obj)
                    else:
                        recorder.on_server_message(obj)
                except Exception:
                    recorder.note_recorder_error()
        # Forward only after recording so the opposite endpoint cannot race.
        try:
            write_all_fd(forward_fd, line)
        except (BrokenPipeError, OSError):
            if recorder is not None:
                recorder.note_undelivered()
            raise


def pump_raw(
    *,
    read_fd: int,
    write_fd: int,
    recorder: SidecarRecorder | None,
    is_client: bool,
    record_jsonrpc: bool,
    cancel: threading.Event | None,
    done: threading.Event,
) -> None:
    """Byte-faithful pump: ``os.read`` + line buffer; optional cancel for stdin only.

    Stdout/stderr pumps pass ``cancel=None`` and drain until ``os.read`` returns
    ``b""`` (pipe EOF) so final responses after child exit are not dropped.
    Never uses buffered TextIO wrappers with select.
    """
    buf = bytearray()
    try:
        while True:
            if cancel is not None and cancel.is_set():
                break
            try:
                ready, _, _ = select.select([read_fd], [], [], 0.2)
            except (ValueError, OSError):
                break
            if not ready:
                continue
            try:
                chunk = os.read(read_fd, READ_CHUNK)
            except OSError:
                break
            if not chunk:
                break
            buf.extend(chunk)
            try:
                process_buffer_lines(
                    buf,
                    forward_fd=write_fd,
                    recorder=recorder,
                    is_client=is_client,
                    record_jsonrpc=record_jsonrpc,
                )
            except (BrokenPipeError, OSError):
                break
        # Flush remaining complete lines, then any partial tail (byte-faithful).
        try:
            process_buffer_lines(
                buf,
                forward_fd=write_fd,
                recorder=recorder,
                is_client=is_client,
                record_jsonrpc=record_jsonrpc,
            )
        except (BrokenPipeError, OSError):
            pass
        if buf:
            try:
                write_all_fd(write_fd, bytes(buf))
            except (BrokenPipeError, OSError):
                pass
            if record_jsonrpc and recorder is not None:
                recorder.note_unparsed(malformed_jsonrpc=True)
            buf.clear()
    finally:
        done.set()


def _remaining(deadline_at: float) -> float:
    """Seconds left until ``deadline_at`` (never negative)."""
    return max(0.0, deadline_at - time.monotonic())


def reap_timeout(deadline_at: float | None, floor: float = 0.1) -> float:
    """Timeout for kill/reap waits: remaining budget, never below ``floor``.

    When the overall deadline is exhausted, still allow a short reap so kill
    is not skipped entirely.
    """
    if deadline_at is None:
        return floor
    return max(floor, _remaining(deadline_at))


def _signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except ValueError:
        return str(signum)


def _record_signal_finalization(recorder: SidecarRecorder, signum: int) -> None:
    recorder.finalization_reason = "signal"
    recorder.finalization_signal = _signal_name(signum)


def run_proxy(
    command: list[str],
    log_path: Path,
    *,
    record_result_payloads: bool = False,
    evidence_sentinels: dict[str, Any] | None = None,
    evidence_fingerprints: dict[str, Any] | None = None,
    evidence_targets: dict[str, Any] | None = None,
    evidence_aggregates: dict[str, Any] | None = None,
    termination_signal: Callable[[], int | None] | None = None,
) -> int:
    """Spawn ``command`` as the real MCP server and relay with recording.

    Returns the child's exit code (or 1 on spawn failure). Guarantees
    ``proxy_meta`` is the last sidecar row and the child is reaped even on
    crash paths. Pump threads are daemon so a blocked write cannot hold the
    process past the shutdown deadline.
    """
    recorder = SidecarRecorder(
        log_path,
        record_result_payloads=record_result_payloads,
        evidence_sentinels=evidence_sentinels,
        evidence_fingerprints=evidence_fingerprints,
        evidence_targets=evidence_targets,
        evidence_aggregates=evidence_aggregates,
    )
    recorder.finalization_reason = "running"
    # The CLI driver does not own this detached process, so leave a companion
    # lifecycle file that lets it distinguish a slow finalizer from a proxy
    # that exited before writing proxy_meta. The temp directory owns cleanup.
    try:
        log_path.with_name(f"{log_path.name}.pid").write_text(str(os.getpid()), encoding="ascii")
    except OSError:
        # Metadata remains authoritative. A missing lifecycle file merely
        # leaves timeout diagnostics with an unknown process state.
        pass
    child: subprocess.Popen[bytes] | None = None
    # Scrub repo PYTHONPATH so the real server does not import from this tree.
    child_env = scrub_child_pythonpath()
    t_in = t_out = t_err = None
    stdin_done = stdout_done = stderr_done = None
    deadline_at: float | None = None
    try:
        try:
            child = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env=child_env,
            )
        except OSError as exc:
            recorder.finalization_reason = "spawn_failure"
            print(f"evals.proxy: failed to spawn {command!r}: {exc}", file=sys.stderr)
            return 1

        assert child.stdin is not None and child.stdout is not None and child.stderr is not None
        # Raw fds — never use the buffered TextIO wrappers with select.
        child_stdin_fd = child.stdin.fileno()
        child_stdout_fd = child.stdout.fileno()
        child_stderr_fd = child.stderr.fileno()
        parent_stdin_fd = sys.stdin.fileno()
        parent_stdout_fd = sys.stdout.fileno()
        parent_stderr_fd = sys.stderr.fileno()

        cancel_stdin = threading.Event()
        stdin_done = threading.Event()
        stdout_done = threading.Event()
        stderr_done = threading.Event()

        # daemon=True: a pump blocked writing to an undrained parent cannot
        # keep the process alive past the shutdown deadline.
        t_in = threading.Thread(
            target=pump_raw,
            kwargs={
                "read_fd": parent_stdin_fd,
                "write_fd": child_stdin_fd,
                "recorder": recorder,
                "is_client": True,
                "record_jsonrpc": True,
                "cancel": cancel_stdin,
                "done": stdin_done,
            },
            name="proxy-stdin",
            daemon=True,
        )
        t_out = threading.Thread(
            target=pump_raw,
            kwargs={
                "read_fd": child_stdout_fd,
                "write_fd": parent_stdout_fd,
                "recorder": recorder,
                "is_client": False,
                "record_jsonrpc": True,
                "cancel": None,  # drain until pipe EOF — never gate on cancel
                "done": stdout_done,
            },
            name="proxy-stdout",
            daemon=True,
        )
        t_err = threading.Thread(
            target=pump_raw,
            kwargs={
                "read_fd": child_stderr_fd,
                "write_fd": parent_stderr_fd,
                "recorder": None,
                "is_client": False,
                "record_jsonrpc": False,  # stderr is not JSON-RPC
                "cancel": None,
                "done": stderr_done,
            },
            name="proxy-stderr",
            daemon=True,
        )
        t_in.start()
        t_out.start()
        t_err.start()

        # Phase 1: run until client stdin EOF or child exits.
        # Child exit cancels the *stdin* pump only — stdout must drain to pipe EOF.
        while (
            child.poll() is None
            and not stdin_done.is_set()
            and (termination_signal is None or termination_signal() is None)
        ):
            time.sleep(0.05)

        requested_signal = termination_signal() if termination_signal is not None else None
        if requested_signal is not None:
            _record_signal_finalization(recorder, requested_signal)
        elif child.poll() is not None:
            recorder.finalization_reason = "child_exit"
        else:
            recorder.finalization_reason = "normal_eof"

        # One deadline for the entire post-EOF / post-child-exit shutdown.
        deadline_at = time.monotonic() + SHUTDOWN_DEADLINE_S
        cancel_stdin.set()
        try:
            os.close(child_stdin_fd)
        except OSError:
            pass

        # Phase 2: wait for pumps (remaining time only — no stacked fixed timeouts).
        while _remaining(deadline_at) > 0:
            if stdout_done.is_set() and stderr_done.is_set() and stdin_done.is_set():
                break
            if child.poll() is not None and stdout_done.is_set() and stderr_done.is_set():
                break
            time.sleep(min(0.05, max(0.01, _remaining(deadline_at))))

        rem = _remaining(deadline_at)
        if rem > 0:
            t_in.join(timeout=rem)
        rem = _remaining(deadline_at)
        if rem > 0:
            t_out.join(timeout=rem)
        rem = _remaining(deadline_at)
        if rem > 0:
            t_err.join(timeout=rem)

        if child.poll() is None:
            rem = _remaining(deadline_at)
            if rem > 0:
                try:
                    child.wait(timeout=rem)
                except subprocess.TimeoutExpired:
                    recorder.child_killed = True
                    child.kill()
                    # Bounded wait after kill — remaining budget with floor.
                    try:
                        child.wait(timeout=reap_timeout(deadline_at))
                    except subprocess.TimeoutExpired:
                        pass
            else:
                recorder.child_killed = True
                child.kill()
                try:
                    child.wait(timeout=reap_timeout(deadline_at))
                except subprocess.TimeoutExpired:
                    pass

        # After kill/exit, join stdout/stderr again (bounded) so meta is last.
        rem = _remaining(deadline_at)
        if rem > 0 and t_out is not None:
            t_out.join(timeout=rem)
        rem = _remaining(deadline_at)
        if rem > 0 and t_err is not None:
            t_err.join(timeout=rem)

        for name, thread, done in (
            ("stdin", t_in, stdin_done),
            ("stdout", t_out, stdout_done),
            ("stderr", t_err, stderr_done),
        ):
            if (thread is not None and thread.is_alive()) or (done is not None and not done.is_set()):
                recorder.pumps_alive_streams.add(name)
        recorder.pumps_alive = bool(recorder.pumps_alive_streams)
        return map_child_returncode(child.returncode)
    except KeyboardInterrupt:
        # Preserve Python's existing SIGINT behaviour: unwind through the
        # finalizer, then let KeyboardInterrupt retain the signal exit status.
        _record_signal_finalization(recorder, signal.SIGINT)
        raise
    except BaseException:
        recorder.finalization_reason = "exception"
        raise
    finally:
        if child is not None and child.poll() is None:
            try:
                recorder.child_killed = True
                child.kill()
                try:
                    child.wait(timeout=reap_timeout(deadline_at))
                except subprocess.TimeoutExpired:
                    pass
            except Exception:
                pass
        # If pumps are still alive at deadline, note it; meta is still last row
        # (finalized flag drops any further appends from daemon pumps).
        for name, thread in (("stdin", t_in), ("stdout", t_out), ("stderr", t_err)):
            if thread is not None and thread.is_alive():
                recorder.pumps_alive_streams.add(name)
        if recorder.pumps_alive_streams:
            recorder.pumps_alive = True
        requested_signal = termination_signal() if termination_signal is not None else None
        if requested_signal is not None:
            _record_signal_finalization(recorder, requested_signal)
        try:
            recorder.write_meta()
        except Exception as exc:
            # Safe to continue: no meta marks the sidecar incomplete, so the parent
            # driver rejects it as authoritative and falls back to the CLI trace.
            print(f"evals.proxy: failed to write proxy_meta: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    # Detach from the CLI's process group so a harness timeout killpg on the
    # agent CLI does not SIGKILL this proxy (+ MCP child). After setsid we are
    # our own session/group leader; CLI group kill leaves us alive to see stdin
    # EOF, flush rows, and write proxy_meta within the shutdown deadline.
    try:
        os.setsid()
    except OSError:
        # Already a session leader, or platform forbids setsid — continue.
        pass
    args = parse_args(argv)
    evidence_fingerprints, evidence_targets, evidence_aggregates = consume_evidence_config(args.evidence_file)
    received_signal: list[int | None] = [None]

    def request_termination(signum: int, _frame: Any) -> None:
        # Do not finalize in the handler: it can interrupt code holding the
        # recorder lock. The relay loop observes this state and drains first.
        if received_signal[0] is None:
            received_signal[0] = signum

    previous_handlers: dict[int, Any] = {}
    for signum in (signal.SIGTERM, signal.SIGHUP):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, request_termination)
    try:
        returncode = run_proxy(
            list(args.command),
            proxy_session_log_path(Path(args.log)),
            record_result_payloads=bool(args.record_result_payloads),
            evidence_fingerprints=evidence_fingerprints,
            evidence_targets=evidence_targets,
            evidence_aggregates=evidence_aggregates,
            termination_signal=lambda: received_signal[0],
        )
    finally:
        for signum, previous_handler in previous_handlers.items():
            signal.signal(signum, previous_handler)

        signum = received_signal[0]
        if signum is not None:
            # Metadata and child cleanup are complete. Re-deliver with the
            # default disposition so subprocess/shell status encodes the signal.
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
