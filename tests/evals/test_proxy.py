"""Offline eval tests for proxy."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from evals.drivers import (
    apply_proxy_sidecar,
    ensure_proxy_pythonpath,
    load_proxy_sidecar,
    load_proxy_sidecar_calls,
)
from evals.proxy import (
    SHUTDOWN_DEADLINE_S,
    SidecarRecorder,
    map_child_returncode,
    process_buffer_lines,
    reap_timeout,
    scrub_child_pythonpath,
    write_all_fd,
)
from evals.proxy import main as proxy_main

REPO = Path(__file__).resolve().parents[2]

FAKE_SERVER = textwrap.dedent(
    r"""
    import json, sys

    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            sys.stdout.write(raw + "\n")
            sys.stdout.flush()
            continue
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            send({
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "serverInfo": {"name": "fake"},
                },
            })
        elif method == "tools/list":
            send({
                "jsonrpc": "2.0",
                "id": mid,
                "result": {"tools": [{"name": "list_work_items", "inputSchema": {}}]},
            })
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            if name == "boom":
                send({
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {
                        "content": [{"type": "text", "text": "fail"}],
                        "isError": True,
                    },
                })
            else:
                body = f"ok:{name}:{json.dumps(args, sort_keys=True)}"
                send({
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {
                        "content": [{"type": "text", "text": body}],
                        "isError": False,
                    },
                })
        elif method and mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
    sys.exit(7)
    """
).lstrip()


def _write_fake_server(path: Path) -> Path:
    path.write_text(FAKE_SERVER, encoding="utf-8")
    return path


def test_proxy_records_tools_call_and_exit_code(tmp_path: Path):
    server = _write_fake_server(tmp_path / "fake_server.py")
    sidecar = tmp_path / "side.jsonl"
    cmd = [
        sys.executable,
        "-m",
        "evals.proxy",
        "--log",
        str(sidecar),
        "--",
        sys.executable,
        str(server),
    ]
    # Drive the proxy: initialize, tools/call ok, tools/call error, unparsed, then close.
    client_in = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + "\n"
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "list_work_items", "arguments": {"project": "P"}},
            }
        )
        + "\n"
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "boom", "arguments": {}},
            }
        )
        + "\n"
        + "NOT_JSON_LINE\n"
    )
    proc = subprocess.run(
        cmd,
        input=client_in.encode("utf-8"),
        capture_output=True,
        cwd=str(REPO),
        timeout=15,
    )
    assert proc.returncode == 7  # child exit propagated
    # Byte-faithful: unparsed line and JSON responses appear on stdout.
    out = proc.stdout.decode("utf-8", errors="replace")
    assert "NOT_JSON_LINE" in out
    assert "list_work_items" in out or "ok:list_work_items" in out

    rows = [json.loads(ln) for ln in sidecar.read_text(encoding="utf-8").splitlines() if ln.strip()]
    call_rows = [r for r in rows if r.get("row_type") != "proxy_meta"]
    meta = next(r for r in rows if r.get("row_type") == "proxy_meta")
    assert len(call_rows) == 2
    assert call_rows[0]["tool"] == "list_work_items"
    assert call_rows[0]["args"] == {"project": "P"}
    assert call_rows[0]["is_error"] is False
    assert call_rows[0]["result_chars"] > 0
    assert call_rows[0]["seq"] == 1
    assert call_rows[1]["tool"] == "boom"
    assert call_rows[1]["is_error"] is True
    assert meta["unparsed_lines"] >= 1
    assert meta["relayed_lines"] >= 3


def test_proxy_byte_faithful_child_receives_exact_bytes(tmp_path: Path):
    """Child sees the exact request bytes the client sent (no re-serialization)."""
    received = tmp_path / "received.bin"
    echo_server = tmp_path / "echo_server.py"
    echo_server.write_text(
        textwrap.dedent(
            f"""
            import sys
            data = sys.stdin.buffer.read()
            open({str(received)!r}, "wb").write(data)
            # Still answer initialize-ish so proxy drains cleanly
            for line in data.splitlines(keepends=True):
                if not line.strip():
                    continue
                try:
                    import json
                    msg = json.loads(line)
                except Exception:
                    sys.stdout.buffer.write(line)
                    sys.stdout.buffer.flush()
                    continue
                if msg.get("id") is not None:
                    sys.stdout.buffer.write(
                        (json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": {{}}}}) + "\\n").encode()
                    )
                    sys.stdout.buffer.flush()
            """
        ),
        encoding="utf-8",
    )
    sidecar = tmp_path / "s.jsonl"
    # Deliberately non-canonical JSON spacing — re-serialization would change it.
    payload = b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{  "x":1}}\n'
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.proxy",
            "--log",
            str(sidecar),
            "--",
            sys.executable,
            str(echo_server),
        ],
        input=payload,
        capture_output=True,
        cwd=str(REPO),
        timeout=10,
    )
    assert proc.returncode == 0
    assert received.read_bytes() == payload


def test_sidecar_recorder_unit(tmp_path: Path):
    rec = SidecarRecorder(tmp_path / "a.jsonl")
    rec.on_client_message(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "t", "arguments": {"a": 1}},
        }
    )
    rec.on_server_message({"jsonrpc": "2.0", "id": 9, "result": {"content": [], "isError": False}})
    rec.write_meta()
    calls = load_proxy_sidecar_calls(tmp_path / "a.jsonl")
    raw_rows = [json.loads(line) for line in (tmp_path / "a.jsonl").read_text().splitlines()]
    raw_call = next(row for row in raw_rows if row.get("row_type") != "proxy_meta")
    assert len(calls) == 1
    assert calls[0]["tool"] == "t"
    assert calls[0]["args"] == {"a": 1}
    assert calls[0]["origin"] == "plane"
    assert "result_text" not in calls[0]
    assert "result_text" not in raw_call
    assert rec.finalized is True


def test_sidecar_result_payload_round_trips_only_when_enabled(tmp_path: Path):
    path = tmp_path / "payload.jsonl"
    rec = SidecarRecorder(path, record_result_payloads=True)
    rec.on_client_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "find_work_items", "arguments": {}},
        }
    )
    result = {"content": [{"type": "text", "text": "workspace result"}], "isError": False}
    rec.on_server_message({"jsonrpc": "2.0", "id": 3, "result": result})
    rec.write_meta()

    expected_text = json.dumps(result, default=str, ensure_ascii=False)
    raw_call = next(
        row
        for row in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
        if row.get("row_type") != "proxy_meta"
    )
    assert raw_call["result_text"] == expected_text
    calls = load_proxy_sidecar_calls(path)
    assert calls[0]["result_text"] == expected_text
    assert calls[0]["result_chars"] == len(expected_text)


def test_append_after_finalize_is_dropped(tmp_path: Path):
    """Once write_meta seals the sidecar, further row appends no-op (meta stays last)."""
    rec = SidecarRecorder(tmp_path / "fin.jsonl")
    rec.on_client_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "before", "arguments": {}},
        }
    )
    rec.on_server_message({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    rec.write_meta()
    assert rec.finalized is True
    assert rec.post_finalize_appends == 0

    # Late pump activity after meta — must not write another call row.
    rec.on_client_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "after", "arguments": {}},
        }
    )
    rec.on_server_message({"jsonrpc": "2.0", "id": 2, "result": {"ok": True}})
    rec._append({"tool": "ghost", "args": {}, "seq": 99})  # noqa: SLF001
    rec.write_meta()  # second meta attempt also dropped

    assert rec.post_finalize_appends >= 2
    text = (tmp_path / "fin.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    assert rows[-1].get("row_type") == "proxy_meta"
    call_tools = [r["tool"] for r in rows if r.get("row_type") != "proxy_meta"]
    assert call_tools == ["before"]
    assert "after" not in call_tools
    assert "ghost" not in call_tools
    assert text.count("proxy_meta") == 1


def test_pumps_alive_meta_classified_incomplete(tmp_path: Path):
    """proxy_meta with pumps_alive=true is incomplete (same as pending_left>0)."""
    p = tmp_path / "s.jsonl"
    rows = [
        {
            "tool": "from_proxy",
            "args": {},
            "is_error": False,
            "result_chars": 1,
            "duration_ms": 1,
            "seq": 1,
        },
        {
            "row_type": "proxy_meta",
            "relayed_lines": 1,
            "unparsed_lines": 0,
            "unmatched_responses": 0,
            "notifications": 0,
            "pending_left": 0,
            "child_killed": False,
            "pumps_alive": True,
        },
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    calls, status = load_proxy_sidecar(p)
    assert status["state"] == "incomplete"
    assert status.get("pumps_alive") is True
    assert len(calls) == 1

    cli = [
        {"tool": "c1", "args": {}, "origin": "plane"},
        {"tool": "c2", "args": {}, "origin": "plane"},
    ]
    notes: list[str] = []
    out, _client, src = apply_proxy_sidecar(cli, [], p, notes)
    assert src != "proxy"
    assert [c["tool"] for c in out] == ["c1", "c2"]
    assert any("proxy_sidecar_incomplete" in n and "pumps_alive" in n for n in notes)
    assert any("deferred_to_cli" in n for n in notes)


def test_reap_timeout_floor_when_deadline_exhausted():
    """Kill/reap waits use remaining budget with a non-zero floor."""
    past = __import__("time").monotonic() - 10.0
    assert reap_timeout(past, floor=0.1) == 0.1
    assert reap_timeout(None, floor=0.1) == 0.1
    future = __import__("time").monotonic() + 5.0
    assert reap_timeout(future, floor=0.1) >= 4.0


def test_proxy_main_requires_command():
    with pytest.raises(SystemExit):
        proxy_main(["--log", "/tmp/x.jsonl"])


def test_server_initiated_request_does_not_pop_pending(tmp_path: Path):
    """Server message with method+id must not complete a tools/call pending slot."""
    rec = SidecarRecorder(tmp_path / "s.jsonl")
    rec.on_client_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "list_work_items", "arguments": {}},
        }
    )
    # Server-initiated request reusing id=1 (roots/list style).
    rec.on_server_message({"jsonrpc": "2.0", "id": 1, "method": "roots/list", "params": {}})
    assert rec.server_requests == 1
    # Real response for the tools/call should still match.
    rec.on_server_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": "ok"}], "isError": False},
        }
    )
    rec.write_meta()
    calls = load_proxy_sidecar_calls(tmp_path / "s.jsonl")
    assert len(calls) == 1
    assert calls[0]["tool"] == "list_work_items"
    assert calls[0]["is_error"] is False


def test_client_response_to_server_request_ignored(tmp_path: Path):
    rec = SidecarRecorder(tmp_path / "s.jsonl")
    # Client answers a server request — no method, has id.
    rec.on_client_message({"jsonrpc": "2.0", "id": 99, "result": {"roots": []}})
    assert rec._pending == {}  # noqa: SLF001 — intentional: no pending opened
    rec.write_meta()
    assert load_proxy_sidecar_calls(tmp_path / "s.jsonl") == []


def test_map_child_returncode_signal():
    assert map_child_returncode(0) == 0
    assert map_child_returncode(1) == 1
    assert map_child_returncode(-9) == 128 + 9
    assert map_child_returncode(-15) == 128 + 15
    assert map_child_returncode(None) == 1


def test_write_all_fd_loops_on_short_writes(tmp_path: Path):
    """write_all_fd must loop until all bytes are written (simulate via pipe)."""
    import os

    r, w = os.pipe()
    payload = b"abcdefghijklmnopqrstuvwxyz" * 100
    # Write in parent; read in same process after.
    write_all_fd(w, payload)
    os.close(w)
    got = b""
    while True:
        chunk = os.read(r, 64)
        if not chunk:
            break
        got += chunk
    os.close(r)
    assert got == payload


def test_proxy_exits_when_child_dies_first(tmp_path: Path):
    """Child exits while parent stdin is still open — proxy must not hang."""
    server = tmp_path / "die_soon.py"
    server.write_text(
        textwrap.dedent(
            """
            import sys, time
            # Emit nothing and exit quickly; leave proxy client stdin open.
            time.sleep(0.15)
            sys.exit(3)
            """
        ),
        encoding="utf-8",
    )
    sidecar = tmp_path / "side.jsonl"
    t0 = __import__("time").monotonic()
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "evals.proxy",
            "--log",
            str(sidecar),
            "--",
            sys.executable,
            str(server),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(REPO),
    )
    try:
        # Keep stdin open (do not close) so the stdin pump blocks on readline;
        # the proxy must still notice child death and exit.
        deadline = SHUTDOWN_DEADLINE_S + 5.0
        try:
            rc = proc.wait(timeout=deadline)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            pytest.fail(f"proxy hung >{deadline}s after child exit")
        elapsed = __import__("time").monotonic() - t0
        # Must finish well under the hang window (not wait the full drain).
        assert elapsed < deadline
        # Child's exit code (3) should propagate; tolerate signal map if the
        # runtime reaps oddly, but meta must still be present.
        assert rc in (3, 128 + 3) or rc == 3
        assert sidecar.is_file()
        text = sidecar.read_text(encoding="utf-8")
        assert "proxy_meta" in text
        # Prefer exact child code when available
        if rc not in (3, 128 + 3):
            # At least ensure we did not hang; surface stderr for diagnosis.
            err = (proc.stderr.read() if proc.stderr else b"").decode()
            assert "proxy_meta" in text, f"rc={rc} stderr={err!r}"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if proc.stdin:
            try:
                proc.stdin.close()
            except Exception:
                pass


def test_proxy_from_foreign_cwd_with_pythonpath(tmp_path: Path):
    """proxy + plane path must resolve when cwd is a temp dir (OpenCode case)."""
    server = tmp_path / "echo_once.py"
    server.write_text(
        textwrap.dedent(
            """
            import json, sys
            line = sys.stdin.readline()
            msg = json.loads(line)
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": {"content": [], "isError": False},
            }) + "\\n")
            sys.stdout.flush()
            """
        ),
        encoding="utf-8",
    )
    sidecar = tmp_path / "side.jsonl"
    foreign = tmp_path / "foreign_cwd"
    foreign.mkdir()
    env = ensure_proxy_pythonpath(dict(**{k: v for k, v in __import__("os").environ.items()}))
    # Drop any ambient PYTHONPATH pollution by putting repo first.
    assert str(REPO) in env["PYTHONPATH"].split(__import__("os").pathsep)
    client_in = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "ping", "arguments": {}},
            }
        )
        + "\n"
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.proxy",
            "--log",
            str(sidecar),
            "--",
            sys.executable,
            str(server),
        ],
        input=client_in.encode(),
        capture_output=True,
        cwd=str(foreign),  # foreign cwd — must still import evals.proxy
        env=env,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    calls = load_proxy_sidecar_calls(sidecar)
    assert len(calls) == 1
    assert calls[0]["tool"] == "ping"


def test_process_buffer_partial_line_and_multi_line_chunk(tmp_path: Path):
    """Partial line stays buffered; two lines in one chunk both process."""
    import os

    rec = SidecarRecorder(tmp_path / "s.jsonl")
    r, w = os.pipe()
    buf = bytearray()
    # Partial line without newline — stays buffered (no tools/call pending yet).
    buf.extend(b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"a","arguments":{}}')
    process_buffer_lines(buf, forward_fd=w, recorder=rec, is_client=True, record_jsonrpc=True)
    assert len(buf) > 0 and b"\n" not in buf
    assert rec._pending == {}  # noqa: SLF001

    # Complete first line + second full line in one extend (multi-line prefetch).
    buf.extend(b'}\n{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"b","arguments":{}}}\n')
    process_buffer_lines(buf, forward_fd=w, recorder=rec, is_client=True, record_jsonrpc=True)
    assert len(buf) == 0
    assert 1 in rec._pending and 2 in rec._pending  # noqa: SLF001
    os.close(w)
    while os.read(r, 65536):
        pass
    os.close(r)


def test_child_exit_drains_final_response(tmp_path: Path):
    """Child writes a final tools/call response then exits immediately — must record it."""
    server = tmp_path / "final_then_exit.py"
    server.write_text(
        textwrap.dedent(
            """
            import json, sys
            line = sys.stdin.readline()
            msg = json.loads(line)
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": {"content": [{"type": "text", "text": "final"}], "isError": False},
            }) + "\\n")
            sys.stdout.flush()
            # Exit immediately after writing final response.
            sys.exit(0)
            """
        ),
        encoding="utf-8",
    )
    sidecar = tmp_path / "side.jsonl"
    client_in = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "final_tool", "arguments": {"x": 1}},
            }
        )
        + "\n"
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.proxy",
            "--log",
            str(sidecar),
            "--",
            sys.executable,
            str(server),
        ],
        input=client_in.encode(),
        capture_output=True,
        cwd=str(REPO),
        timeout=15,
    )
    assert proc.returncode == 0
    assert b"final" in proc.stdout
    calls = load_proxy_sidecar_calls(sidecar)
    assert len(calls) == 1
    assert calls[0]["tool"] == "final_tool"
    assert calls[0]["is_error"] is False


def test_scrub_child_pythonpath_removes_repo():
    import os

    env = {"PYTHONPATH": f"{REPO}{os.pathsep}/other/lib", "FOO": "1"}
    scrubbed = scrub_child_pythonpath(env)
    assert "/other/lib" in scrubbed["PYTHONPATH"]
    assert str(REPO) not in scrubbed["PYTHONPATH"].split(os.pathsep)
    # Only-repo entry drops the var entirely
    only = scrub_child_pythonpath({"PYTHONPATH": str(REPO)})
    assert "PYTHONPATH" not in only


def test_proxy_child_env_pythonpath_clean(tmp_path: Path):
    """Real MCP child must not inherit the monorepo PYTHONPATH entry."""
    server = tmp_path / "check_env.py"
    server.write_text(
        textwrap.dedent(
            f"""
            import json, os, sys
            root = {str(REPO)!r}
            pp = os.environ.get("PYTHONPATH", "")
            parts = [p for p in pp.split(os.pathsep) if p]
            bad = root in parts
            line = sys.stdin.readline()
            msg = json.loads(line)
            sys.stdout.write(json.dumps({{
                "jsonrpc": "2.0",
                "id": msg["id"],
                "result": {{"content": [{{"type": "text", "text": "bad=" + str(bad)}}], "isError": False}},
            }}) + "\\n")
            sys.stdout.flush()
            sys.exit(0 if not bad else 9)
            """
        ),
        encoding="utf-8",
    )
    sidecar = tmp_path / "side.jsonl"
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    env = ensure_proxy_pythonpath(dict(__import__("os").environ))
    assert str(REPO) in env.get("PYTHONPATH", "")
    client_in = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "envcheck", "arguments": {}},
            }
        )
        + "\n"
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.proxy",
            "--log",
            str(sidecar),
            "--",
            sys.executable,
            str(server),
        ],
        input=client_in.encode(),
        capture_output=True,
        cwd=str(foreign),
        env=env,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    assert b"bad=False" in proc.stdout


def test_rapid_response_pairing(tmp_path: Path):
    """Record-before-forward: fast child responses must pair with requests (no unmatched).

    Real subprocess child replies instantly; many iterations stress the race where
    a response could land on the stdout pump before _pending[id] was registered.
    """
    server = tmp_path / "instant_reply.py"
    server.write_text(
        textwrap.dedent(
            """
            import json, sys
            for line in sys.stdin.buffer:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                mid = msg.get("id")
                if mid is None:
                    continue
                # Instant reply — no sleep — maximize race window.
                sys.stdout.buffer.write(
                    (json.dumps({
                        "jsonrpc": "2.0",
                        "id": mid,
                        "result": {"content": [{"type": "text", "text": "ok"}], "isError": False},
                    }) + "\\n").encode()
                )
                sys.stdout.buffer.flush()
            """
        ),
        encoding="utf-8",
    )
    n = 40
    lines = []
    for i in range(1, n + 1):
        lines.append(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": i,
                    "method": "tools/call",
                    "params": {"name": f"tool_{i}", "arguments": {"i": i}},
                }
            )
        )
    client_in = "\n".join(lines) + "\n"
    sidecar = tmp_path / "side.jsonl"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.proxy",
            "--log",
            str(sidecar),
            "--",
            sys.executable,
            str(server),
        ],
        input=client_in.encode("utf-8"),
        capture_output=True,
        cwd=str(REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    rows = [json.loads(ln) for ln in sidecar.read_text(encoding="utf-8").splitlines() if ln.strip()]
    call_rows = [r for r in rows if r.get("row_type") != "proxy_meta"]
    meta = next(r for r in rows if r.get("row_type") == "proxy_meta")
    assert rows[-1].get("row_type") == "proxy_meta"
    assert len(call_rows) == n, f"paired {len(call_rows)}/{n}; meta={meta}"
    assert meta.get("unmatched_responses", 0) == 0
    assert meta.get("pending_left", 0) == 0
    tools = {r["tool"] for r in call_rows}
    assert tools == {f"tool_{i}" for i in range(1, n + 1)}


def test_meta_is_last_row_after_forced_kill(tmp_path: Path):
    """After forced child kill, proxy_meta is the last sidecar row."""
    import time as time_mod

    server = tmp_path / "hang.py"
    server.write_text(
        textwrap.dedent(
            """
            import sys, time
            # Read one line (so proxy has something to record) then hang forever.
            line = sys.stdin.buffer.readline()
            if line:
                import json
                try:
                    msg = json.loads(line)
                    mid = msg.get("id")
                    if mid is not None:
                        sys.stdout.buffer.write(
                            (json.dumps({"jsonrpc": "2.0", "id": mid, "result": {}}) + "\\n").encode()
                        )
                        sys.stdout.buffer.flush()
                except Exception:
                    pass
            while True:
                time.sleep(60)
            """
        ),
        encoding="utf-8",
    )
    sidecar = tmp_path / "side.jsonl"
    client_in = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "hang_tool", "arguments": {}},
            }
        )
        + "\n"
    )
    # Close stdin after one request so proxy enters shutdown while child hangs
    # → kill path under SHUTDOWN_DEADLINE_S.
    t0 = time_mod.monotonic()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.proxy",
            "--log",
            str(sidecar),
            "--",
            sys.executable,
            str(server),
        ],
        input=client_in.encode("utf-8"),
        capture_output=True,
        cwd=str(REPO),
        timeout=SHUTDOWN_DEADLINE_S + 15,
    )
    elapsed = time_mod.monotonic() - t0
    assert sidecar.is_file()
    rows = [json.loads(ln) for ln in sidecar.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert rows, "sidecar empty"
    assert rows[-1].get("row_type") == "proxy_meta"
    meta = rows[-1]
    # Child was hung; kill path should have fired (or child reaped after kill).
    assert meta.get("child_killed") is True or proc.returncode != 0
    # Wall clock bounded by deadline (+ small slack for process startup).
    assert elapsed < SHUTDOWN_DEADLINE_S + 5.0


def test_bounded_shutdown_wall_clock(tmp_path: Path):
    """Shutdown after child death stays within SHUTDOWN_DEADLINE_S (+ small slack)."""
    import time as time_mod

    server = tmp_path / "die_after_reply.py"
    server.write_text(
        textwrap.dedent(
            """
            import json, sys, time
            line = sys.stdin.readline()
            msg = json.loads(line)
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": msg["id"],
                "result": {"content": [], "isError": False},
            }) + "\\n")
            sys.stdout.flush()
            sys.exit(0)
            """
        ),
        encoding="utf-8",
    )
    sidecar = tmp_path / "side.jsonl"
    client_in = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "quick", "arguments": {}},
            }
        )
        + "\n"
    )
    t0 = time_mod.monotonic()
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "evals.proxy",
            "--log",
            str(sidecar),
            "--",
            sys.executable,
            str(server),
        ],
        input=client_in.encode(),
        capture_output=True,
        cwd=str(REPO),
        timeout=SHUTDOWN_DEADLINE_S + 5,
    )
    elapsed = time_mod.monotonic() - t0
    assert proc.returncode == 0
    assert elapsed < SHUTDOWN_DEADLINE_S + 2.0
    rows = [json.loads(ln) for ln in sidecar.read_text().splitlines() if ln.strip()]
    assert rows[-1].get("row_type") == "proxy_meta"


def test_proxy_survives_cli_group_kill_and_writes_meta(tmp_path: Path):
    """Proxy os.setsid() detaches from the CLI process group.

    Simulate: CLI process-group leader spawns proxy as a child (same group);
    proxy main() calls setsid and leaves the group; SIGKILL the CLI group;
    proxy still finalizes proxy_meta within the shutdown deadline.
    """
    import os
    import signal
    import time

    server = tmp_path / "echo_server.py"
    server.write_text(
        textwrap.dedent(
            """
            import json, sys
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                mid = msg.get("id")
                if mid is not None:
                    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {}}) + "\\n")
                    sys.stdout.flush()
            """
        ),
        encoding="utf-8",
    )
    sidecar = tmp_path / "side.jsonl"
    leader_script = tmp_path / "cli_leader.py"
    leader_script.write_text(
        textwrap.dedent(
            f"""
            import os, subprocess, sys, time
            from pathlib import Path
            sidecar = Path({str(sidecar)!r})
            server = Path({str(server)!r})
            # Spawn proxy in our process group (no start_new_session on child).
            # proxy main() will os.setsid() and detach.
            proxy = subprocess.Popen(
                [
                    sys.executable, "-m", "evals.proxy",
                    "--log", str(sidecar),
                    "--",
                    sys.executable, str(server),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            # Give setsid a moment, then write a tools/call and keep stdin open briefly.
            time.sleep(0.4)
            if proxy.stdin:
                req = (
                    '{{"jsonrpc":"2.0","id":1,"method":"tools/call",'
                    '"params":{{"name":"t","arguments":{{}}}}}}\\n'
                )
                proxy.stdin.write(req.encode())
                proxy.stdin.flush()
            # Stay alive as group leader until killed by the test harness.
            time.sleep(9999)
            """
        ),
        encoding="utf-8",
    )

    # Leader is a process-group leader (like run_cli_subprocess).
    env = {**os.environ, "PYTHONPATH": str(REPO) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    leader = subprocess.Popen(
        [sys.executable, str(leader_script)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(REPO),
        env=env,
    )
    try:
        # Wait until proxy has started (sidecar created) and setsid likely done.
        boot = time.monotonic() + 5.0
        while time.monotonic() < boot:
            if sidecar.is_file():
                break
            time.sleep(0.05)
        time.sleep(0.5)  # allow setsid + optional tools/call
        # SIGKILL the CLI process group — must NOT kill the detached proxy.
        try:
            os.killpg(leader.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            leader.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            leader.kill()
            leader.wait(timeout=1.0)

        # Proxy should see stdin EOF (leader dead → pipe closed), finalize meta.
        deadline = time.monotonic() + SHUTDOWN_DEADLINE_S + 5.0
        meta_seen = False
        while time.monotonic() < deadline:
            if sidecar.is_file():
                text = sidecar.read_text(encoding="utf-8")
                if "proxy_meta" in text:
                    meta_seen = True
                    break
            time.sleep(0.1)
        assert meta_seen, (
            f"proxy_meta missing after group kill; sidecar={sidecar.read_text() if sidecar.is_file() else None!r}"
        )
        rows = [json.loads(ln) for ln in sidecar.read_text().splitlines() if ln.strip()]
        assert rows[-1].get("row_type") == "proxy_meta"
    finally:
        if leader.poll() is None:
            try:
                os.killpg(leader.pid, signal.SIGKILL)
            except Exception:
                leader.kill()
            try:
                leader.wait(timeout=2.0)
            except Exception:
                pass
