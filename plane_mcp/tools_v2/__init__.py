"""Consolidated (v2) tool surface — 29 action-dispatch tools.

One tool per resource, dispatching on a required `action` parameter, replacing
the 177 verb-per-resource tools in `plane_mcp.tools`. Both surfaces coexist;
callers select with the `--v2` flag (see `plane_mcp/__main__.py`).

Each module exports `register_typed(mcp)` and `register_str(mcp)` over a shared
`_dispatch()`:

  typed  Pydantic return types -- keeps typed structuredContent. Default.
  str    `-> str` JSON returns -- smaller wire payload, no typed output.

See docs/tool-consolidation-plan.md for measurements and the rollout plan.
"""

from __future__ import annotations

import importlib
import pkgutil

from fastmcp import FastMCP

VARIANTS = ("typed", "str")


def module_names() -> list[str]:
    """Every consolidated tool module, in a stable order."""
    return sorted(m.name for m in pkgutil.iter_modules(__path__) if not m.name.startswith("_"))


def register_tools_v2(mcp: FastMCP, variant: str = "typed") -> int:
    """Register the consolidated surface. Returns the number of modules registered.

    Raises ValueError on an unknown variant, and propagates any module's
    registration error rather than silently serving a partial surface.
    """
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}, got {variant!r}")
    count = 0
    for name in module_names():
        mod = importlib.import_module(f"{__name__}.{name}")
        getattr(mod, f"register_{variant}")(mcp)
        count += 1
    return count
