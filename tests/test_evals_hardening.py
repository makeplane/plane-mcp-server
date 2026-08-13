"""Offline tests for eval harness hardening (taxonomy, resume, seed retry, fingerprint, canary)."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from plane.errors.errors import HttpError

from evals import report as report_mod
from evals import run as run_mod
from evals import runner as runner_mod
from evals import seed as seed_mod
from evals.drivers import AgentRun, ClaudeCliDriver, parse_claude_json_result
from evals.report import is_infra_error_row, load_rows, summarize
from evals.results import RESULT_SCHEMA_VERSION
from evals.run import (
    is_infra_cli_stop_reason,
    load_resume_skip_keys,
    run_canary,
    run_live,
    should_skip_resume_row,
)
from evals.seed import create_project_with_identifier_retry, is_identifier_collision
from evals.tasks import battery_fingerprint, task_author

# Pinned hash of the fixed synthetic catalog in test_battery_fingerprint_stable_and_sensitive.
# Recompute only if the serialization format of battery_fingerprint changes deliberately.
PINNED_SYNTHETIC_BATTERY = "81be78bde8c7"


def _data_rows(path: Path) -> list[dict]:
    """Parse JSONL skipping meta / non-task lines (run.py writes a meta header)."""
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("row_type") == "meta" or row.get("task_id") is None:
            continue
        out.append(row)
    return out


@pytest.fixture(autouse=True)
def _eval_creds(monkeypatch):
    monkeypatch.setenv("EVAL_PLANE_API_KEY", "test-key")
    monkeypatch.setenv("EVAL_PLANE_WORKSPACE_SLUG", "test-ws")
    monkeypatch.delenv("EVAL_PLANE_BASE_URL", raising=False)
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.delenv("REDIS_PORT", raising=False)


# ---------------------------------------------------------------------------
# Resume skip decision (pure)
# ---------------------------------------------------------------------------


def test_should_skip_resume_row_completed_success():
    assert should_skip_resume_row({"error": None, "error_class": None, "success": True}) is True


def test_should_skip_resume_row_verify_fail_without_error():
    # Completed attempt (agent ran, verify failed) — do not re-run on resume.
    assert should_skip_resume_row({"error": None, "error_class": None, "success": False}) is True


def test_should_skip_resume_row_infra_seed_retries():
    assert should_skip_resume_row({"error": "HttpError: 409", "error_class": "infra_seed"}) is False


def test_should_skip_resume_row_infra_cli_retries():
    assert should_skip_resume_row({"error": "timeout after 120s", "error_class": "infra_cli"}) is False


def test_should_skip_resume_row_non_null_error_retries():
    assert should_skip_resume_row({"error": "TypeError: x", "error_class": "task"}) is False
    assert should_skip_resume_row({"error": "boom", "error_class": None}) is False


def test_load_resume_skip_keys_summary(tmp_path: Path):
    p = tmp_path / "out.jsonl"
    rows = [
        {"task_id": "R1", "rep": 0, "surface": "v2", "error": None, "error_class": None},
        {"task_id": "R1", "rep": 1, "surface": "v2", "error": "x", "error_class": "infra_seed"},
        {"task_id": "W1", "rep": 0, "surface": "v2", "error": None, "success": False},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    skip, n_skip, n_retry = load_resume_skip_keys(p, surface="v2")
    assert skip == {("R1", 0), ("W1", 0)}
    assert n_skip == 2
    assert n_retry == 1


def test_load_resume_skip_keys_n_retry_ignores_later_success(tmp_path: Path):
    """Historical error row whose later row succeeded must not inflate n_retry."""
    p = tmp_path / "out.jsonl"
    rows = [
        {"task_id": "R1", "rep": 0, "surface": "v2", "error": "boom", "error_class": "infra_cli"},
        {"task_id": "R1", "rep": 0, "surface": "v2", "error": None, "error_class": None, "success": True},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    skip, n_skip, n_retry = load_resume_skip_keys(p, surface="v2")
    assert skip == {("R1", 0)}
    assert n_skip == 1
    assert n_retry == 0


def test_load_resume_skip_keys_surface_mismatch(tmp_path: Path):
    p = tmp_path / "out.jsonl"
    p.write_text(json.dumps({"task_id": "R1", "rep": 0, "surface": "full", "error": None}) + "\n")
    with pytest.raises(SystemExit, match="surface"):
        load_resume_skip_keys(p, surface="v2")


def test_load_resume_skip_keys_battery_model_driver_mismatch(tmp_path: Path):
    p = tmp_path / "out.jsonl"
    p.write_text(
        json.dumps(
            {
                "task_id": "R1",
                "rep": 0,
                "surface": "v2",
                "battery": "aaaaaaaaaaaa",
                "model": "sonnet",
                "driver": "claude-cli",
                "error": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="battery"):
        load_resume_skip_keys(p, surface="v2", battery="bbbbbbbbbbbb")
    with pytest.raises(SystemExit, match="model"):
        load_resume_skip_keys(p, surface="v2", battery="aaaaaaaaaaaa", model="haiku")
    with pytest.raises(SystemExit, match="driver"):
        load_resume_skip_keys(p, surface="v2", battery="aaaaaaaaaaaa", model="sonnet", driver="sdk")
    # Missing keys on older rows: pass (back-compat)
    p2 = tmp_path / "old.jsonl"
    p2.write_text(json.dumps({"task_id": "R1", "rep": 0, "surface": "v2", "error": None}) + "\n")
    skip, _, _ = load_resume_skip_keys(p2, surface="v2", battery="anything", model="sonnet", driver="claude-cli")
    assert ("R1", 0) in skip


def test_resume_identity_uses_resolved_model_not_tier_label(tmp_path: Path):
    p = tmp_path / "tiered.jsonl"
    p.write_text(
        json.dumps(
            {
                "task_id": "R1",
                "rep": 0,
                "surface": "v2",
                "model": "provider-reported-id",
                "requested_model": "standard",
                "requested_tier": "standard",
                "resolved_model": "old-standard-id",
                "error": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    skip, _, _ = load_resume_skip_keys(p, surface="v2", model="old-standard-id")
    assert skip == {("R1", 0)}
    with pytest.raises(SystemExit, match="model"):
        load_resume_skip_keys(p, surface="v2", model="new-standard-id")


def test_load_resume_skip_keys_truncated_json(tmp_path: Path, capsys):
    p = tmp_path / "out.jsonl"
    p.write_text(
        json.dumps({"task_id": "R1", "rep": 0, "surface": "v2", "error": None})
        + "\n"
        + '{"task_id": "W1", "rep": 0, "surface": "v2", "error":\n',  # truncated
        encoding="utf-8",
    )
    skip, n_skip, n_retry = load_resume_skip_keys(p, surface="v2")
    assert skip == {("R1", 0)}
    assert n_skip == 1
    err = capsys.readouterr().err
    assert "invalid JSON" in err


def test_load_resume_skip_keys_missing_file(tmp_path: Path):
    skip, n_skip, n_retry = load_resume_skip_keys(tmp_path / "missing.jsonl", surface="v2")
    assert skip == set() and n_skip == 0 and n_retry == 0


def test_parse_args_resume_and_canary():
    a = run_mod.parse_args(["--resume", "evals/results/x.jsonl", "--dry-run"])
    assert a.resume == "evals/results/x.jsonl"
    b = run_mod.parse_args(["--canary", "--tasks", "R1"])
    assert b.canary is True


# ---------------------------------------------------------------------------
# Error taxonomy (seed raise → infra_seed row)
# ---------------------------------------------------------------------------


def test_run_live_seed_failure_is_infra_seed(tmp_path: Path, monkeypatch):
    out = tmp_path / "rows.jsonl"

    fake_plane = MagicMock()
    monkeypatch.setattr(runner_mod, "make_plane_client", lambda: (fake_plane, "test-ws"))

    def boom_seed(plane, run_id, needs, ctx):
        ctx["project_name"] = "EVAL deadbeef"
        raise HttpError("identifier already taken", 409)

    monkeypatch.setattr(runner_mod, "seed", boom_seed)
    monkeypatch.setattr(runner_mod, "teardown", lambda plane, ctx: None)

    task = {
        "id": "T1",
        "prompt": "do {project}",
        "tags": set(),
        "optimal_tools": {"list_work_items"},
        "alternate_tools": set(),
        "optimal_calls": 1,
        "needs": set(),
        "verify": lambda *a, **k: (_ for _ in ()).throw(AssertionError("verify must not run")),
    }

    rc = asyncio.run(
        run_live(
            [task],
            model_alias="standard",
            reps=1,
            surface="full",
            out_path=out,
            driver_name="claude-cli",
        )
    )
    assert rc == 0
    rows = _data_rows(out)
    assert len(rows) == 1
    row = rows[0]
    assert row["schema_version"] == RESULT_SCHEMA_VERSION
    assert row["error_class"] == "infra_seed"
    assert row["success"] is False
    assert "HttpError" in (row["error"] or "")
    assert "identifier" in (row["error"] or "").lower()
    assert row["battery"]  # fingerprint written
    assert row["requested_model"] == "standard"
    assert row["requested_tier"] == "standard"
    assert row["resolved_model"] == "sonnet"
    assert row["model"] == "sonnet"
    meta = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert meta["schema_version"] == RESULT_SCHEMA_VERSION
    assert meta["requested_tier"] == "standard"
    assert meta["resolved_model"] == "sonnet"


def test_run_live_driver_exception_is_infra_cli(tmp_path: Path, monkeypatch):
    out = tmp_path / "rows.jsonl"
    fake_plane = MagicMock()
    monkeypatch.setattr(runner_mod, "make_plane_client", lambda: (fake_plane, "test-ws"))

    def ok_seed(plane, run_id, needs, ctx):
        ctx.update({"project_name": "EVAL deadbeef", "project_id": "p1"})

    monkeypatch.setattr(runner_mod, "seed", ok_seed)
    monkeypatch.setattr(runner_mod, "teardown", lambda plane, ctx: None)

    class BoomDriver:
        name = "claude-cli"

        def run_task(self, *args, **kwargs):
            raise RuntimeError("claude cli failed: json_parse_failed")

    monkeypatch.setattr(runner_mod, "get_driver", lambda name, **kw: BoomDriver())

    task = {
        "id": "T2",
        "prompt": "do {project}",
        "tags": set(),
        "optimal_tools": {"list_work_items"},
        "alternate_tools": set(),
        "optimal_calls": 1,
        "needs": set(),
        "verify": lambda *a, **k: (False, "nope"),
    }

    rc = asyncio.run(
        run_live(
            [task],
            model_alias="sonnet",
            reps=1,
            surface="full",
            out_path=out,
            driver_name="claude-cli",
        )
    )
    assert rc == 0
    row = _data_rows(out)[0]
    assert row["error_class"] == "infra_cli"
    assert "RuntimeError" in (row["error"] or "")


def test_run_live_timeout_agent_is_infra_cli(tmp_path: Path, monkeypatch):
    """Driver returns stopped_reason=timeout → row error_class=infra_cli, battery continues."""
    from evals.drivers import agent_run_to_harness_dict

    out = tmp_path / "rows.jsonl"
    fake_plane = MagicMock()
    monkeypatch.setattr(runner_mod, "make_plane_client", lambda: (fake_plane, "test-ws"))
    monkeypatch.setattr(runner_mod, "seed", lambda *a, **k: k["ctx"].update({"project_name": "P", "project_id": "1"}))
    monkeypatch.setattr(runner_mod, "teardown", lambda *a, **k: None)

    class TimeoutDriver:
        name = "claude-cli"

        def run_task(self, *args, **kwargs):
            return AgentRun(
                calls=[],
                final_text="",
                usage=None,
                stopped_reason="timeout",
                notes=["timeout after 900s"],
            )

    monkeypatch.setattr(runner_mod, "get_driver", lambda name, **kw: TimeoutDriver())

    verify_calls: list[Any] = []

    async def verify(*a, **k):
        verify_calls.append(1)
        return True, "should not run"

    task = {
        "id": "T3",
        "prompt": "do {project}",
        "tags": set(),
        "optimal_tools": {"list_work_items"},
        "alternate_tools": set(),
        "optimal_calls": 1,
        "needs": set(),
        "verify": verify,
    }

    rc = asyncio.run(
        run_live(
            [task],
            model_alias="sonnet",
            reps=1,
            surface="full",
            out_path=out,
            driver_name="claude-cli",
        )
    )
    assert rc == 0
    row = _data_rows(out)[0]
    assert row["error_class"] == "infra_cli"
    assert row["error"] == "timeout after 900s"  # from driver_notes, not recomputed
    assert row["stop_reason"] == "timeout"
    assert verify_calls == []
    d = agent_run_to_harness_dict(
        AgentRun(calls=[], final_text="", usage=None, stopped_reason="timeout"),
        optimal=set(),
        alternate=set(),
        classify=lambda t, o, a: "out_of_set",
    )
    assert d["stop_reason"] == "timeout"


def test_run_live_error_during_execution_is_infra_cli(tmp_path: Path, monkeypatch):
    """exit 1 + parseable JSON subtype error_during_execution → infra_cli; verify not called."""
    out = tmp_path / "rows.jsonl"
    fake_plane = MagicMock()
    monkeypatch.setattr(runner_mod, "make_plane_client", lambda: (fake_plane, "test-ws"))
    monkeypatch.setattr(runner_mod, "seed", lambda *a, **k: k["ctx"].update({"project_name": "P", "project_id": "1"}))
    monkeypatch.setattr(runner_mod, "teardown", lambda *a, **k: None)

    payload = {
        "type": "result",
        "subtype": "error_during_execution",
        "is_error": True,
        "result": "MCP server crashed",
        "session_id": "sess-err",
        "num_turns": 1,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout=json.dumps(payload), stderr="claude boom")

    monkeypatch.setattr(runner_mod, "get_driver", lambda name, **kw: ClaudeCliDriver(runner=fake_run))

    verify_calls: list[Any] = []

    async def verify(*a, **k):
        verify_calls.append(1)
        return False, "nope"

    task = {
        "id": "T4",
        "prompt": "do {project}",
        "tags": set(),
        "optimal_tools": {"list_work_items"},
        "alternate_tools": set(),
        "optimal_calls": 1,
        "needs": set(),
        "verify": verify,
    }
    rc = asyncio.run(
        run_live(
            [task],
            model_alias="sonnet",
            reps=1,
            surface="full",
            out_path=out,
            driver_name="claude-cli",
        )
    )
    assert rc == 0
    row = _data_rows(out)[0]
    assert row["error_class"] == "infra_cli"
    assert row["stop_reason"] == "error_during_execution"
    assert verify_calls == []
    assert "claude_exit=1" in (row.get("driver_notes") or [])


def test_run_live_error_max_turns_is_task_path(tmp_path: Path, monkeypatch):
    """exit 1 + subtype error_max_turns stays in the task denominator (not infra_cli)."""
    out = tmp_path / "rows.jsonl"
    fake_plane = MagicMock()
    monkeypatch.setattr(runner_mod, "make_plane_client", lambda: (fake_plane, "test-ws"))
    monkeypatch.setattr(runner_mod, "seed", lambda *a, **k: k["ctx"].update({"project_name": "P", "project_id": "1"}))
    monkeypatch.setattr(runner_mod, "teardown", lambda *a, **k: None)

    payload = {
        "type": "result",
        "subtype": "error_max_turns",
        "is_error": True,
        "result": "hit max turns",
        "session_id": "sess-max",
        "num_turns": 15,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(runner_mod, "get_driver", lambda name, **kw: ClaudeCliDriver(runner=fake_run))

    verify_calls: list[Any] = []

    async def verify(*a, **k):
        verify_calls.append(1)
        return False, "agent exhausted turns"

    task = {
        "id": "T5",
        "prompt": "do {project}",
        "tags": set(),
        "optimal_tools": {"list_work_items"},
        "alternate_tools": set(),
        "optimal_calls": 1,
        "needs": set(),
        "verify": verify,
    }
    rc = asyncio.run(
        run_live(
            [task],
            model_alias="sonnet",
            reps=1,
            surface="full",
            out_path=out,
            driver_name="claude-cli",
        )
    )
    assert rc == 0
    row = _data_rows(out)[0]
    assert row["error_class"] is None
    assert row["stop_reason"] == "error_max_turns"
    assert row["success"] is False
    assert verify_calls == [1]


def test_is_infra_cli_stop_reason_matrix():
    assert is_infra_cli_stop_reason("timeout") is True
    assert is_infra_cli_stop_reason("error_during_execution") is True
    assert is_infra_cli_stop_reason("error") is True
    assert is_infra_cli_stop_reason("error_max_turns") is False
    assert is_infra_cli_stop_reason("end_turn") is False
    assert is_infra_cli_stop_reason("max_turns") is False


def test_parse_claude_json_preserves_error_subtype():
    out = parse_claude_json_result(
        {
            "type": "result",
            "subtype": "error_during_execution",
            "is_error": True,
            "result": "x",
            "session_id": "s",
            "num_turns": 1,
        }
    )
    assert out["stopped_reason"] == "error_during_execution"


# ---------------------------------------------------------------------------
# Driver timeout containment
# ---------------------------------------------------------------------------


def test_claude_driver_timeout_returns_agent_run_not_raise():
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout") or 120)

    driver = ClaudeCliDriver(runner=fake_run)
    run = driver.run_task(
        "hello",
        mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
        model="sonnet",
        max_turns=2,
        cwd=Path("/tmp"),
    )
    assert run.stopped_reason == "timeout"
    assert run.calls == []
    assert any("timeout after" in n for n in run.notes)


def test_claude_driver_json_parse_failure_raises_for_infra_cli():
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="not-json", stderr="boom")

    driver = ClaudeCliDriver(runner=fake_run)
    with pytest.raises(RuntimeError, match="claude cli failed"):
        driver.run_task(
            "hello",
            mcp_env={"PLANE_API_KEY": "k", "PLANE_WORKSPACE_SLUG": "ws"},
            model=None,
            max_turns=1,
            cwd=Path("/tmp"),
        )


# ---------------------------------------------------------------------------
# Seed identifier retry
# ---------------------------------------------------------------------------


def test_create_project_retries_409_then_succeeds(monkeypatch):
    attempts: list[str] = []

    class FakeProjects:
        def create(self, *, workspace_slug, data):
            ident = data.identifier
            attempts.append(ident)
            if len(attempts) < 3:
                raise HttpError("Project identifier already taken", 409)
            return MagicMock(id="proj-ok", identifier=ident)

    plane = MagicMock()
    plane.projects = FakeProjects()

    # Force deterministic retries after first collision.
    suffixes = iter(["AAAA", "BBBB"])
    monkeypatch.setattr(seed_mod.secrets, "token_hex", lambda n: next(suffixes))

    project = create_project_with_identifier_retry(
        plane,
        "ws",
        name="EVAL abcd",
        identifier_prefix="EV",
        initial_suffix="DEAD",
    )
    assert project.id == "proj-ok"
    assert attempts[0] == "EVDEAD"
    assert len(attempts) == 3
    assert attempts[1] != attempts[0]
    assert attempts[2] != attempts[1]
    assert attempts[1] == "EVAAAA"
    assert attempts[2] == "EVBBBB"


def test_create_project_raises_after_max_409s(monkeypatch):
    attempts: list[str] = []

    class Always409:
        def create(self, *, workspace_slug, data):
            attempts.append(data.identifier)
            raise HttpError("identifier already taken", 409)

    plane = MagicMock()
    plane.projects = Always409()
    suffixes = iter(["1111", "2222", "3333", "should-not-use"])
    monkeypatch.setattr(seed_mod.secrets, "token_hex", lambda n: next(suffixes))

    with pytest.raises(HttpError) as ei:
        create_project_with_identifier_retry(
            plane,
            "ws",
            name="EVAL x",
            identifier_prefix="EV",
            initial_suffix="0000",
        )
    assert ei.value.status_code == 409
    assert len(attempts) == 3
    assert attempts[0] == "EV0000"
    assert attempts[1] != attempts[0]
    assert attempts[1] == "EV1111"
    assert attempts[2] == "EV2222"


def test_create_project_non_collision_error_does_not_retry():
    class Fail500:
        def create(self, *, workspace_slug, data):
            raise HttpError("server error", 500)

    plane = MagicMock()
    plane.projects = Fail500()
    with pytest.raises(HttpError) as ei:
        create_project_with_identifier_retry(
            plane,
            "ws",
            name="EVAL x",
            identifier_prefix="EV",
            initial_suffix="0000",
        )
    assert ei.value.status_code == 500


def test_identifier_collision_requires_status_and_language():
    assert is_identifier_collision(HttpError("identifier already taken", 409)) is True
    assert is_identifier_collision(HttpError("project exists", 400)) is True
    # Validation-shaped: mentions identifier but not collision language → no retry
    assert is_identifier_collision(HttpError("identifier is required", 400)) is False
    assert is_identifier_collision(HttpError("identifier already taken", 500)) is False


# ---------------------------------------------------------------------------
# Battery fingerprint + author
# ---------------------------------------------------------------------------


def test_task_author_default():
    assert task_author({}) == "claude"
    assert task_author({"author": "alice"}) == "alice"


def test_battery_fingerprint_stable_and_sensitive():
    t1 = {
        "id": "A",
        "prompt": "p1 {project}",
        "optimal_tools": {"b", "a"},
        "alternate_tools": {"c"},
        "optimal_calls": 2,
        "surface_tools": {
            "v2": {
                "optimal_tools": {"find_work_items"},
                "alternate_tools": set(),
            }
        },
    }
    t2 = {
        "id": "B",
        "prompt": "p2",
        "optimal_tools": {"x"},
        "alternate_tools": set(),
        "optimal_calls": 1,
        "surface_tools": {},
    }
    # Order of list must not matter (sorted by id).
    h1 = battery_fingerprint([t2, t1])
    h2 = battery_fingerprint([t1, t2])
    assert h1 == h2 == PINNED_SYNTHETIC_BATTERY
    assert len(h1) == 12

    t1_edit = {**t1, "prompt": "p1 edited {project}"}
    assert battery_fingerprint([t1_edit, t2]) != PINNED_SYNTHETIC_BATTERY

    # Subset of selected tasks → different fingerprint (documented ceiling).
    assert battery_fingerprint([t1]) != PINNED_SYNTHETIC_BATTERY


def test_battery_fingerprint_catalog_is_nonempty():
    from evals.tasks import TASKS

    fp = battery_fingerprint()
    assert len(fp) == 12
    assert battery_fingerprint(list(TASKS)) == fp


def test_battery_fingerprint_changes_with_new_debias_tasks():
    """Adding I/L content must change the catalog fingerprint (content hash)."""
    from evals.tasks import TASKS, TASKS_BY_ID

    full = battery_fingerprint()
    without_debias = [t for t in TASKS if not str(t.get("id", "")).startswith(("I", "L"))]
    assert without_debias, "pre-debias catalog should be non-empty"
    reduced = battery_fingerprint(without_debias)
    assert reduced != full
    # Single new task also moves the hash relative to a reduced set.
    assert battery_fingerprint(without_debias + [TASKS_BY_ID["I1"]]) != reduced


# ---------------------------------------------------------------------------
# Report excludes infra_ rows
# ---------------------------------------------------------------------------


def test_summarize_excludes_infra_errors_from_success():
    rows = [
        {"task_id": "R1", "success": True, "num_calls": 2, "calls": [], "error": None},
        {
            "task_id": "R1",
            "success": False,
            "num_calls": 0,
            "calls": [],
            "error": "HttpError: 409",
            "error_class": "infra_seed",
        },
        {
            "task_id": "R1",
            "success": False,
            "num_calls": 0,
            "calls": [],
            "error": "timeout after 120s",
            "error_class": "infra_cli",
        },
        {"task_id": "R1", "success": False, "num_calls": 3, "calls": [], "error": None},
    ]
    summary = summarize(rows)
    assert summary["_meta"]["infra_errors"] == 2
    assert summary["R1"]["n"] == 2  # only non-infra, non-error rows
    assert summary["R1"]["k"] == 1
    assert summary["R1"]["success"] == "1/2"
    assert summary["R1"]["infra_err"] == 2
    assert is_infra_error_row(rows[1]) is True
    assert is_infra_error_row(rows[0]) is False


def test_print_table_shows_infra_errors(capsys):
    summary = {
        "R1": {
            "n": 1,
            "k": 1,
            "success": "1/1",
            "wilson_lo": 0.2,
            "wilson_hi": 1.0,
            "med_calls": 1.0,
            "calls_q1": 1.0,
            "calls_q3": 1.0,
            "optimal_calls": 1,
            "mispick_rate": 0.0,
            "errored_calls": 0,
            "capped": 0,
            "harness_err": 0,
            "infra_err": 2,
            "med_result_tokens": None,
            "p95_result_tokens": None,
            "med_cum_input": 0.0,
        },
        "_meta": {"infra_errors": 2},
    }
    report_mod.print_table(summary, "Summary: test")
    out = capsys.readouterr().out
    assert "infra errors: 2" in out
    assert "i_err" in out
    assert "R1" in out
    # per-task infra_err value rendered next to h_err
    assert "    2" in out  # i_err column value


def test_is_infra_error_row_covers_sdk():
    assert is_infra_error_row({"error_class": "infra_sdk"}) is True
    assert is_infra_error_row({"error_class": "infra_cli"}) is True
    assert is_infra_error_row({"error_class": "task"}) is False


def test_load_rows_dedupe_latest_wins(tmp_path: Path):
    p = tmp_path / "dup.jsonl"
    rows = [
        {"task_id": "R1", "rep": 0, "surface": "v2", "success": True, "num_calls": 1},
        {"task_id": "R1", "rep": 0, "surface": "v2", "success": False, "num_calls": 9},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    loaded = load_rows(p)  # default dedupe=latest
    assert len(loaded) == 1
    assert loaded[0].num_calls == 9
    assert loaded[0].success is False


def test_load_rows_no_dedupe_warns_on_duplicate_keys(tmp_path: Path, capsys):
    p = tmp_path / "dup.jsonl"
    rows = [
        {"task_id": "R1", "rep": 0, "surface": "v2", "success": True},
        {"task_id": "R1", "rep": 0, "surface": "v2", "success": False},
    ]
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    loaded = load_rows(p, dedupe="none")
    assert len(loaded) == 2
    err = capsys.readouterr().err
    assert "duplicate" in err
    assert "R1" in err


# ---------------------------------------------------------------------------
# Canary mode
# ---------------------------------------------------------------------------


def test_canary_detects_broken_verifier(monkeypatch):
    fake_plane = MagicMock()
    monkeypatch.setattr(runner_mod, "make_plane_client", lambda: (fake_plane, "test-ws"))
    monkeypatch.setattr(runner_mod, "seed", lambda *a, **k: k["ctx"].update({"project_name": "P", "project_id": "1"}))
    monkeypatch.setattr(runner_mod, "teardown", lambda *a, **k: None)

    async def always_ok(plane, ctx, run):
        return True, "false positive"

    async def correctly_fails(plane, ctx, run):
        return False, "empty agent correctly rejected"

    tasks = [
        {
            "id": "GOOD",
            "prompt": "x {project}",
            "optimal_tools": {"a"},
            "alternate_tools": set(),
            "optimal_calls": 1,
            "needs": set(),
            "verify": correctly_fails,
        },
        {
            "id": "BAD",
            "prompt": "y {project}",
            "optimal_tools": {"a"},
            "alternate_tools": set(),
            "optimal_calls": 1,
            "needs": set(),
            "verify": always_ok,
        },
    ]
    rc = asyncio.run(run_canary(tasks, surface="full"))
    assert rc == 1


def test_canary_passes_when_all_verifiers_reject(monkeypatch):
    fake_plane = MagicMock()
    monkeypatch.setattr(runner_mod, "make_plane_client", lambda: (fake_plane, "test-ws"))
    monkeypatch.setattr(runner_mod, "seed", lambda *a, **k: k["ctx"].update({"project_name": "P", "project_id": "1"}))
    monkeypatch.setattr(runner_mod, "teardown", lambda *a, **k: None)

    async def reject(plane, ctx, run):
        assert run == {"final_text": "", "calls": []}
        return False, "no-op rejected"

    tasks = [
        {
            "id": "G1",
            "prompt": "x {project}",
            "optimal_tools": {"a"},
            "alternate_tools": set(),
            "optimal_calls": 1,
            "needs": set(),
            "verify": reject,
        },
    ]
    rc = asyncio.run(run_canary(tasks, surface="full"))
    assert rc == 0


def test_canary_exits_1_when_all_tasks_skipped(monkeypatch):
    fake_plane = MagicMock()
    monkeypatch.setattr(runner_mod, "make_plane_client", lambda: (fake_plane, "test-ws"))
    monkeypatch.setattr(runner_mod, "seed", lambda *a, **k: None)
    monkeypatch.setattr(runner_mod, "teardown", lambda *a, **k: None)
    monkeypatch.setattr(
        runner_mod,
        "resolve_surface_tool_sets",
        lambda task, surface: {
            "skip": "unsupported on surface",
            "optimal_tools": set(),
            "alternate_tools": set(),
            "classification": "exact",
        },
    )
    tasks = [
        {
            "id": "SKIPME",
            "prompt": "x {project}",
            "optimal_tools": {"a"},
            "alternate_tools": set(),
            "optimal_calls": 1,
            "needs": set(),
            "verify": lambda *a, **k: (False, "unused"),
        },
    ]
    rc = asyncio.run(run_canary(tasks, surface="v2"))
    assert rc == 1


# ---------------------------------------------------------------------------
# End-to-end resume
# ---------------------------------------------------------------------------


def test_run_live_resume_skips_completed_retries_infra(tmp_path: Path, monkeypatch):
    out = tmp_path / "resume.jsonl"
    # Pre-write: completed R1/0 + infra R2/0 (same surface/battery/model/driver as this run).
    # Battery is computed from the task list below — seed the file after we know it,
    # or write rows without battery (back-compat) and only check skip/retry behavior.
    prior = [
        {
            "task_id": "R1",
            "rep": 0,
            "surface": "full",
            "driver": "claude-cli",
            "model": "sonnet",
            "error": None,
            "error_class": None,
            "success": True,
        },
        {
            "task_id": "R2",
            "rep": 0,
            "surface": "full",
            "driver": "claude-cli",
            "model": "sonnet",
            "error": "HttpError: 409",
            "error_class": "infra_seed",
            "success": False,
        },
    ]
    out.write_text("\n".join(json.dumps(r) for r in prior) + "\n", encoding="utf-8")

    fake_plane = MagicMock()
    monkeypatch.setattr(runner_mod, "make_plane_client", lambda: (fake_plane, "test-ws"))
    seed_calls: list[str] = []

    def ok_seed(plane, run_id, needs, ctx):
        # Infer task from empty ctx; runner sets project for verify path.
        ctx.update({"project_name": "EVAL resume", "project_id": "p1"})
        seed_calls.append(run_id)

    monkeypatch.setattr(runner_mod, "seed", ok_seed)
    monkeypatch.setattr(runner_mod, "teardown", lambda *a, **k: None)

    class OkDriver:
        name = "claude-cli"

        def run_task(self, *args, **kwargs):
            return AgentRun(
                calls=[{"tool": "list_work_items", "args": {}, "origin": "plane"}],
                final_text="done",
                usage=None,
                stopped_reason="end_turn",
            )

    monkeypatch.setattr(runner_mod, "get_driver", lambda name, **kw: OkDriver())

    async def verify_ok(plane, ctx, run):
        return True, "ok"

    tasks = [
        {
            "id": "R1",
            "prompt": "do {project}",
            "tags": set(),
            "optimal_tools": {"list_work_items"},
            "alternate_tools": set(),
            "optimal_calls": 1,
            "needs": set(),
            "verify": verify_ok,
        },
        {
            "id": "R2",
            "prompt": "do {project}",
            "tags": set(),
            "optimal_tools": {"list_work_items"},
            "alternate_tools": set(),
            "optimal_calls": 1,
            "needs": set(),
            "verify": verify_ok,
        },
    ]

    rc = asyncio.run(
        run_live(
            tasks,
            model_alias="sonnet",
            reps=1,
            surface="full",
            out_path=out,
            driver_name="claude-cli",
            resume=True,
        )
    )
    assert rc == 0
    # Only R2 should have been re-seeded/run (R1 completed → RESUME_SKIP).
    assert len(seed_calls) == 1
    data = _data_rows(out)
    # prior 2 + 1 new R2 row (meta may also exist if file was empty — it wasn't)
    assert len(data) == 3
    new_r2 = data[-1]
    assert new_r2["task_id"] == "R2"
    assert new_r2["success"] is True
    assert new_r2["error_class"] is None
    assert new_r2["final_text"] == "done"
