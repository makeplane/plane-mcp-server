"""Offline eval tests for cli."""

from __future__ import annotations

import pytest

from evals import cli as run_mod
from evals.cli import cmd_dry_run, cmd_list, parse_args, resolve_model_for_driver
from evals.cli import main as eval_main
from evals.tasks.catalog import TASKS

DESIGN_IDS = {
    "R1",
    "R2",
    "R3",
    "R4",
    "R5",
    "R6",
    "W1",
    "W2",
    "W3",
    "W4",
    "W5",
    "W6",
    "W7",
    "W8",
    "S1",
    "S2",
    "S3",
    "S4",
    "C1",
    "C2",
}

EXTRA_IDS = {"W9", "W10", "R7", "S5"}  # bulk, pages, transitions, features


@pytest.mark.parametrize("case", ["list-task-ids", "dry-run-all"])
def test_cmd_behaviours(case, capsys):
    if case == "list-task-ids":
        assert cmd_list() == 0
        out = capsys.readouterr().out
        for task_id in DESIGN_IDS | EXTRA_IDS:
            assert task_id in out
    else:
        assert cmd_dry_run(list(TASKS)) == 0
        out = capsys.readouterr().out
        assert "Seed plan:" in out
        for task_id in ("R1", "W9", "S4", "C2", "R7"):
            assert f"=== {task_id} ===" in out


@pytest.mark.parametrize("case", ["list", "driver", "resume-and-canary"])
def test_parse_args_behaviours(case):
    if case == "list":
        args = parse_args(["--list", "--label", "candidate-build"])
        assert args.list is True
        assert args.label == "candidate-build"
        assert parse_args(["--list"]).label == "local"
    elif case == "driver":
        assert parse_args(["--driver", "claude-cli", "--dry-run"]).driver == "claude-cli"
        defaults = parse_args(["--dry-run"])
        assert (defaults.driver, defaults.model, defaults.provider) == ("api", "standard", "anthropic")
        assert defaults.record_result_payloads is False
        recorded = parse_args(["--driver", "claude-cli", "--record-result-payloads", "--dry-run"])
        assert recorded.record_result_payloads is True
    else:
        assert run_mod.parse_args(["--resume", "evals/output/x.jsonl", "--dry-run"]).resume == "evals/output/x.jsonl"
        assert run_mod.parse_args(["--canary", "--tasks", "R1"]).canary is True
        assert run_mod.parse_args(["--canary", "--canary-strict", "R1,R2"]).canary_strict == "R1,R2"


def test_canary_strict_cli_passes_explicit_required_ids(monkeypatch):
    seen: dict = {}

    async def fake_canary(tasks, *, label, required_task_ids):
        seen.update(task_ids=[task["id"] for task in tasks], label=label, required=required_task_ids)
        return 0

    monkeypatch.setattr(run_mod, "run_canary", fake_canary)
    rc = eval_main(["--canary", "--tasks", "R1,R2", "--canary-strict", "R1", "--label", "ci"])
    assert rc == 0
    assert seen == {"task_ids": ["R1", "R2"], "label": "ci", "required": {"R1"}}


def test_canary_strict_cli_rejects_invalid_usage(capsys):
    assert eval_main(["--canary-strict", "R1"]) == 2
    assert "requires --canary" in capsys.readouterr().err
    assert eval_main(["--canary", "--canary-strict", "NOPE"]) == 2
    assert "unknown --canary-strict" in capsys.readouterr().err


def test_model_tiers_resolve_per_driver_and_provider():
    assert resolve_model_for_driver("api", "standard", provider="anthropic") == "claude-sonnet-5"
    assert resolve_model_for_driver("api", "fast", provider="anthropic") == "claude-haiku-4-5"
    assert resolve_model_for_driver("api", "standard", provider="openai") == "gpt-5.6-sol"
    assert resolve_model_for_driver("api", "fast", provider="openai") == "gpt-5.6-luna"
    assert resolve_model_for_driver("claude-cli", "standard") == "sonnet"
    assert resolve_model_for_driver("claude-cli", "fast") == "haiku"
    assert resolve_model_for_driver("codex-cli", "standard") == "gpt-5.6-sol"
    assert resolve_model_for_driver("codex-cli", "fast") == "gpt-5.6-luna"
    assert resolve_model_for_driver("antigravity-cli", "standard") == "gemini-3.6-flash-high"
    assert resolve_model_for_driver("antigravity-cli", "fast") == "gemini-3.6-flash-low"


@pytest.mark.parametrize(
    ("driver", "model"),
    [
        ("api", "claude-opus-5"),
        ("api", "sonnet"),
        ("claude-cli", "sonnet"),
        ("codex-cli", "sonnet"),
        ("antigravity-cli", "gemini-3.1-pro-high"),
        ("opencode-cli", "haiku"),
    ],
)
def test_non_tier_model_strings_pass_through_unchanged(driver, model):
    assert resolve_model_for_driver(driver, model) == model


@pytest.mark.parametrize("case", ["direct-guidance", "cli-error"])
def test_unmapped_behaviours(case, tmp_path, capsys):
    if case == "direct-guidance":
        with pytest.raises(ValueError, match=r"opencode models"):
            resolve_model_for_driver("opencode-cli", "standard")
        return

    out = tmp_path / "must-not-exist.jsonl"
    rc = eval_main(["--driver", "opencode-cli", "--model", "standard", "--tasks", "R1", "--out", str(out)])
    assert rc == 2
    assert "explicit provider/model ID" in capsys.readouterr().err
    assert out.exists() is False


def test_tier_mapping_is_scoped_to_cli_provider():
    with pytest.raises(ValueError, match=r"codex-cli.*anthropic.*explicit model ID"):
        resolve_model_for_driver("codex-cli", "standard", provider="anthropic")


def test_qualified_model_id_passes_through_unchanged():
    assert resolve_model_for_driver("opencode-cli", "openai/gpt-4o") == "openai/gpt-4o"
