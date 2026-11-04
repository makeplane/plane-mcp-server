"""Persisted run-identity guards for every report path."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals import report as report_mod
from evals.report import identity


def _row(task_id: str = "R1", **overrides: Any) -> dict[str, Any]:
    row = {
        "task_id": task_id,
        "rep": 0,
        "label": "candidate",
        "battery": "samebattery1",
        "resolved_model": "configured-model",
        "provider": "anthropic",
        "driver": "api",
        "server": "local",
        "model": "realized-model",
        "requested_model": "standard",
        "requested_tier": "standard",
        "tool_manifest_fingerprint": "manifest-a",
        "success": True,
        "num_calls": 1,
        "calls": [],
    }
    row.update(overrides)
    return row


def _meta(**overrides: Any) -> dict[str, Any]:
    row = {
        "row_type": "meta",
        "run_id": "run-1",
        "battery": "samebattery1",
        "resolved_model": "configured-model",
        "provider": "anthropic",
        "driver": "api",
        "server": "local",
        "model": "configured-model",
    }
    row.update(overrides)
    return row


def _write(path: Path, *rows: dict[str, Any]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _assert_refused(rc: int, capsys, detail: str) -> None:
    assert rc == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "comparability cannot be established from the persisted identity" in captured.err
    assert detail in captured.err
    assert "aggregate success" not in captured.err
    assert "A/B compare" not in captured.err


def test_single_summary_refuses_rows_mixing_batteries(tmp_path, capsys):
    path = tmp_path / "mixed.jsonl"
    _write(path, _row("R1", battery="battery-a"), _row("R2", battery="battery-b"))

    _assert_refused(report_mod.main([str(path)]), capsys, "rows disagree on battery")


def test_ab_report_refuses_battery_mismatch(tmp_path, capsys):
    path_a = tmp_path / "a.jsonl"
    path_b = tmp_path / "b.jsonl"
    _write(path_a, _row(battery="battery-a"))
    _write(path_b, _row(battery="battery-b"))

    _assert_refused(report_mod.main([str(path_a), str(path_b)]), capsys, "battery differs across files")


def test_multi_surface_table_refuses_battery_mismatch(tmp_path, capsys):
    path_a = tmp_path / "a.jsonl"
    path_b = tmp_path / "b.jsonl"
    _write(path_a, _row(battery="battery-a"))
    _write(path_b, _row(battery="battery-b"))

    rc = report_mod.main(["--table", str(path_a), str(path_b)])

    _assert_refused(rc, capsys, "battery differs across files")


def test_vary_battery_is_a_usage_error(tmp_path, capsys):
    path = tmp_path / "a.jsonl"
    _write(path, _row())

    assert report_mod.main(["--vary", "battery", str(path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--vary battery is not allowed" in captured.err
    assert "measurement universe cannot be waived" in captured.err


def test_vary_all_is_a_usage_error(tmp_path, capsys):
    path = tmp_path / "a.jsonl"
    _write(path, _row())

    assert report_mod.main(["--vary", "all", str(path)]) == 2
    assert "--vary has no 'all'" in capsys.readouterr().err


def test_vary_requires_each_dimension_to_be_named(tmp_path, capsys):
    path = tmp_path / "a.jsonl"
    _write(path, _row())

    assert report_mod.main(["--vary", "provider,", str(path)]) == 2
    assert "--vary requires a dimension name" in capsys.readouterr().err


def test_requested_tier_cannot_be_declared_as_an_identity_dimension(tmp_path, capsys):
    path = tmp_path / "a.jsonl"
    _write(path, _row())

    assert report_mod.main(["--vary", "requested_tier", str(path)]) == 2
    assert "unknown --vary dimension(s): requested_tier" in capsys.readouterr().err


def test_vary_resolved_model_prints_treatment_and_reports_normally(tmp_path, capsys):
    path_a = tmp_path / "a.jsonl"
    path_b = tmp_path / "b.jsonl"
    _write(path_a, _row(resolved_model="model-a", model="realized-a"))
    _write(path_b, _row(resolved_model="model-b", model="realized-b"))

    rc = report_mod.main(["--vary", "resolved_model", str(path_a), str(path_b)])

    assert rc == 0
    captured = capsys.readouterr()
    assert "Treatment: resolved_model" in captured.out
    assert "Realized model evidence:" in captured.out
    assert "A/B compare:" in captured.out
    assert captured.err == ""


def test_two_varied_dimensions_print_end_to_end_attribution_label(tmp_path, capsys):
    path_a = tmp_path / "a.jsonl"
    path_b = tmp_path / "b.jsonl"
    _write(path_a, _row(resolved_model="model-a", provider="anthropic"))
    _write(path_b, _row(resolved_model="model-b", provider="openai"))

    rc = report_mod.main(["--vary", "provider,resolved_model", str(path_a), str(path_b)])

    assert rc == 0
    output = capsys.readouterr().out
    assert (
        "Treatment: resolved_model, provider — end-to-end comparison; effect not attributable to any single dimension"
    ) in output


def test_header_row_disagreement_is_refused_before_latest_wins_dedupe(tmp_path, capsys):
    path = tmp_path / "resume.jsonl"
    _write(
        path,
        _meta(),
        _row(resolved_model="conflicting-model"),
        _row(resolved_model="configured-model"),
    )

    _assert_refused(report_mod.main([str(path)]), capsys, "raw rows on resolved_model")


def test_single_header_row_identity_disagreement_is_refused(tmp_path, capsys):
    path = tmp_path / "integrity.jsonl"
    _write(path, _meta(), _row(provider="openai"))

    _assert_refused(report_mod.main([str(path)]), capsys, "raw rows on provider")


def test_conflicting_meta_headers_are_refused(tmp_path, capsys):
    path = tmp_path / "headers.jsonl"
    _write(path, _meta(run_id="run-1"), _meta(run_id="run-2", driver="codex-cli"))

    _assert_refused(report_mod.main([str(path)]), capsys, "conflicting meta headers for driver")


def test_missing_identity_value_is_not_a_wildcard(tmp_path, capsys):
    path_a = tmp_path / "legacy.jsonl"
    path_b = tmp_path / "identified.jsonl"
    _write(path_a, _row(battery=""))
    _write(path_b, _row(battery="samebattery1"))

    _assert_refused(report_mod.main([str(path_a), str(path_b)]), capsys, "<missing>")


def test_requested_tier_difference_is_not_an_identity_mismatch(tmp_path, capsys):
    path_a = tmp_path / "tier.jsonl"
    path_b = tmp_path / "model-id.jsonl"
    _write(path_a, _row(requested_model="standard", requested_tier="standard"))
    _write(path_b, _row(requested_model="configured-model", requested_tier=None))

    assert report_mod.main([str(path_a), str(path_b)]) == 0
    captured = capsys.readouterr()
    assert "A/B compare:" in captured.out
    assert captured.err == ""


def test_meta_configured_model_is_not_compared_to_row_realized_model(tmp_path, capsys):
    path = tmp_path / "api.jsonl"
    _write(path, _meta(model="configured-model"), _row(model="provider-reported-model"))

    assert report_mod.main([str(path)]) == 0
    captured = capsys.readouterr()
    assert "Realized model evidence:" in captured.out
    assert "provider-reported-model" in captured.out
    assert captured.err == ""


def test_resume_rows_may_have_different_run_ids(tmp_path, capsys):
    path = tmp_path / "resume.jsonl"
    _write(path, _meta(run_id="original"), _row("R1", run_id="original"), _row("R2", run_id="resumed"))

    assert report_mod.main([str(path)]) == 0
    assert capsys.readouterr().err == ""


def test_unacknowledged_provider_mismatch_is_refused(tmp_path, capsys):
    path_a = tmp_path / "a.jsonl"
    path_b = tmp_path / "b.jsonl"
    _write(path_a, _row(provider="anthropic"))
    _write(path_b, _row(provider="openai"))

    _assert_refused(report_mod.main([str(path_a), str(path_b)]), capsys, "provider differs across files")


def test_realized_model_change_within_run_is_flagged_without_refusal(tmp_path, capsys):
    path = tmp_path / "changed-model.jsonl"
    _write(path, _row("R1", model="reported-a"), _row("R2", model="reported-b"))

    assert report_mod.main([str(path)]) == 0
    captured = capsys.readouterr()
    assert "WARNING: realized model changed within" in captured.out
    assert "reported-a,reported-b" in captured.out
    assert captured.err == ""


def test_vary_driver_header_limits_claim_to_end_to_end_driver_question(tmp_path, capsys):
    path_a = tmp_path / "api.jsonl"
    path_b = tmp_path / "cli.jsonl"
    _write(path_a, _row(driver="api"))
    _write(path_b, _row(driver="codex-cli"))

    assert report_mod.main(["--vary", "driver", str(path_a), str(path_b)]) == 0
    output = capsys.readouterr().out
    assert "end-to-end driver question; cannot support a surface-only claim" in output


def test_server_can_be_declared_as_a_treatment(tmp_path, capsys):
    path_a = tmp_path / "local.jsonl"
    path_b = tmp_path / "external.jsonl"
    _write(path_a, _row(server="local"))
    _write(path_b, _row(server="external"))

    assert report_mod.main(["--vary", "server", str(path_a), str(path_b)]) == 0
    assert "Treatment: server" in capsys.readouterr().out


def test_markdown_table_separates_identity_header_from_table(tmp_path, capsys):
    path = tmp_path / "model.jsonl"
    _write(path, _row())

    assert report_mod.main(["--table", "--markdown", str(path)]) == 0
    output = capsys.readouterr().out
    assert "Realized model evidence:" in output
    assert "\n\n| task |" in output


def test_report_rejects_manifest_variation_within_one_result_file(tmp_path, capsys):
    path = tmp_path / "manifest-changed.jsonl"
    _write(
        path,
        _row("R1", tool_manifest_fingerprint="manifest-a"),
        _row("R2", tool_manifest_fingerprint="manifest-b"),
    )
    _assert_refused(
        report_mod.main([str(path)]),
        capsys,
        "rows disagree on tool_manifest_fingerprint",
    )


def test_report_rejects_mixed_present_and_missing_manifests_within_one_file(tmp_path, capsys):
    path = tmp_path / "partially-identified.jsonl"
    _write(
        path,
        _row("R1", tool_manifest_fingerprint="manifest-a"),
        _row("R2", tool_manifest_fingerprint=None),
    )

    _assert_refused(
        report_mod.main([str(path)]),
        capsys,
        "<missing>",
    )


def test_report_identifies_but_does_not_refuse_different_tool_manifests(tmp_path, capsys):
    path_a = tmp_path / "surface-a.jsonl"
    path_b = tmp_path / "surface-b.jsonl"
    _write(path_a, _row(tool_manifest_fingerprint="manifest-a"))
    _write(path_b, _row(tool_manifest_fingerprint="manifest-b"))

    assert report_mod.main([str(path_a), str(path_b)]) == 0
    captured = capsys.readouterr()
    assert "Tool manifest evidence:" in captured.out
    assert "manifest-a" in captured.out
    assert "manifest-b" in captured.out
    assert captured.err == ""


def test_missing_manifest_observation_is_not_fatal(tmp_path, capsys):
    path = tmp_path / "no-manifest.jsonl"
    _write(path, _row(tool_manifest_fingerprint=None))

    assert report_mod.main([str(path)]) == 0
    output = capsys.readouterr().out
    assert "Summary:" in output
    assert "TOOL MANIFEST ABSENT" not in output


def test_ab_and_table_refuse_when_any_tool_manifest_is_absent(tmp_path, capsys):
    path_a = tmp_path / "surface-a.jsonl"
    path_b = tmp_path / "surface-b.jsonl"
    _write(path_a, _row(tool_manifest_fingerprint="manifest-a"))
    _write(path_b, _row(tool_manifest_fingerprint=None))

    _assert_refused(
        report_mod.main([str(path_a), str(path_b)]),
        capsys,
        "tool_manifest_fingerprint is missing for comparison input(s)",
    )

    _assert_refused(
        report_mod.main(["--table", str(path_a), str(path_b)]),
        capsys,
        "every compared surface must be identified",
    )

    _write(path_a, _row(tool_manifest_fingerprint=None))
    _assert_refused(
        report_mod.main([str(path_a), str(path_b)]),
        capsys,
        "every compared surface must be identified",
    )


def test_exact_run_keys_name_missing_and_duplicate_rows_before_latest_wins(tmp_path, capsys):
    path = tmp_path / "wrong-keys.jsonl"
    _write(
        path,
        _meta(expected_rows=2, expected_task_ids=["R1", "R2"], expected_reps=1),
        _row("R1"),
        _row("R1"),
    )

    assert report_mod.main([str(path)]) == 1
    output = capsys.readouterr().out
    assert "RUN INCOMPLETE:" in output
    assert "missing keys=[R2[rep=0]]" in output
    assert "unexpected keys=[R1[rep=0]]" in output


def test_exact_run_keys_reject_duplicate_excess_even_when_every_expected_key_exists(tmp_path, capsys):
    path = tmp_path / "duplicate-excess.jsonl"
    _write(
        path,
        _meta(expected_rows=2, expected_task_ids=["R1", "R2"], expected_reps=1),
        _row("R1"),
        _row("R2"),
        _row("R1"),
    )

    assert report_mod.main([str(path)]) == 1
    output = capsys.readouterr().out
    assert "RUN INCOMPLETE: 2/2 rows completed" in output
    assert "unexpected keys=[R1[rep=0]]" in output


def test_exact_run_keys_accept_append_only_retry_history_when_only_last_row_is_terminal(tmp_path, capsys):
    path = tmp_path / "retry-history.jsonl"
    _write(
        path,
        _meta(expected_rows=2, expected_task_ids=["R1", "R2"], expected_reps=1),
        _row("R1", success=False, error="timeout", error_class="infra_cli"),
        _row("R2"),
        _row("R1"),
    )

    assert report_mod.main([str(path)]) == 0
    output = capsys.readouterr().out
    assert "RUN COMPLETE: 2/2 rows completed" in output


def test_exact_run_keys_accept_declared_task_subset_with_all_repetitions(tmp_path, capsys):
    path = tmp_path / "subset.jsonl"
    _write(
        path,
        _meta(expected_rows=4, expected_task_ids=["R1", "W1"], expected_reps=2),
        _row("R1", rep=0),
        _row("R1", rep=1),
        _row("W1", rep=0),
        _row("W1", rep=1),
    )

    assert report_mod.main([str(path)]) == 0
    assert "RUN COMPLETE: 4/4 rows completed" in capsys.readouterr().out


def test_malformed_exact_run_expectation_is_refused_instead_of_treated_as_legacy(tmp_path, capsys):
    path = tmp_path / "malformed-expectation.jsonl"
    _write(
        path,
        _meta(expected_rows=2, expected_task_ids=["R1", "R1"], expected_reps=1),
        _row("R1"),
    )

    assert report_mod.main([str(path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid run expectation" in captured.err
    assert "expected_task_ids contains duplicates" in captured.err


def test_infra_error_rows_without_a_manifest_do_not_refuse_the_comparison(tmp_path: Path):
    """A row that died in seeding never ran an agent, so it has no manifest to carry.

    The first live A/B was refused because six infra_seed rows (L2 and L5, identical on both
    surfaces) had no tool_manifest_fingerprint — rows every statistic already excludes. Demanding
    identity from a row that never reached the surface refuses sound comparisons.
    """
    path = tmp_path / "rows.jsonl"
    surface = {
        "task_id": "R1",
        "rep": 0,
        "label": "local",
        "battery": "b1",
        "server": "local",
        "driver": "codex-cli",
        "provider": "openai",
        "resolved_model": "m",
        "tool_manifest_fingerprint": "fp-a",
        "success": True,
    }
    seed_failure = {
        "task_id": "L5",
        "rep": 0,
        "label": "local",
        "battery": "b1",
        "server": "local",
        "driver": "codex-cli",
        "provider": "openai",
        "resolved_model": "m",
        "error_class": "infra_seed",
        "error": "boom",
    }
    path.write_text(
        json.dumps(surface) + "\n" + json.dumps(seed_failure) + "\n",
        encoding="utf-8",
    )
    report = identity.validate_persisted_identity([path])
    assert report.files[0].values[identity.TOOL_MANIFEST_FIELD] == "fp-a"


def test_expected_skip_rows_without_a_manifest_do_not_refuse_the_comparison(tmp_path: Path):
    """A plan-gated skip ends before the agent starts, so it carries no manifest either.

    Every report command exited 2 on any file mixing one such skip with evaluated rows, even
    though summary semantics count an expected skip as a complete row.
    """
    path = tmp_path / "rows.jsonl"
    common = {
        "rep": 0,
        "label": "local",
        "battery": "b1",
        "server": "local",
        "driver": "codex-cli",
        "provider": "openai",
        "resolved_model": "m",
    }
    surface = {**common, "task_id": "R1", "tool_manifest_fingerprint": "fp-a", "success": True}
    plan_gated = {**common, "task_id": "L4", "skipped": "env:plan-gated:customers"}
    path.write_text(json.dumps(surface) + "\n" + json.dumps(plan_gated) + "\n", encoding="utf-8")

    report = identity.validate_persisted_identity([path])
    assert report.files[0].values[identity.TOOL_MANIFEST_FIELD] == "fp-a"
