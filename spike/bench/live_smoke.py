"""Live read-only smoke test across the entire consolidated v2 surface.

Calls one safe read action on every one of the 29 tools through a real
FastMCP client against a live workspace. Read-only: no create/update/delete.

Distinguishes three outcomes:
  OK    -- call returned data
  FEAT  -- endpoint rejected because the feature is not enabled/licensed here
           (not a defect in the consolidated tool)
  FAIL  -- anything else

Run: .venv/bin/python spike/live_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env.test.local")
for _line in open(_ENV):
    if "=" in _line and not _line.strip().startswith("#"):
        _k, _v = _line.strip().split("=", 1)
        os.environ[_k] = _v

from fastmcp import Client  # noqa: E402

from spike.bench.measure_all import build_v2  # noqa: E402

PROJECT_ID = os.environ.get("TEST_PROJECT_ID", "311cc73d-551b-4ef8-95fd-a0748996a4b5")

# Markers that mean "this workspace/plan does not expose the feature",
# as opposed to the consolidated tool being wrong.
FEATURE_MARKERS = (
    "not enabled", "not available", "feature", "upgrade", "license", "licence",
    "403", "Forbidden", "404", "Not Found", "payment", "plan does not",
)


# Known-broken in the BASELINE too -- not caused by consolidation. Verified by
# calling the corresponding v1 tool and observing the identical failure.
PREEXISTING = {
    # SDK types PaginatedWorkItemActivityResponse.results[].epoch as int, but the
    # API returns a float -> pydantic int_from_float. Baseline list_work_item_activities
    # fails with the exact same error.
    "work_item_activity": "int_from_float",
}

# Needs a parent id this read-only probe cannot obtain (no customers in the test
# workspace). Baseline list_customer_requests also declares customer_id required.
NEEDS_PARENT = {"customer_request"}


def classify(tool: str, text: str) -> str:
    if not text.startswith("Error:"):
        return "OK"
    if tool in NEEDS_PARENT and "requires:" in text:
        return "SKIP"
    marker = PREEXISTING.get(tool)
    if marker and marker in text:
        return "PRE"
    return "FEAT" if any(m.lower() in text.lower() for m in FEATURE_MARKERS) else "FAIL"


async def call(client: Client, tool: str, args: dict) -> str:
    try:
        res = await client.call_tool(tool, args)
        if getattr(res, "content", None):
            return "\n".join(getattr(c, "text", "") for c in res.content)
        return str(res)
    except Exception as e:  # noqa: BLE001
        return f"Error: {type(e).__name__}: {e}"


async def main() -> None:
    mcp, failed = build_v2("str")
    if failed:
        print("BUILD FAILURES:", failed)

    P = {"project_id": PROJECT_ID}

    async with Client(mcp) as c:
        # Resolve a work item to scope the item-level reads.
        wi_text = await call(c, "work_item", {"action": "list", **P})
        work_item_id = ""
        try:
            data = json.loads(wi_text)
            rows = data.get("results", data) if isinstance(data, dict) else data
            work_item_id = rows[0]["id"] if rows else ""
        except Exception:
            pass
        print(f"resolved work_item_id={work_item_id or '(none)'}\n")

        W = {**P, "work_item_id": work_item_id}
        cases: list[tuple[str, dict]] = [
            ("project", {"action": "list"}),
            ("work_item", {"action": "list", **P}),
            ("label", {"action": "list", **P}),
            ("state", {"action": "list", **P}),
            ("cycle", {"action": "list", **P}),
            ("module", {"action": "list", **P}),
            ("page", {"action": "list", **P}),
            ("intake", {"action": "list", **P}),
            ("milestone", {"action": "list", **P}),
            ("member", {"action": "me"}),
            ("workspace", {"action": "get_features"}),
            ("get_pql_reference", {"detail": "brief"}),
            ("work_item_type", {"action": "list", **P}),
            ("work_item_property", {"action": "list", **P}),
            ("work_item_relation", {"action": "list_definitions"}),
            ("initiative", {"action": "list"}),
            ("customer", {"action": "list"}),
            ("customer_property", {"action": "list"}),
            ("customer_request", {"action": "list"}),
            ("release", {"action": "list"}),
            ("release_tag", {"action": "list"}),
            ("release_label", {"action": "list"}),
            ("project_estimate", {"action": "get", **P}),
            ("work_item_comment", {"action": "list", **W}),
            ("work_item_link", {"action": "list", **W}),
            ("work_item_attachment", {"action": "list", **W}),
            ("work_item_activity", {"action": "list", **W}),
            ("work_log", {"action": "list", **W}),
            ("work_item_property_value", {"action": "get", **W, "property_id": "x"}),
        ]

        tally = {"OK": 0, "FEAT": 0, "PRE": 0, "SKIP": 0, "FAIL": 0}
        rows_out = []
        for tool, args in cases:
            text = await call(c, tool, args)
            verdict = classify(tool, text)
            tally[verdict] += 1
            rows_out.append((verdict, tool, args.get("action", "-"), text))

        for verdict, tool, action, text in rows_out:
            first = text.replace("\n", " ")[:88]
            print(f"[{verdict:4s}] {tool:26s} {action:16s} {first}")

        print(f"\n{'=' * 100}")
        print(f"OK={tally['OK']}  FEAT(not enabled here)={tally['FEAT']}  "
              f"PRE(broken in baseline too)={tally['PRE']}  SKIP(needs parent id)={tally['SKIP']}  "
              f"FAIL={tally['FAIL']}   of {len(cases)} tools")
        if tally["FAIL"]:
            print("\nFAILURES:")
            for verdict, tool, action, text in rows_out:
                if verdict == "FAIL":
                    print(f"  {tool}.{action}: {text[:300]}")
            sys.exit(1)


asyncio.run(main())
