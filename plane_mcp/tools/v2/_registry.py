"""Module discovery and the assembled legacy-name alias table."""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

import plane_mcp.tools.v2 as _pkg


def modules() -> list[ModuleType]:
    """Every resource module, in a stable order.

    Order is load-bearing: tool definitions sit at the front of a client's
    prompt cache, so reordering them invalidates the whole conversation.
    """
    names = sorted(m.name for m in pkgutil.iter_modules(_pkg.__path__) if not m.name.startswith("_"))
    return [importlib.import_module(f"{_pkg.__name__}.{name}") for name in names]


def alias_table() -> dict[str, tuple[str, str]]:
    """Legacy tool name -> (resource tool name, action). Raises on a collision."""
    table: dict[str, tuple[str, str]] = {}
    seen: dict[tuple[str, str], str] = {}
    for mod in modules():
        for legacy, action in getattr(mod, "LEGACY", {}).items():
            if legacy in table:
                raise ValueError(f"duplicate legacy tool name: {legacy}")
            target = (mod.NAME, action)
            if target in seen:
                raise ValueError(f"{legacy} and {seen[target]} both map to {target}")
            table[legacy] = target
            seen[target] = legacy
    return table


def unmapped_table() -> dict[str, str]:
    """Legacy tool name -> why it has no alias.

    An alias renames a tool; it cannot reshape one. Where a v1 tool encoded its
    action in a *parameter* (`manage_project_archive(archive=False)`), no single
    (tool, action) pair reproduces it, so it is declared here instead of being
    aliased to whichever half looks closest.
    """
    table: dict[str, str] = {}
    for mod in modules():
        for legacy, reason in getattr(mod, "LEGACY_UNMAPPED", {}).items():
            if legacy in table:
                raise ValueError(f"duplicate unmapped legacy tool name: {legacy}")
            if not reason:
                raise ValueError(f"{legacy} is declared unmapped with no reason")
            table[legacy] = reason
    return table
