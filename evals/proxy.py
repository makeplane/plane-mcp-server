"""Stdio MCP recording proxy — byte-faithful JSON-RPC relay with sidecar call log.

Usage:
  python -m evals.proxy --log SIDECAR.jsonl [--record-result-payloads] -- <target server command...>

Spawns the target as a child, relays parent stdin → child stdin and child
stdout → parent stdout as raw bytes (byte-faithful; does not re-serialize).
Parses complete newline-delimited JSON lines from a *copy* of each direction
to record ``tools/call`` request/response pairs into the sidecar JSONL.

I/O uses ``os.read`` on raw fds + per-direction bytearray buffers — never
``select`` + buffered ``readline`` (partial lines would hang; buffered
prefetch would stall multi-line clients).

Child stderr is forwarded to our stderr. Exit code matches the child
(negative/signal codes map to conventional 128+signum).
"""

from __future__ import annotations

import argparse
import json
import os
import select
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

# Single post-EOF / child-exit deadline for the whole shutdown sequence.
SHUTDOWN_DEADLINE_S = 10.0
READ_CHUNK = 65536

# Repo root for PYTHONPATH scrubbing (parent of evals/). Deliberately computed
# here rather than imported from ``evals``: this module runs inside the MCP
# server's process tree with the repo scrubbed off PYTHONPATH, so it cannot
# import its own package.
REPO_ROOT = Path(__file__).resolve().parent.parent


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

    def __init__(self, log_path: Path, *, record_result_payloads: bool = False) -> None:
        self.log_path = log_path
        self.record_result_payloads = record_result_payloads
        self._lock = threading.Lock()
        self._pending: dict[Any, dict[str, Any]] = {}
        self._seq = 0
        self.relayed_lines = 0
        self.unparsed_lines = 0
        self.unmatched_responses = 0
        self.notifications = 0
        self.server_requests = 0
        self.child_killed = False
        self.pumps_alive = False
        self.finalized = False
        # Post-finalize append attempts (not written; for tests / diagnostics).
        self.post_finalize_appends = 0
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")

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

    def note_unparsed(self) -> None:
        with self._lock:
            self.unparsed_lines += 1
            self.relayed_lines += 1

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
        if method != "tools/call":
            return
        params = obj.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        name = params.get("name") or ""
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        req_id = obj.get("id")
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
        if self.record_result_payloads:
            row["result_text"] = result_text
        self._append(row)

    def write_meta(self) -> None:
        """Write proxy_meta as the last row and seal the sidecar (atomic under lock)."""
        with self._lock:
            if self.finalized:
                self.post_finalize_appends += 1
                return
            row = {
                "row_type": "proxy_meta",
                "relayed_lines": self.relayed_lines,
                "unparsed_lines": self.unparsed_lines,
                "unmatched_responses": self.unmatched_responses,
                "notifications": self.notifications,
                "server_requests": self.server_requests,
                "pending_left": len(self._pending),
                "child_killed": self.child_killed,
                "pumps_alive": self.pumps_alive,
            }
            line = json.dumps(row, default=str, ensure_ascii=False) + "\n"
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            self.finalized = True


def try_parse_json_line(line: bytes) -> dict[str, Any] | None:
    """Parse a JSON object line; return None on failure (never raises)."""
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
    return obj if isinstance(obj, dict) else None


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
            obj = try_parse_json_line(line)
            if obj is None:
                recorder.note_unparsed()
            else:
                recorder.note_relayed()
                try:
                    if is_client:
                        recorder.on_client_message(obj)
                    else:
                        recorder.on_server_message(obj)
                except Exception:
                    pass
        # Forward only after recording so the opposite endpoint cannot race.
        write_all_fd(forward_fd, line)


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
                recorder.note_unparsed()
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


def run_proxy(
    command: list[str],
    log_path: Path,
    *,
    record_result_payloads: bool = False,
) -> int:
    """Spawn ``command`` as the real MCP server and relay with recording.

    Returns the child's exit code (or 1 on spawn failure). Guarantees
    ``proxy_meta`` is the last sidecar row and the child is reaped even on
    crash paths. Pump threads are daemon so a blocked write cannot hold the
    process past the shutdown deadline.
    """
    recorder = SidecarRecorder(log_path, record_result_payloads=record_result_payloads)
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
        while child.poll() is None and not stdin_done.is_set():
            time.sleep(0.05)

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

        pumps_still = any(t is not None and t.is_alive() for t in (t_in, t_out, t_err)) or not all(
            e.is_set() if e is not None else True for e in (stdin_done, stdout_done, stderr_done)
        )
        recorder.pumps_alive = pumps_still
        return map_child_returncode(child.returncode)
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
        if t_out is not None or t_err is not None or t_in is not None:
            still = any(t is not None and t.is_alive() for t in (t_in, t_out, t_err))
            if still:
                recorder.pumps_alive = True
        try:
            recorder.write_meta()
        except Exception as exc:
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
    return run_proxy(
        list(args.command),
        Path(args.log),
        record_result_payloads=bool(args.record_result_payloads),
    )


if __name__ == "__main__":
    raise SystemExit(main())
