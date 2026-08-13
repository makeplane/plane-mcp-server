"""CLI subprocess lifecycle: process-group launch, timeout kill, bounded reap."""

from __future__ import annotations

import os
import signal
import subprocess
from typing import Any

# Bounded drain after process-group kill so communicate() never hangs forever
# when a grandchild still holds the pipe open.
_CLI_TIMEOUT_DRAIN_S = 2.0


def kill_process_group(proc: subprocess.Popen[Any]) -> bool:
    """SIGKILL the process group whose leader is ``proc``.

    With ``start_new_session=True``, ``pgid == proc.pid`` even after the leader
    has been reaped — call ``killpg(proc.pid, …)`` directly (never fall back to
    killing only the leader, which leaves grandchildren alive).

    Returns True if ``killpg`` delivered the signal; False if the group is
    already fully gone (``ProcessLookupError`` = success for cleanup, but the
    kill itself did not run).
    """
    if proc.pid is None:
        return False
    try:
        # Do NOT use getpgid: if the leader is already reaped, getpgid fails and
        # a proc.kill() fallback would recreate the original orphan bug.
        os.killpg(proc.pid, signal.SIGKILL)
        return True
    except ProcessLookupError:
        # No process left in the group — fully gone.
        return False


def _decode_pipe(data: str | bytes | None, *, text: bool) -> str | bytes | None:
    if data is None or not text or isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def _close_pipes_and_reap(proc: subprocess.Popen[Any], *, drain_s: float = _CLI_TIMEOUT_DRAIN_S) -> tuple[Any, Any]:
    """Bounded drain / close after a group kill. Never hangs unbounded."""
    try:
        return proc.communicate(timeout=drain_s)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        for stream in (proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        return None, None


def run_cli_subprocess(
    cmd: list[str],
    *,
    timeout: float | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    capture_output: bool = True,
    text: bool = True,
    **_kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    """Run a CLI in its own process group; kill the **whole group** on timeout/interrupt.

    Node wrappers (e.g. ``codex``) spawn native grandchildren. Plain
    ``subprocess.run`` on timeout only kills the parent; the grandchild keeps
    stdout open and ``communicate()`` hangs indefinitely. This runner:

    1. launches with ``start_new_session=True`` (new process group; pgid=pid);
    2. on timeout **or any other exception** (incl. KeyboardInterrupt),
       ``os.killpg(pid, SIGKILL)`` the group;
    3. drains pipes with a **bounded** second ``communicate`` (never unbounded).

    Raises ``subprocess.TimeoutExpired`` with attribute
    ``killed_process_group=True`` only when killpg actually delivered the signal.
    """
    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "start_new_session": True,
        "stdout": subprocess.PIPE if capture_output else None,
        "stderr": subprocess.PIPE if capture_output else None,
        "text": text,
    }
    if env is not None:
        popen_kwargs["env"] = env

    proc = subprocess.Popen(cmd, **popen_kwargs)  # noqa: S603 — eval harness launches user CLIs
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        killed = kill_process_group(proc)
        out, err = _close_pipes_and_reap(proc)
        if out is None and err is None:
            stdout = _decode_pipe(exc.stdout, text=text) or ("" if text else b"")
            stderr = _decode_pipe(exc.stderr, text=text) or ("" if text else b"")
        else:
            stdout, stderr = out, err
        te = subprocess.TimeoutExpired(
            cmd=cmd,
            timeout=timeout if timeout is not None else 0,
            output=stdout,
            stderr=stderr,
        )
        te.killed_process_group = killed  # type: ignore[attr-defined]
        raise te from None
    except BaseException:
        # KeyboardInterrupt / SystemExit / etc. — do not leave the CLI tree running.
        # start_new_session means SIGINT no longer reaches the group automatically.
        kill_process_group(proc)
        _close_pipes_and_reap(proc)
        raise

    return subprocess.CompletedProcess(cmd, proc.returncode if proc.returncode is not None else 0, stdout, stderr)


def note_timeout_kill(notes: list[str], exc: BaseException) -> None:
    """Append process-group kill note when killpg actually delivered the signal."""
    if getattr(exc, "killed_process_group", False):
        notes.append("timeout_killed_process_group")


__all__ = [
    "kill_process_group",
    "note_timeout_kill",
    "run_cli_subprocess",
]
