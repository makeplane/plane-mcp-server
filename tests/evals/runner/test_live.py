"""Offline eval tests for live."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from plane.errors.errors import HttpError

from evals import cli as run_mod
from evals.drivers import (
    ClaudeCliDriver,
)
from evals.report import load_rows, summarize
from evals.results import RESULT_SCHEMA_VERSION, AgentRun, TaskResult
from evals.runner import (
    is_infra_cli_stop_reason,
    run_live,
)
from evals.runner import live as runner_live
from evals.runner.live import stdio_server_env
from evals.tasks.skip import TaskSkipped
from tests.evals.conftest import _data_rows


def _taxonomy_task(
    task_id: str,
    verify: Any,
    *,
    prompt: str = "do {project}",
    needs: set[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": task_id,
        "prompt": prompt,
        "tags": set(),
        "optimal_tools": {"list_work_items"},
        "alternate_tools": {"search_work_items"},
        "optimal_calls": 1,
        "needs": set(needs or set()),
        "verify": verify,
    }


def test_stdio_behaviours(monkeypatch):
    def test_stdio_env_still_works_for_cli_drivers(monkeypatch):
        monkeypatch.setenv("EVAL_PLANE_API_KEY", "k")
        monkeypatch.setenv("EVAL_PLANE_WORKSPACE_SLUG", "ws")
        monkeypatch.delenv("EVAL_PLANE_BASE_URL", raising=False)
        env = stdio_server_env()
        assert env["PLANE_API_KEY"] == "k"
        assert "ANTHROPIC_API_KEY" not in env

    def test_stdio_server_env_does_not_leak_ambient_secrets(monkeypatch):
        monkeypatch.setenv("SOME_SECRET", "x")

        environment = runner_live.stdio_server_env()

        assert "SOME_SECRET" not in environment
        assert environment["PLANE_API_KEY"] == "test-key"
        assert environment["PLANE_WORKSPACE_SLUG"] == "test-ws"
        assert environment["PLANE_BASE_URL"] == "https://api.plane.so"

    with pytest.MonkeyPatch.context() as mp:
        test_stdio_env_still_works_for_cli_drivers(mp)
    with pytest.MonkeyPatch.context() as mp:
        test_stdio_server_env_does_not_leak_ambient_secrets(mp)


def test_live_run_rejects_non_positive_reps(capsys):
    assert run_mod.main(["--tasks", "R1", "--reps", "0"]) == 2
    assert "--reps must be at least 1" in capsys.readouterr().err


def test_run_behaviours(tmp_path, monkeypatch, capsys):
    def test_run_live_seed_failure_is_infra_seed(tmp_path, monkeypatch):
        out = tmp_path / "rows.jsonl"

        fake_plane = MagicMock()
        driver = MagicMock()
        torn: list[dict[str, Any]] = []
        monkeypatch.setattr(runner_live, "make_plane_client", lambda: (fake_plane, "test-ws"))

        def boom_seed(plane, run_id, needs, ctx):
            ctx["project_name"] = "EVAL deadbeef"
            raise HttpError("identifier already taken", 409)

        monkeypatch.setattr(runner_live, "seed", boom_seed)
        monkeypatch.setattr(runner_live, "teardown", lambda plane, ctx: torn.append(dict(ctx)))
        monkeypatch.setattr(runner_live, "get_driver", lambda name, **kw: driver)

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
                label="local",
                out_path=out,
                driver_name="claude-cli",
                resolved_model_id="sonnet",
            )
        )
        assert rc == 0
        rows = _data_rows(out)
        assert len(rows) == 1
        row = rows[0]
        assert row["schema_version"] == RESULT_SCHEMA_VERSION
        assert row["error_class"] == "infra_seed"
        assert row["success"] is False
        assert row["verify_note"] == ""
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
        driver.run_task.assert_not_called()
        assert torn == [{"project_name": "EVAL deadbeef"}]

    def test_run_live_missing_bug_type_uses_context_skip_reason(tmp_path, monkeypatch):
        out = tmp_path / "rows.jsonl"
        driver = MagicMock()
        torn: list[dict[str, Any]] = []

        def seed_without_bug_type(plane, run_id, needs, ctx):
            ctx.update(
                {
                    "project_name": "EVAL no bug type",
                    "project_id": "p1",
                    "bug_type_skip_reason": "plan:work-item-types-disabled",
                }
            )

        monkeypatch.setattr(runner_live, "make_plane_client", lambda: (object(), "test-ws"))
        monkeypatch.setattr(runner_live, "seed", seed_without_bug_type)
        monkeypatch.setattr(runner_live, "teardown", lambda plane, ctx: torn.append(dict(ctx)))
        monkeypatch.setattr(runner_live, "get_driver", lambda name, **kw: driver)

        task = _taxonomy_task(
            "BUGTYPE",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("verify must not run")),
            needs={"bug_type"},
        )
        rc = asyncio.run(run_live([task], model_alias="standard", reps=1, label="local", out_path=out))

        assert rc == 0
        row = _data_rows(out)[0]
        assert row["skipped"] == "plan:work-item-types-disabled"
        assert row["verify_note"] == "plan:work-item-types-disabled"
        assert row["error"] is None
        assert row["error_class"] is None
        driver.run_task.assert_not_called()
        assert torn == [
            {
                "project_name": "EVAL no bug type",
                "project_id": "p1",
                "bug_type_skip_reason": "plan:work-item-types-disabled",
            }
        ]

    def test_run_live_prompt_bind_failure_is_infra_seed(tmp_path, monkeypatch):
        out = tmp_path / "rows.jsonl"
        driver = MagicMock()
        torn: list[dict[str, Any]] = []

        monkeypatch.setattr(runner_live, "make_plane_client", lambda: (object(), "test-ws"))
        monkeypatch.setattr(
            runner_live,
            "seed",
            lambda *a, **k: k["ctx"].update({"project_name": "EVAL prompt", "project_id": "p1"}),
        )
        monkeypatch.setattr(runner_live, "teardown", lambda plane, ctx: torn.append(dict(ctx)))
        monkeypatch.setattr(runner_live, "get_driver", lambda name, **kw: driver)

        task = _taxonomy_task(
            "PROMPT",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("verify must not run")),
            prompt="use {missing_seed_id} in {project}",
        )
        rc = asyncio.run(run_live([task], model_alias="standard", reps=1, label="local", out_path=out))

        assert rc == 0
        row = _data_rows(out)[0]
        assert row["error_class"] == "infra_seed"
        assert row["verify_note"] == ""
        assert row["error"].startswith("PromptBindError: missing prompt field {missing_seed_id}")
        driver.run_task.assert_not_called()
        assert torn == [{"project_name": "EVAL prompt", "project_id": "p1"}]

    def test_run_live_api_driver_exception_is_infra_api(tmp_path, monkeypatch):
        out = tmp_path / "rows.jsonl"
        driver = MagicMock()
        driver.run_task.side_effect = RuntimeError("provider unavailable")
        torn: list[dict[str, Any]] = []

        monkeypatch.setattr(runner_live, "make_plane_client", lambda: (object(), "test-ws"))
        monkeypatch.setattr(
            runner_live,
            "seed",
            lambda *a, **k: k["ctx"].update({"project_name": "EVAL api", "project_id": "p1"}),
        )
        monkeypatch.setattr(runner_live, "teardown", lambda plane, ctx: torn.append(dict(ctx)))
        monkeypatch.setattr(runner_live, "get_driver", lambda name, **kw: driver)

        task = _taxonomy_task(
            "APIERR",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("verify must not run")),
        )
        rc = asyncio.run(
            run_live(
                [task],
                model_alias="standard",
                reps=1,
                label="local",
                out_path=out,
                driver_name="api",
                resolved_model_id="provider-model-id",
            )
        )

        assert rc == 0
        row = _data_rows(out)[0]
        assert row["error_class"] == "infra_api"
        assert row["verify_note"] == ""
        assert row["success"] is False
        assert row["error"] == "RuntimeError: provider unavailable"
        driver.run_task.assert_called_once()
        assert torn == [{"project_name": "EVAL api", "project_id": "p1"}]

    def test_run_live_driver_exception_is_infra_cli(tmp_path, monkeypatch):
        out = tmp_path / "rows.jsonl"
        fake_plane = MagicMock()
        monkeypatch.setattr(runner_live, "make_plane_client", lambda: (fake_plane, "test-ws"))

        def ok_seed(plane, run_id, needs, ctx):
            ctx.update({"project_name": "EVAL deadbeef", "project_id": "p1"})

        monkeypatch.setattr(runner_live, "seed", ok_seed)
        monkeypatch.setattr(runner_live, "teardown", lambda plane, ctx: None)

        class BoomDriver:
            name = "claude-cli"

            def run_task(self, *args, **kwargs):
                raise RuntimeError("claude cli failed: json_parse_failed")

        monkeypatch.setattr(runner_live, "get_driver", lambda name, **kw: BoomDriver())

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
                label="local",
                out_path=out,
                driver_name="claude-cli",
            )
        )
        assert rc == 0
        row = _data_rows(out)[0]
        assert row["error_class"] == "infra_cli"
        assert "RuntimeError" in (row["error"] or "")

    def test_run_live_timeout_agent_is_infra_cli(tmp_path, monkeypatch):
        from evals.results import agent_run_to_harness_dict

        out = tmp_path / "rows.jsonl"
        fake_plane = MagicMock()
        monkeypatch.setattr(runner_live, "make_plane_client", lambda: (fake_plane, "test-ws"))
        monkeypatch.setattr(
            runner_live, "seed", lambda *a, **k: k["ctx"].update({"project_name": "P", "project_id": "1"})
        )
        monkeypatch.setattr(runner_live, "teardown", lambda *a, **k: None)

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

        monkeypatch.setattr(runner_live, "get_driver", lambda name, **kw: TimeoutDriver())

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
                label="local",
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

    def test_run_live_error_during_execution_is_infra_cli(tmp_path, monkeypatch):
        out = tmp_path / "rows.jsonl"
        fake_plane = MagicMock()
        monkeypatch.setattr(runner_live, "make_plane_client", lambda: (fake_plane, "test-ws"))
        monkeypatch.setattr(
            runner_live, "seed", lambda *a, **k: k["ctx"].update({"project_name": "P", "project_id": "1"})
        )
        monkeypatch.setattr(runner_live, "teardown", lambda *a, **k: None)

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

        monkeypatch.setattr(runner_live, "get_driver", lambda name, **kw: ClaudeCliDriver(runner=fake_run))

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
                label="local",
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

    def test_run_live_error_max_turns_is_task_path(tmp_path, monkeypatch):
        out = tmp_path / "rows.jsonl"
        fake_plane = MagicMock()
        monkeypatch.setattr(runner_live, "make_plane_client", lambda: (fake_plane, "test-ws"))
        monkeypatch.setattr(
            runner_live, "seed", lambda *a, **k: k["ctx"].update({"project_name": "P", "project_id": "1"})
        )
        monkeypatch.setattr(runner_live, "teardown", lambda *a, **k: None)

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

        monkeypatch.setattr(runner_live, "get_driver", lambda name, **kw: ClaudeCliDriver(runner=fake_run))

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
                label="local",
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

    def test_run_live_verifier_skip_is_not_a_failure(tmp_path, monkeypatch):
        out = tmp_path / "rows.jsonl"
        driver = MagicMock()
        driver.run_task.return_value = AgentRun(
            calls=[],
            final_text="done",
            usage=None,
            stopped_reason="end_turn",
        )
        torn: list[dict[str, Any]] = []

        async def skip_verify(plane, ctx, run):
            raise TaskSkipped("env:verification-unavailable")

        monkeypatch.setattr(runner_live, "make_plane_client", lambda: (object(), "test-ws"))
        monkeypatch.setattr(
            runner_live,
            "seed",
            lambda *a, **k: k["ctx"].update({"project_name": "EVAL skip", "project_id": "p1"}),
        )
        monkeypatch.setattr(runner_live, "teardown", lambda plane, ctx: torn.append(dict(ctx)))
        monkeypatch.setattr(runner_live, "get_driver", lambda name, **kw: driver)

        rc = asyncio.run(
            run_live(
                [_taxonomy_task("VERIFYSKIP", skip_verify)],
                model_alias="standard",
                reps=1,
                label="local",
                out_path=out,
            )
        )

        assert rc == 0
        row = _data_rows(out)[0]
        assert row["skipped"] == "env:verification-unavailable"
        assert row["verify_note"] == "env:verification-unavailable"
        assert row["success"] is False
        assert row["error"] is None
        assert row["error_class"] is None
        assert torn == [{"project_name": "EVAL skip", "project_id": "p1"}]

    def test_run_live_verifier_exception_is_task_error(tmp_path, monkeypatch):
        out = tmp_path / "rows.jsonl"
        driver = MagicMock()
        driver.run_task.return_value = AgentRun(
            calls=[],
            final_text="done",
            usage=None,
            stopped_reason="end_turn",
        )
        torn: list[dict[str, Any]] = []

        async def broken_verify(plane, ctx, run):
            raise ValueError("verifier broke")

        monkeypatch.setattr(runner_live, "make_plane_client", lambda: (object(), "test-ws"))
        monkeypatch.setattr(
            runner_live,
            "seed",
            lambda *a, **k: k["ctx"].update({"project_name": "EVAL verify", "project_id": "p1"}),
        )
        monkeypatch.setattr(runner_live, "teardown", lambda plane, ctx: torn.append(dict(ctx)))
        monkeypatch.setattr(runner_live, "get_driver", lambda name, **kw: driver)

        rc = asyncio.run(
            run_live(
                [_taxonomy_task("VERIFYERR", broken_verify)],
                model_alias="standard",
                reps=1,
                label="local",
                out_path=out,
            )
        )

        assert rc == 0
        row = _data_rows(out)[0]
        assert row["success"] is False
        assert row["error_class"] == "task"
        assert row["error"] == "ValueError: verifier broke"
        assert row["verify_note"] == ""
        assert row["skipped"] is None
        assert torn == [{"project_name": "EVAL verify", "project_id": "p1"}]

    def test_run_live_external_server_nulls_catalog_mispicks(tmp_path, monkeypatch):
        out = tmp_path / "rows.jsonl"
        driver = MagicMock()
        driver.run_task.return_value = AgentRun(
            calls=[{"tool": "search_work_items", "args": {}}],
            final_text="done",
            usage=None,
            stopped_reason="end_turn",
        )

        async def verify_ok(plane, ctx, run):
            return True, "external ok"

        monkeypatch.setattr(runner_live, "make_plane_client", lambda: (object(), "test-ws"))
        monkeypatch.setattr(
            runner_live,
            "seed",
            lambda *a, **k: k["ctx"].update({"project_name": "EVAL external", "project_id": "p1"}),
        )
        monkeypatch.setattr(runner_live, "teardown", lambda plane, ctx: None)
        monkeypatch.setattr(runner_live, "get_driver", lambda name, **kw: driver)

        rc = asyncio.run(
            run_live(
                [_taxonomy_task("EXTERNAL", verify_ok)],
                model_alias="standard",
                reps=1,
                label="local",
                out_path=out,
                server_cmd=["/bin/foreign", "stdio"],
            )
        )

        assert rc == 0
        row = _data_rows(out)[0]
        assert row["success"] is True
        assert row["server"] == "external"
        assert row["alternate_calls"] is None
        assert row["out_of_set_calls"] is None

    def test_run_live_success_keeps_requested_and_resolved_models(tmp_path, monkeypatch):
        out = tmp_path / "rows.jsonl"
        driver = MagicMock()
        driver.run_task.return_value = AgentRun(
            calls=[{"tool": "list_work_items", "args": {}}],
            final_text="done",
            usage=None,
            stopped_reason="end_turn",
        )

        async def verify_ok(plane, ctx, run):
            return True, "local ok"

        monkeypatch.setattr(runner_live, "make_plane_client", lambda: (object(), "test-ws"))
        monkeypatch.setattr(
            runner_live,
            "seed",
            lambda *a, **k: k["ctx"].update({"project_name": "EVAL local", "project_id": "p1"}),
        )
        monkeypatch.setattr(runner_live, "teardown", lambda plane, ctx: None)
        monkeypatch.setattr(runner_live, "get_driver", lambda name, **kw: driver)

        rc = asyncio.run(
            run_live(
                [_taxonomy_task("SUCCESS", verify_ok)],
                model_alias="standard",
                resolved_model_id="provider-model-id",
                reps=1,
                label="local",
                out_path=out,
            )
        )

        assert rc == 0
        row = _data_rows(out)[0]
        assert row["success"] is True
        assert row["requested_model"] == "standard"
        assert row["requested_tier"] == "standard"
        assert row["resolved_model"] == "provider-model-id"
        assert row["server"] == "local"

    def test_run_live_multi_rep_uses_fresh_seed_and_teardown_per_rep(tmp_path, monkeypatch):
        out = tmp_path / "multi.jsonl"
        fake_plane = MagicMock()
        monkeypatch.setattr(runner_live, "make_plane_client", lambda: (fake_plane, "test-ws"))
        seed_ids: list[str] = []
        teardown_projects: list[str] = []

        def fresh_seed(plane, run_id, needs, ctx):
            seed_ids.append(run_id)
            ctx.update({"project_name": f"EVAL {run_id[:8]}", "project_id": run_id})

        def record_teardown(plane, ctx):
            teardown_projects.append(ctx["project_id"])

        async def fake_agent(**kwargs):
            return TaskResult(final_text="done", stop_reason="end_turn")

        monkeypatch.setattr(runner_live, "seed", fresh_seed)
        monkeypatch.setattr(runner_live, "teardown", record_teardown)
        monkeypatch.setattr(runner_live, "get_driver", lambda name, **kwargs: object())
        monkeypatch.setattr(runner_live, "run_agent_task_via_driver", fake_agent)

        async def verify_ok(plane, ctx, run):
            return True, "ok"

        task = {
            "id": "R1",
            "prompt": "do {project}",
            "tags": set(),
            "optimal_tools": {"list_work_items"},
            "alternate_tools": set(),
            "optimal_calls": 1,
            "needs": set(),
            "verify": verify_ok,
        }

        rc = asyncio.run(
            run_live(
                [task],
                model_alias="sonnet",
                reps=3,
                label="local",
                out_path=out,
                driver_name="claude-cli",
            )
        )

        assert rc == 0
        assert len(seed_ids) == 3
        assert len(set(seed_ids)) == 3
        assert teardown_projects == seed_ids
        rows = _data_rows(out)
        assert [row["rep"] for row in rows] == [0, 1, 2]
        assert all(row["success"] is True for row in rows)

    def test_run_live_passes_server_cmd_to_non_claude(monkeypatch, tmp_path):
        from evals.runner import live as run_mod

        captured: dict = {}

        def fake_get_driver(name, **kwargs):
            captured["name"] = name
            captured["kwargs"] = kwargs

            class Dummy:
                def run_task(self, *a, **k):
                    return AgentRun(
                        calls=[],
                        final_text="",
                        usage=None,
                        stopped_reason="end_turn",
                        call_source="json",
                    )

            return Dummy()

        monkeypatch.setattr(run_mod, "get_driver", fake_get_driver)
        monkeypatch.setattr(run_mod, "make_plane_client", lambda: (object(), "ws"))
        monkeypatch.setattr(run_mod, "seed", lambda *a, **k: k["ctx"].update({"project_name": "P", "project_id": "1"}))
        monkeypatch.setattr(run_mod, "teardown", lambda *a, **k: None)

        import asyncio

        async def _verify(*a, **k):
            return False, "n"

        task = {
            "id": "T",
            "prompt": "x {project}",
            "optimal_tools": {"a"},
            "alternate_tools": set(),
            "optimal_calls": 1,
            "needs": set(),
            "verify": _verify,
        }
        rc = asyncio.run(
            run_mod.run_live(
                [task],
                model_alias="sonnet",
                reps=1,
                label="local",
                out_path=tmp_path / "o.jsonl",
                driver_name="opencode-cli",
                server_cmd=["/bin/foreign", "stdio"],
            )
        )
        assert rc == 0
        assert captured["name"] == "opencode-cli"
        assert captured["kwargs"].get("server_command") == ["/bin/foreign", "stdio"]

    def test_run_live_reports_progress_per_repetition(tmp_path, monkeypatch, capsys):
        out = tmp_path / "out.jsonl"

        async def passes(_plane, _ctx, _run):
            return True, "ok"

        monkeypatch.setattr(runner_live, "make_plane_client", lambda: (object(), "ws"))
        monkeypatch.setattr(runner_live, "seed", lambda *a, **k: k["ctx"].update({"project_name": "EVAL x"}))
        monkeypatch.setattr(runner_live, "teardown", lambda *a, **k: None)

        async def fake_drive(**kwargs):
            return TaskResult(final_text="done", num_calls=2)

        monkeypatch.setattr(runner_live, "_drive_agent", fake_drive)
        monkeypatch.setattr(runner_live, "get_driver", lambda *a, **k: MagicMock())

        tasks = [_taxonomy_task("R1", passes), _taxonomy_task("R2", passes)]
        rc = asyncio.run(run_live(tasks, model_alias="standard", reps=1, label="local", out_path=out))
        assert rc == 0

        printed = capsys.readouterr().out
        # Position out of total, before the task runs.
        assert "[ 1/2] R1 rep=0 running" in printed
        assert "[ 2/2] R2 rep=0 running" in printed
        # A running tally after each, and one closing summary.
        assert "1/2 done · 1 pass · 0 fail · 0 skip" in printed
        assert "finished 2/2 in " in printed
        assert "2 pass, 0 fail, 0 skip" in printed

    _d0 = tmp_path / "test_run_live_seed_failure_is_infra_seed"
    _d0.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        test_run_live_seed_failure_is_infra_seed(_d0, mp)
    _d1 = tmp_path / "test_run_live_missing_bug_type_uses_context_skip_reason"
    _d1.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        test_run_live_missing_bug_type_uses_context_skip_reason(_d1, mp)
    _d2 = tmp_path / "test_run_live_prompt_bind_failure_is_infra_seed"
    _d2.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        test_run_live_prompt_bind_failure_is_infra_seed(_d2, mp)
    _d3 = tmp_path / "test_run_live_api_driver_exception_is_infra_api"
    _d3.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        test_run_live_api_driver_exception_is_infra_api(_d3, mp)
    _d4 = tmp_path / "test_run_live_driver_exception_is_infra_cli"
    _d4.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        test_run_live_driver_exception_is_infra_cli(_d4, mp)
    _d5 = tmp_path / "test_run_live_timeout_agent_is_infra_cli"
    _d5.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        test_run_live_timeout_agent_is_infra_cli(_d5, mp)
    _d6 = tmp_path / "test_run_live_error_during_execution_is_infra_cli"
    _d6.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        test_run_live_error_during_execution_is_infra_cli(_d6, mp)
    _d7 = tmp_path / "test_run_live_error_max_turns_is_task_path"
    _d7.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        test_run_live_error_max_turns_is_task_path(_d7, mp)
    _d8 = tmp_path / "test_run_live_verifier_skip_is_not_a_failure"
    _d8.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        test_run_live_verifier_skip_is_not_a_failure(_d8, mp)
    _d9 = tmp_path / "test_run_live_verifier_exception_is_task_error"
    _d9.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        test_run_live_verifier_exception_is_task_error(_d9, mp)
    _d10 = tmp_path / "test_run_live_external_server_nulls_catalog_mispicks"
    _d10.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        test_run_live_external_server_nulls_catalog_mispicks(_d10, mp)
    _d11 = tmp_path / "test_run_live_success_keeps_requested_and_resolved_models"
    _d11.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        test_run_live_success_keeps_requested_and_resolved_models(_d11, mp)
    _d12 = tmp_path / "test_run_live_multi_rep_uses_fresh_seed_and_teardown_per_rep"
    _d12.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        test_run_live_multi_rep_uses_fresh_seed_and_teardown_per_rep(_d12, mp)
    _d13 = tmp_path / "test_run_live_passes_server_cmd_to_non_claude"
    _d13.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        test_run_live_passes_server_cmd_to_non_claude(mp, _d13)
    _d14 = tmp_path / "test_run_live_reports_progress_per_repetition"
    _d14.mkdir()
    with pytest.MonkeyPatch.context() as mp:
        test_run_live_reports_progress_per_repetition(_d14, mp, capsys)


def test_is_infra_cli_stop_reason_matrix():
    assert is_infra_cli_stop_reason("timeout") is True
    assert is_infra_cli_stop_reason("error_during_execution") is True
    assert is_infra_cli_stop_reason("error") is True
    assert is_infra_cli_stop_reason("error_max_turns") is False
    assert is_infra_cli_stop_reason("end_turn") is False
    assert is_infra_cli_stop_reason("max_turns") is False


def test_task_skipped_from_seed_records_a_skip_row(tmp_path: Path, monkeypatch):
    """A fixture that cannot be seeded records a skip — no agent, no crash.

    Genuine skips (an absent activity worker, a plan-gated feature) reach the
    row through TaskSkipped, so the driver must never run and the row must not
    count as a failure.
    """
    out = tmp_path / "out.jsonl"
    driven: list[str] = []
    torn: list[Any] = []
    driver = MagicMock()

    def skip_seed(*_args: Any, **_kwargs: Any) -> None:
        raise TaskSkipped("env:no-activity-worker")

    monkeypatch.setattr(runner_live, "make_plane_client", lambda: (object(), "ws"))
    monkeypatch.setattr(runner_live, "seed", skip_seed)
    monkeypatch.setattr(runner_live, "teardown", lambda *a, **k: torn.append(1))
    monkeypatch.setattr(
        runner_live,
        "get_driver",
        lambda *a, **k: driven.append("ran") or driver,
    )

    tasks = [
        {
            "id": "L2",
            "prompt": "x {project}",
            "optimal_tools": {"a"},
            "alternate_tools": set(),
            "optimal_calls": 1,
            "needs": {"activity_feed"},
            "verify": None,  # never reached
        },
    ]
    rc = asyncio.run(run_live(tasks, model_alias="standard", reps=1, label="local", out_path=out))

    assert rc == 0
    assert driven == ["ran"]  # driver is constructed once per run, never invoked
    row = _data_rows(out)[0]
    assert row["task_id"] == "L2"
    assert row["skipped"] == "env:no-activity-worker"
    assert row["error"] is None
    assert row["error_class"] is None
    assert row["label"] == "local"
    assert torn == [1]  # teardown still runs
    driver.run_task.assert_not_called()

    # `skipped` is the discriminator, not `success` — a skip must leave the
    # success denominator empty rather than counting as a failed task.
    summary = summarize(load_rows(out))
    assert "L2" not in summary.tasks
    assert summary.aggregate_n == 0


# ---------------------------------------------------------------------------
# Progress reporting
# ---------------------------------------------------------------------------


def test_elapsed_formats_minutes_then_hours(monkeypatch):
    from evals.runner.live import _elapsed

    clock = {"now": 1000.0}
    monkeypatch.setattr(runner_live.time, "monotonic", lambda: clock["now"])
    clock["now"] = 1000.0 + 9
    assert _elapsed(1000.0) == "00:09"
    clock["now"] = 1000.0 + 75
    assert _elapsed(1000.0) == "01:15"
    clock["now"] = 1000.0 + 3671
    assert _elapsed(1000.0) == "1:01:11"
