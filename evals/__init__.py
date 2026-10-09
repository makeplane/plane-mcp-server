"""Plane MCP tool-surface eval harness."""

from pathlib import Path

# Repository root: the harness launches this repo's MCP server and resolves
# task working directories against it.
REPO_ROOT = Path(__file__).resolve().parent.parent

__all__ = ["REPO_ROOT"]
