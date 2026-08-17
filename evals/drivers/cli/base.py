"""The subprocess template every CLI vendor driver fills in.

A CLI driver runs the agent the user already pays for, in a process the harness does not
control. So unlike the API driver it cannot see the tool calls happen: it wraps the MCP
server with the recording proxy (``sidecar``), supervises the process group
(``process``), and reconciles what the proxy captured against what the vendor reported.

Vendors supply four things — the MCP config file, the command, the output parse, and any
post-reconciliation handling. Everything shared about the run lives here.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals import REPO_ROOT
from evals.drivers.cli.process import note_timeout_kill, run_cli_subprocess
from evals.drivers.cli.sidecar import (
    ProxySidecarResult,
    apply_proxy_sidecar,
    ensure_proxy_pythonpath,
    harvest_proxy_after_cli_timeout,
    load_proxy_sidecar,
    proxy_wrap_server_command,
)
from evals.evidence import (
    configured_evidence_labels,
    normalize_evidence_aggregates,
    normalize_evidence_sentinels,
    normalize_evidence_targets,
    observed_aggregate_labels,
    write_evidence_config,
)
from evals.results import AgentRun


@dataclass
class CliLaunch:
    """Vendor-prepared CLI launch details."""

    cwd: Path
    config_args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    artifact_dir: Path | None = None


@dataclass
class CliOutput:
    """Normalized vendor output consumed by the shared ``AgentRun`` assembly."""

    final_text: str
    calls: list[dict[str, Any]] = field(default_factory=list)
    client_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    usage_total: dict[str, Any] | None = None
    stopped_reason: str = "end_turn"
    raw_ref: str | None = None
    call_source: str = "json"
    hit_max_turns: bool = False


class CliOutputError(RuntimeError):
    """Signal that vendor output could not produce a valid ``AgentRun``."""


class CliRunError(RuntimeError):
    """CLI failure retaining typed sidecar observations for the result row."""

    def __init__(self, message: str, sidecar: ProxySidecarResult | None = None) -> None:
        super().__init__(message)
        self.trace_integrity = sidecar.trace_integrity if sidecar is not None else True
        self.trace_integrity_reason = sidecar.trace_integrity_reason if sidecar is not None else None
        self.tool_manifest_fingerprint = sidecar.tool_manifest_fingerprint if sidecar is not None else None


class CliDriver(ABC):
    """Template for CLI drivers that run one MCP-backed subprocess task."""

    name: str
    experimental = False
    default_call_source = "json"
    run_notes: tuple[str, ...] = ()
    temp_dir_prefix = "plane-eval-cli-"
    temp_dir_in_cwd = False
    exit_note_prefix: str | None = None
    include_stderr_in_exit_note = False

    def __init__(
        self,
        *,
        python_bin: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        server_command: list[str] | None = None,
        use_proxy: bool = True,
        record_result_payloads: bool = False,
    ) -> None:
        self.python_bin = python_bin or sys.executable
        self._runner = runner or run_cli_subprocess
        self.server_command = list(server_command) if server_command else None
        self.use_proxy = use_proxy
        self.record_result_payloads = record_result_payloads

    def validate_run(self) -> None:
        """Reject a launch before any temporary state is created, if needed."""
        return None

    @abstractmethod
    def write_mcp_config(
        self,
        temp_dir: Path,
        *,
        task_cwd: Path,
        server_command: list[str],
        child_env: dict[str, str],
    ) -> CliLaunch:
        """Write vendor MCP configuration and return launch settings."""

    @abstractmethod
    def build_command(
        self,
        prompt: str,
        *,
        model: str | None,
        max_turns: int,
        system: str | None,
        launch: CliLaunch,
    ) -> list[str]:
        """Build the vendor CLI command."""

    def invoke_cli(
        self,
        command: list[str],
        *,
        launch: CliLaunch,
        timeout_s: int,
    ) -> subprocess.CompletedProcess[str]:
        """Invoke the configured runner with the shared subprocess contract."""
        kwargs: dict[str, Any] = {
            "cwd": str(launch.cwd),
            "capture_output": True,
            "text": True,
            "timeout": timeout_s,
        }
        if launch.env is not None:
            kwargs["env"] = launch.env
        return self._runner(command, **kwargs)

    @abstractmethod
    def parse_output(
        self,
        proc: subprocess.CompletedProcess[str],
        *,
        launch: CliLaunch,
        task_cwd: Path,
        max_turns: int,
        notes: list[str],
    ) -> CliOutput:
        """Parse vendor output into the normalized CLI result shape."""

    def finalize_run(
        self,
        proc: subprocess.CompletedProcess[str],
        *,
        output: CliOutput,
        notes: list[str],
    ) -> None:
        """Apply vendor handling that must occur after proxy reconciliation."""
        del output
        if proc.returncode != 0 and self.exit_note_prefix:
            notes.append(f"{self.exit_note_prefix}_exit={proc.returncode}")
            stderr = proc.stderr or ""
            if self.include_stderr_in_exit_note and stderr.strip():
                notes.append(stderr.strip()[:500])

    def run_task(
        self,
        prompt: str,
        mcp_env: dict[str, str],
        model: str | None,
        max_turns: int,
        *,
        system: str | None = None,
        cwd: Path | None = None,
        evidence_sentinels: dict[str, Any] | None = None,
        evidence_targets: dict[str, Any] | None = None,
        evidence_aggregates: dict[str, Any] | None = None,
        artifact_dir: Path | None = None,
    ) -> AgentRun:
        """Run one CLI task using the shared configuration/proxy/timeout flow."""
        task_cwd = (cwd or REPO_ROOT).resolve()
        notes = list(self.run_notes)
        self.validate_run()
        temp_parent = str(task_cwd) if self.temp_dir_in_cwd else None

        with (
            tempfile.TemporaryDirectory(prefix=self.temp_dir_prefix, dir=temp_parent) as td,
            tempfile.TemporaryDirectory(prefix="plane-eval-evidence-") as evidence_td,
        ):
            temp_dir = Path(td)
            sidecar = temp_dir / "proxy-sidecar.jsonl"
            child_env = {
                key: value for key, value in mcp_env.items() if key.startswith("PLANE_") or key in ("PATH", "HOME")
            }
            evidence = normalize_evidence_sentinels(evidence_sentinels)
            targets = normalize_evidence_targets(evidence_targets)
            aggregates = normalize_evidence_aggregates(evidence_aggregates)
            evidence_active = bool(configured_evidence_labels(evidence, targets, aggregates))

            def verify_aggregate_observations(calls: list[dict[str, Any]]) -> None:
                """Turn observed proxy values into labels using harness-held seed truth."""
                for call in calls:
                    labels = set(call.get("observed_sentinels") or [])
                    labels.update(observed_aggregate_labels(call.get("observed_aggregates"), aggregates))
                    if evidence_active:
                        call["observed_sentinels"] = sorted(labels)

            real_command = (
                list(self.server_command) if self.server_command else [self.python_bin, "-m", "plane_mcp", "stdio"]
            )
            server_command = real_command
            if self.use_proxy:
                evidence_path = None
                if evidence_active:
                    evidence_path = Path(evidence_td) / "proxy-evidence.json"
                    write_evidence_config(evidence_path, evidence, targets, aggregates)
                server_command = proxy_wrap_server_command(
                    real_command,
                    sidecar_path=sidecar,
                    python_bin=self.python_bin,
                    record_result_payloads=self.record_result_payloads,
                    evidence_path=evidence_path,
                )
                child_env = ensure_proxy_pythonpath(child_env)

            launch = self.write_mcp_config(
                temp_dir,
                task_cwd=task_cwd,
                server_command=server_command,
                child_env=child_env,
            )
            launch.artifact_dir = (
                artifact_dir
                if artifact_dir is not None
                else task_cwd / "evals" / "output" / "driver-artifacts" / self.name
            ).resolve()
            command = self.build_command(
                prompt,
                model=model,
                max_turns=max_turns,
                system=system,
                launch=launch,
            )
            timeout_s = max(120, max_turns * 60)
            # Persisted schema v1 defines wall time as the CLI invocation only.
            started_at = time.perf_counter()

            try:
                proc = self.invoke_cli(command, launch=launch, timeout_s=timeout_s)
            except subprocess.TimeoutExpired as exc:
                wall = time.perf_counter() - started_at
                notes.append(f"timeout after {timeout_s}s")
                note_timeout_kill(notes, exc)
                calls: list[dict[str, Any]] = []
                client_calls: list[dict[str, Any]] = []
                call_source = self.default_call_source
                trace_integrity = True
                trace_integrity_reason = None
                tool_manifest_fingerprint = None
                if self.use_proxy:
                    sidecar_result = harvest_proxy_after_cli_timeout(
                        calls,
                        client_calls,
                        sidecar,
                        notes,
                    )
                    calls, client_calls, call_source = sidecar_result
                    verify_aggregate_observations(calls)
                    trace_integrity = sidecar_result.trace_integrity
                    trace_integrity_reason = sidecar_result.trace_integrity_reason
                    tool_manifest_fingerprint = sidecar_result.tool_manifest_fingerprint
                evidence_available = False
                if evidence_active and call_source == "proxy":
                    _proxy_calls, status = load_proxy_sidecar(sidecar)
                    evidence_available = bool(
                        status.get("state") == "complete" and status.get("evidence_trace_available")
                    )
                    if not evidence_available:
                        notes.append("proxy_response_evidence_unavailable")
                return AgentRun(
                    calls=calls,
                    client_tool_calls=client_calls,
                    final_text="",
                    usage=None,
                    stopped_reason="timeout",
                    raw_ref=None,
                    usage_scope="run",
                    call_source=call_source,
                    hit_max_turns=False,
                    wall_time_s=round(wall, 3),
                    evidence_trace_available=evidence_available,
                    trace_integrity=trace_integrity,
                    trace_integrity_reason=trace_integrity_reason,
                    tool_manifest_fingerprint=tool_manifest_fingerprint,
                    experimental=self.experimental,
                    notes=notes,
                )

            wall = time.perf_counter() - started_at
            try:
                output = self.parse_output(
                    proc,
                    launch=launch,
                    task_cwd=task_cwd,
                    max_turns=max_turns,
                    notes=notes,
                )
            except CliOutputError as exc:
                sidecar_result = None
                if self.use_proxy:
                    sidecar_result = apply_proxy_sidecar([], [], sidecar, notes)
                detail = "; ".join(notes)
                raise CliRunError(f"{exc}: {detail}", sidecar_result) from None

            trace_integrity = True
            trace_integrity_reason = None
            tool_manifest_fingerprint = None
            if self.use_proxy:
                sidecar_result = apply_proxy_sidecar(
                    output.calls,
                    output.client_tool_calls,
                    sidecar,
                    notes,
                )
                calls, client_calls, proxy_source = sidecar_result
                output.calls = calls
                output.client_tool_calls = client_calls
                verify_aggregate_observations(output.calls)
                trace_integrity = sidecar_result.trace_integrity
                trace_integrity_reason = sidecar_result.trace_integrity_reason
                tool_manifest_fingerprint = sidecar_result.tool_manifest_fingerprint
                if proxy_source == "proxy":
                    output.call_source = "proxy"

            evidence_available = False
            if evidence_active and output.call_source == "proxy":
                _proxy_calls, status = load_proxy_sidecar(sidecar)
                evidence_available = bool(status.get("state") == "complete" and status.get("evidence_trace_available"))
                if not evidence_available:
                    notes.append("proxy_response_evidence_unavailable")

            self.finalize_run(proc, output=output, notes=notes)
            return AgentRun(
                calls=output.calls,
                client_tool_calls=output.client_tool_calls,
                final_text=output.final_text,
                usage=output.usage,
                usage_total=output.usage_total,
                stopped_reason=output.stopped_reason,
                raw_ref=output.raw_ref,
                usage_scope="run",
                call_source=output.call_source,
                hit_max_turns=output.hit_max_turns,
                wall_time_s=round(wall, 3),
                evidence_trace_available=evidence_available,
                trace_integrity=trace_integrity,
                trace_integrity_reason=trace_integrity_reason,
                tool_manifest_fingerprint=tool_manifest_fingerprint,
                experimental=self.experimental,
                notes=notes,
            )


__all__ = [
    "CliDriver",
    "CliLaunch",
    "CliOutput",
    "CliOutputError",
    "CliRunError",
]
