"""Offline eval tests for cli driver."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from evals.drivers import (
    AntigravityCliDriver,
    ClaudeCliDriver,
    CodexCliDriver,
    OpencodeCliDriver,
    apply_proxy_sidecar,
    ensure_proxy_pythonpath,
    harvest_proxy_after_cli_timeout,
    load_proxy_sidecar,
    load_proxy_sidecar_calls,
    proxy_wrap_server_command,
    run_cli_subprocess,
    wait_for_proxy_meta,
)
from evals.drivers.driver import CliDriver, CliLaunch, CliOutput


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not owned by us
    return True


REPO = Path(__file__).resolve().parents[3]


def test_run_behaviours(tmp_path, monkeypatch):
    def test_run_cli_subprocess_kills_process_group_on_timeout(tmp_path):
        pidfile = tmp_path / "pids.txt"
        script = tmp_path / "sticky_cli.py"
        script.write_text(
            textwrap.dedent(
                f"""
                import os, subprocess, sys, time
                from pathlib import Path
                pidfile = Path({str(pidfile)!r})
                # Grandchild stays in the same process group (no start_new_session).
                child = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(9999)"],
                )
                pidfile.write_text(f"{{os.getpid()}}\\n{{child.pid}}\\n")
                # Hold our stdout open forever (simulates grandchild pipe hold).
                time.sleep(9999)
                """
            ),
            encoding="utf-8",
        )

        t0 = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired) as ei:
            run_cli_subprocess(
                [sys.executable, str(script)],
                timeout=1.0,
                capture_output=True,
                text=True,
            )
        elapsed = time.monotonic() - t0
        assert elapsed < 6.0, f"timeout path took {elapsed:.1f}s (unbounded communicate hang?)"
        assert getattr(ei.value, "killed_process_group", False) is True

        # Wait briefly for reaping
        deadline = time.monotonic() + 3.0
        pids: list[int] = []
        while time.monotonic() < deadline:
            if pidfile.is_file():
                pids = [int(x) for x in pidfile.read_text().splitlines() if x.strip()]
                if len(pids) == 2 and not any(_pid_alive(p) for p in pids):
                    break
            time.sleep(0.05)
        assert len(pids) == 2, f"pidfile incomplete: {pidfile} {pids}"
        alive = [p for p in pids if _pid_alive(p)]
        assert not alive, f"process group members still alive: {alive}"

    def test_run_cli_subprocess_baseexception_kills_group(tmp_path, monkeypatch):
        import evals.drivers as drivers_mod

        pidfile = tmp_path / "pids.txt"
        script = tmp_path / "sticky.py"
        script.write_text(
            textwrap.dedent(
                f"""
                import os, subprocess, sys, time
                from pathlib import Path
                pidfile = Path({str(pidfile)!r})
                child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(9999)"])
                pidfile.write_text(f"{{os.getpid()}}\\n{{child.pid}}\\n")
                time.sleep(9999)
                """
            ),
            encoding="utf-8",
        )

        real_comm = subprocess.Popen.communicate
        calls = {"n": 0}

        def boom_communicate(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                # Wait until pidfile is written so we can assert both die.
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    if pidfile.is_file() and len(pidfile.read_text().splitlines()) >= 2:
                        break
                    time.sleep(0.02)
                raise KeyboardInterrupt("injected mid-communicate")
            return real_comm(self, *a, **k)

        monkeypatch.setattr(subprocess.Popen, "communicate", boom_communicate)

        t0 = time.monotonic()
        with pytest.raises(KeyboardInterrupt):
            run_cli_subprocess(
                [sys.executable, str(script)],
                timeout=30.0,
                capture_output=True,
                text=True,
            )
        assert time.monotonic() - t0 < 6.0

        deadline = time.monotonic() + 3.0
        pids: list[int] = []
        while time.monotonic() < deadline:
            if pidfile.is_file():
                pids = [int(x) for x in pidfile.read_text().splitlines() if x.strip()]
                if len(pids) == 2 and not any(_pid_alive(p) for p in pids):
                    break
            time.sleep(0.05)
        assert len(pids) == 2
        alive = [p for p in pids if _pid_alive(p)]
        assert not alive, f"group survived BaseException path: {alive}"
        # silence unused import lint if any
        assert drivers_mod.run_cli_subprocess is run_cli_subprocess

    _d0 = tmp_path / "test_run_cli_subprocess_kills_process_group_on_timeout"
    _d0.mkdir()
    test_run_cli_subprocess_kills_process_group_on_timeout(_d0)
    _d1 = tmp_path / "test_run_cli_subprocess_baseexception_kills_group"
    _d1.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        test_run_cli_subprocess_baseexception_kills_group(_d1, mp)


def test_killpg_reaps_grandchild_when_leader_already_dead(tmp_path: Path):
    """killpg(leader_pid) works after the leader is reaped (no getpgid / no proc.kill fallback).

    Simulates: leader already gone, only grandchild remains in the process group.
    """
    import signal
    from types import SimpleNamespace

    from evals.drivers import kill_process_group

    pidfile = tmp_path / "pids.txt"
    script = tmp_path / "sticky_leader.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import os, subprocess, sys, time
            from pathlib import Path
            pidfile = Path({str(pidfile)!r})
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(9999)"],
            )
            pidfile.write_text(f"{{os.getpid()}}\\n{{child.pid}}\\n")
            time.sleep(9999)
            """
        ),
        encoding="utf-8",
    )
    leader = subprocess.Popen(
        [sys.executable, str(script)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 3.0
        pids: list[int] = []
        while time.monotonic() < deadline:
            if pidfile.is_file():
                pids = [int(x) for x in pidfile.read_text().splitlines() if x.strip()]
                if len(pids) == 2:
                    break
            time.sleep(0.02)
        assert len(pids) == 2, pids
        leader_pid, child_pid = pids

        # Kill ONLY the leader (not the group) — grandchild survives in the group.
        os.kill(leader_pid, signal.SIGKILL)
        try:
            leader.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass
        assert not _pid_alive(leader_pid)
        assert _pid_alive(child_pid), "precondition: grandchild must still be alive"

        t0 = time.monotonic()
        # Direct killpg(leader_pid) — pgid == original leader pid under start_new_session.
        ok = kill_process_group(SimpleNamespace(pid=leader_pid))
        assert ok is True
        assert time.monotonic() - t0 < 3.0

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and _pid_alive(child_pid):
            time.sleep(0.05)
        assert not _pid_alive(child_pid), "grandchild survived killpg after leader death"
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


def test_cli_behaviours(tmp_path, monkeypatch):
    def test_cli_driver_timeout_notes_process_group_kill(tmp_path):
        script = tmp_path / "slow.py"
        script.write_text(
            textwrap.dedent(
                """
                import time
                time.sleep(9999)
                """
            ),
            encoding="utf-8",
        )

        # Use real run_cli_subprocess with a tiny timeout via fake that wraps it.
        from evals.drivers import run_cli_subprocess as real_runner

        def short_timeout_runner(cmd, **kwargs):
            kwargs = dict(kwargs)
            kwargs["timeout"] = 0.5
            # Replace the CLI binary with our sticky sleeper
            return real_runner([sys.executable, str(script)], **kwargs)

        driver = ClaudeCliDriver(runner=short_timeout_runner, use_proxy=False)
        t0 = time.monotonic()
        run = driver.run_task(
            "hi",
            mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
            model="sonnet",
            max_turns=1,
            cwd=tmp_path,
        )
        assert time.monotonic() - t0 < 6.0
        assert run.stopped_reason == "timeout"
        assert "timeout_killed_process_group" in run.notes

    def test_cli_driver_template_inherits_proxy_first_and_timeout_harvest(tmp_path, monkeypatch):
        clock = {"now": 0.0}
        monkeypatch.setattr("evals.drivers.driver.time.perf_counter", lambda: clock["now"])

        class MinimalCliDriver(CliDriver):
            name = "minimal-cli"
            temp_dir_prefix = "plane-eval-minimal-"

            def write_mcp_config(
                self,
                temp_dir: Path,
                *,
                task_cwd: Path,
                server_command: list[str],
                child_env: dict[str, str],
            ) -> CliLaunch:
                del temp_dir, child_env
                # Harness-owned setup takes five seconds on the fake clock. The
                # persisted wall time must start after this hook returns.
                clock["now"] = 5.0
                self.sidecar_path = Path(server_command[server_command.index("--log") + 1])
                return CliLaunch(cwd=task_cwd)

            def build_command(
                self,
                prompt: str,
                *,
                model: str | None,
                max_turns: int,
                system: str | None,
                launch: CliLaunch,
            ) -> list[str]:
                del model, max_turns, system, launch
                return ["minimal", prompt]

            def parse_output(
                self,
                proc: subprocess.CompletedProcess[str],
                *,
                task_cwd: Path,
                max_turns: int,
                notes: list[str],
            ) -> CliOutput:
                del proc, task_cwd, max_turns, notes
                return CliOutput(
                    final_text="done",
                    calls=[
                        {"tool": "cli_fallback_one", "args": {}, "origin": "plane"},
                        {"tool": "cli_fallback_two", "args": {}, "origin": "plane"},
                    ],
                )

        def write_complete_sidecar(path: Path, tool: str) -> None:
            rows = [
                {
                    "tool": tool,
                    "args": {},
                    "is_error": False,
                    "result_chars": 2,
                    "duration_ms": 1,
                    "seq": 1,
                },
                {"row_type": "proxy_meta", "pending_left": 0, "pumps_alive": False},
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

        success_driver: MinimalCliDriver

        def success_runner(cmd, **kwargs):
            write_complete_sidecar(success_driver.sidecar_path, "proxy_first")
            clock["now"] = 7.0
            return subprocess.CompletedProcess(cmd, 0, stdout="ignored", stderr="")

        success_driver = MinimalCliDriver(runner=success_runner, use_proxy=True)
        success = success_driver.run_task("go", {}, None, 1, cwd=tmp_path)
        assert success.call_source == "proxy"
        assert [call["tool"] for call in success.calls] == ["proxy_first"]
        assert success.wall_time_s == 2.0

        timeout_driver: MinimalCliDriver

        def timeout_runner(cmd, **kwargs):
            write_complete_sidecar(timeout_driver.sidecar_path, "before_timeout")
            clock["now"] = 8.0
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs["timeout"])

        timeout_driver = MinimalCliDriver(runner=timeout_runner, use_proxy=True)
        timed_out = timeout_driver.run_task("go", {}, None, 1, cwd=tmp_path)
        assert timed_out.stopped_reason == "timeout"
        assert timed_out.call_source == "proxy"
        assert [call["tool"] for call in timed_out.calls] == ["before_timeout"]
        assert timed_out.wall_time_s == 3.0

    _d0 = tmp_path / "test_cli_driver_timeout_notes_process_group_kill"
    _d0.mkdir()
    test_cli_driver_timeout_notes_process_group_kill(_d0)
    _d1 = tmp_path / "test_cli_driver_template_inherits_proxy_first_and_timeout_harvest"
    _d1.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        test_cli_driver_template_inherits_proxy_first_and_timeout_harvest(_d1, mp)


def test_old_payload_free_sidecar_still_parses(tmp_path: Path):
    path = tmp_path / "old.jsonl"
    path.write_text(
        json.dumps(
            {
                "tool": "legacy",
                "args": {},
                "is_error": False,
                "result_chars": 17,
                "duration_ms": 1,
                "seq": 1,
            }
        )
        + "\n"
        + json.dumps({"row_type": "proxy_meta", "pending_left": 0, "pumps_alive": False})
        + "\n",
        encoding="utf-8",
    )

    calls, status = load_proxy_sidecar(path)
    assert status["state"] == "complete"
    assert calls[0]["result_chars"] == 17
    assert "result_text" not in calls[0]


def test_apply_behaviours(tmp_path):
    def test_apply_proxy_sidecar_replaces_when_nonempty(tmp_path):
        side = tmp_path / "s.jsonl"
        side.write_text(
            json.dumps(
                {
                    "tool": "find_work_items",
                    "args": {"q": "x"},
                    "is_error": False,
                    "result_chars": 12,
                    "duration_ms": 5,
                    "seq": 1,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        notes: list[str] = []
        calls, client, src = apply_proxy_sidecar(
            [{"tool": "old", "args": {}, "origin": "plane"}],
            [],
            side,
            notes,
        )
        assert src == "proxy"
        assert calls[0]["tool"] == "find_work_items"
        assert calls[0]["duration_ms"] == 5
        assert any("calls_from_proxy" in n for n in notes)

    def test_apply_proxy_sidecar_empty_fallback(tmp_path):
        side = tmp_path / "empty.jsonl"
        side.write_text("", encoding="utf-8")
        notes: list[str] = []
        original = [{"tool": "from_cli", "args": {}, "origin": "plane"}]
        calls, _client, src = apply_proxy_sidecar(original, [], side, notes)
        assert calls is original or calls == original
        assert "proxy_sidecar_empty" in notes
        assert src != "proxy" or calls == original

    def test_apply_proxy_incomplete_defers_to_richer_cli(tmp_path):
        p = tmp_path / "s.jsonl"
        # Incomplete: one proxy call, no meta.
        p.write_text(
            json.dumps(
                {
                    "tool": "from_proxy",
                    "args": {},
                    "is_error": False,
                    "result_chars": 1,
                    "duration_ms": 1,
                    "seq": 1,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        cli = [
            {"tool": "c1", "args": {}, "origin": "plane"},
            {"tool": "c2", "args": {}, "origin": "plane"},
        ]
        notes: list[str] = []
        calls, _client, src = apply_proxy_sidecar(cli, [], p, notes)
        assert src != "proxy"
        assert [c["tool"] for c in calls] == ["c1", "c2"]
        assert any("proxy_sidecar_incomplete" in n for n in notes)
        assert any("deferred_to_cli" in n for n in notes)

    _d0 = tmp_path / "test_apply_proxy_sidecar_replaces_when_nonempty"
    _d0.mkdir()
    test_apply_proxy_sidecar_replaces_when_nonempty(_d0)
    _d1 = tmp_path / "test_apply_proxy_sidecar_empty_fallback"
    _d1.mkdir()
    test_apply_proxy_sidecar_empty_fallback(_d1)
    _d2 = tmp_path / "test_apply_proxy_incomplete_defers_to_richer_cli"
    _d2.mkdir()
    test_apply_proxy_incomplete_defers_to_richer_cli(_d2)


def test_proxy_wrap_server_command():
    out = proxy_wrap_server_command(
        ["python", "-m", "plane_mcp", "stdio"],
        sidecar_path=Path("/tmp/s.jsonl"),
        python_bin="/venv/bin/python",
    )
    assert out[:5] == ["/venv/bin/python", "-m", "evals.proxy", "--log", "/tmp/s.jsonl"]
    assert out[5] == "--"
    assert out[6:] == ["python", "-m", "plane_mcp", "stdio"]

    with_payloads = proxy_wrap_server_command(
        ["server"],
        sidecar_path=Path("/tmp/s.jsonl"),
        python_bin="python",
        record_result_payloads=True,
    )
    assert with_payloads[5:7] == ["--record-result-payloads", "--"]


def test_load_behaviours(tmp_path):
    def test_load_proxy_sidecar_sorts_by_seq(tmp_path):
        p = tmp_path / "s.jsonl"
        # Append in reverse response order.
        rows = [
            {"tool": "b", "args": {}, "is_error": False, "result_chars": 1, "duration_ms": 1, "seq": 2},
            {"tool": "a", "args": {}, "is_error": False, "result_chars": 1, "duration_ms": 1, "seq": 1},
            {
                "row_type": "proxy_meta",
                "relayed_lines": 2,
                "unparsed_lines": 0,
                "unmatched_responses": 0,
                "notifications": 0,
                "pending_left": 0,
                "child_killed": False,
            },
        ]
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        calls = load_proxy_sidecar_calls(p)
        assert [c["tool"] for c in calls] == ["a", "b"]

    def test_load_proxy_sidecar_torn_final_line(tmp_path):
        p = tmp_path / "s.jsonl"
        good = {
            "tool": "a",
            "args": {},
            "is_error": False,
            "result_chars": 1,
            "duration_ms": 1,
            "seq": 1,
        }
        # Complete call row + torn final line (no proxy_meta).
        p.write_text(json.dumps(good) + "\n" + '{"tool": "b", "args":', encoding="utf-8")
        calls, status = load_proxy_sidecar(p)
        assert status["state"] == "incomplete"
        assert status["torn_line"] is True
        assert status["meta"] is None
        assert [c["tool"] for c in calls] == ["a"]

    _d0 = tmp_path / "test_load_proxy_sidecar_sorts_by_seq"
    _d0.mkdir()
    test_load_proxy_sidecar_sorts_by_seq(_d0)
    _d1 = tmp_path / "test_load_proxy_sidecar_torn_final_line"
    _d1.mkdir()
    test_load_proxy_sidecar_torn_final_line(_d1)


def test_server_cmd_reaches_all_cli_drivers(tmp_path: Path):
    def make_fake(driver_cls: type, bag: dict):
        def fake_run(cmd, **kwargs):
            bag["cmd"] = cmd
            if driver_cls is ClaudeCliDriver and "--mcp-config" in cmd:
                cfg = Path(cmd[cmd.index("--mcp-config") + 1])
                bag["cfg"] = json.loads(cfg.read_text())
            elif driver_cls is OpencodeCliDriver:
                cwd = kwargs.get("cwd")
                if cwd:
                    cfg = Path(cwd) / "opencode.json"
                    if cfg.is_file():
                        bag["cfg"] = json.loads(cfg.read_text())
            elif driver_cls is AntigravityCliDriver:
                env = kwargs.get("env") or {}
                home = env.get("HOME")
                if home:
                    for rel in (
                        Path(".gemini") / "config" / "mcp_config.json",
                        Path(".gemini") / "antigravity-cli" / "mcp_config.json",
                    ):
                        p = Path(home) / rel
                        if p.is_file():
                            bag.setdefault("cfgs", []).append(json.loads(p.read_text()))
            elif driver_cls is CodexCliDriver:
                bag["cmd_joined"] = " ".join(cmd)
            out = (
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "ok",
                        "session_id": "s",
                        "num_turns": 1,
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                    }
                )
                if driver_cls is ClaudeCliDriver
                else "{}"
            )
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

        return fake_run

    for Driver, bin_key in (
        (ClaudeCliDriver, "claude_bin"),
        (CodexCliDriver, "codex_bin"),
        (AntigravityCliDriver, "agy_bin"),
        (OpencodeCliDriver, "opencode_bin"),
    ):
        seen: dict = {}
        kwargs = {
            "runner": make_fake(Driver, seen),
            "use_proxy": True,
            "record_result_payloads": True,
            "python_bin": sys.executable,
            "server_command": ["/ext/bin/foreign-mcp", "stdio", "--mode", "candidate"],
        }
        if Driver is CodexCliDriver:
            kwargs["allow_live"] = True
        kwargs[bin_key] = "fake-bin"
        driver = Driver(**kwargs)
        driver.run_task(
            "hi",
            mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
            model="sonnet",
            max_turns=1,
            cwd=tmp_path,
        )
        blob = json.dumps(seen)
        assert "foreign-mcp" in blob or "foreign-mcp" in seen.get("cmd_joined", "")
        assert "record-result-payloads" in blob or "record-result-payloads" in seen.get("cmd_joined", "")


def test_use_proxy_false_call_source_not_proxy(tmp_path: Path):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout='{"result":"x"}', stderr="")

    for Driver in (AntigravityCliDriver, OpencodeCliDriver):
        d = Driver(runner=fake_run, use_proxy=False, python_bin=sys.executable)
        run = d.run_task(
            "hi",
            mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
            model=None,
            max_turns=1,
            cwd=tmp_path,
        )
        assert run.call_source != "proxy"


def test_ensure_proxy_pythonpath_injects_repo():
    env = ensure_proxy_pythonpath({})
    assert str(REPO) in env["PYTHONPATH"].split(__import__("os").pathsep)
    # Idempotent
    env2 = ensure_proxy_pythonpath(env)
    assert env2["PYTHONPATH"].count(str(REPO)) == 1


def test_timeout_behaviours(tmp_path):
    def test_timeout_harvests_sidecar_calls(tmp_path):
        side_calls = [
            {
                "tool": "pre_timeout",
                "args": {"a": 1},
                "is_error": False,
                "result_chars": 3,
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
            },
        ]

        def fake_run(cmd, **kwargs):
            # Plant a complete sidecar next to the mcp config (temp dir still alive).
            cfg = Path(cmd[cmd.index("--mcp-config") + 1])
            # Sidecar path is in the same temp dir as mcp.json for Claude.
            # Find sidecar from proxy args in mcp config.
            mcp = json.loads(cfg.read_text())
            args = mcp["mcpServers"]["plane"]["args"]
            log_idx = args.index("--log") + 1
            side = Path(args[log_idx])
            side.write_text("\n".join(json.dumps(r) for r in side_calls) + "\n", encoding="utf-8")
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

        driver = ClaudeCliDriver(runner=fake_run, use_proxy=True, python_bin=sys.executable)
        run = driver.run_task(
            "hi",
            mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
            model="sonnet",
            max_turns=1,
            cwd=tmp_path,
        )
        assert run.stopped_reason == "timeout"
        assert run.call_source == "proxy"
        assert len(run.calls) == 1
        assert run.calls[0]["tool"] == "pre_timeout"

    def test_timeout_harvest_waits_for_delayed_meta(tmp_path):
        import threading
        import time as time_mod

        call_row = {
            "tool": "late_meta_tool",
            "args": {"n": 1},
            "is_error": False,
            "result_chars": 2,
            "duration_ms": 1,
            "seq": 1,
        }
        meta_row = {
            "row_type": "proxy_meta",
            "relayed_lines": 1,
            "unparsed_lines": 0,
            "unmatched_responses": 0,
            "notifications": 0,
            "pending_left": 0,
            "child_killed": False,
            "pumps_alive": False,
        }
        seen: dict = {"waited": False}

        def fake_run(cmd, **kwargs):
            cfg = Path(cmd[cmd.index("--mcp-config") + 1])
            mcp = json.loads(cfg.read_text())
            args = mcp["mcpServers"]["plane"]["args"]
            side = Path(args[args.index("--log") + 1])
            # Call row first — no meta yet (simulates proxy still finalizing).
            side.write_text(json.dumps(call_row) + "\n", encoding="utf-8")

            def write_meta_later() -> None:
                time_mod.sleep(0.45)
                with side.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(meta_row) + "\n")
                seen["waited"] = True

            threading.Thread(target=write_meta_later, daemon=True).start()
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

        t0 = time_mod.monotonic()
        driver = ClaudeCliDriver(runner=fake_run, use_proxy=True, python_bin=sys.executable)
        run = driver.run_task(
            "hi",
            mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
            model="sonnet",
            max_turns=1,
            cwd=tmp_path,
        )
        elapsed = time_mod.monotonic() - t0
        assert run.stopped_reason == "timeout"
        assert run.call_source == "proxy"
        assert len(run.calls) == 1
        assert run.calls[0]["tool"] == "late_meta_tool"
        assert seen["waited"] is True
        # Must have waited for the delayed meta (~0.45s), not returned instantly.
        assert elapsed >= 0.4
        assert "proxy_meta_wait_timeout" not in run.notes

    _d0 = tmp_path / "test_timeout_harvests_sidecar_calls"
    _d0.mkdir()
    test_timeout_harvests_sidecar_calls(_d0)
    _d1 = tmp_path / "test_timeout_harvest_waits_for_delayed_meta"
    _d1.mkdir()
    test_timeout_harvest_waits_for_delayed_meta(_d1)


def test_wait_for_proxy_meta_unit(tmp_path: Path):
    side = tmp_path / "s.jsonl"
    side.write_text("", encoding="utf-8")
    assert wait_for_proxy_meta(side, max_wait_s=0.15, poll_s=0.05) is False
    side.write_text(json.dumps({"row_type": "proxy_meta", "pending_left": 0}) + "\n", encoding="utf-8")
    assert wait_for_proxy_meta(side, max_wait_s=1.0, poll_s=0.05) is True


def test_harvest_proxy_after_cli_timeout_incomplete_note(tmp_path: Path):
    """If meta never arrives, harvest still returns with incomplete note."""
    side = tmp_path / "s.jsonl"
    side.write_text(
        json.dumps({"tool": "only", "args": {}, "seq": 1, "is_error": False, "result_chars": 0}) + "\n",
        encoding="utf-8",
    )
    notes: list[str] = []
    calls, _client, src = harvest_proxy_after_cli_timeout([], [], side, notes, max_wait_s=0.25)
    assert "proxy_meta_wait_timeout" in notes
    assert len(calls) == 1
    assert src == "proxy"
    assert any("incomplete" in n for n in notes)
