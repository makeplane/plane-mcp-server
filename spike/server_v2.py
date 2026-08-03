"""Spike v2 stdio entrypoint: current surface with intake consolidated.

Registers all 177 existing tools, removes the 5 verb-per-resource intake tools,
and adds the single consolidated `intake` tool in their place -> 173 tools.

Everything else is untouched, so an interactive session can compare the
consolidated tool against the rest of the surface side by side.

Run:  .venv/bin/python -m spike.server_v2
Env:  PLANE_API_KEY, PLANE_WORKSPACE_SLUG, PLANE_BASE_URL
      (or drop them in .env.test.local, which this module loads if present)
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.test.local")
if os.path.exists(_ENV):
    for _line in open(_ENV):
        if "=" in _line and not _line.strip().startswith("#"):
            _k, _v = _line.strip().split("=", 1)
            os.environ.setdefault(_k, _v)

from fastmcp import FastMCP  # noqa: E402

from plane_mcp.instructions import SERVER_INSTRUCTIONS  # noqa: E402
from plane_mcp.tools import register_tools  # noqa: E402
from spike.intake_v2 import register_variant_d  # noqa: E402

REPLACED = [
    "list_intake_work_items",
    "create_intake_work_item",
    "retrieve_intake_work_item",
    "update_intake_work_item",
    "delete_intake_work_item",
]


def build() -> FastMCP:
    mcp = FastMCP("Plane MCP Server (spike v2)", instructions=SERVER_INSTRUCTIONS)
    register_tools(mcp)
    for name in REPLACED:
        try:
            mcp.remove_tool(name)
        except Exception as e:  # noqa: BLE001
            print(f"warn: could not remove {name}: {e}", file=sys.stderr)
    register_variant_d(mcp)
    return mcp


if __name__ == "__main__":
    build().run()
