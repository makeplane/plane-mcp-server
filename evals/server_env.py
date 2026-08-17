"""Environment construction for a stdio MCP server child process.

A foundational leaf, shared by the live runner and the standalone tool-token listing. It
lived in ``runner.live``, so importing a pure token-counting helper pulled in the whole
live-run composition root — every driver, seeder, task and report module with it.
"""

from __future__ import annotations

import os

DEFAULT_PLANE_BASE_URL = "https://api.plane.so"


def stdio_server_env(*, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build MCP stdio env from scratch — never inherit os.environ (F6)."""
    environment: dict[str, str] = {}
    if path := os.environ.get("PATH"):
        environment["PATH"] = path
    if home := os.environ.get("HOME"):
        environment["HOME"] = home
    environment["PLANE_API_KEY"] = os.environ["EVAL_PLANE_API_KEY"]
    environment["PLANE_WORKSPACE_SLUG"] = os.environ["EVAL_PLANE_WORKSPACE_SLUG"]
    environment["PLANE_BASE_URL"] = os.environ.get("EVAL_PLANE_BASE_URL", DEFAULT_PLANE_BASE_URL)
    if extra:
        environment.update(extra)
    return environment


__all__ = ["DEFAULT_PLANE_BASE_URL", "stdio_server_env"]
