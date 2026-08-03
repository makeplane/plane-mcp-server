"""Measure intake-module variants A/B/C/D against the real tools/list payload."""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("PLANE_API_KEY", "x")
os.environ.setdefault("PLANE_WORKSPACE_SLUG", "x")

from fastmcp import FastMCP  # noqa: E402

from plane_mcp.server import get_stdio_mcp  # noqa: E402
from spike.compress import compress  # noqa: E402
from spike.intake_v2 import register_variant_c, register_variant_d  # noqa: E402

J = dict(separators=(",", ":"))


def size(dd: dict) -> tuple[int, int]:
    """(total chars, outputSchema chars) for one tool descriptor."""
    o = dd.get("outputSchema")
    return len(json.dumps(dd, **J)), (len(json.dumps(o, **J)) if o else 0)


def compressed(dd: dict) -> dict:
    out = dict(dd)
    if out.get("inputSchema"):
        out["inputSchema"] = compress(out["inputSchema"])
    if out.get("outputSchema"):
        out["outputSchema"] = compress(out["outputSchema"])
    return out


async def descriptors(mcp: FastMCP, only: set[str] | None = None) -> list[dict]:
    tools = await mcp.list_tools()
    dds = [t.to_mcp_tool().model_dump(exclude_none=True) for t in tools]
    if only is not None:
        dds = [d for d in dds if d["name"] in only]
    return dds


def report(label: str, dds: list[dict], base: int | None = None) -> int:
    tot = sum(size(d)[0] for d in dds)
    out = sum(size(d)[1] for d in dds)
    delta = "" if base is None else f"   {(1 - tot / base) * 100:+6.1f}% vs A"
    print(
        f"{label:46s} tools={len(dds):2d}  {tot:7,d} ch  ~{tot // 4:6,d} tok"
        f"   (outputSchema {out // 4:6,d} tok){delta}"
    )
    return tot


async def main() -> None:
    intake_names = {
        "list_intake_work_items",
        "create_intake_work_item",
        "retrieve_intake_work_item",
        "update_intake_work_item",
        "delete_intake_work_item",
    }
    a = await descriptors(get_stdio_mcp(), only=intake_names)

    mc = FastMCP("c")
    register_variant_c(mc)
    c = await descriptors(mc)

    md = FastMCP("d")
    register_variant_d(md)
    d = await descriptors(md)

    print("\n" + "=" * 108)
    print("INTAKE MODULE — variant comparison")
    print("=" * 108)
    base = report("A  baseline (5 tools, typed, inlined schema)", a)
    report("B  A + schema compression (non-breaking)", [compressed(x) for x in a], base)
    report("C  consolidated 1 tool, typed union return", c, base)
    report("D  consolidated 1 tool, -> str (no outputSchema)", d, base)
    report("BD C + schema compression", [compressed(x) for x in c], base)
    print("=" * 108)

    # Decomposition: how much is consolidation, how much is the schema treatment?
    a_tot = base
    c_tot = sum(size(x)[0] for x in c)
    d_tot = sum(size(x)[0] for x in d)
    print(f"\nconsolidation alone (A->C)          : {(1 - c_tot / a_tot) * 100:5.1f}%")
    print(f"output-schema removal alone (C->D)  : {(1 - d_tot / c_tot) * 100:5.1f}%")
    print(f"combined (A->D)                     : {(1 - d_tot / a_tot) * 100:5.1f}%")


asyncio.run(main())
