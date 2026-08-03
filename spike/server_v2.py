"""Runnable stdio entrypoint for the FULL consolidated v2 surface (29 tools).

Registers every module in spike/v2/ -- the complete 177 -> 29 consolidation.
Supersedes the pilot server that kept the 177-tool surface and swapped
only `intake`.

Variant is chosen with PLANE_MCP_V2_VARIANT:

  typed  (default)  Pydantic return types -- variant BD. Non-breaking:
                    structuredContent stays typed. ~42k wire / ~14.3k model-facing.
  str               `-> str` JSON returns -- variant D. Smallest wire payload
                    (~15.8k) but gives up typed structured output, and
                    work_item_attachment.read cannot return images.

Run:
  .venv/bin/python -m spike.server_v2

Credentials: PLANE_API_KEY, PLANE_WORKSPACE_SLUG, PLANE_BASE_URL from the
environment, or from .env.test.local if present (env always wins).
"""

from __future__ import annotations

import importlib
import os
import pkgutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.test.local")
if os.path.exists(_ENV):
    for _line in open(_ENV):
        if "=" in _line and not _line.strip().startswith("#"):
            _k, _v = _line.strip().split("=", 1)
            os.environ.setdefault(_k, _v)

from fastmcp import FastMCP  # noqa: E402

import spike.v2 as v2pkg  # noqa: E402
from plane_mcp.instructions import SERVER_INSTRUCTIONS  # noqa: E402

VARIANT = os.environ.get("PLANE_MCP_V2_VARIANT", "typed").strip().lower()
if VARIANT not in ("typed", "str"):
    sys.exit(f"PLANE_MCP_V2_VARIANT must be 'typed' or 'str', got {VARIANT!r}")


def build() -> FastMCP:
    mcp = FastMCP(f"Plane MCP Server (v2-{VARIANT})", instructions=SERVER_INSTRUCTIONS)
    registered, failed = 0, []
    for mod_info in sorted(pkgutil.iter_modules(v2pkg.__path__), key=lambda m: m.name):
        if mod_info.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"spike.v2.{mod_info.name}")
            getattr(mod, f"register_{VARIANT}")(mcp)
            registered += 1
        except Exception as e:  # noqa: BLE001
            failed.append(f"{mod_info.name}: {type(e).__name__}: {e}")
    # stderr only -- stdout is the MCP protocol channel and must stay clean.
    print(f"[spike v2-{VARIANT}] registered {registered} modules", file=sys.stderr)
    for f in failed:
        print(f"[spike v2-{VARIANT}] FAILED {f}", file=sys.stderr)
    return mcp


if __name__ == "__main__":
    build().run()
