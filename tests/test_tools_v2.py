"""Unit tests for the consolidated v2 tool surface (offline, no network).

Guards the properties that a module edit is most likely to break:
registration, tool-name parity across variants, docstring/action agreement, and
the schema-size conventions the consolidation depends on.
"""

from __future__ import annotations

import asyncio
import importlib
import os

import pytest
from fastmcp import FastMCP

os.environ.setdefault("PLANE_API_KEY", "test")
os.environ.setdefault("PLANE_WORKSPACE_SLUG", "test")

from plane_mcp.server import get_stdio_mcp  # noqa: E402
from plane_mcp.tools_v2 import VARIANTS, module_names, register_tools_v2  # noqa: E402

EXPECTED_TOOL_COUNT = 29

# `get_pql_reference` is carried over 1:1 from v1 and has no `action` parameter.
NO_ACTION_PARAM = {"get_pql_reference"}


def tool_map(surface: str = "v2"):
    async def go():
        return {t.name: t for t in await get_stdio_mcp(surface=surface).list_tools()}

    return asyncio.run(go())


def tool_names(mcp) -> set[str]:
    return {t.name for t in asyncio.run(mcp.list_tools())}


def test_every_module_is_discovered():
    assert len(module_names()) == EXPECTED_TOOL_COUNT


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_module_registers(variant):
    """A module that fails to register must raise, not silently serve a partial surface."""
    mcp = FastMCP(f"t-{variant}")
    assert register_tools_v2(mcp, variant=variant) == EXPECTED_TOOL_COUNT


def test_unknown_variant_is_rejected():
    with pytest.raises(ValueError, match="variant must be one of"):
        register_tools_v2(FastMCP("t"), variant="nope")


def test_surface_selection():
    """v1 stays the default; v2 is opt-in and materially smaller."""
    v1 = tool_map("v1")
    v2 = tool_map("v2")
    assert len(v2) == EXPECTED_TOOL_COUNT
    assert len(v1) > len(v2)
    default = tool_names(get_stdio_mcp())
    assert default == set(v1), "the default surface must remain v1"


def test_unknown_surface_is_rejected():
    with pytest.raises(ValueError, match="surface must be one of"):
        get_stdio_mcp(surface="v3")


def test_variants_expose_identical_tool_names():
    """typed and str differ only in return type -- never in the tool surface."""
    names = {}
    for variant in VARIANTS:
        mcp = FastMCP(f"t-{variant}")
        register_tools_v2(mcp, variant=variant)
        names[variant] = tool_names(mcp)
    assert names["typed"] == names["str"]


def test_every_tool_requires_an_action():
    """`action` is the dispatch key and the only schema-level required param."""
    for name, tool in tool_map().items():
        if name in NO_ACTION_PARAM:
            continue
        required = (tool.parameters or {}).get("required", [])
        assert required == ["action"], f"{name}: expected required==['action'], got {required}"


def test_descriptions_document_every_action():
    """The description is the only place a per-action contract can live, so it
    must actually mention each action the module dispatches on."""
    for name in module_names():
        mod = importlib.import_module(f"plane_mcp.tools_v2.{name}")
        actions = getattr(mod, "ACTIONS", None)
        if not actions:
            continue  # 1:1 carry-overs have no action list
        doc = getattr(mod, "DOC", "")
        missing = [a for a in actions if a not in doc]
        assert not missing, f"{name}: actions absent from DOC: {missing}"


def nullable_ratio(surface: str) -> tuple[int, int]:
    """(nullable params, total params) across a surface's input schemas."""
    nullable = total = 0
    for tool in tool_map(surface).values():
        for spec in ((tool.parameters or {}).get("properties") or {}).values():
            total += 1
            variants = spec.get("anyOf")
            if isinstance(variants, list) and any(
                isinstance(v, dict) and v.get("type") == "null" for v in variants
            ):
                nullable += 1
    return nullable, total


def test_nullable_params_stay_well_below_v1():
    """Plain typed defaults (`= ""`) instead of `| None = None` are what keep the
    input schema small -- Pydantic renders every `X | None` as a verbose
    anyOf-with-null block.

    Some `| None` params are legitimate (dicts, tri-state booleans, ints where 0
    is meaningful), so this asserts a *relative* invariant against the v1
    baseline rather than a magic threshold: it self-calibrates as either surface
    grows, and does not pretend the correct answer is zero.

    At the time of writing: v1 63.0%, v2 23.5%.
    """
    v2_nullable, v2_total = nullable_ratio("v2")
    v1_nullable, v1_total = nullable_ratio("v1")
    assert v2_total > 0 and v1_total > 0

    v2_ratio = v2_nullable / v2_total
    v1_ratio = v1_nullable / v1_total
    assert v2_ratio < v1_ratio / 2, (
        f"v2 is {v2_nullable}/{v2_total} = {v2_ratio:.1%} nullable against a v1 baseline of "
        f"{v1_ratio:.1%} -- the `= \"\"` convention has regressed"
    )


def test_dispatch_rejects_bad_input_readably():
    """Required-field enforcement moved from JSON Schema to runtime, so the
    error strings are the contract. They must name the problem."""
    from plane_mcp.tools_v2.label import _dispatch

    assert "unknown action" in _dispatch("bogus", "p", "", "", "", "", "", None, "", "", None)
    assert "requires: project_id" in _dispatch("list", "", "", "", "", "", "", None, "", "", None)
