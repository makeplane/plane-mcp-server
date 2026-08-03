"""Correctness checks for the spike.

1. compress() must be lossless: re-inlining every $ref must reproduce the
   nullable-collapsed original exactly.
2. The nullable collapse must be semantically equivalent (same accepted types).
3. Variant D's validation paths must return readable errors, not raise.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("PLANE_API_KEY", "x")
os.environ.setdefault("PLANE_WORKSPACE_SLUG", "x")

from fastmcp import FastMCP  # noqa: E402

from plane_mcp.server import get_stdio_mcp  # noqa: E402
from plane_mcp.tools_v2.intake import register_str as register_variant_d  # noqa: E402
from spike.bench.compress import _collapse_nullable, compress  # noqa: E402


def inline(node, defs):
    """Re-expand $refs so the result can be compared with the original."""
    if isinstance(node, list):
        return [inline(n, defs) for n in node]
    if isinstance(node, dict):
        ref = node.get("$ref")
        if ref and ref.startswith("#/$defs/"):
            return inline(defs[ref.split("/")[-1]], defs)
        return {k: inline(v, defs) for k, v in node.items() if k != "$defs"}
    return node


def nullable_equivalent(a, b) -> bool:
    """True if b is a's nullable-collapsed form (or identical)."""
    return _collapse_nullable(a) == b


async def main() -> None:
    tools = await get_stdio_mcp().list_tools()
    dds = [t.to_mcp_tool().model_dump(exclude_none=True) for t in tools]

    checked = lossless = 0
    failures = []
    for dd in dds:
        for field in ("inputSchema", "outputSchema"):
            original = dd.get(field)
            if not original:
                continue
            checked += 1
            comp = compress(json.loads(json.dumps(original)))
            defs = comp.get("$defs", {})
            restored = inline({k: v for k, v in comp.items() if k != "$defs"}, defs)
            expected = _collapse_nullable(json.loads(json.dumps(original)))
            if restored == expected:
                lossless += 1
            else:
                failures.append(f"{dd['name']}.{field}")

    print(f"compress() losslessness: {lossless}/{checked} schemas round-trip exactly")
    if failures:
        print(f"  FAILURES ({len(failures)}): {', '.join(failures[:10])}")
    else:
        print("  no failures — every $ref re-inlines to the collapsed original")

    # nullable collapse equivalence spot-check
    probe = {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "default": None,
        "description": "x",
    }
    got = _collapse_nullable(probe)
    ok = got == {"type": ["string", "null"], "default": None, "description": "x"}
    print(f"\nnullable collapse shape: {'OK' if ok else 'WRONG'}  ->  {json.dumps(got)}")

    # Variant D error paths (no network needed)
    md = FastMCP("d")
    register_variant_d(md)
    tool = (await md.list_tools())[0]
    cases = [
        ({"action": "bogus"}, "unknown action"),
        ({"action": "list"}, "requires: project_id"),
        ({"action": "retrieve", "project_id": "p"}, "requires: work_item_id"),
        ({"action": "update", "project_id": "p", "work_item_id": "w", "status": 0}, "snoozed_till"),
        ({"action": "update", "project_id": "p", "work_item_id": "w", "status": 2}, "duplicate_to"),
    ]
    print("\nvariant D validation paths:")
    for args, expect in cases:
        res = await tool.run(args)
        text = str(res.content[0].text if res.content else res)
        status = "OK " if expect in text else "FAIL"
        print(f"  [{status}] {str(args):72s} -> {text[:60]}")


asyncio.run(main())
