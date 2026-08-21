"""Command-line wiring and model resolution for the eval harness."""

from __future__ import annotations

import argparse
import asyncio
import shlex
import sys
import uuid
from pathlib import Path
from typing import Any

from evals.drivers import KNOWN_DRIVERS
from evals.drivers.api import (
    KNOWN_API_PROVIDERS,
    MODEL_TIERS,
    UnmappedModelTierError,
    backend_model_aliases,
    resolve_backend_model,
)
from evals.runner import run_canary, run_live
from evals.seed import seed_plan
from evals.tasks.catalog import TASKS, get_tasks
from evals.tasks.prompts import format_task_prompt

API_MODEL_TIERS: dict[str, dict[str, str]] = {
    provider: aliases for provider in KNOWN_API_PROVIDERS if (aliases := backend_model_aliases(provider))
}
# CLI drivers have an implicit provider selected by their own authentication
# and configuration. Keep the provider dimension explicit so a tier never
# crosses vendor boundaries by accident.
CLI_DRIVER_PROVIDERS: dict[str, str | None] = {
    "claude-cli": "anthropic",
    "codex-cli": "openai",
    "antigravity-cli": "google",
    # OpenCode is multi-provider and location-configured. Its installed catalog
    # is the only reliable source, so the harness does not guess a default.
    "opencode-cli": None,
}
CLI_MODEL_TIERS: dict[str, dict[str, dict[str, str]]] = {
    "claude-cli": {
        "anthropic": {"standard": "sonnet", "fast": "haiku"},
    },
    "codex-cli": {
        "openai": backend_model_aliases("openai"),
    },
    "antigravity-cli": {
        "google": {
            "standard": "gemini-3.6-flash-high",
            "fast": "gemini-3.6-flash-low",
        },
    },
    "opencode-cli": {},
}

DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "output"


def resolve_model_for_driver(driver_name: str, model: str, *, provider: str | None = None) -> str:
    """Resolve a harness tier for a driver/provider, or pass a model ID through.

    Only ``standard`` and ``fast`` are tier names. Any other string, including
    vendor aliases and qualified provider/model IDs, is passed through exactly.
    """
    key = (driver_name or "api").strip().lower()
    if key == "api":
        return resolve_backend_model(provider or "anthropic", model)
    if model not in MODEL_TIERS:
        return model
    if key not in CLI_DRIVER_PROVIDERS:
        raise ValueError(f"unknown driver {driver_name!r}; expected one of {sorted(KNOWN_DRIVERS)}")
    provider_id = provider.strip().lower() if provider else CLI_DRIVER_PROVIDERS[key]
    if provider_id is None:
        raise UnmappedModelTierError(
            f"model tier {model!r} is not mapped for driver {key!r}; OpenCode models depend on "
            "the providers configured for this project. Pass an explicit provider/model ID with "
            "--model, using one listed by 'opencode models'"
        )
    table = CLI_MODEL_TIERS.get(key, {}).get(provider_id, {})
    try:
        return table[model]
    except KeyError as exc:
        raise UnmappedModelTierError(
            f"model tier {model!r} is not mapped for driver {key!r} and provider {provider_id!r}; "
            "pass an explicit model ID with --model"
        ) from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Plane MCP tool-surface eval harness")
    p.add_argument("--list", action="store_true", help="Print task table (no network)")
    p.add_argument("--dry-run", action="store_true", help="Print resolved prompts + seed plan (no network)")
    p.add_argument("--tasks", type=str, default=None, help="Comma-separated task ids (default: all)")
    p.add_argument(
        "--model",
        type=str,
        default="standard",
        help=(
            "Harness tier (standard/fast) or a free-form model ID. "
            "Tiers resolve per driver and provider; all other strings pass through unchanged."
        ),
    )
    p.add_argument("--reps", type=int, default=1, help="Repetitions per task")
    p.add_argument(
        "--label",
        type=str,
        default="local",
        help="Column label for this run in reports (default: local).",
    )
    p.add_argument(
        "--server-cmd",
        type=str,
        default=None,
        help=(
            "External MCP stdio server launch command (shlex-split). Enables external "
            "mode while retaining the same observed tool-use metrics."
        ),
    )
    p.add_argument(
        "--server-env",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="Extra env var for the (external) MCP server child; repeatable.",
    )
    p.add_argument(
        "--driver",
        type=str,
        default="api",
        choices=sorted(KNOWN_DRIVERS),
        help=(
            "Agent backend: api | claude-cli | codex-cli | antigravity-cli | opencode-cli. Not required for --canary."
        ),
    )
    p.add_argument(
        "--provider",
        type=str,
        default="anthropic",
        choices=sorted(KNOWN_API_PROVIDERS),
        help="Model API provider for --driver api (default: anthropic).",
    )
    p.add_argument(
        "--record-result-payloads",
        action="store_true",
        help=(
            "CLI drivers only: record serialized tool-result text for tokenizer counting, and "
            "the request args beside it so a recorded result can be attributed to its target "
            "(off by default; sidecars and rows may contain live workspace data)"
        ),
    )
    p.add_argument("--out", type=str, default=None, help="JSONL output path")
    p.add_argument(
        "--resume",
        type=str,
        default=None,
        metavar="OUT.jsonl",
        help=(
            "Resume into an existing JSONL (also the --out target). Skip "
            "(task_id, rep, label) keys that completed or were plan-gated; re-run rows "
            "with errors, cleanup failures, fixture collisions, or unknown skips."
        ),
    )
    p.add_argument(
        "--canary",
        action="store_true",
        help=(
            "Verifier canary: seed each task, call verify with an empty agent result "
            "(no driver/model), teardown. Exit 1 if any verifier returns ok=True on do-nothing."
        ),
    )
    p.add_argument(
        "--canary-strict",
        type=str,
        default=None,
        metavar="TASK_IDS",
        help=(
            "Strict canary coverage: comma-separated task ids that must be verified. "
            "Plan-gated skips outside this explicit eligible set remain allowed."
        ),
    )
    return p.parse_args(argv)


