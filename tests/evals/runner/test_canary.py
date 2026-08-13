"""Offline eval tests for canary."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from evals.runner import canary as runner_canary
from evals.runner import (
    run_canary,
)
from evals.tasks.skip import TaskSkipped


def test_canary_detects_broken_verifier(monkeypatch):
    fake_plane = MagicMock()
    monkeypatch.setattr(runner_canary, "make_plane_client", lambda: (fake_plane, "test-ws"))
    monkeypatch.setattr(
        runner_canary, "seed", lambda *a, **k: k["ctx"].update({"project_name": "P", "project_id": "1"})
    )
    monkeypatch.setattr(runner_canary, "teardown", lambda *a, **k: None)

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
    rc = asyncio.run(run_canary(tasks, label="local"))
    assert rc == 1


def test_canary_passes_when_all_verifiers_reject(monkeypatch):
    fake_plane = MagicMock()
    monkeypatch.setattr(runner_canary, "make_plane_client", lambda: (fake_plane, "test-ws"))
    monkeypatch.setattr(
        runner_canary, "seed", lambda *a, **k: k["ctx"].update({"project_name": "P", "project_id": "1"})
    )
    monkeypatch.setattr(runner_canary, "teardown", lambda *a, **k: None)

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
    rc = asyncio.run(run_canary(tasks, label="local"))
    assert rc == 0


def test_canary_exits_1_when_all_tasks_skipped(monkeypatch):
    fake_plane = MagicMock()
    monkeypatch.setattr(runner_canary, "make_plane_client", lambda: (fake_plane, "test-ws"))
    monkeypatch.setattr(
        runner_canary,
        "seed",
        lambda *a, **k: (_ for _ in ()).throw(TaskSkipped("fixture unavailable")),
    )
    monkeypatch.setattr(runner_canary, "teardown", lambda *a, **k: None)
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
    rc = asyncio.run(run_canary(tasks, label="local"))
    assert rc == 1
