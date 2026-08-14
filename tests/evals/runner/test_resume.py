"""Offline eval tests for resume."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from evals.report import (
    is_meta_row,
)
from evals.results import AgentRun
from evals.runner import (
    is_meta_or_non_task_row,
    load_resume_skip_keys,
    make_run_meta_row,
    maybe_write_run_meta,
    run_live,
    should_skip_resume_row,
)
from evals.runner import live as runner_live
from tests.evals.conftest import _data_rows


def test_should_skip_behaviours():
    def test_should_skip_resume_row_completed_success():
        assert should_skip_resume_row({"error": None, "error_class": None, "success": True}) is True

    def test_should_skip_resume_row_verify_fail_without_error():
        assert should_skip_resume_row({"error": None, "error_class": None, "success": False}) is True

    def test_should_skip_resume_row_infra_seed_retries():
        assert should_skip_resume_row({"error": "HttpError: 409", "error_class": "infra_seed"}) is False

    def test_should_skip_resume_row_infra_cli_retries():
        assert should_skip_resume_row({"error": "timeout after 120s", "error_class": "infra_cli"}) is False

    def test_should_skip_resume_row_non_null_error_retries():
        assert should_skip_resume_row({"error": "TypeError: x", "error_class": "task"}) is False
        assert should_skip_resume_row({"error": "boom", "error_class": None}) is False

    test_should_skip_resume_row_completed_success()
    test_should_skip_resume_row_verify_fail_without_error()
    test_should_skip_resume_row_infra_seed_retries()
    test_should_skip_resume_row_infra_cli_retries()
    test_should_skip_resume_row_non_null_error_retries()


def test_load_behaviours(tmp_path, capsys):
    def test_load_resume_skip_keys_summary(tmp_path):
        p = tmp_path / "out.jsonl"
        rows = [
            {"task_id": "R1", "rep": 0, "label": "local", "error": None, "error_class": None},
            {"task_id": "R1", "rep": 1, "label": "local", "error": "x", "error_class": "infra_seed"},
            {"task_id": "W1", "rep": 0, "label": "local", "error": None, "success": False},
        ]
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        skip, n_skip, n_retry = load_resume_skip_keys(p, label="local")
        assert skip == {("R1", 0, "local"), ("W1", 0, "local")}
        assert n_skip == 2
        assert n_retry == 1

    def test_load_resume_skip_keys_n_retry_ignores_later_success(tmp_path):
        p = tmp_path / "out.jsonl"
        rows = [
            {"task_id": "R1", "rep": 0, "label": "local", "error": "boom", "error_class": "infra_cli"},
            {"task_id": "R1", "rep": 0, "label": "local", "error": None, "error_class": None, "success": True},
        ]
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        skip, n_skip, n_retry = load_resume_skip_keys(p, label="local")
        assert skip == {("R1", 0, "local")}
        assert n_skip == 1
        assert n_retry == 0

    def test_load_resume_skip_keys_label_mismatch(tmp_path):
        p = tmp_path / "out.jsonl"
        p.write_text(json.dumps({"task_id": "R1", "rep": 0, "label": "other", "error": None}) + "\n")
        with pytest.raises(SystemExit, match="label"):
            load_resume_skip_keys(p, label="local")

    def test_load_resume_skip_keys_battery_model_driver_mismatch(tmp_path):
        p = tmp_path / "out.jsonl"
        p.write_text(
            json.dumps(
                {
                    "task_id": "R1",
                    "rep": 0,
                    "label": "local",
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
            load_resume_skip_keys(p, label="local", battery="bbbbbbbbbbbb")
        with pytest.raises(SystemExit, match="model"):
            load_resume_skip_keys(p, label="local", battery="aaaaaaaaaaaa", model="haiku")
        with pytest.raises(SystemExit, match="driver"):
            load_resume_skip_keys(p, label="local", battery="aaaaaaaaaaaa", model="sonnet", driver="unknown")
        # Missing keys on older rows: pass (back-compat)
        p2 = tmp_path / "old.jsonl"
        p2.write_text(json.dumps({"task_id": "R1", "rep": 0, "label": "local", "error": None}) + "\n")
        skip, _, _ = load_resume_skip_keys(p2, label="local", battery="anything", model="sonnet", driver="claude-cli")
        assert ("R1", 0, "local") in skip

    def test_load_resume_skip_keys_truncated_json(tmp_path, capsys):
        p = tmp_path / "out.jsonl"
        p.write_text(
            json.dumps({"task_id": "R1", "rep": 0, "label": "local", "error": None})
            + "\n"
            + '{"task_id": "W1", "rep": 0, "label": "local", "error":\n',  # truncated
            encoding="utf-8",
        )
        skip, n_skip, n_retry = load_resume_skip_keys(p, label="local")
        assert skip == {("R1", 0, "local")}
        assert n_skip == 1
        err = capsys.readouterr().err
        assert "invalid JSON" in err

    def test_load_resume_skip_keys_missing_file(tmp_path):
        skip, n_skip, n_retry = load_resume_skip_keys(tmp_path / "missing.jsonl", label="local")
        assert skip == set() and n_skip == 0 and n_retry == 0

    _d0 = tmp_path / "test_load_resume_skip_keys_summary"
    _d0.mkdir()
    test_load_resume_skip_keys_summary(_d0)
    _d1 = tmp_path / "test_load_resume_skip_keys_n_retry_ignores_later_success"
    _d1.mkdir()
    test_load_resume_skip_keys_n_retry_ignores_later_success(_d1)
    _d2 = tmp_path / "test_load_resume_skip_keys_label_mismatch"
    _d2.mkdir()
    test_load_resume_skip_keys_label_mismatch(_d2)
    _d3 = tmp_path / "test_load_resume_skip_keys_battery_model_driver_mismatch"
    _d3.mkdir()
    test_load_resume_skip_keys_battery_model_driver_mismatch(_d3)
    _d4 = tmp_path / "test_load_resume_skip_keys_truncated_json"
    _d4.mkdir()
    test_load_resume_skip_keys_truncated_json(_d4, capsys)
    _d5 = tmp_path / "test_load_resume_skip_keys_missing_file"
    _d5.mkdir()
    test_load_resume_skip_keys_missing_file(_d5)


def test_resume_behaviours(tmp_path):
    def test_resume_identity_uses_resolved_model_not_tier_label(tmp_path):
        p = tmp_path / "tiered.jsonl"
        p.write_text(
            json.dumps(
                {
                    "task_id": "R1",
                    "rep": 0,
                    "label": "local",
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

        skip, _, _ = load_resume_skip_keys(p, label="local", model="old-standard-id")
        assert skip == {("R1", 0, "local")}
        with pytest.raises(SystemExit, match="model"):
            load_resume_skip_keys(p, label="local", model="new-standard-id")

    def test_resume_skips_meta_and_mismatch_checks_it(tmp_path):
        p = tmp_path / "out.jsonl"
        p.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "row_type": "meta",
                            "label": "candidate",
                            "battery": "bbbbbbbbbbbb",
                            "model": "sonnet",
                            "driver": "claude-cli",
                        }
                    ),
                    json.dumps(
                        {
                            "task_id": "R1",
                            "rep": 0,
                            "label": "candidate",
                            "error": None,
                            "error_class": None,
                            "success": True,
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        skip, n_skip, n_retry = load_resume_skip_keys(
            p, label="candidate", battery="bbbbbbbbbbbb", model="sonnet", driver="claude-cli"
        )
        assert skip == {("R1", 0, "candidate")}
        assert n_skip == 1 and n_retry == 0

        with pytest.raises(SystemExit, match="battery"):
            load_resume_skip_keys(p, label="candidate", battery="aaaaaaaaaaaa", model="sonnet", driver="claude-cli")

    _d0 = tmp_path / "test_resume_identity_uses_resolved_model_not_tier_label"
    _d0.mkdir()
    test_resume_identity_uses_resolved_model_not_tier_label(_d0)
    _d1 = tmp_path / "test_resume_skips_meta_and_mismatch_checks_it"
    _d1.mkdir()
    test_resume_skips_meta_and_mismatch_checks_it(_d1)


def test_run_live_resume_skips_completed_retries_infra(tmp_path: Path, monkeypatch):
    out = tmp_path / "resume.jsonl"
    # Pre-write: completed R1/0 + infra R2/0 (same label/battery/model/driver as this run).
    # Battery is computed from the task list below — seed the file after we know it,
    # or write rows without battery (back-compat) and only check skip/retry behavior.
    prior = [
        {
            "task_id": "R1",
            "rep": 0,
            "label": "local",
            "driver": "claude-cli",
            "model": "sonnet",
            "error": None,
            "error_class": None,
            "success": True,
        },
        {
            "task_id": "R2",
            "rep": 0,
            "label": "local",
            "driver": "claude-cli",
            "model": "sonnet",
            "error": "HttpError: 409",
            "error_class": "infra_seed",
            "success": False,
        },
    ]
    out.write_text("\n".join(json.dumps(r) for r in prior) + "\n", encoding="utf-8")

    fake_plane = MagicMock()
    monkeypatch.setattr(runner_live, "make_plane_client", lambda: (fake_plane, "test-ws"))
    seed_calls: list[str] = []

    def ok_seed(plane, run_id, needs, ctx):
        # Infer task from empty ctx; runner sets project for verify path.
        ctx.update({"project_name": "EVAL resume", "project_id": "p1"})
        seed_calls.append(run_id)

    monkeypatch.setattr(runner_live, "seed", ok_seed)
    monkeypatch.setattr(runner_live, "teardown", lambda *a, **k: None)

    class OkDriver:
        name = "claude-cli"

        def run_task(self, *args, **kwargs):
            return AgentRun(
                calls=[{"tool": "list_work_items", "args": {}, "origin": "plane"}],
                final_text="done",
                usage=None,
                stopped_reason="end_turn",
            )

    monkeypatch.setattr(runner_live, "get_driver", lambda name, **kw: OkDriver())

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
            label="local",
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


def test_make_run_meta_row_and_write_once(tmp_path: Path):
    path = tmp_path / "out.jsonl"
    meta = make_run_meta_row(
        run_id="rid",
        label="candidate",
        server="local",
        battery="abcd1234ef00",
        model="sonnet",
        driver="claude-cli",
        git_sha="deadbeef",
        ts="2026-01-01T00:00:00+00:00",
    )
    assert meta["row_type"] == "meta"
    assert is_meta_row(meta)
    assert is_meta_or_non_task_row(meta)
    assert maybe_write_run_meta(path, meta) is True
    # Append a data row — a truncating rewrite on the second call would destroy it.
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"task_id": "R1", "rep": 0, "label": "candidate", "success": True}) + "\n")
    assert maybe_write_run_meta(path, meta) is False  # file non-empty
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["row_type"] == "meta"
    assert json.loads(lines[1])["task_id"] == "R1"
