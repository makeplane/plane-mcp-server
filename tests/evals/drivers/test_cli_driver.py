"""Offline eval tests for cli driver."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
import tomllib

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
    proxy_pid_path,
    proxy_wrap_server_command,
    run_cli_subprocess,
    wait_for_proxy_meta,
)
from evals.drivers.driver import CliDriver, CliLaunch, CliOutput, CliOutputError
from evals.evidence import EVIDENCE_SENTINELS_ENV, TARGET_ENTITY_EVIDENCE
from tests.evals.conftest import case_params


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not owned by us
    return True


REPO = Path(__file__).resolve().parents[3]


def _run_cli_subprocess_kills_process_group_on_timeout(tmp_path, _monkeypatch):
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


def _run_cli_subprocess_baseexception_kills_group(tmp_path, monkeypatch):
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


@pytest.mark.parametrize(
    "case",
    case_params(_run_cli_subprocess_kills_process_group_on_timeout, _run_cli_subprocess_baseexception_kills_group),
)
def test_run_behaviours(case, tmp_path, monkeypatch):
    case(tmp_path, monkeypatch)


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


def _cli_driver_timeout_notes_process_group_kill(tmp_path, _monkeypatch):
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


def _cli_driver_template_inherits_proxy_first_and_timeout_harvest(tmp_path, monkeypatch):
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
            {
                "row_type": "proxy_meta",
                "pending_left": 0,
                "pumps_alive": False,
                "last_seq": 1,
                "tool_request_count": 1,
            },
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


@pytest.mark.parametrize(
    "case",
    case_params(
        _cli_driver_timeout_notes_process_group_kill,
        _cli_driver_template_inherits_proxy_first_and_timeout_harvest,
    ),
)
def test_cli_behaviours(case, tmp_path, monkeypatch):
    case(tmp_path, monkeypatch)


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
        + json.dumps(
            {
                "row_type": "proxy_meta",
                "pending_left": 0,
                "pumps_alive": False,
                "last_seq": 1,
                "tool_request_count": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    calls, status = load_proxy_sidecar(path)
    assert status["state"] == "complete"
    assert calls[0]["result_chars"] == 17
    assert "result_text" not in calls[0]


def test_cli_parse_failure_retains_lossy_sidecar_integrity(tmp_path: Path):
    class BrokenOutputDriver(CliDriver):
        name = "broken-output-cli"

        def write_mcp_config(self, temp_dir, *, task_cwd, server_command, child_env):
            del temp_dir, child_env
            self.sidecar_path = Path(server_command[server_command.index("--log") + 1])
            return CliLaunch(cwd=task_cwd)

        def build_command(self, prompt, *, model, max_turns, system, launch):
            del prompt, model, max_turns, system, launch
            return ["broken-output"]

        def parse_output(self, proc, *, task_cwd, max_turns, notes):
            del proc, task_cwd, max_turns, notes
            raise CliOutputError("cannot parse output")

    driver: BrokenOutputDriver

    def fake_run(command, **kwargs):
        del kwargs
        rows = [
            {
                "row_type": "proxy_meta",
                "unmatched_responses": 1,
                "pending_left": 0,
                "non_tool_pending_left": 0,
                "last_seq": 0,
                "tool_request_count": 0,
            }
        ]
        driver.sidecar_path.write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="bad", stderr="")

    driver = BrokenOutputDriver(runner=fake_run, use_proxy=True)

    with pytest.raises(RuntimeError, match="cannot parse output") as exc_info:
        driver.run_task("go", {}, None, 1, cwd=tmp_path)

    assert exc_info.value.trace_integrity is False
    assert exc_info.value.trace_integrity_reason == "recorder_loss"


def _apply_proxy_sidecar_replaces_when_nonempty(tmp_path):
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
        max_wait_s=0,
    )
    assert src == "proxy"
    assert calls[0]["tool"] == "find_work_items"
    assert calls[0]["duration_ms"] == 5
    assert any("calls_from_proxy" in n for n in notes)


def _apply_proxy_sidecar_empty_fallback(tmp_path):
    side = tmp_path / "empty.jsonl"
    side.write_text("", encoding="utf-8")
    notes: list[str] = []
    original = [{"tool": "from_cli", "args": {}, "origin": "plane"}]
    calls, _client, src = apply_proxy_sidecar(original, [], side, notes, max_wait_s=0)
    assert calls is original or calls == original
    assert "proxy_sidecar_empty" in notes
    assert src != "proxy" or calls == original


def _apply_proxy_incomplete_defers_to_richer_cli(tmp_path):
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
    calls, _client, src = apply_proxy_sidecar(cli, [], p, notes, max_wait_s=0)
    assert src != "proxy"
    assert [c["tool"] for c in calls] == ["c1", "c2"]
    assert any("proxy_sidecar_incomplete" in n for n in notes)
    assert any("deferred_to_cli" in n for n in notes)


def _apply_proxy_with_skipped_row_defers_to_richer_cli(tmp_path):
    p = tmp_path / "s.jsonl"
    rows = [
        json.dumps(
            {
                "tool": "from_proxy",
                "args": {},
                "is_error": False,
                "result_chars": 1,
                "duration_ms": 1,
                "seq": 1,
            }
        ),
        "{corrupted mid-stream row",
        json.dumps(
            {
                "row_type": "proxy_meta",
                "pending_left": 0,
                "pumps_alive": False,
                "last_seq": 1,
                "tool_request_count": 1,
            }
        ),
    ]
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    cli = [
        {"tool": "c1", "args": {}, "origin": "plane"},
        {"tool": "c2", "args": {}, "origin": "plane"},
    ]
    notes: list[str] = []

    calls, _client, src = apply_proxy_sidecar(cli, [], p, notes)

    assert src == "json"
    assert [call["tool"] for call in calls] == ["c1", "c2"]
    assert "proxy_sidecar_incomplete:skipped_rows=1" in notes
    assert "proxy_sidecar_deferred_to_cli_trace" in notes


@pytest.mark.parametrize(
    "case",
    case_params(
        _apply_proxy_sidecar_replaces_when_nonempty,
        _apply_proxy_sidecar_empty_fallback,
        _apply_proxy_incomplete_defers_to_richer_cli,
        _apply_proxy_with_skipped_row_defers_to_richer_cli,
    ),
)
def test_apply_behaviours(case, tmp_path):
    case(tmp_path)


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


def _load_proxy_sidecar_sorts_by_seq(tmp_path):
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
            "non_tool_pending_left": 0,
            "last_seq": 1,
            "tool_request_count": 1,
            "child_killed": False,
        },
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    calls = load_proxy_sidecar_calls(p)
    assert [c["tool"] for c in calls] == ["a", "b"]


def _load_proxy_sidecar_torn_final_line(tmp_path):
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


@pytest.mark.parametrize(
    "case",
    case_params(_load_proxy_sidecar_sorts_by_seq, _load_proxy_sidecar_torn_final_line),
)
def test_load_behaviours(case, tmp_path):
    case(tmp_path)


@pytest.mark.parametrize(
    ("bad_row", "case_id"),
    [
        ("{corrupted mid-stream row", "invalid-json"),
        (json.dumps(["not", "an", "object"]), "non-object-json"),
        (json.dumps({"args": {"lost": "tool"}}), "missing-tool"),
    ],
    ids=lambda value: value if value in {"invalid-json", "non-object-json", "missing-tool"} else None,
)
def test_load_proxy_sidecar_skipped_row_makes_trace_incomplete(tmp_path: Path, bad_row: str, case_id: str):
    path = tmp_path / f"{case_id}.jsonl"
    call = {
        "tool": "visible",
        "args": {},
        "is_error": False,
        "result_chars": 1,
        "duration_ms": 1,
        "seq": 1,
    }
    meta = {"row_type": "proxy_meta", "pending_left": 0, "pumps_alive": False}
    path.write_text("\n".join((json.dumps(call), bad_row, json.dumps(meta))) + "\n", encoding="utf-8")

    calls, status = load_proxy_sidecar(path)

    assert [row["tool"] for row in calls] == ["visible"]
    assert status["skipped_rows"] == 1
    assert status["state"] == "incomplete"


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
                codex_home = Path(kwargs["env"]["CODEX_HOME"])
                with (codex_home / "config.toml").open("rb") as stream:
                    bag["cfg"] = tomllib.load(stream)
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


def test_codex_isolated_home_effective_mcp_server_list_is_exactly_plane(tmp_path: Path):
    codex_bin = shutil.which("codex")
    assert codex_bin is not None, "Codex CLI is required to observe its effective MCP configuration"

    fake_user_home = tmp_path / "fake-user"
    global_config = fake_user_home / ".codex" / "config.toml"
    global_config.parent.mkdir(parents=True)
    global_config.write_text(
        '[mcp_servers.forbidden_global]\ncommand = "/usr/bin/false"\n',
        encoding="utf-8",
    )
    project_config = tmp_path / ".codex" / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text(
        '[mcp_servers.forbidden_project]\ncommand = "/usr/bin/false"\n',
        encoding="utf-8",
    )
    driver = CodexCliDriver(codex_bin=codex_bin, runner=lambda *_args, **_kwargs: None, allow_live=True)
    launch = driver.write_mcp_config(
        tmp_path / "task-state",
        task_cwd=tmp_path,
        server_command=["/usr/bin/true"],
        child_env={"PATH": os.environ["PATH"]},
    )
    assert launch.env is not None
    effective_env = {**launch.env, "HOME": str(fake_user_home)}

    observed = subprocess.run(
        [codex_bin, "mcp", "list", "--json"],
        cwd=tmp_path,
        env=effective_env,
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    server_names = sorted(item["name"] for item in json.loads(observed.stdout))

    assert server_names == ["plane"]


def test_opencode_isolated_environment_effective_mcp_server_list_is_exactly_plane(tmp_path: Path):
    opencode_bin = shutil.which("opencode")
    assert opencode_bin is not None, "OpenCode CLI is required to observe its effective MCP configuration"

    driver = OpencodeCliDriver(opencode_bin=opencode_bin, runner=lambda *_args, **_kwargs: None)
    task_state = tmp_path / "task-state"
    task_state.mkdir()
    launch = driver.write_mcp_config(
        task_state,
        task_cwd=tmp_path,
        server_command=["/usr/bin/true"],
        child_env={"PATH": os.environ["PATH"]},
    )
    assert launch.env is not None
    user_config = Path(launch.env["HOME"]) / ".config" / "opencode" / "opencode.json"
    user_config.parent.mkdir(parents=True)
    user_config.write_text(
        json.dumps(
            {
                "mcp": {
                    "forbidden_global": {
                        "type": "local",
                        "command": ["/usr/bin/false"],
                        "enabled": True,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    observed = subprocess.run(
        [opencode_bin, "debug", "config"],
        cwd=launch.cwd,
        env=launch.env,
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    effective_config = json.loads(observed.stdout)

    assert sorted((effective_config.get("mcp") or {}).keys()) == ["plane"]


@pytest.mark.parametrize(
    ("Driver", "bin_key"),
    [
        pytest.param(ClaudeCliDriver, "claude_bin", id="claude-config"),
        pytest.param(CodexCliDriver, "codex_bin", id="codex-argv"),
        pytest.param(AntigravityCliDriver, "agy_bin", id="antigravity-home-config"),
        pytest.param(OpencodeCliDriver, "opencode_bin", id="opencode-cwd-config"),
    ],
)
def test_cli_agent_surfaces_never_contain_evidence_truth(
    tmp_path: Path,
    Driver: type[CliDriver],
    bin_key: str,
):
    sentinel = "hidden-target-fact-7b0a1f9c"
    total_count = 918273
    grouped_counts = {"project-1": 564738, "project-2": 102938}
    seen: dict[str, object] = {}

    def fake_run(cmd, **kwargs):
        configs: list[dict] = []
        proxy_args: list[str] | None = None
        if Driver is ClaudeCliDriver:
            config_path = Path(cmd[cmd.index("--mcp-config") + 1])
            configs.append(json.loads(config_path.read_text()))
            proxy_args = configs[0]["mcpServers"]["plane"]["args"]
        elif Driver is CodexCliDriver:
            codex_home = Path(kwargs["env"]["CODEX_HOME"])
            with (codex_home / "config.toml").open("rb") as stream:
                config = tomllib.load(stream)
            configs.append(config)
            server = config["mcp_servers"]["plane"]
            proxy_args = [server["command"], *server["args"]]
        elif Driver is AntigravityCliDriver:
            fake_home = Path(kwargs["env"]["HOME"])
            for rel in (
                Path(".gemini/config/mcp_config.json"),
                Path(".gemini/antigravity-cli/mcp_config.json"),
            ):
                configs.append(json.loads((fake_home / rel).read_text()))
            proxy_args = configs[0]["mcpServers"]["plane"]["args"]
        else:
            config_path = Path(kwargs["cwd"]) / "opencode.json"
            configs.append(json.loads(config_path.read_text()))
            proxy_args = configs[0]["mcp"]["plane"]["command"]

        if Driver is ClaudeCliDriver:
            assert "--strict-mcp-config" in cmd
            assert set(configs[0]["mcpServers"]) == {"plane"}
        elif Driver is CodexCliDriver:
            assert set(configs[0]["mcp_servers"]) == {"plane"}
        elif Driver is AntigravityCliDriver:
            assert set(configs[0]["mcpServers"]) == {"plane"}
            assert all(kwargs["env"].get(name) for name in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"))
        else:
            assert set(configs[0]["mcp"]) == {"plane"}
            assert all(kwargs["env"].get(name) for name in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"))

        assert proxy_args is not None and "--evidence-file" in proxy_args
        evidence_path = Path(proxy_args[proxy_args.index("--evidence-file") + 1])
        launch_cwd = Path(kwargs["cwd"]).resolve()
        assert not evidence_path.resolve().is_relative_to(launch_cwd)
        # Even a lazy MCP launcher leaves only non-invertible fingerprints for a
        # shell-capable agent that follows the pathname before proxy startup.
        evidence_config = evidence_path.read_text(encoding="utf-8")
        seen["surface"] = json.dumps({"argv": cmd, "configs": configs, "evidence_file": evidence_config})
        out = (
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "ok",
                    "session_id": "s",
                    "num_turns": 1,
                }
            )
            if Driver is ClaudeCliDriver
            else "{}"
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    kwargs = {
        "runner": fake_run,
        "python_bin": sys.executable,
        "use_proxy": True,
        bin_key: "fake-bin",
    }
    if Driver is CodexCliDriver:
        kwargs["allow_live"] = True
    driver = Driver(**kwargs)
    driver.run_task(
        "hi",
        mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
        model="test-model",
        max_turns=1,
        cwd=tmp_path,
        evidence_sentinels={TARGET_ENTITY_EVIDENCE: [sentinel]},
        evidence_targets={TARGET_ENTITY_EVIDENCE: ["target-1", *grouped_counts]},
        evidence_aggregates={
            TARGET_ENTITY_EVIDENCE: [
                {"kind": "total_count", "value": total_count},
                {"kind": "grouped_counts", "values": grouped_counts},
            ]
        },
    )

    surface = str(seen["surface"])
    assert sentinel not in surface
    assert str(total_count) not in surface
    assert all(str(count) not in surface for count in grouped_counts.values())
    assert EVIDENCE_SENTINELS_ENV not in surface


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


def _timeout_harvests_sidecar_calls(tmp_path):
    side_calls = [
        {
            "tool": "pre_timeout",
            "args": {"a": 1},
            "is_error": False,
            "result_chars": 3,
            "duration_ms": 1,
            "seq": 1,
            "observed_sentinels": [TARGET_ENTITY_EVIDENCE],
        },
        {
            "row_type": "proxy_meta",
            "relayed_lines": 1,
            "unparsed_lines": 0,
            "unmatched_responses": 0,
            "notifications": 0,
            "pending_left": 0,
            "non_tool_pending_left": 0,
            "last_seq": 1,
            "tool_request_count": 1,
            "child_killed": False,
            "evidence_trace_available": True,
        },
    ]

    def fake_run(cmd, **kwargs):
        # Plant a complete sidecar next to the mcp config (temp dir still alive).
        cfg = Path(cmd[cmd.index("--mcp-config") + 1])
        # Sidecar path is in the same temp dir as mcp.json for Claude.
        # Find sidecar from proxy args in mcp config.
        mcp = json.loads(cfg.read_text())
        assert EVIDENCE_SENTINELS_ENV not in mcp["mcpServers"]["plane"]["env"]
        args = mcp["mcpServers"]["plane"]["args"]
        assert "--evidence-file" in args
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
        evidence_sentinels={TARGET_ENTITY_EVIDENCE: ["hidden-target-fact-7b0a1f9c"]},
        evidence_targets={TARGET_ENTITY_EVIDENCE: ["target-1"]},
    )
    assert run.stopped_reason == "timeout"
    assert run.call_source == "proxy"
    assert len(run.calls) == 1
    assert run.calls[0]["tool"] == "pre_timeout"
    assert run.calls[0]["observed_sentinels"] == [TARGET_ENTITY_EVIDENCE]
    assert run.evidence_trace_available is True


def test_cli_verifier_compares_observed_aggregate_values_after_agent_run(tmp_path):
    rows = [
        {
            "tool": "count_work_items",
            "args": {"project_id": "project-1"},
            "is_error": False,
            "result_chars": 17,
            "duration_ms": 1,
            "seq": 1,
            "observed_sentinels": [],
            "observed_aggregates": [{"label": TARGET_ENTITY_EVIDENCE, "kind": "total_count", "value": 3}],
        },
        {
            "tool": "count_work_items",
            "args": {"project_id": "project-1"},
            "is_error": False,
            "result_chars": 17,
            "duration_ms": 1,
            "seq": 2,
            "observed_sentinels": [],
            "observed_aggregates": [{"label": TARGET_ENTITY_EVIDENCE, "kind": "total_count", "value": 4}],
        },
        {
            "row_type": "proxy_meta",
            "relayed_lines": 2,
            "unparsed_lines": 0,
            "unmatched_responses": 0,
            "notifications": 0,
            "pending_left": 0,
            "non_tool_pending_left": 0,
            "last_seq": 2,
            "tool_request_count": 2,
            "child_killed": False,
            "evidence_trace_available": True,
        },
    ]

    def fake_run(cmd, **kwargs):
        del kwargs
        config_path = Path(cmd[cmd.index("--mcp-config") + 1])
        config = json.loads(config_path.read_text(encoding="utf-8"))
        proxy_args = config["mcpServers"]["plane"]["args"]
        sidecar = Path(proxy_args[proxy_args.index("--log") + 1])
        sidecar.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

    driver = ClaudeCliDriver(runner=fake_run, use_proxy=True, python_bin=sys.executable)
    run = driver.run_task(
        "hi",
        mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
        model="sonnet",
        max_turns=1,
        cwd=tmp_path,
        evidence_targets={TARGET_ENTITY_EVIDENCE: ["project-1"]},
        evidence_aggregates={TARGET_ENTITY_EVIDENCE: [{"kind": "total_count", "value": 4}]},
    )

    assert run.evidence_trace_available is True
    assert run.calls[0]["observed_sentinels"] == []
    assert run.calls[1]["observed_sentinels"] == [TARGET_ENTITY_EVIDENCE]


def _timeout_harvest_waits_for_delayed_meta(tmp_path):
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


def _timeout_incomplete_sidecar_cannot_supply_response_evidence(tmp_path):
    def fake_run(cmd, **kwargs):
        cfg = Path(cmd[cmd.index("--mcp-config") + 1])
        mcp = json.loads(cfg.read_text())
        args = mcp["mcpServers"]["plane"]["args"]
        side = Path(args[args.index("--log") + 1])
        call = {
            "tool": "evidence_call",
            "args": {},
            "is_error": False,
            "result_chars": 5,
            "duration_ms": 1,
            "seq": 1,
            "observed_sentinels": [TARGET_ENTITY_EVIDENCE],
        }
        meta = {
            "row_type": "proxy_meta",
            "pending_left": 0,
            "pumps_alive": False,
            "last_seq": 1,
            "tool_request_count": 1,
            "evidence_trace_available": True,
        }
        side.write_text(
            "\n".join((json.dumps(call), "{corrupted mid-stream row", json.dumps(meta))) + "\n",
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=1)

    driver = ClaudeCliDriver(runner=fake_run, use_proxy=True, python_bin=sys.executable)
    run = driver.run_task(
        "hi",
        mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
        model="sonnet",
        max_turns=1,
        cwd=tmp_path,
        evidence_sentinels={TARGET_ENTITY_EVIDENCE: ["hidden-target-fact-7b0a1f9c"]},
        evidence_targets={TARGET_ENTITY_EVIDENCE: ["target-1"]},
    )

    assert run.call_source == "proxy"
    assert run.calls[0]["observed_sentinels"] == [TARGET_ENTITY_EVIDENCE]
    assert run.evidence_trace_available is False
    assert "proxy_sidecar_incomplete:skipped_rows=1" in run.notes
    assert "proxy_response_evidence_unavailable" in run.notes


@pytest.mark.parametrize(
    "case",
    case_params(
        _timeout_harvests_sidecar_calls,
        _timeout_harvest_waits_for_delayed_meta,
        _timeout_incomplete_sidecar_cannot_supply_response_evidence,
    ),
)
def test_timeout_behaviours(case, tmp_path):
    case(tmp_path)


def test_wait_for_proxy_meta_unit(tmp_path: Path):
    side = tmp_path / "s.jsonl"
    side.write_text("", encoding="utf-8")
    assert wait_for_proxy_meta(side, max_wait_s=0.15, poll_s=0.05) is False
    side.write_text(json.dumps({"row_type": "proxy_meta", "pending_left": 0}) + "\n", encoding="utf-8")
    assert wait_for_proxy_meta(side, max_wait_s=1.0, poll_s=0.05) is True


def test_normal_completion_waits_for_delayed_proxy_meta(tmp_path: Path):
    """The shared normal path must not harvest the call row before final metadata."""
    import threading

    early_notes: list[str] = []
    meta_written = threading.Event()
    writer_threads: list[threading.Thread] = []

    def fake_run(cmd, **kwargs):
        del kwargs
        config_path = Path(cmd[cmd.index("--mcp-config") + 1])
        config = json.loads(config_path.read_text(encoding="utf-8"))
        proxy_args = config["mcpServers"]["plane"]["args"]
        sidecar = Path(proxy_args[proxy_args.index("--log") + 1])
        call = {
            "tool": "delayed_meta_tool",
            "args": {"n": 1},
            "is_error": False,
            "result_chars": 2,
            "duration_ms": 1,
            "seq": 1,
        }
        sidecar.write_text(json.dumps(call) + "\n", encoding="utf-8")
        proxy_pid_path(sidecar).write_text(str(os.getpid()), encoding="ascii")

        # This is the historical harvest: the call exists, but finalization has
        # not happened, so accepting it now manufactures recorder loss.
        early = apply_proxy_sidecar([], [], sidecar, early_notes, max_wait_s=0)
        assert early.trace_integrity is False
        assert "proxy_sidecar_incomplete:no_meta=1" in early_notes

        def write_meta_later() -> None:
            time.sleep(0.08)
            meta = {
                "row_type": "proxy_meta",
                "pending_left": 0,
                "non_tool_pending_left": 0,
                "unmatched_responses": 0,
                "unparsed_lines": 0,
                "recorder_errors": 0,
                "pumps_alive": False,
                "last_seq": 1,
                "tool_request_count": 1,
            }
            with sidecar.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(meta) + "\n")
            meta_written.set()

        writer = threading.Thread(target=write_meta_later, daemon=True)
        writer_threads.append(writer)
        writer.start()
        output = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "done",
            "session_id": "delayed-meta-session",
            "num_turns": 1,
        }
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(output), stderr="")

    driver = ClaudeCliDriver(runner=fake_run, use_proxy=True, python_bin=sys.executable)
    run = driver.run_task(
        "hi",
        mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
        model="sonnet",
        max_turns=1,
        cwd=tmp_path,
    )
    for writer in writer_threads:
        writer.join(timeout=1.0)

    assert meta_written.is_set()
    assert run.trace_integrity is True
    assert run.trace_integrity_reason is None
    assert run.call_source == "proxy"
    assert [call["tool"] for call in run.calls] == ["delayed_meta_tool"]
    assert not any("no_meta" in note for note in run.notes)


def test_proxy_meta_wait_timeout_is_fatal_and_diagnosable(tmp_path: Path):
    sidecar = tmp_path / "never-finalized.jsonl"
    sidecar.write_text(
        json.dumps({"tool": "unfinished", "args": {}, "seq": 1}) + "\n",
        encoding="utf-8",
    )
    proxy_pid_path(sidecar).write_text(str(os.getpid()), encoding="ascii")
    notes: list[str] = []

    result = apply_proxy_sidecar([], [], sidecar, notes, poll_s=0.005, max_wait_s=0.03)

    assert result.trace_integrity is False
    assert result.trace_integrity_reason == "recorder_loss"
    assert "proxy_meta_wait_timeout:proxy_alive=1" in notes
    assert "proxy_sidecar_incomplete:no_meta=1" in notes


def test_proxy_exit_before_meta_is_fatal_and_diagnosable(tmp_path: Path):
    sidecar = tmp_path / "exited-before-meta.jsonl"
    sidecar.write_text(
        json.dumps({"tool": "unfinished", "args": {}, "seq": 1}) + "\n",
        encoding="utf-8",
    )
    exited = subprocess.Popen([sys.executable, "-c", "pass"])
    exited.wait(timeout=2.0)
    proxy_pid_path(sidecar).write_text(str(exited.pid), encoding="ascii")
    notes: list[str] = []
    started = time.monotonic()

    result = apply_proxy_sidecar([], [], sidecar, notes, poll_s=0.01, max_wait_s=1.0)

    assert time.monotonic() - started < 0.2
    assert result.trace_integrity is False
    assert result.trace_integrity_reason == "recorder_loss"
    assert "proxy_meta_missing_after_proxy_exit" in notes
    assert "proxy_sidecar_incomplete:no_meta=1" in notes


def test_proxy_meta_fast_path_does_not_sleep(tmp_path: Path, monkeypatch):
    sidecar = tmp_path / "already-finalized.jsonl"
    meta = {
        "row_type": "proxy_meta",
        "pending_left": 0,
        "last_seq": 0,
        "tool_request_count": 0,
    }
    sidecar.write_text(json.dumps(meta) + "\n", encoding="utf-8")
    monkeypatch.setattr("evals.drivers.cli.sidecar.time.sleep", lambda _seconds: pytest.fail("fast path slept"))
    notes: list[str] = []
    started = time.monotonic()

    result = apply_proxy_sidecar([], [], sidecar, notes, max_wait_s=1.0)

    assert time.monotonic() - started < 0.1
    assert result.trace_integrity is True
    assert not any("proxy_meta_wait" in note for note in notes)


def test_harvest_proxy_after_cli_timeout_incomplete_note(tmp_path: Path):
    """If meta never arrives, harvest still returns with incomplete note."""
    side = tmp_path / "s.jsonl"
    side.write_text(
        json.dumps({"tool": "only", "args": {}, "seq": 1, "is_error": False, "result_chars": 0}) + "\n",
        encoding="utf-8",
    )
    notes: list[str] = []
    calls, _client, src = harvest_proxy_after_cli_timeout([], [], side, notes, max_wait_s=0.25)
    assert "proxy_meta_wait_timeout:proxy_state=unknown" in notes
    assert len(calls) == 1
    assert src == "proxy"
    assert any("incomplete" in n for n in notes)
