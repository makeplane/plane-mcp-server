"""Offline eval tests for live."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from plane.errors.errors import HttpError

from evals import cli as run_mod
from evals.drivers import (
    ClaudeCliDriver,
)
from evals.evidence import TARGET_ENTITY_EVIDENCE
from evals.report import load_rows, summarize
from evals.results import RESULT_SCHEMA_VERSION, AgentRun, TaskResult
from evals.runner import (
    is_infra_cli_stop_reason,
    run_live,
)
from evals.runner import live as runner_live
from evals.runner.live import stdio_server_env
from evals.tasks.skip import TaskSkipped
from tests.evals.conftest import _data_rows, case_params


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
        "needs": set(needs or set()),
        "verify": verify,
    }


def _stdio_env_still_works_for_cli_drivers(monkeypatch):
    monkeypatch.setenv("EVAL_PLANE_API_KEY", "k")
    monkeypatch.setenv("EVAL_PLANE_WORKSPACE_SLUG", "ws")
    monkeypatch.delenv("EVAL_PLANE_BASE_URL", raising=False)
    env = stdio_server_env()
    assert env["PLANE_API_KEY"] == "k"
    assert "ANTHROPIC_API_KEY" not in env


def _stdio_server_env_does_not_leak_ambient_secrets(monkeypatch):
    monkeypatch.setenv("SOME_SECRET", "x")

    environment = runner_live.stdio_server_env()

    assert "SOME_SECRET" not in environment
    assert environment["PLANE_API_KEY"] == "test-key"
    assert environment["PLANE_WORKSPACE_SLUG"] == "test-ws"
    assert environment["PLANE_BASE_URL"] == "https://api.plane.so"


@pytest.mark.parametrize(
    "case",
    case_params(_stdio_env_still_works_for_cli_drivers, _stdio_server_env_does_not_leak_ambient_secrets),
)
def test_stdio_behaviours(monkeypatch, case):
    case(monkeypatch)


def test_live_run_rejects_non_positive_reps(capsys):
    assert run_mod.main(["--tasks", "R1", "--reps", "0"]) == 2
    assert "--reps must be at least 1" in capsys.readouterr().err


def _run_seed_failure_is_infra_seed(tmp_path, monkeypatch, _capsys):
    out = tmp_path / "rows.jsonl"

    fake_plane = MagicMock()
    driver = MagicMock()
    torn: list[dict[str, Any]] = []
    monkeypatch.setattr(runner_live, "make_plane_client", lambda: (fake_plane, "test-ws"))

    def boom_seed(plane, run_id, needs, ctx, task_id=None):
        ctx["project_name"] = "EVAL deadbeef"
        raise HttpError("identifier already taken", 409)

    monkeypatch.setattr(runner_live, "seed", boom_seed)
    monkeypatch.setattr(runner_live, "teardown", lambda plane, ctx: torn.append(dict(ctx)))
    monkeypatch.setattr(runner_live, "get_driver", lambda name, **kw: driver)

    task = {
        "id": "T1",
        "prompt": "do {project}",
        "tags": set(),
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
    assert rc == 1
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


def _run_missing_bug_type_uses_context_skip_reason(tmp_path, monkeypatch, _capsys):
    out = tmp_path / "rows.jsonl"
    driver = MagicMock()
    torn: list[dict[str, Any]] = []

    def seed_without_bug_type(plane, run_id, needs, ctx, task_id=None):
        ctx.update(
            {
                "project_name": "EVAL no bug type",
                "project_id": "p1",
                "bug_type_skip_reason": "env:plan-gated:work-item-types",
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
    assert row["skipped"] == "env:plan-gated:work-item-types"
    assert row["verify_note"] == "env:plan-gated:work-item-types"
    assert row["error"] is None
    assert row["error_class"] is None
    driver.run_task.assert_not_called()
    assert torn == [
        {
            "project_name": "EVAL no bug type",
            "project_id": "p1",
            "bug_type_skip_reason": "env:plan-gated:work-item-types",
        }
    ]


def _run_prompt_bind_failure_is_infra_seed(tmp_path, monkeypatch, _capsys):
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

    assert rc == 1
    row = _data_rows(out)[0]
    assert row["error_class"] == "infra_seed"
    assert row["verify_note"] == ""
    assert row["error"].startswith("PromptBindError: missing prompt field {missing_seed_id}")
    driver.run_task.assert_not_called()
    assert torn == [{"project_name": "EVAL prompt", "project_id": "p1"}]


def _run_api_driver_exception_is_infra_api(tmp_path, monkeypatch, _capsys):
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

    assert rc == 1
    row = _data_rows(out)[0]
    assert row["error_class"] == "infra_api"
    assert row["verify_note"] == ""
    assert row["success"] is False
    assert row["error"] == "RuntimeError: provider unavailable"
    driver.run_task.assert_called_once()
    assert torn == [{"project_name": "EVAL api", "project_id": "p1"}]


def _run_driver_exception_is_infra_cli(tmp_path, monkeypatch, _capsys):
    out = tmp_path / "rows.jsonl"
    fake_plane = MagicMock()
    monkeypatch.setattr(runner_live, "make_plane_client", lambda: (fake_plane, "test-ws"))

    def ok_seed(plane, run_id, needs, ctx, task_id=None):
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
    assert rc == 1
    row = _data_rows(out)[0]
    assert row["error_class"] == "infra_cli"
    assert "RuntimeError" in (row["error"] or "")


def _run_timeout_agent_is_infra_cli(tmp_path, monkeypatch, _capsys):
    from evals.results import agent_run_to_harness_dict

    out = tmp_path / "rows.jsonl"
    fake_plane = MagicMock()
    monkeypatch.setattr(runner_live, "make_plane_client", lambda: (fake_plane, "test-ws"))
    monkeypatch.setattr(runner_live, "seed", lambda *a, **k: k["ctx"].update({"project_name": "P", "project_id": "1"}))
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
    assert rc == 1
    row = _data_rows(out)[0]
    assert row["error_class"] == "infra_cli"
    assert row["error"] == "timeout after 900s"  # from driver_notes, not recomputed
    assert row["stop_reason"] == "timeout"
    assert verify_calls == []
    d = agent_run_to_harness_dict(AgentRun(calls=[], final_text="", usage=None, stopped_reason="timeout"))
    assert d["stop_reason"] == "timeout"


def _run_error_during_execution_is_infra_cli(tmp_path, monkeypatch, _capsys):
    out = tmp_path / "rows.jsonl"
    fake_plane = MagicMock()
    monkeypatch.setattr(runner_live, "make_plane_client", lambda: (fake_plane, "test-ws"))
    monkeypatch.setattr(runner_live, "seed", lambda *a, **k: k["ctx"].update({"project_name": "P", "project_id": "1"}))
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
    assert rc == 1
    row = _data_rows(out)[0]
    assert row["error_class"] == "infra_cli"
    assert row["stop_reason"] == "error_during_execution"
    assert verify_calls == []
    assert "claude_exit=1" in (row.get("driver_notes") or [])


def _run_error_max_turns_is_task_path(tmp_path, monkeypatch, _capsys):
    out = tmp_path / "rows.jsonl"
    fake_plane = MagicMock()
    monkeypatch.setattr(runner_live, "make_plane_client", lambda: (fake_plane, "test-ws"))
    monkeypatch.setattr(runner_live, "seed", lambda *a, **k: k["ctx"].update({"project_name": "P", "project_id": "1"}))
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


def _run_verifier_skip_is_not_a_failure(tmp_path, monkeypatch, _capsys):
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

    assert rc == 1
    row = _data_rows(out)[0]
    assert row["skipped"] == "env:verification-unavailable"
    assert row["verify_note"] == "env:verification-unavailable"
    assert row["success"] is False
    assert row["error"] is None
    assert row["error_class"] is None
    assert torn == [{"project_name": "EVAL skip", "project_id": "p1"}]


def _run_verifier_exception_is_task_error(tmp_path, monkeypatch, _capsys):
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

    assert rc == 1
    row = _data_rows(out)[0]
    assert row["success"] is False
    assert row["error_class"] == "task"
    assert row["error"] == "ValueError: verifier broke"
    assert row["verify_note"] == ""
    assert row["skipped"] is None
    assert torn == [{"project_name": "EVAL verify", "project_id": "p1"}]


def _run_external_server_records_observed_calls(tmp_path, monkeypatch, _capsys):
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
    assert row["num_calls"] == 1
    assert row["calls"][0]["tool"] == "search_work_items"


def _run_success_keeps_requested_and_resolved_models(tmp_path, monkeypatch, _capsys):
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


def _run_multi_rep_uses_fresh_seed_and_teardown_per_rep(tmp_path, monkeypatch, _capsys):
    out = tmp_path / "multi.jsonl"
    fake_plane = MagicMock()
    monkeypatch.setattr(runner_live, "make_plane_client", lambda: (fake_plane, "test-ws"))
    seed_ids: list[str] = []
    teardown_projects: list[str] = []

    def fresh_seed(plane, run_id, needs, ctx, task_id=None):
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


def test_runner_passes_seeded_evidence_to_driver_and_retains_only_labels(monkeypatch):
    sentinel = "hidden-target-fact-2f81a0cd"
    captured: dict[str, Any] = {}

    class Driver:
        def run_task(self, *args, **kwargs):
            captured.update(kwargs)
            return AgentRun(
                calls=[
                    {
                        "tool": "read_any_route",
                        "args": {},
                        "is_error": False,
                        "result_chars": 42,
                        "observed_sentinels": [TARGET_ENTITY_EVIDENCE],
                    }
                ],
                final_text="count: 4",
                usage=None,
                stopped_reason="end_turn",
                call_source="api",
                evidence_trace_available=True,
            )

    monkeypatch.setenv("EVAL_PLANE_API_KEY", "key")
    monkeypatch.setenv("EVAL_PLANE_WORKSPACE_SLUG", "ws")
    task = {"id": "R2", "prompt": "In {project}, count.", "tags": {"read"}, "needs": {"items"}}
    context = {
        "project_name": "EVAL deadbeef",
        "evidence_sentinels": {TARGET_ENTITY_EVIDENCE: [sentinel]},
    }

    row = asyncio.run(
        runner_live.run_agent_task_via_driver(
            driver=Driver(),
            model_id="model",
            task=task,
            ctx=context,
            workspace_slug="ws",
        )
    )

    assert captured["evidence_sentinels"] == context["evidence_sentinels"]
    assert row.evidence_trace_available is True
    assert row.calls[0].observed_sentinels == [TARGET_ENTITY_EVIDENCE]
    assert sentinel not in json.dumps(row.to_row())


def _run_passes_server_cmd_to_non_claude(tmp_path, monkeypatch, _capsys):
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


def _run_reports_progress_per_repetition(tmp_path, monkeypatch, capsys):
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


_RUN_CASES = case_params(
    _run_seed_failure_is_infra_seed,
    _run_missing_bug_type_uses_context_skip_reason,
    _run_prompt_bind_failure_is_infra_seed,
    _run_api_driver_exception_is_infra_api,
    _run_driver_exception_is_infra_cli,
    _run_timeout_agent_is_infra_cli,
    _run_error_during_execution_is_infra_cli,
    _run_error_max_turns_is_task_path,
    _run_verifier_skip_is_not_a_failure,
    _run_verifier_exception_is_task_error,
    _run_external_server_records_observed_calls,
    _run_success_keeps_requested_and_resolved_models,
    _run_multi_rep_uses_fresh_seed_and_teardown_per_rep,
    _run_passes_server_cmd_to_non_claude,
    _run_reports_progress_per_repetition,
)


@pytest.mark.parametrize("case", _RUN_CASES)
def test_run_behaviours(case, tmp_path, monkeypatch, capsys):
    case(tmp_path, monkeypatch, capsys)


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
    assert summary.expected_skips == 1
    assert summary.unexpected_skips == 0
    assert summary.complete is True


def test_attachment_storage_connection_failure_is_infra_seed(tmp_path, monkeypatch):
    out = tmp_path / "out.jsonl"
    driver = MagicMock()

    def storage_unreachable(*_args, **_kwargs):
        raise ConnectionError("localhost:9000 attachment storage unreachable")

    monkeypatch.setattr(runner_live, "make_plane_client", lambda: (object(), "ws"))
    monkeypatch.setattr(runner_live, "seed", storage_unreachable)
    monkeypatch.setattr(runner_live, "teardown", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner_live, "get_driver", lambda *args, **kwargs: driver)

    rc = asyncio.run(
        run_live([_taxonomy_task("L5", None)], model_alias="standard", reps=1, label="local", out_path=out)
    )

    assert rc == 1
    row = _data_rows(out)[0]
    assert row["task_id"] == "L5"
    assert row["error_class"] == "infra_seed"
    assert row["error"] == "ConnectionError: localhost:9000 attachment storage unreachable"
    assert row["skipped"] is None
    driver.run_task.assert_not_called()


def test_activity_read_connection_failure_is_infra_seed_and_incomplete(tmp_path, monkeypatch, capsys):
    from evals.seed.work_items import CHECKOUT_TIMEOUT_TITLE, require_activities

    out = tmp_path / "out.jsonl"
    driver = MagicMock()

    def activity_backend_unreachable(**kwargs):
        raise ConnectionError("activity backend unreachable")

    plane = SimpleNamespace(work_items=SimpleNamespace(activities=SimpleNamespace(list=activity_backend_unreachable)))

    def seed_l2(plane, run_id, needs, ctx, task_id=None):
        ctx.update(
            {
                "workspace_slug": "ws",
                "project_id": "project-1",
                "items": {CHECKOUT_TIMEOUT_TITLE: "work-item-1"},
            }
        )
        require_activities(plane, "ws", ctx)

    monkeypatch.setattr(runner_live, "make_plane_client", lambda: (plane, "ws"))
    monkeypatch.setattr(runner_live, "seed", seed_l2)
    monkeypatch.setattr(runner_live, "teardown", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner_live, "get_driver", lambda *args, **kwargs: driver)

    rc = asyncio.run(
        run_live(
            [_taxonomy_task("L2", None, needs={"activity_feed"})],
            model_alias="standard",
            reps=1,
            label="local",
            out_path=out,
        )
    )

    assert rc == 1
    row = _data_rows(out)[0]
    assert row["error_class"] == "infra_seed"
    assert row["error"] == "ConnectionError: activity backend unreachable"
    assert row["skipped"] is None
    assert "RUN INCOMPLETE:" in capsys.readouterr().out
    driver.run_task.assert_not_called()


@pytest.mark.parametrize(
    ("reason", "expected_rc", "verdict"),
    [
        ("env:plan-gated:customers", 0, "RUN COMPLETE:"),
        ("env:no-activity-worker", 0, "RUN COMPLETE:"),
        ("env:plan-gated:customerz", 1, "RUN INCOMPLETE:"),
        ("env:fixture-collision:customers:Acme Corp", 1, "RUN INCOMPLETE:"),
        ("env:new-skip-reason", 1, "RUN INCOMPLETE:"),
    ],
)
def test_run_live_completeness_skip_taxonomy(tmp_path, monkeypatch, capsys, reason, expected_rc, verdict):
    out = tmp_path / "out.jsonl"

    def skip_seed(*_args, **_kwargs):
        raise TaskSkipped(reason)

    monkeypatch.setattr(runner_live, "make_plane_client", lambda: (object(), "ws"))
    monkeypatch.setattr(runner_live, "seed", skip_seed)
    monkeypatch.setattr(runner_live, "teardown", lambda *a, **k: None)
    monkeypatch.setattr(runner_live, "get_driver", lambda *a, **k: MagicMock())
    tasks = [_taxonomy_task("R1", None), _taxonomy_task("R2", None)]

    rc = asyncio.run(run_live(tasks, model_alias="standard", reps=1, label="local", out_path=out))

    assert rc == expected_rc
    output = capsys.readouterr().out
    assert "success: 0/0" in output
    assert "EXECUTION COVERAGE: 0/2 rows evaluated (0.0%)" in output
    assert f"R1,R2 ({reason})" in output
    assert verdict in output
    if reason.startswith("env:fixture-collision:"):
        assert "unexpected skips=2 [fixture-collision=2]" in output


def test_run_live_cleanup_failure_is_incomplete_without_changing_success(tmp_path, monkeypatch, capsys):
    out = tmp_path / "out.jsonl"
    delete_calls: list[tuple[str, str]] = []

    def fail_delete(kind: str, object_id: str) -> None:
        delete_calls.append((kind, object_id))
        raise RuntimeError(f"delete failed for {kind} {object_id}")

    class _Page:
        results: list[Any] = []
        next_page_results = False
        next_cursor = None

    plane = SimpleNamespace(
        customers=SimpleNamespace(
            list=lambda **kw: _Page(),
            delete=lambda **kw: fail_delete("customer", kw["customer_id"]),
            properties=SimpleNamespace(list=lambda **kw: _Page(), delete=lambda **kw: None),
        ),
        releases=SimpleNamespace(
            tags=SimpleNamespace(
                list=lambda **kw: _Page(),
                delete=lambda **kw: fail_delete("release_tag", kw["tag_id"]),
            )
        ),
    )
    driver = MagicMock()
    driver.run_task.return_value = AgentRun(calls=[], final_text="done", usage=None, stopped_reason="end_turn")

    async def verify_ok(*_args, **_kwargs):
        return True, "ok"

    monkeypatch.setattr(runner_live, "make_plane_client", lambda: (plane, "ws"))
    monkeypatch.setattr(
        runner_live,
        "seed",
        lambda *a, **k: k["ctx"].update(
            {
                "workspace_slug": "ws",
                "project_name": "EVAL cleanup",
                "project_id": None,
                "workspace_objects": [
                    {"kind": "customer", "id": "customer-1"},
                    {"kind": "release_tag", "id": "tag-1"},
                ],
                "workspace_baseline": {
                    "customers": set(),
                    "release_tags": set(),
                    "customer_properties": set(),
                },
            }
        ),
    )
    monkeypatch.setattr(runner_live, "get_driver", lambda *a, **k: driver)

    rc = asyncio.run(
        run_live(
            [_taxonomy_task("R1", verify_ok)],
            model_alias="standard",
            reps=1,
            label="local",
            out_path=out,
        )
    )

    assert rc == 1
    row = _data_rows(out)[0]
    assert row["success"] is True
    assert row["cleanup_error"].startswith("TeardownError: 2 cleanup operation(s) failed:")
    assert delete_calls == [("customer", "customer-1"), ("release_tag", "tag-1")]
    output = capsys.readouterr().out
    assert "success: 1/1 (100.0%)" in output
    assert "EXECUTION COVERAGE: 1/1 rows evaluated (100.0%)" in output
    assert "RUN INCOMPLETE:" in output
    assert "cleanup errors=1" in output


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