def _task_ids(raw: str | None) -> list[str] | None:
    if raw is None:
        return None
    return [t.strip() for t in raw.split(",") if t.strip()]


def cmd_list() -> int:
    print(f"{'id':<6} {'tags':<18} prompt")
    print("-" * 100)
    for task in TASKS:
        tags = ",".join(sorted(task["tags"]))
        prompt = task["prompt"].replace("\n", " ")
        if len(prompt) > 70:
            prompt = prompt[:67] + "..."
        print(f"{task['id']:<6} {tags:<18} {prompt}")
    return 0


def cmd_dry_run(tasks: list[dict[str, Any]]) -> int:
    needs: set[str] = set()
    for task in tasks:
        needs |= set(task.get("needs") or set())
    print("Seed plan:")
    for line in seed_plan(needs):
        print(f"  {line}")
    print()
    sample_ctx = {"project_name": "EVAL deadbeef"}
    for task in tasks:
        resolved = format_task_prompt(task, sample_ctx, strict=False)
        print(f"=== {task['id']} ===")
        print(f"needs: {sorted(task.get('needs') or [])}")
        print(f"author: {task.get('author') or 'claude'}")
        print(f"prompt:\n  {resolved}")
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.list:
        return cmd_list()

    ids = _task_ids(args.tasks)
    try:
        tasks = get_tasks(ids)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2

    if args.dry_run:
        return cmd_dry_run(tasks)

    if args.reps < 1:
        print("error: --reps must be at least 1", file=sys.stderr)
        return 2

    label = (args.label or "local").strip() or "local"
    server_cmd: list[str] | None = None
    if args.server_cmd:
        server_cmd = shlex.split(args.server_cmd)
        if not server_cmd:
            print("error: --server-cmd is empty", file=sys.stderr)
            return 2

    server_env: dict[str, str] = {}
    for pair in args.server_env:
        key, sep, val = pair.partition("=")
        if not sep or not key:
            print(f"error: --server-env expects KEY=VAL, got {pair!r}", file=sys.stderr)
            return 2
        server_env[key] = val

    # Canary: live env only — no driver/model required.
    if args.canary:
        required_ids = set(_task_ids(args.canary_strict) or [])
        if args.canary_strict is not None and not required_ids:
            print("error: --canary-strict requires at least one task id", file=sys.stderr)
            return 2
        known_ids = {str(task["id"]) for task in TASKS}
        unknown_required = sorted(required_ids - known_ids)
        if unknown_required:
            print(f"error: unknown --canary-strict task id(s): {', '.join(unknown_required)}", file=sys.stderr)
            return 2
        return asyncio.run(run_canary(tasks, label=label, required_task_ids=required_ids))
    if args.canary_strict is not None:
        print("error: --canary-strict requires --canary", file=sys.stderr)
        return 2

    driver_name = (getattr(args, "driver", None) or "api").strip().lower()
    if driver_name not in KNOWN_DRIVERS:
        print(
            f"error: unknown --driver {driver_name!r}; expected one of {sorted(KNOWN_DRIVERS)}",
            file=sys.stderr,
        )
        return 2

    if args.resume:
        out = Path(args.resume)
    elif args.out:
        out = Path(args.out)
    else:
        out = DEFAULT_OUT_DIR / f"{uuid.uuid4().hex}.jsonl"

    try:
        model_id = resolve_model_for_driver(
            driver_name,
            args.model,
            provider=args.provider if driver_name == "api" else None,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return asyncio.run(
        run_live(
            tasks,
            model_alias=args.model,
            reps=args.reps,
            label=label,
            out_path=out,
            driver_name=driver_name,
            provider=args.provider,
            server_cmd=server_cmd,
            server_env=server_env or None,
            resume=bool(args.resume),
            record_result_payloads=bool(args.record_result_payloads),
            resolved_model_id=model_id,
        )
    )


__all__ = [
    "API_MODEL_TIERS",
    "CLI_DRIVER_PROVIDERS",
    "CLI_MODEL_TIERS",
    "DEFAULT_OUT_DIR",
    "MODEL_TIERS",
    "cmd_dry_run",
    "cmd_list",
    "main",
    "parse_args",
    "resolve_model_for_driver",
]


if __name__ == "__main__":
    raise SystemExit(main())
