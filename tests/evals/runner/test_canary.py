"""Offline eval tests for honest verifier-canary coverage."""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from plane.errors.errors import HttpError

from evals.runner import canary as runner_canary
from evals.runner import run_canary
from evals.tasks.schema import verify_s2
from evals.tasks.skip import TaskSkipped


def _task(task_id: str, verify: Any) -> dict[str, Any]:
    return {
        "id": task_id,
        "prompt": "x {project}",
        "needs": set(),
        "verify": verify,
    }


def _install_harness(monkeypatch, *, plane=None, seed=None, teardown=None):
    monkeypatch.setattr(runner_canary, "make_plane_client", lambda: (plane or MagicMock(), "test-ws"))
    monkeypatch.setattr(
        runner_canary,
        "seed",
        seed or (lambda *args, **kwargs: kwargs["ctx"].update({"project_name": "P", "project_id": "1"})),
    )
    monkeypatch.setattr(runner_canary, "teardown", teardown or (lambda *args, **kwargs: None))


async def _reject(_plane, _ctx, run):
    assert run["calls"] == []
    assert run["call_source"] == "canary"
    return False, "zero-call probe rejected"


def test_canary_detects_a_verifier_that_accepts_empty_output(monkeypatch):
    _install_harness(monkeypatch)

    async def always_ok(_plane, _ctx, _run):
        return True, "false positive"

    rc = asyncio.run(run_canary([_task("GOOD", _reject), _task("BAD", always_ok)], label="local"))
    assert rc == 1


def test_canary_accepts_a_fully_verified_set(monkeypatch, capsys):
    _install_harness(monkeypatch)
    rc = asyncio.run(run_canary([_task("G1", _reject)], label="local"))
    assert rc == 0
    output = capsys.readouterr().out
    assert "verified=1/1 ids=['G1']" in output
    assert "skipped=0 ids=[]" in output
    assert "errored=0 ids=[]" in output


def test_canary_treats_missing_s2_estimate_as_a_rejected_probe_not_an_error(monkeypatch, capsys):
    def missing_estimate(**kwargs):
        raise HttpError("Estimate not found", 404, {})

    plane = SimpleNamespace(estimates=SimpleNamespace(retrieve=missing_estimate))

    def seed(*args, **kwargs):
        kwargs["ctx"].update({"workspace_slug": "test-ws", "project_name": "P", "project_id": "1"})

    _install_harness(monkeypatch, plane=plane, seed=seed)
    rc = asyncio.run(run_canary([_task("S2", verify_s2)], label="local"))

    assert rc == 0
    output = capsys.readouterr().out
    assert "verified=1/1 ids=['S2']" in output
    assert "errored=0 ids=[]" in output
    assert "requested Fibonacci scale was not created" in output


def test_canary_reports_partial_coverage_without_saying_all(monkeypatch, capsys):
    def seed(*args, **kwargs):
        if kwargs["task_id"] == "SKIP":
            raise TaskSkipped("env:plan-gated:releases")
        kwargs["ctx"].update({"project_name": "P", "project_id": "1"})

    _install_harness(monkeypatch, seed=seed)
    rc = asyncio.run(run_canary([_task("GOOD", _reject), _task("SKIP", _reject)], label="local"))
    assert rc == 0
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "verified=1/2 ids=['GOOD']" in output
    assert "skipped=1 ids=['SKIP']" in output
    assert "env:plan-gated:releases" in output
    assert not re.search(r"\ball\b", output, flags=re.IGNORECASE)


def test_canary_strict_mode_fails_when_a_required_id_is_skipped(monkeypatch, capsys):
    def seed(*args, **kwargs):
        if kwargs["task_id"] == "SKIP":
            raise TaskSkipped("env:plan-gated:releases")
        kwargs["ctx"].update({"project_name": "P", "project_id": "1"})

    _install_harness(monkeypatch, seed=seed)
    rc = asyncio.run(
        run_canary(
            [_task("GOOD", _reject), _task("SKIP", _reject)],
            label="local",
            required_task_ids={"GOOD", "SKIP"},
        )
    )
    assert rc == 1
    assert "missing required ids=['SKIP']" in capsys.readouterr().err


def test_canary_teardown_error_affects_exit_and_error_report(monkeypatch, capsys):
    def teardown(*args, **kwargs):
        raise RuntimeError("cleanup failed")

    _install_harness(monkeypatch, teardown=teardown)
    rc = asyncio.run(run_canary([_task("G1", _reject)], label="local"))
    assert rc == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "errored=1 ids=['G1']" in output
    assert "teardown RuntimeError: cleanup failed" in output


def test_canary_labels_attachment_storage_seed_failure_as_infrastructure(monkeypatch, capsys):
    def seed(*args, **kwargs):
        raise ConnectionError("localhost:9000 attachment storage unreachable")

    _install_harness(monkeypatch, seed=seed)
    rc = asyncio.run(run_canary([_task("L5", _reject)], label="local"))

    assert rc == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "L5 canary ERROR[infra_seed]" in output
    assert "infra_seed ConnectionError: localhost:9000 attachment storage unreachable" in output
    assert "skipped=0 ids=[]" in output


def test_canary_catches_adversarial_canned_contract_output(monkeypatch, capsys):
    _install_harness(monkeypatch)

    async def accepts_fabricated_count(_plane, _ctx, run):
        if run["final_text"] == "":
            return False, "empty rejected"
        return run["final_text"] == "count: 0", "fabricated zero accepted"

    rc = asyncio.run(run_canary([_task("R2", accepts_fabricated_count)], label="local"))
    assert rc == 1
    output = capsys.readouterr().out
    assert "accepted canary probe" in output
    assert "count: 0" in output


def test_canary_exits_nonzero_when_no_task_is_verified(monkeypatch):
    _install_harness(
        monkeypatch,
        seed=lambda *args, **kwargs: (_ for _ in ()).throw(TaskSkipped("fixture unavailable")),
    )
    rc = asyncio.run(run_canary([_task("SKIPME", _reject)], label="local"))
    assert rc == 1
