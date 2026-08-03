"""Full-surface measurement: baseline vs the consolidated v2 surface.

Builds every variant from the real registration code and reports the actual
tools/list payload size for each:

  A   baseline            -- 177 tools, typed Pydantic returns
  B   baseline + compress -- 177 tools, lossless schema compression
  C   v2 typed            -- 29 tools, typed Pydantic returns
  BD  v2 typed + compress -- 29 tools, compression (NON-BREAKING target)
  D   v2 str              -- 29 tools, `-> str` (no outputSchema)

Run: .venv/bin/python spike/measure_all.py
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import pkgutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PLANE_API_KEY", "x")
os.environ.setdefault("PLANE_WORKSPACE_SLUG", "x")

from fastmcp import FastMCP  # noqa: E402

import plane_mcp.tools_v2 as v2pkg  # noqa: E402
from benchmarks.compress import compress  # noqa: E402
from plane_mcp.server import get_stdio_mcp  # noqa: E402

J = dict(separators=(",", ":"))


def v2_modules() -> list[str]:
    return sorted(
        m.name for m in pkgutil.iter_modules(v2pkg.__path__) if not m.name.startswith("_")
    )


def build_v2(variant: str) -> tuple[FastMCP, list[str]]:
    """variant: 'typed' | 'str'. Returns (server, list of modules that failed)."""
    mcp = FastMCP(f"Plane MCP Server (v2-{variant})")
    failed: list[str] = []
    for name in v2_modules():
        try:
            mod = importlib.import_module(f"plane_mcp.tools_v2.{name}")
            getattr(mod, f"register_{variant}")(mcp)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{name}: {type(e).__name__}: {e}")
    return mcp, failed


def measure(dds: list[dict]) -> tuple[int, int, int, int]:
    """(total, outputSchema, inputSchema, description) in chars."""
    tot = out = inp = desc = 0
    for d in dds:
        tot += len(json.dumps(d, **J))
        if d.get("outputSchema"):
            out += len(json.dumps(d["outputSchema"], **J))
        if d.get("inputSchema"):
            inp += len(json.dumps(d["inputSchema"], **J))
        desc += len(d.get("description", "") or "")
    return tot, out, inp, desc


def compressed(dds: list[dict]) -> list[dict]:
    res = []
    for d in dds:
        c = dict(d)
        for f in ("inputSchema", "outputSchema"):
            if c.get(f):
                c[f] = compress(c[f])
        res.append(c)
    return res


async def dds_of(mcp: FastMCP) -> list[dict]:
    return [t.to_mcp_tool().model_dump(exclude_none=True) for t in await mcp.list_tools()]


def row(label: str, dds: list[dict], base: int | None) -> int:
    tot, out, inp, desc = measure(dds)
    delta = "" if base is None else f"  {(1 - tot / base) * 100:+6.1f}%"
    print(
        f"{label:34s} {len(dds):4d} tools  {tot:8,d} ch  ~{tot // 4:7,d} tok"
        f"   [out {out // 4:6,d} | in {inp // 4:5,d} | desc {desc // 4:5,d}]{delta}"
    )
    return tot


async def main() -> None:
    base_dds = await dds_of(get_stdio_mcp())

    typed_mcp, f1 = build_v2("typed")
    str_mcp, f2 = build_v2("str")
    typed_dds = await dds_of(typed_mcp)
    str_dds = await dds_of(str_mcp)

    if f1 or f2:
        print("MODULE FAILURES:")
        for f in sorted(set(f1 + f2)):
            print("  -", f)
        print()

    print("=" * 118)
    print("FULL SURFACE — baseline vs consolidated v2")
    print("=" * 118)
    base = row("A   baseline", base_dds, None)
    row("B   baseline + compression", compressed(base_dds), base)
    row("C   v2 typed", typed_dds, base)
    row("BD  v2 typed + compression", compressed(typed_dds), base)
    row("D   v2 str", str_dds, base)
    print("=" * 118)

    n_base, n_v2 = len(base_dds), len(typed_dds)
    print(f"\ntools: {n_base} -> {n_v2}  ({(1 - n_v2 / n_base) * 100:.0f}% fewer)")
    print(f"modules built: {len(v2_modules())}")

    # per-tool detail for the str variant
    print("\n--- v2 str, per tool ---")
    for d in sorted(str_dds, key=lambda x: -len(json.dumps(x, **J))):
        print(f"  {len(json.dumps(d, **J)) // 4:6,d} tok  {d['name']}")


if __name__ == "__main__":
    asyncio.run(main())
