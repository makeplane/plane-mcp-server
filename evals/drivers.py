"""Agent-driver abstraction for the Plane MCP eval harness.

Drivers run one task against a tool surface and return a normalized
``AgentRun``. The default ``sdk`` driver preserves the historical Anthropic
SDK + in-process MCP client path. CLI drivers (``claude-cli``, ``codex-cli``)
spawn locally installed agent CLIs on the user's subscription — no Anthropic
API key required for those paths.

Real CLI surfaces (probed on this machine, 2026-08-12):

Claude Code (``claude`` v2.1.228):
  - ``-p`` / ``--print`` headless
  - ``--mcp-config <file|json>`` (repeatable; ``--strict-mcp-config``)
  - ``--output-format json|text|stream-json`` (print mode)
  - ``--max-turns <n>`` (print mode; *hidden* from ``--help`` but present)
  - ``--model <alias|id>``
  - ``--permission-mode`` choices: acceptEdits, auto, bypassPermissions,
    manual, dontAsk, plan
  - ``--dangerously-skip-permissions``, ``--allowedTools`` / ``--allowed-tools``
  - Transcript: ``~/.claude/projects/<cwd-with-/-as-->/<session_id>.jsonl``
    with ``assistant`` rows whose ``message.content`` holds ``tool_use`` blocks.
  - MCP tools surface as ``mcp__<server>__<tool>`` — strip for classification.

Codex (``codex exec``):
  - ``codex exec --json`` JSONL events on stdout
  - ``-c key=value`` / ``--config`` for config.toml overrides (incl. mcp_servers)
  - ``-m`` / ``--model``
  - Session rollouts: ``~/.codex/sessions/**/rollout-*.jsonl`` with
    ``response_item`` / ``function_call`` payloads (name + arguments JSON string)
  - Marked **experimental**; live runs are opt-in (metered quota).
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

REPO_ROOT = Path(__file__).resolve().parent.parent

# mcp__plane__list_work_items  → list_work_items
# mcp__plane-mcp-server__foo  → foo
_MCP_PREFIX_RE = re.compile(r"^mcp__[^_]+(?:_[^_]+)*__(.+)$")
# Alternate: mcp__server__tool with multi-segment server names
_MCP_PREFIX_RE2 = re.compile(r"^mcp__.+?__(.+)$")


def proxy_wrap_server_command(
    real_command: list[str],
    *,
    sidecar_path: Path,
    python_bin: str | None = None,
) -> list[str]:
    """Return ``[python, -m, evals.proxy, --log, sidecar, --, *real_command]``."""
    py = python_bin or sys.executable
    return [py, "-m", "evals.proxy", "--log", str(sidecar_path), "--", *real_command]


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
        calls.append(
            {
                "tool": str(tool),
                "args": row.get("args") if isinstance(row.get("args"), dict) else (row.get("args") or {}),
                "origin": "plane",
                "is_error": bool(row.get("is_error")),
                "result_chars": int(row.get("result_chars") or 0),
                "duration_ms": row.get("duration_ms"),
                "seq": row.get("seq"),
            }
        )

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


# Bounded drain after process-group kill so communicate() never hangs forever
# when a grandchild still holds the pipe open.
_CLI_TIMEOUT_DRAIN_S = 2.0


def _kill_process_group(proc: subprocess.Popen[Any]) -> bool:
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
        killed = _kill_process_group(proc)
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
        _kill_process_group(proc)
        _close_pipes_and_reap(proc)
        raise

    return subprocess.CompletedProcess(cmd, proc.returncode if proc.returncode is not None else 0, stdout, stderr)


def _note_timeout_kill(notes: list[str], exc: BaseException) -> None:
    """Append process-group kill note when killpg actually delivered the signal."""
    if getattr(exc, "killed_process_group", False):
        notes.append("timeout_killed_process_group")


@dataclass
class AgentRun:
    """Normalized result of one agent task execution."""

    # Plane MCP tools only for classification: {tool, args, origin='plane', raw_tool?}
    calls: list[dict[str, Any]]
    final_text: str
    usage: dict[str, Any] | None
    stopped_reason: str
    raw_ref: str | None = None
    # Client/harness built-ins (ToolSearch, Bash, …) — excluded from mispick metrics
    client_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    # Cache-aware run totals (CLI); do not put uncached-only input_tokens into cum_input_tokens
    usage_total: dict[str, Any] | None = None
    # Harness extras (optional; defaults keep SDK path simple)
    usage_scope: str = "run"  # 'run' | 'iteration'
    call_source: str = "unknown"  # 'json' | 'transcript' | 'stream' | 'sdk'
    hit_max_turns: bool = False
    wall_time_s: float = 0.0
    experimental: bool = False
    notes: list[str] = field(default_factory=list)


class AgentDriver(Protocol):
    """Pluggable agent backend for evals.run."""

    name: str

    def run_task(
        self,
        prompt: str,
        mcp_env: dict[str, str],
        model: str | None,
        max_turns: int,
        *,
        system: str | None = None,
        cwd: Path | None = None,
    ) -> AgentRun: ...


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def strip_mcp_prefix(name: str) -> str:
    """Strip Claude/Codex MCP tool name prefixes for classification.

    Examples:
      mcp__plane__list_work_items → list_work_items
      mcp__plane-mcp-server__find_work_items → find_work_items
    """
    if not name:
        return name
    m = _MCP_PREFIX_RE2.match(name)
    if m:
        return m.group(1)
    return name


def is_plane_mcp_tool(name: str) -> bool:
    """True when the raw tool name is from our Plane MCP server (pre-strip).

    Claude surfaces MCP tools as ``mcp__<server>__<tool>``. Our config registers
    the server as ``plane``, so names look like ``mcp__plane__find_work_items``.
    Built-ins (``ToolSearch``, ``Bash``, …) have no ``mcp__`` prefix.
    """
    if not name:
        return False
    # mcp__plane__tool  or  mcp__plane-foo__tool
    return name.startswith("mcp__plane__") or name.startswith("mcp__plane-")


def normalize_tool_call(name: str, args: Any) -> dict[str, Any]:
    """Tag a tool call as plane (classifiable) or client (excluded from mispicks)."""
    raw = str(name or "")
    if not isinstance(args, dict):
        args = {"_raw": args}
    if is_plane_mcp_tool(raw):
        return {
            "tool": strip_mcp_prefix(raw),
            "args": args,
            "origin": "plane",
            "raw_tool": raw,
        }
    return {
        "tool": raw,  # keep built-in name as-is (ToolSearch, Bash, …)
        "args": args,
        "origin": "client",
        "raw_tool": raw,
    }


def split_plane_and_client_calls(
    calls: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition tagged calls into plane vs client lists.

    Prefer explicit ``origin`` from ``normalize_tool_call``. Untagged calls
    (SDK path) default to plane so existing harness behavior is unchanged.
    """
    plane: list[dict[str, Any]] = []
    client: list[dict[str, Any]] = []
    for c in calls:
        origin = c.get("origin")
        if origin is None:
            raw = str(c.get("raw_tool") or c.get("tool") or "")
            if is_plane_mcp_tool(raw):
                origin = "plane"
            elif raw.startswith("mcp__"):
                origin = "client"  # other MCP server
            else:
                origin = "plane"  # bare name → assume plane (SDK)
        if origin == "client":
            client.append(c)
        else:
            plane.append(c)
    return plane, client


