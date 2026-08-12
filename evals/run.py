"""Compatibility entry point for the Plane MCP eval harness.

CLI concerns live in :mod:`evals.cli`; live execution and result bookkeeping
live in :mod:`evals.runner`. Existing imports and ``python -m evals.run`` remain
stable through this façade.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from evals import runner
from evals.cli import (
    API_MODEL_ALIASES,
    CLI_MODEL_ALIASES,
    DEFAULT_OUT_DIR,
    MODEL_ALIASES,
    cmd_dry_run,
    cmd_list,
    main,
    parse_args,
    resolve_model_for_driver,
)
from evals.runner import (
    KNOWN_SURFACES,
    MAX_ITERATIONS,
    MAX_TOKENS,
    classify_call,
    is_infra_cli_stop_reason,
    is_meta_or_non_task_row,
    load_resume_skip_keys,
    make_run_meta_row,
    maybe_write_run_meta,
    run_agent_task_via_driver,
    run_canary,
    should_skip_resume_row,
    stdio_server_env,
)


async def run_live(
    tasks: list[dict[str, Any]],
    *,
    model_alias: str,
    reps: int,
    surface: str,
    out_path: Path,
    driver_name: str = "api",
    provider: str = "anthropic",
    server_cmd: list[str] | None = None,
    server_env: dict[str, str] | None = None,
    resume: bool = False,
    record_result_payloads: bool = False,
) -> int:
    """Delegate the legacy API while preserving its model-alias behavior."""
    model_id = resolve_model_for_driver(driver_name, model_alias, provider=provider)
    return await runner.run_live(
        tasks,
        model_alias=model_alias,
        reps=reps,
        surface=surface,
        out_path=out_path,
        driver_name=driver_name,
        provider=provider,
        server_cmd=server_cmd,
        server_env=server_env,
        resume=resume,
        record_result_payloads=record_result_payloads,
        resolved_model_id=model_id,
    )


__all__ = [
    "API_MODEL_ALIASES",
    "CLI_MODEL_ALIASES",
    "DEFAULT_OUT_DIR",
    "KNOWN_SURFACES",
    "MAX_ITERATIONS",
    "MAX_TOKENS",
    "MODEL_ALIASES",
    "classify_call",
    "cmd_dry_run",
    "cmd_list",
    "is_infra_cli_stop_reason",
    "is_meta_or_non_task_row",
    "load_resume_skip_keys",
    "main",
    "make_run_meta_row",
    "maybe_write_run_meta",
    "parse_args",
    "resolve_model_for_driver",
    "run_agent_task_via_driver",
    "run_canary",
    "run_live",
    "should_skip_resume_row",
    "stdio_server_env",
]


if __name__ == "__main__":
    raise SystemExit(main())
