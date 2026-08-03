"""Self-check for v2 modules. Run:  .venv/bin/python spike/check_v2.py [name ...]

For each spike/v2/<name>.py it imports the module, registers both variants on a
throwaway FastMCP instance, and reports the resulting schema size. Any import
error, missing register_* function, or duplicate tool name shows up here.
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

import spike.v2 as v2pkg  # noqa: E402

J = dict(separators=(",", ":"))


def module_names(argv: list[str]) -> list[str]:
    if argv:
        return argv
    return sorted(
        m.name
        for m in pkgutil.iter_modules(v2pkg.__path__)
        if not m.name.startswith("_")
    )


async def main() -> None:
    names = module_names(sys.argv[1:])
    ok = bad = 0
    tot_typed = tot_str = 0
    print(f"{'module':28s} {'tool':26s} {'typed tok':>10s} {'str tok':>9s}  status")
    print("-" * 88)
    for name in names:
        try:
            mod = importlib.import_module(f"spike.v2.{name}")
        except Exception as e:  # noqa: BLE001
            print(f"{name:28s} {'-':26s} {'-':>10s} {'-':>9s}  IMPORT FAIL: {type(e).__name__}: {e}")
            bad += 1
            continue
        row = []
        for label, fn_name in (("typed", "register_typed"), ("str", "register_str")):
            fn = getattr(mod, fn_name, None)
            if fn is None:
                row.append(None)
                continue
            try:
                m = FastMCP(f"{name}-{label}")
                fn(m)
                tools = await m.list_tools()
                dds = [t.to_mcp_tool().model_dump(exclude_none=True) for t in tools]
                row.append((sum(len(json.dumps(d, **J)) for d in dds), [d["name"] for d in dds]))
            except Exception as e:  # noqa: BLE001
                print(f"{name:28s} {'-':26s} {'-':>10s} {'-':>9s}  {fn_name} FAIL: {type(e).__name__}: {e}")
                row.append("ERR")
        if any(r is None or r == "ERR" for r in row):
            bad += 1
            if all(r is None for r in row):
                print(f"{name:28s} {'-':26s} {'-':>10s} {'-':>9s}  MISSING register_typed/register_str")
            continue
        (t_ch, t_names), (s_ch, s_names) = row
        tot_typed += t_ch
        tot_str += s_ch
        flag = "OK" if t_names == s_names else f"NAME MISMATCH {t_names} vs {s_names}"
        print(f"{name:28s} {','.join(s_names):26s} {t_ch // 4:10,d} {s_ch // 4:9,d}  {flag}")
        ok += 1 if flag == "OK" else 0
        bad += 0 if flag == "OK" else 1
    print("-" * 88)
    print(f"{ok} ok, {bad} failing   |   totals: typed ~{tot_typed // 4:,} tok   str ~{tot_str // 4:,} tok")
    if bad:
        sys.exit(1)


asyncio.run(main())
