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


def test_cmd_behaviours(capsys):
    def test_cmd_list_prints_all_task_ids(capsys):
        rc = cmd_list()
        assert rc == 0
        out = capsys.readouterr().out
        for tid in DESIGN_IDS | EXTRA_IDS:
            assert tid in out

    def test_cmd_dry_run_all_tasks(capsys):
        rc = cmd_dry_run(list(TASKS))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Seed plan:" in out
        for tid in ("R1", "W9", "S4", "C2", "R7"):
            assert f"=== {tid} ===" in out

    test_cmd_list_prints_all_task_ids(capsys)
    test_cmd_dry_run_all_tasks(capsys)


def test_parse_args_behaviours():
    def test_parse_args_list():
        a = parse_args(["--list", "--label", "candidate-build"])
        assert a.list is True
        assert a.label == "candidate-build"
        assert parse_args(["--list"]).label == "local"

    def test_parse_args_accepts_driver():
        a = parse_args(["--driver", "claude-cli", "--dry-run"])
        assert a.driver == "claude-cli"
        b = parse_args(["--dry-run"])
        assert b.driver == "api"
        assert b.model == "standard"
        assert b.provider == "anthropic"
        assert b.record_result_payloads is False
        c = parse_args(["--driver", "claude-cli", "--record-result-payloads", "--dry-run"])
        assert c.record_result_payloads is True

    def test_parse_args_resume_and_canary():
        a = run_mod.parse_args(["--resume", "evals/output/x.jsonl", "--dry-run"])
        assert a.resume == "evals/output/x.jsonl"
        b = run_mod.parse_args(["--canary", "--tasks", "R1"])
        assert b.canary is True

    test_parse_args_list()
    test_parse_args_accepts_driver()
    test_parse_args_resume_and_canary()


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


def test_unmapped_behaviours(tmp_path, capsys):
    def test_unmapped_opencode_tier_fails_with_explicit_model_guidance():
        with pytest.raises(ValueError, match=r"opencode models"):
            resolve_model_for_driver("opencode-cli", "standard")

    def test_unmapped_tier_cli_error_is_loud_and_prevents_run(tmp_path, capsys):
        out = tmp_path / "must-not-exist.jsonl"

        rc = eval_main(
            [
                "--driver",
                "opencode-cli",
                "--model",
                "standard",
                "--tasks",
                "R1",
                "--out",
                str(out),
            ]
        )

        assert rc == 2
        assert "explicit provider/model ID" in capsys.readouterr().err
        assert out.exists() is False

    test_unmapped_opencode_tier_fails_with_explicit_model_guidance()
    _d1 = tmp_path / "test_unmapped_tier_cli_error_is_loud_and_prevents_run"
    _d1.mkdir()
    test_unmapped_tier_cli_error_is_loud_and_prevents_run(_d1, capsys)


def test_tier_mapping_is_scoped_to_cli_provider():
    with pytest.raises(ValueError, match=r"codex-cli.*anthropic.*explicit model ID"):
        resolve_model_for_driver("codex-cli", "standard", provider="anthropic")


def test_qualified_model_id_passes_through_unchanged():
    assert resolve_model_for_driver("opencode-cli", "openai/gpt-4o") == "openai/gpt-4o"