def normalize_claude_usage(data: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Parse Claude print-mode usage into (raw_usage, usage_total).

    Real envelope (probed 2026-08-12, ``claude -p --output-format json``)::

        {
          "usage": {
            "input_tokens": 10,              # uncached NEW input only — NOT run total
            "cache_creation_input_tokens": 17459,
            "cache_read_input_tokens": 18464,
            "output_tokens": 143,
            "iterations": [...],
            ...
          },
          "modelUsage": {
            "<model-id>": {
              "inputTokens": 10,
              "outputTokens": 143,
              "cacheReadInputTokens": 18464,
              "cacheCreationInputTokens": 17459,
              "costUSD": 0.037,
              ...
            }
          },
          "total_cost_usd": 0.037
        }

    ``usage.input_tokens`` alone is misleading for multi-turn cached runs (live
    rows showed 8–10 while cache_read was 180k+). We keep the split fields and
    compute an inclusive total under ``usage_total``; callers must **not** copy
    bare ``input_tokens`` into ``cum_input_tokens``.
    """
    usage = data.get("usage")
    if usage is not None and not isinstance(usage, dict):
        usage = None
    model_usage = data.get("modelUsage") or data.get("model_usage")
    if model_usage is not None and not isinstance(model_usage, dict):
        model_usage = None
    cost = data.get("total_cost_usd")
    if cost is None and isinstance(usage, dict):
        cost = usage.get("total_cost_usd")

    if usage is None and model_usage is None and cost is None:
        return None, None

    raw = dict(usage or {})
    if cost is not None:
        raw["total_cost_usd"] = cost
    if model_usage is not None:
        raw["modelUsage"] = model_usage

    # Prefer summing modelUsage (per-model run totals) when present
    sum_in = sum_out = sum_cr = sum_cc = sum_cost = 0.0
    used_model_usage = False
    if model_usage:
        for _mid, mu in model_usage.items():
            if not isinstance(mu, dict):
                continue
            used_model_usage = True
            sum_in += float(mu.get("inputTokens") or mu.get("input_tokens") or 0)
            sum_out += float(mu.get("outputTokens") or mu.get("output_tokens") or 0)
            sum_cr += float(mu.get("cacheReadInputTokens") or mu.get("cache_read_input_tokens") or 0)
            sum_cc += float(mu.get("cacheCreationInputTokens") or mu.get("cache_creation_input_tokens") or 0)
            sum_cost += float(mu.get("costUSD") or mu.get("cost_usd") or 0)

    if used_model_usage:
        uncached_in = int(sum_in)
        out_tok = int(sum_out)
        cache_read = int(sum_cr)
        cache_write = int(sum_cc)
        total_cost = float(sum_cost) if sum_cost else cost
    else:
        uncached_in = int(raw.get("input_tokens") or 0)
        out_tok = int(raw.get("output_tokens") or 0)
        cache_read = int(raw.get("cache_read_input_tokens") or 0)
        cache_write = int(raw.get("cache_creation_input_tokens") or 0)
        total_cost = cost

    usage_total: dict[str, Any] = {
        "input_tokens": uncached_in,  # uncached / new tokens only
        "output_tokens": out_tok,
        "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
        "total_input_tokens_including_cache": uncached_in + cache_read + cache_write,
        "total_cost_usd": total_cost,
        "modelUsage": model_usage,
        "source": "modelUsage" if used_model_usage else "usage",
    }
    return raw, usage_total


def _claude_project_dir(cwd: Path) -> Path:
    """Map a cwd to ``~/.claude/projects/<munged>`` (``/`` → ``-``)."""
    munged = str(cwd.resolve()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / munged


def parse_claude_json_result(payload: dict[str, Any] | str) -> dict[str, Any]:
    """Extract final text, usage, session id, num_turns from ``claude -p --output-format json``.

    The print-mode JSON envelope is a single object (``type=result``) with
    ``result``, ``session_id``, ``num_turns``, ``total_cost_usd``, ``usage``,
    and ``modelUsage``. Per-call tool detail is usually **absent** — callers
    should fall back to the session transcript.
    """
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object from claude, got {type(payload)}")

    data = payload

    final = data.get("result")
    if final is None:
        final = data.get("final_text") or data.get("text") or ""
    if not isinstance(final, str):
        final = json.dumps(final, default=str)

    usage, usage_total = normalize_claude_usage(data)

    session_id = data.get("session_id") or data.get("sessionId")
    num_turns = data.get("num_turns")
    if num_turns is None:
        num_turns = data.get("numTurns")
    is_error = bool(data.get("is_error") or data.get("isError"))
    subtype = data.get("subtype") or ""
    stop_reason = data.get("stop_reason") or data.get("terminal_reason") or ""

    # Tool calls rarely present in the result envelope; collect if present.
    calls: list[dict[str, Any]] = []
    for key in ("tool_calls", "tools", "calls"):
        raw = data.get(key)
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                name = item.get("name") or item.get("tool") or ""
                args = item.get("input") or item.get("arguments") or item.get("args") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"_raw": args}
                calls.append(normalize_tool_call(str(name), args))

    # Preserve Claude error subtypes (e.g. error_during_execution, error_max_turns).
    # is_error alone collapses to "error" and loses the subtype run.py uses for infra_cli.
    if is_error and subtype and str(subtype) not in ("success", ""):
        stopped = str(subtype)
    elif is_error:
        stopped = "error"
    else:
        stopped = str(stop_reason) if stop_reason else "end_turn"
        if subtype and subtype not in ("success", "") and stopped == "end_turn":
            stopped = str(subtype)

    plane_calls, client_calls = split_plane_and_client_calls(calls)

    return {
        "final_text": final,
        "usage": usage,
        "usage_total": usage_total,
        "session_id": session_id,
        "num_turns": int(num_turns) if num_turns is not None else None,
        "calls": plane_calls,
        "client_tool_calls": client_calls,
        "stopped_reason": stopped,
        "raw": data,
    }


def parse_claude_transcript_calls(transcript_path: Path) -> list[dict[str, Any]]:
    """Parse ``tool_use`` blocks from a Claude Code session JSONL transcript.

    Returns tagged calls (``origin`` plane|client). Use
    ``split_plane_and_client_calls`` before classification.
    """
    calls: list[dict[str, Any]] = []
    if not transcript_path.is_file():
        return calls
    with transcript_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = row.get("message") if isinstance(row, dict) else None
            if not isinstance(msg, dict):
                if row.get("type") == "assistant" and isinstance(row.get("content"), list):
                    content = row["content"]
                else:
                    continue
            else:
                content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue
                name = str(block.get("name") or "")
                args = block.get("input") or {}
                if not isinstance(args, dict):
                    args = {"_raw": args}
                calls.append(normalize_tool_call(name, args))
    return calls


def find_claude_transcript(session_id: str | None, cwd: Path) -> Path | None:
    """Locate ``~/.claude/projects/<munged-cwd>/<session_id>.jsonl``."""
    if not session_id:
        return None
    candidate = _claude_project_dir(cwd) / f"{session_id}.jsonl"
    if candidate.is_file():
        return candidate
    # Fallback: scan project dir for a file containing the session id
    proj = _claude_project_dir(cwd)
    if not proj.is_dir():
        return None
    direct = proj / f"{session_id}.jsonl"
    if direct.is_file():
        return direct
    for p in proj.glob("*.jsonl"):
        if session_id in p.name:
            return p
    return None


def _codex_parse_tool_args(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            return {"_raw": raw_args}
        return args if isinstance(args, dict) else {"_raw": args}
    if isinstance(raw_args, dict):
        return raw_args
    return {"_raw": raw_args}


def parse_codex_jsonl_events(lines: list[str] | str) -> dict[str, Any]:
    """Parse ``codex exec --json`` stdout (JSONL) for function_call + final text + usage.

    Supports both schemas:
    - **v0.147+ streamable**: ``thread.started`` / ``item.completed`` / ``turn.completed``
      (``thread_id`` matches rollout filename suffix).
    - **Legacy**: ``session_meta`` / ``response_item`` / ``event_msg`` payloads.
    New keys are tried first; legacy handling is retained.
    """
    if isinstance(lines, str):
        lines = lines.splitlines()
    calls: list[dict[str, Any]] = []
    final_parts: list[str] = []
    usage: dict[str, Any] | None = None
    stopped = "end_turn"
    session_id: str | None = None

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        rtype = row.get("type")

        # --- New schema (codex exec --json v0.147+): try first ---
        if rtype == "thread.started":
            session_id = row.get("thread_id") or session_id
            continue
        if rtype == "turn.started":
            continue
        if rtype == "item.completed":
            item = row.get("item") if isinstance(row.get("item"), dict) else {}
            itype = item.get("type")
            if itype == "agent_message":
                text = item.get("text")
                if text:
                    final_parts.append(str(text))
            elif itype in (
                "function_call",
                "tool_call",
                "mcp_tool_call",
                "command_execution",
                "file_change",
            ):
                # Proxy sidecar is primary for plane calls; harvest best-effort names.
                name = str(item.get("name") or item.get("tool") or item.get("command") or itype)
                args = item.get("arguments") or item.get("args") or item.get("input") or {}
                calls.append(normalize_tool_call(name, _codex_parse_tool_args(args)))
            continue
        if rtype == "turn.completed":
            u = row.get("usage") if isinstance(row.get("usage"), dict) else {}
            if u:
                usage = {
                    "input_tokens": u.get("input_tokens", 0) or 0,
                    "output_tokens": u.get("output_tokens", 0) or 0,
                    "cache_read_input_tokens": u.get("cached_input_tokens", 0) or 0,
                    "cache_creation_input_tokens": u.get("cache_write_input_tokens", 0) or 0,
                    "total_tokens": u.get("total_tokens"),
                }
            continue

        # --- Legacy schema ---
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}

        if rtype == "session_meta":
            session_id = payload.get("id") or session_id
            continue

        if rtype == "response_item":
            pt = payload.get("type")
            if pt == "function_call":
                name = str(payload.get("name") or "")
                calls.append(normalize_tool_call(name, _codex_parse_tool_args(payload.get("arguments") or "{}")))
            elif pt == "message":
                # Assistant final-ish content
                role = payload.get("role")
                content = payload.get("content")
                if role == "assistant" and isinstance(content, list):
                    for c in content:
                        if isinstance(c, dict) and c.get("type") in ("output_text", "text"):
                            t = c.get("text") or c.get("output_text")
                            if t:
                                final_parts.append(str(t))
            continue

        if rtype == "event_msg":
            pt = payload.get("type")
            if pt == "agent_message":
                msg = payload.get("message") or payload.get("text")
                if msg:
                    final_parts.append(str(msg))
            elif pt == "token_count":
                info = payload.get("info") or {}
                total = info.get("total_token_usage") or info.get("last_token_usage") or {}
                if isinstance(total, dict):
                    usage = {
                        "input_tokens": total.get("input_tokens", 0) or 0,
                        "output_tokens": total.get("output_tokens", 0) or 0,
                        "cache_read_input_tokens": total.get("cached_input_tokens", 0) or 0,
                        "cache_creation_input_tokens": total.get("cache_write_input_tokens", 0) or 0,
                        "total_tokens": total.get("total_tokens"),
                    }
            elif pt == "task_complete":
                stopped = "end_turn"
            elif pt == "turn_aborted":
                stopped = "aborted"
            continue

    plane_calls, client_calls = split_plane_and_client_calls(calls)
    return {
        "calls": plane_calls,
        "client_tool_calls": client_calls,
        "final_text": "\n".join(final_parts).strip(),
        "usage": usage,
        "stopped_reason": stopped,
        "session_id": session_id,
    }


def parse_codex_rollout_calls(rollout_path: Path) -> list[dict[str, Any]]:
    """Parse function_call records from a Codex session rollout JSONL (plane only)."""
    if not rollout_path.is_file():
        return []
    lines = rollout_path.read_text(encoding="utf-8").splitlines()
    return parse_codex_jsonl_events(lines)["calls"]


def find_codex_rollout(session_id: str | None, *, after_ts: float | None = None) -> Path | None:
    """Find a rollout JSONL under ``~/.codex/sessions`` matching *session_id* exactly.

    Matches filename containing the id (rollout filenames end with ``thread_id``)
    or a first-line ``session_meta`` / ``thread.started`` id field.

    **No newest-after-ts fallback**: under parallel runs that would pick another
    task's rollout and corrupt final_text. Callers should note
    ``codex_rollout_unmatched`` when this returns None.

    ``after_ts`` is accepted for API compatibility but ignored.
    """
    del after_ts  # intentionally unused — see docstring
    if not session_id:
        return None
    root = Path.home() / ".codex" / "sessions"
    if not root.is_dir():
        return None
    sid = str(session_id)
    for p in root.rglob("*.jsonl"):
        if sid in p.name:
            return p
    for p in root.rglob("rollout-*.jsonl"):
        try:
            with p.open(encoding="utf-8") as fh:
                first = fh.readline()
            row = json.loads(first)
        except Exception:
            continue
        # Legacy session_meta
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if payload.get("id") == sid:
            return p
        # New thread.started on first line (unusual but possible)
        if row.get("type") == "thread.started" and row.get("thread_id") == sid:
            return p
        if row.get("thread_id") == sid or row.get("session_id") == sid:
            return p
    return None


def write_claude_mcp_config(
    path: Path,
    *,
    command: str,
    args: list[str],
    env: dict[str, str],
    server_name: str = "plane",
) -> None:
    """Write a Claude-compatible mcp-config JSON file."""
    cfg = {
        "mcpServers": {
            server_name: {
                "command": command,
                "args": args,
                "env": env,
            }
        }
    }
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def write_codex_mcp_override_args(
    *,
    command: str,
    args: list[str],
    env: dict[str, str],
    server_name: str = "plane",
) -> list[str]:
    """Build ``codex exec -c ...`` overrides for a stdio MCP server.

    Codex stores MCP under ``[mcp_servers.<name>]`` with ``command``, ``args``,
    and ``env`` (see user config.toml). Overrides use dotted ``-c`` paths.
    """
    out: list[str] = [
        "-c",
        f"mcp_servers.{server_name}.command={json.dumps(command)}",
        "-c",
        f"mcp_servers.{server_name}.args={json.dumps(args)}",
    ]
    # env table — pass each key
    for k, v in env.items():
        out.extend(["-c", f"mcp_servers.{server_name}.env.{k}={json.dumps(v)}"])
    return out


# ---------------------------------------------------------------------------
# Claude CLI driver
# ---------------------------------------------------------------------------


class ClaudeCliDriver:
    """Run tasks via ``claude -p`` on the user's Claude Code subscription."""

    name = "claude-cli"

    def __init__(
        self,
        *,
        claude_bin: str = "claude",
        python_bin: str | None = None,
        permission_mode: str = "bypassPermissions",
        strict_mcp: bool = True,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        server_command: list[str] | None = None,
        use_proxy: bool = True,
    ) -> None:
        self.claude_bin = claude_bin
        self.python_bin = python_bin or sys.executable
        self.permission_mode = permission_mode
        self.strict_mcp = strict_mcp
        self._runner = runner or run_cli_subprocess
        # Full replacement for the MCP server launch (external surfaces under
        # benchmark): [command, *args]. None → this repo's `-m plane_mcp stdio`.
        self.server_command = list(server_command) if server_command else None
        self.use_proxy = use_proxy

    def run_task(
        self,
        prompt: str,
        mcp_env: dict[str, str],
        model: str | None,
        max_turns: int,
        *,
        system: str | None = None,
        cwd: Path | None = None,
    ) -> AgentRun:
        cwd = (cwd or REPO_ROOT).resolve()
        notes: list[str] = []
        t0 = time.perf_counter()

        with tempfile.TemporaryDirectory(prefix="plane-eval-claude-") as td:
            td_path = Path(td)
            mcp_cfg = td_path / "mcp.json"
            sidecar = td_path / "proxy-sidecar.jsonl"
            # Only pass Plane-related env into the MCP child (plus PATH/HOME if present).
            child_env = {k: v for k, v in mcp_env.items() if k.startswith("PLANE_") or k in ("PATH", "HOME")}
            if self.server_command:
                real_cmd = list(self.server_command)
            else:
                real_cmd = [self.python_bin, "-m", "plane_mcp", "stdio"]
            if self.use_proxy:
                wrapped = proxy_wrap_server_command(real_cmd, sidecar_path=sidecar, python_bin=self.python_bin)
                server_cmd, server_args = wrapped[0], wrapped[1:]
                child_env = ensure_proxy_pythonpath(child_env)
            else:
                server_cmd, server_args = real_cmd[0], real_cmd[1:]
            write_claude_mcp_config(
                mcp_cfg,
                command=server_cmd,
                args=server_args,
                env=child_env,
                server_name="plane",
            )

            cmd: list[str] = [
                self.claude_bin,
                "-p",
                "--output-format",
                "json",
                "--mcp-config",
                str(mcp_cfg),
                "--permission-mode",
                self.permission_mode,
                "--max-turns",
                str(max_turns),
            ]
            if self.strict_mcp:
                cmd.append("--strict-mcp-config")
            if model:
                cmd.extend(["--model", model])
            if system:
                cmd.extend(["--append-system-prompt", system])
            # Allow MCP tools from our server without interactive prompts
            # --allowedTools is variadic and would swallow the trailing prompt; use = form.
            cmd.append("--allowedTools=mcp__plane__*")
            cmd.append(prompt)

            timeout_s = max(120, max_turns * 60)
            try:
                proc = self._runner(
                    cmd,
                    cwd=str(cwd),
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                wall = time.perf_counter() - t0
                notes.append(f"timeout after {timeout_s}s")
                _note_timeout_kill(notes, exc)
                # Wait for proxy finalization before harvesting / temp dir teardown.
                calls: list[dict[str, Any]] = []
                client_calls: list[dict[str, Any]] = []
                call_source = "json"
                if self.use_proxy:
                    calls, client_calls, call_source = harvest_proxy_after_cli_timeout(
                        calls, client_calls, sidecar, notes
                    )
                return AgentRun(
                    calls=calls,
                    client_tool_calls=client_calls,
                    final_text="",
                    usage=None,
                    stopped_reason="timeout",
                    raw_ref=None,
                    usage_scope="run",
                    call_source=call_source,
                    hit_max_turns=False,
                    wall_time_s=round(wall, 3),
                    notes=notes,
                )

            wall = time.perf_counter() - t0
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""

            parsed: dict[str, Any] | None = None
            parse_err: str | None = None
            # JSON may be the whole stdout or the last JSON object line
            for candidate in (stdout.strip(), *(reversed(stdout.strip().splitlines()) if stdout else [])):
                if not candidate or not candidate.lstrip().startswith("{"):
                    continue
                try:
                    parsed = parse_claude_json_result(candidate)
                    break
                except (json.JSONDecodeError, ValueError, TypeError) as exc:
                    parse_err = str(exc)
                    continue

            if parsed is None:
                notes.append(f"json_parse_failed: {parse_err or 'no JSON object in stdout'}")
                if proc.returncode != 0:
                    notes.append(f"claude_exit={proc.returncode}")
                    if stderr.strip():
                        notes.append(stderr.strip()[:500])
                if self.use_proxy:
                    apply_proxy_sidecar([], [], sidecar, notes)
                detail = "; ".join(notes)
                raise RuntimeError(f"claude cli failed: {detail}")

            # Parseable JSON can still be a hard CLI failure (exit 1 + is_error subtype).
            if proc.returncode != 0:
                notes.append(f"claude_exit={proc.returncode}")
                if stderr.strip():
                    notes.append(stderr.strip()[:500])

            # JSON rarely embeds per-call tool detail — prefer transcript when present.
            calls = list(parsed.get("calls") or [])
            client_calls = list(parsed.get("client_tool_calls") or [])
            call_source = "json" if (calls or client_calls) else "json"
            session_id = parsed.get("session_id")
            transcript = find_claude_transcript(session_id, cwd)
            if transcript is not None:
                tagged = parse_claude_transcript_calls(transcript)
                t_plane, t_client = split_plane_and_client_calls(tagged)
                if t_plane or t_client:
                    calls, client_calls = t_plane, t_client
                    call_source = "transcript"
                    notes.append(f"calls_from_transcript:{transcript}")
            if not calls and not client_calls:
                notes.append("no_tool_calls_in_json_or_transcript")

            # Proxy sidecar (when enabled) replaces CLI-parsed plane calls.
            if self.use_proxy:
                calls, client_calls, proxy_src = apply_proxy_sidecar(calls, client_calls, sidecar, notes)
                if proxy_src == "proxy":
                    call_source = "proxy"

            num_turns = parsed.get("num_turns")
            hit_max = bool(num_turns is not None and int(num_turns) >= max_turns)
            stopped = parsed["stopped_reason"]
            if hit_max and stopped in ("end_turn", "completed", ""):
                stopped = "max_turns"

            raw_ref = str(transcript) if transcript else (f"session:{session_id}" if session_id else None)
            return AgentRun(
                calls=calls,
                client_tool_calls=client_calls,
                final_text=parsed["final_text"],
                usage=parsed.get("usage"),
                usage_total=parsed.get("usage_total"),
                stopped_reason=stopped,
                raw_ref=raw_ref,
                usage_scope="run",
                call_source=call_source,
                hit_max_turns=hit_max,
                wall_time_s=round(wall, 3),
                notes=notes,
            )


# ---------------------------------------------------------------------------
# Codex CLI driver (experimental — do not spend live quota from CI)
# ---------------------------------------------------------------------------


class CodexCliDriver:
    """Run tasks via ``codex exec`` (experimental; metered quota).

    Live invocation is supported for the interface, but the eval harness should
    only exercise this driver when the team explicitly opts in. Offline tests
    inject a fake runner and never touch the real binary.
    """

    name = "codex-cli"
    experimental = True

    def __init__(
        self,
        *,
        codex_bin: str = "codex",
        python_bin: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        allow_live: bool = False,
        server_command: list[str] | None = None,
        use_proxy: bool = True,
    ) -> None:
        self.codex_bin = codex_bin
        self.python_bin = python_bin or sys.executable
        self._runner = runner or run_cli_subprocess
        self.allow_live = allow_live
        self.server_command = list(server_command) if server_command else None
        self.use_proxy = use_proxy

    def run_task(
        self,
        prompt: str,
        mcp_env: dict[str, str],
        model: str | None,
        max_turns: int,
        *,
        system: str | None = None,
        cwd: Path | None = None,
    ) -> AgentRun:
        cwd = (cwd or REPO_ROOT).resolve()
        notes = ["experimental:codex-cli"]
        if self._runner is run_cli_subprocess and not self.allow_live:
            raise RuntimeError(
                "CodexCliDriver refuses live runs by default (metered weekly quota). "
                "Pass allow_live=True or inject a fake runner for tests."
            )

        child_env = {k: v for k, v in mcp_env.items() if k.startswith("PLANE_") or k in ("PATH", "HOME")}
        with tempfile.TemporaryDirectory(prefix="plane-eval-codex-") as td:
            td_path = Path(td)
            sidecar = td_path / "proxy-sidecar.jsonl"
            if self.server_command:
                real_cmd = list(self.server_command)
            else:
                real_cmd = [self.python_bin, "-m", "plane_mcp", "stdio"]
            if self.use_proxy:
                wrapped = proxy_wrap_server_command(real_cmd, sidecar_path=sidecar, python_bin=self.python_bin)
                server_cmd, server_args = wrapped[0], wrapped[1:]
                child_env = ensure_proxy_pythonpath(child_env)
            else:
                server_cmd, server_args = real_cmd[0], real_cmd[1:]
            mcp_args = write_codex_mcp_override_args(
                command=server_cmd,
                args=server_args,
                env=child_env,
                server_name="plane",
            )
            cmd: list[str] = [
                self.codex_bin,
                "exec",
                "--json",
                "--skip-git-repo-check",
                *mcp_args,
            ]
            if model:
                cmd.extend(["-m", model])
            full_prompt = prompt if not system else f"{system}\n\n{prompt}"
            cmd.append(full_prompt)

            t0 = time.perf_counter()
            timeout_s = max(120, max_turns * 60)
            try:
                proc = self._runner(
                    cmd,
                    cwd=str(cwd),
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                wall = time.perf_counter() - t0
                notes.append(f"timeout after {timeout_s}s")
                _note_timeout_kill(notes, exc)
                calls_to: list[dict[str, Any]] = []
                client_to: list[dict[str, Any]] = []
                call_source = "stream"
                if self.use_proxy:
                    calls_to, client_to, call_source = harvest_proxy_after_cli_timeout(
                        calls_to, client_to, sidecar, notes
                    )
                return AgentRun(
                    calls=calls_to,
                    client_tool_calls=client_to,
                    final_text="",
                    usage=None,
                    stopped_reason="timeout",
                    raw_ref=None,
                    usage_scope="run",
                    call_source=call_source,
                    hit_max_turns=False,
                    wall_time_s=round(wall, 3),
                    experimental=True,
                    notes=notes,
                )
            wall = time.perf_counter() - t0
            stdout = proc.stdout or ""
            parsed = parse_codex_jsonl_events(stdout)
            calls = list(parsed.get("calls") or [])
            client_calls = list(parsed.get("client_tool_calls") or [])
            call_source = "stream"
            session_id = parsed.get("session_id")

            # Exact-match rollout only — never steal another parallel task's file.
            need_rollout = (not calls and not client_calls) or not parsed.get("final_text")
            if need_rollout and session_id:
                rollout = find_codex_rollout(session_id)
                if rollout is not None:
                    full = parse_codex_jsonl_events(rollout.read_text(encoding="utf-8").splitlines())
                    if not calls and not client_calls:
                        calls = list(full.get("calls") or [])
                        client_calls = list(full.get("client_tool_calls") or [])
                        if calls or client_calls:
                            call_source = "transcript"
                            notes.append(f"calls_from_rollout:{rollout}")
                    if full.get("final_text") and not parsed.get("final_text"):
                        parsed["final_text"] = full["final_text"]
                        notes.append(f"final_text_from_rollout:{rollout}")
                    if full.get("usage") and not parsed.get("usage"):
                        parsed["usage"] = full["usage"]
                else:
                    notes.append("codex_rollout_unmatched")
            elif need_rollout and not session_id:
                notes.append("codex_rollout_unmatched")

            if self.use_proxy:
                calls, client_calls, proxy_src = apply_proxy_sidecar(calls, client_calls, sidecar, notes)
                if proxy_src == "proxy":
                    call_source = "proxy"

            if proc.returncode != 0:
                notes.append(f"codex_exit={proc.returncode}")

            usage = parsed.get("usage")
            usage_total = None
            if isinstance(usage, dict):
                usage_total = {
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
                    "total_input_tokens_including_cache": (
                        int(usage.get("input_tokens") or 0)
                        + int(usage.get("cache_read_input_tokens") or 0)
                        + int(usage.get("cache_creation_input_tokens") or 0)
                    ),
                    "source": "codex_token_count",
                }

            raw_ref = f"session:{session_id}" if session_id else None
            return AgentRun(
                calls=calls,
                client_tool_calls=client_calls,
                final_text=parsed.get("final_text") or "",
                usage=usage,
                usage_total=usage_total,
                stopped_reason=parsed.get("stopped_reason") or "end_turn",
                raw_ref=raw_ref,
                usage_scope="run",
                call_source=call_source,
                hit_max_turns=False,  # codex exec has no max-turns flag in --help
                wall_time_s=round(wall, 3),
                experimental=True,
                notes=notes,
            )


# ---------------------------------------------------------------------------
# Antigravity CLI (agy) — proxy-first
# ---------------------------------------------------------------------------


def write_antigravity_mcp_config(
    path: Path,
    *,
    command: str,
    args: list[str],
    env: dict[str, str],
    server_name: str = "plane",
) -> None:
    """Write ``mcpServers`` map JSON (Antigravity / agy mcp_config shape)."""
    cfg = {
        "mcpServers": {
            server_name: {
                "command": command,
                "args": args,
                "env": env,
            }
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def prepare_antigravity_fake_home(
    fake_home: Path,
    *,
    command: str,
    args: list[str],
    env: dict[str, str],
    real_home: Path | None = None,
) -> None:
    """Build an isolated HOME for agy with MCP config + shared auth artifacts.

    Writes mcp_config.json to BOTH documented locations (cheap; live probe
    should settle which path agy actually reads):
      - ~/.gemini/config/mcp_config.json
      - ~/.gemini/antigravity-cli/mcp_config.json

    Creates ``antigravity-cli`` as a **real directory** (never a symlink of the
    whole tree — that would write mcp_config and runtime logs into real user
    state). Auth artifacts (``antigravity-oauth-token``) are plain **copies** —
    never symlinks — so an in-place token refresh cannot write through into the
    real home. Staleness over a single eval run is negligible.
    """
    real_home = real_home or Path.home()
    gemini_root = fake_home / ".gemini"
    gemini_root.mkdir(parents=True, exist_ok=True)

    real_cli = real_home / ".gemini" / "antigravity-cli"
    fake_cli = gemini_root / "antigravity-cli"
    # Always a real directory — never symlink the whole tree.
    if fake_cli.is_symlink() or fake_cli.is_file():
        fake_cli.unlink()
    fake_cli.mkdir(parents=True, exist_ok=True)

    # Share auth via plain COPY only — never symlink (in-place token refresh
    # must not write through into the real home).
    if real_cli.is_dir():
        for name in ("antigravity-oauth-token",):
            src = real_cli / name
            dst = fake_cli / name
            if src.is_file() and not dst.exists():
                try:
                    dst.write_bytes(src.read_bytes())
                except OSError:
                    pass

    # Dual write as real files (not through any symlink).
    for rel in (
        Path(".gemini") / "config" / "mcp_config.json",
        Path(".gemini") / "antigravity-cli" / "mcp_config.json",
    ):
        write_antigravity_mcp_config(
            fake_home / rel,
            command=command,
            args=args,
            env=env,
            server_name="plane",
        )


class AntigravityCliDriver:
    """Run tasks via Google Antigravity CLI (``agy``).

    Probed flags (2026-08-12, ``agy --help``):
      - ``-p`` / ``--print`` headless single-prompt mode
      - ``--output-format`` text|json|stream-json
      - ``--model``, ``--dangerously-skip-permissions``
      - MCP via ``~/.gemini/config/mcp_config.json`` (``mcpServers`` map;
        stdio: command/args/env). No CLI flag for MCP config → HOME isolation.
      - No max-turns / turn-cap flag in help → ``hit_max_turns=False`` + note.

    Tool calls come from the recording proxy sidecar (protocol-layer), not
    agy stdout parsing.
    """

    name = "antigravity-cli"

    def __init__(
        self,
        *,
        agy_bin: str = "agy",
        python_bin: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        server_command: list[str] | None = None,
        use_proxy: bool = True,
    ) -> None:
        self.agy_bin = agy_bin
        self.python_bin = python_bin or sys.executable
        self._runner = runner or run_cli_subprocess
        self.server_command = list(server_command) if server_command else None
        self.use_proxy = use_proxy

    def run_task(
        self,
        prompt: str,
        mcp_env: dict[str, str],
        model: str | None,
        max_turns: int,
        *,
        system: str | None = None,
        cwd: Path | None = None,
    ) -> AgentRun:
        cwd = (cwd or REPO_ROOT).resolve()
        notes: list[str] = ["no_turn_cap"]
        t0 = time.perf_counter()
        child_env_plane = {k: v for k, v in mcp_env.items() if k.startswith("PLANE_") or k in ("PATH", "HOME")}

        with tempfile.TemporaryDirectory(prefix="plane-eval-antigravity-") as td:
            td_path = Path(td)
            sidecar = td_path / "proxy-sidecar.jsonl"
            fake_home = td_path / "home"
            if self.server_command:
                real_cmd = list(self.server_command)
            else:
                real_cmd = [self.python_bin, "-m", "plane_mcp", "stdio"]
            if self.use_proxy:
                wrapped = proxy_wrap_server_command(real_cmd, sidecar_path=sidecar, python_bin=self.python_bin)
                server_cmd, server_args = wrapped[0], wrapped[1:]
                child_env_plane = ensure_proxy_pythonpath(child_env_plane)
            else:
                server_cmd, server_args = real_cmd[0], real_cmd[1:]
            prepare_antigravity_fake_home(
                fake_home,
                command=server_cmd,
                args=server_args,
                env=child_env_plane,
            )

            full_prompt = prompt if not system else f"{system}\n\n{prompt}"
            cmd: list[str] = [
                self.agy_bin,
                "-p",
                "--output-format",
                "json",
                "--dangerously-skip-permissions",
            ]
            if model:
                cmd.extend(["--model", model])
            cmd.append(full_prompt)

            run_env = {**os.environ, "HOME": str(fake_home)}
            if "PATH" in child_env_plane:
                run_env["PATH"] = child_env_plane["PATH"]

            timeout_s = max(120, max_turns * 60)
            try:
                try:
                    proc = self._runner(
                        cmd,
                        cwd=str(cwd),
                        capture_output=True,
                        text=True,
                        timeout=timeout_s,
                        env=run_env,
                    )
                except TypeError:
                    # Some test runners reject ``env=``; retry without it.
                    # TimeoutExpired from this path must still hit the harvest below.
                    proc = self._runner(
                        cmd,
                        cwd=str(cwd),
                        capture_output=True,
                        text=True,
                        timeout=timeout_s,
                    )
            except subprocess.TimeoutExpired as exc:
                wall = time.perf_counter() - t0
                notes.append(f"timeout after {timeout_s}s")
                _note_timeout_kill(notes, exc)
                calls_to: list[dict[str, Any]] = []
                client_to: list[dict[str, Any]] = []
                call_source = "json"
                if self.use_proxy:
                    calls_to, client_to, call_source = harvest_proxy_after_cli_timeout(
                        calls_to, client_to, sidecar, notes
                    )
                return AgentRun(
                    calls=calls_to,
                    client_tool_calls=client_to,
                    final_text="",
                    usage=None,
                    stopped_reason="timeout",
                    usage_scope="run",
                    call_source=call_source,
                    wall_time_s=round(wall, 3),
                    notes=notes,
                )

            wall = time.perf_counter() - t0
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            calls: list[dict[str, Any]] = []
            client_calls: list[dict[str, Any]] = []
            call_source = "json"
            if self.use_proxy:
                calls, client_calls, call_source = apply_proxy_sidecar(calls, client_calls, sidecar, notes)
            if proc.returncode != 0:
                notes.append(f"agy_exit={proc.returncode}")
                if stderr.strip():
                    notes.append(stderr.strip()[:500])

            final_text = stdout.strip()
            try:
                if final_text.lstrip().startswith("{"):
                    blob = json.loads(final_text)
                    if isinstance(blob, dict):
                        final_text = str(blob.get("result") or blob.get("text") or blob.get("response") or final_text)
            except json.JSONDecodeError:
                pass

            return AgentRun(
                calls=calls,
                client_tool_calls=client_calls,
                final_text=final_text,
                usage=None,
                stopped_reason="error" if proc.returncode else "end_turn",
                usage_scope="run",
                call_source=call_source,
                hit_max_turns=False,
                wall_time_s=round(wall, 3),
                notes=notes,
            )


# ---------------------------------------------------------------------------
# OpenCode CLI — proxy-first
# ---------------------------------------------------------------------------


def write_opencode_mcp_config(
    path: Path,
    *,
    command: list[str],
    env: dict[str, str],
    server_name: str = "plane",
) -> None:
    """Write project ``opencode.json`` with a local MCP server entry.

    Schema (opencode.ai docs / probed binary strings, 2026-08-12)::

        {"mcp": {"plane": {"type": "local", "command": [...], "environment": {...}}}}
    """
    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            server_name: {
                "type": "local",
                "command": list(command),
                "environment": env,
                "enabled": True,
            }
        },
    }
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


class OpencodeCliDriver:
    """Run tasks via ``opencode run`` (proxy-first call recording).

    Probed flags (2026-08-12):
      - ``opencode run [message..]`` non-interactive
      - ``--format json|default``, ``-m/--model``
      - MCP via ``opencode.json`` ``mcp`` section (local: type/command/environment)
        written into the task cwd (or a temp project dir).
      - No turn-cap flag → ``hit_max_turns=False`` + note ``no_turn_cap``.
    """

    name = "opencode-cli"

    def __init__(
        self,
        *,
        opencode_bin: str = "opencode",
        python_bin: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        server_command: list[str] | None = None,
        use_proxy: bool = True,
    ) -> None:
        self.opencode_bin = opencode_bin
        self.python_bin = python_bin or sys.executable
        self._runner = runner or run_cli_subprocess
        self.server_command = list(server_command) if server_command else None
        self.use_proxy = use_proxy

    def run_task(
        self,
        prompt: str,
        mcp_env: dict[str, str],
        model: str | None,
        max_turns: int,
        *,
        system: str | None = None,
        cwd: Path | None = None,
    ) -> AgentRun:
        base_cwd = (cwd or REPO_ROOT).resolve()
        notes: list[str] = ["no_turn_cap"]
        t0 = time.perf_counter()
        child_env_plane = {k: v for k, v in mcp_env.items() if k.startswith("PLANE_") or k in ("PATH", "HOME")}

        with tempfile.TemporaryDirectory(prefix="plane-eval-opencode-", dir=str(base_cwd)) as td:
            # Project-local opencode.json so we do not pollute the user's global config.
            proj = Path(td)
            sidecar = proj / "proxy-sidecar.jsonl"
            if self.server_command:
                real_cmd = list(self.server_command)
            else:
                real_cmd = [self.python_bin, "-m", "plane_mcp", "stdio"]
            if self.use_proxy:
                launch = proxy_wrap_server_command(real_cmd, sidecar_path=sidecar, python_bin=self.python_bin)
                child_env_plane = ensure_proxy_pythonpath(child_env_plane)
            else:
                launch = real_cmd
            write_opencode_mcp_config(
                proj / "opencode.json",
                command=launch,
                env=child_env_plane,
                server_name="plane",
            )

            full_prompt = prompt if not system else f"{system}\n\n{prompt}"
            cmd: list[str] = [
                self.opencode_bin,
                "run",
                "--format",
                "json",
            ]
            if model:
                cmd.extend(["-m", model])
            cmd.append(full_prompt)

            timeout_s = max(120, max_turns * 60)
            try:
                proc = self._runner(
                    cmd,
                    cwd=str(proj),
                    capture_output=True,
                    text=True,
                    timeout=timeout_s,
                )
            except subprocess.TimeoutExpired as exc:
                wall = time.perf_counter() - t0
                notes.append(f"timeout after {timeout_s}s")
                _note_timeout_kill(notes, exc)
                calls_to: list[dict[str, Any]] = []
                client_to: list[dict[str, Any]] = []
                call_source = "json"
                if self.use_proxy:
                    calls_to, client_to, call_source = harvest_proxy_after_cli_timeout(
                        calls_to, client_to, sidecar, notes
                    )
                return AgentRun(
                    calls=calls_to,
                    client_tool_calls=client_to,
                    final_text="",
                    usage=None,
                    stopped_reason="timeout",
                    usage_scope="run",
                    call_source=call_source,
                    wall_time_s=round(wall, 3),
                    notes=notes,
                )

            wall = time.perf_counter() - t0
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            calls: list[dict[str, Any]] = []
            client_calls: list[dict[str, Any]] = []
            call_source = "json"
            if self.use_proxy:
                calls, client_calls, call_source = apply_proxy_sidecar(calls, client_calls, sidecar, notes)
            if proc.returncode != 0:
                notes.append(f"opencode_exit={proc.returncode}")
                if stderr.strip():
                    notes.append(stderr.strip()[:500])

            final_text = stdout.strip()
            # JSONL events: concatenate text-ish fields best-effort.
            if final_text and "\n" in final_text:
                parts: list[str] = []
                for line in final_text.splitlines():
                    line = line.strip()
                    if not line.startswith("{"):
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict):
                        for key in ("text", "message", "part", "delta"):
                            v = row.get(key)
                            if isinstance(v, str) and v.strip():
                                parts.append(v)
                        if row.get("type") in ("text", "message") and isinstance(row.get("content"), str):
                            parts.append(row["content"])
                if parts:
                    final_text = "\n".join(parts)

            return AgentRun(
                calls=calls,
                client_tool_calls=client_calls,
                final_text=final_text,
                usage=None,
                stopped_reason="error" if proc.returncode else "end_turn",
                usage_scope="run",
                call_source=call_source,
                hit_max_turns=False,
                wall_time_s=round(wall, 3),
                notes=notes,
            )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

KNOWN_DRIVERS = frozenset({"sdk", "claude-cli", "codex-cli", "antigravity-cli", "opencode-cli"})


def get_driver(name: str, **kwargs: Any) -> AgentDriver | None:
    """Return a driver instance, or None for the in-process ``sdk`` path."""
    key = (name or "sdk").strip().lower()
    if key == "sdk":
        return None  # handled inline in evals.run
    if key == "claude-cli":
        return ClaudeCliDriver(**kwargs)
    if key == "codex-cli":
        return CodexCliDriver(**kwargs)
    if key == "antigravity-cli":
        return AntigravityCliDriver(**kwargs)
    if key == "opencode-cli":
        return OpencodeCliDriver(**kwargs)
    raise ValueError(f"unknown driver {name!r}; expected one of {sorted(KNOWN_DRIVERS)}")


def agent_run_to_harness_dict(
    run: AgentRun,
    *,
    optimal: set[str],
    alternate: set[str],
    classify: Callable[[str, set[str], set[str]], str],
    skip_result_tokens: bool = True,
) -> dict[str, Any]:
    """Map an ``AgentRun`` onto the dict shape expected by ``run_live`` rows.

    Only **plane** MCP tools are classified and counted in ``num_calls`` /
    mispick metrics. Client built-ins (``ToolSearch``, …) go to
    ``client_tool_calls`` and are excluded.

    CLI drivers never populate ``cum_input_tokens`` from bare
    ``usage.input_tokens`` (that field is uncached-only under Claude Code and
    misreads multi-turn cached runs as ~10 tokens). Use ``usage_total`` instead.
    """
    # Re-split in case callers passed a mixed list
    plane_src, client_extra = split_plane_and_client_calls(list(run.calls))
    client_src = list(run.client_tool_calls) + client_extra

    calls: list[dict[str, Any]] = []
    for c in plane_src:
        tool = c.get("tool") or ""
        args = c.get("args") or {}
        try:
            args_chars = len(json.dumps(args, default=str))
        except Exception:
            args_chars = len(str(args))
        rec: dict[str, Any] = {
            "tool": tool,
            "class": classify(str(tool), optimal, alternate),
            "args_chars": args_chars,
            "result_tokens": None,
            "result_chars": int(c["result_chars"]) if c.get("result_chars") is not None else 0,
            "result_kind": "text",
            "is_error": bool(c.get("is_error")),
        }
        if c.get("duration_ms") is not None:
            rec["duration_ms"] = c["duration_ms"]
        # Action-dispatch surfaces: the action arg IS the second half of the
        # tool choice — keep it (args content is otherwise not persisted).
        if isinstance(args, dict) and isinstance(args.get("action"), str):
            rec["action"] = args["action"]
        if skip_result_tokens:
            rec["result_tokens_skipped"] = "no API key / CLI driver has no count_tokens"
        calls.append(rec)

    client_tool_calls: list[dict[str, Any]] = []
    for c in client_src:
        tool = c.get("tool") or c.get("raw_tool") or ""
        args = c.get("args") or {}
        try:
            args_chars = len(json.dumps(args, default=str))
        except Exception:
            args_chars = len(str(args))
        client_tool_calls.append(
            {
                "tool": tool,
                "args_chars": args_chars,
                "raw_tool": c.get("raw_tool") or tool,
            }
        )

    stop_reason = run.stopped_reason
    hit_max = run.hit_max_turns
    if hit_max:
        stop_reason = stop_reason if stop_reason not in ("end_turn", "completed", None, "") else "max_turns"

    errored = sum(1 for c in calls if c.get("is_error"))
    alternate_n = sum(1 for c in calls if c["class"] == "alternate")
    out_of_set_n = sum(1 for c in calls if c["class"] == "out_of_set")

    # CLI path: never write misleading cum_input_tokens from uncached-only field
    is_cli = run.call_source in ("json", "transcript", "stream", "proxy") or run.usage_scope == "run"
    usage_total = run.usage_total
    if usage_total is None and isinstance(run.usage, dict) and is_cli:
        # Best-effort rebuild if driver forgot usage_total
        _, usage_total = normalize_claude_usage({"usage": run.usage, "modelUsage": run.usage.get("modelUsage")})

    if is_cli and skip_result_tokens:
        cum_input: int | None = None
        cum_reason: str | None = (
            "CLI driver: Claude usage.input_tokens is uncached-only; "
            "see usage_total (cache_read/cache_creation/output/cost) for run accounting"
        )
        usage_per_iteration: list[dict[str, int]] = []
    else:
        cum_input = 0
        cum_reason = None
        usage_per_iteration = []
        if run.usage and run.usage_scope == "iteration":
            pass  # SDK fills this separately

    return {
        "final_text": run.final_text,
        "calls": calls,
        "num_calls": len(calls),
        "client_tool_calls": client_tool_calls,
        "client_tool_call_count": len(client_tool_calls),
        "errored_calls": errored,
        "alternate_calls": alternate_n,
        "out_of_set_calls": out_of_set_n,
        "total_result_tokens": 0
        if skip_result_tokens
        else sum(c["result_tokens"] or 0 for c in calls if c.get("result_tokens") is not None),
        "usage_per_iteration": usage_per_iteration,
        "cum_input_tokens": cum_input,
        "cum_input_tokens_reason": cum_reason,
        "wall_time_s": run.wall_time_s,
        "stop_reason": stop_reason,
        "hit_max_iterations": hit_max,
        "result_pair_mismatch": False,
        "token_count_failures": 0,
        "usage_scope": run.usage_scope,
        "call_source": run.call_source,
        "driver_raw_ref": run.raw_ref,
        "driver_notes": list(run.notes),
        "result_tokens_skipped_reason": (
            "CLI driver: count_tokens requires Anthropic API key; skipped" if skip_result_tokens else None
        ),
        "usage": run.usage,
        "usage_total": usage_total,
    }
