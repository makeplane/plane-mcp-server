"""Live read-only smoke test for the v2 surface (skipped without credentials).

Calls one safe read action on every consolidated tool against a real workspace.
Read-only: no create, update, or delete.

Enable with:
    PLANE_TEST_API_KEY=...  PLANE_TEST_WORKSPACE_SLUG=...  PLANE_TEST_PROJECT_ID=...  pytest

Unlike tests/test_integration.py, this *skips* rather than fails when the
credentials are absent, so a default `pytest` run stays green offline.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from fastmcp import Client

API_KEY = os.getenv("PLANE_TEST_API_KEY")
WORKSPACE = os.getenv("PLANE_TEST_WORKSPACE_SLUG")
PROJECT_ID = os.getenv("PLANE_TEST_PROJECT_ID")

pytestmark = pytest.mark.skipif(
    not (API_KEY and WORKSPACE and PROJECT_ID),
    reason="needs PLANE_TEST_API_KEY, PLANE_TEST_WORKSPACE_SLUG and PLANE_TEST_PROJECT_ID",
)

# Errors that mean "this plan/workspace does not expose the feature" rather than
# "the consolidated tool is broken".
FEATURE_MARKERS = (
    "not enabled", "not available", "feature", "upgrade", "license",
    "403", "forbidden", "404", "not found", "payment", "plan does not",
)

# Broken on the v1 surface too -- the SDK types `epoch` as int but the API
# returns a float, so pydantic raises int_from_float. Not a consolidation bug.
KNOWN_BROKEN = {"work_item_activity": "int_from_float"}

# Needs a parent id a read-only probe cannot obtain.
NEEDS_PARENT = {"customer_request"}


def read_probes(project_id: str, work_item_id: str) -> list[tuple[str, dict]]:
    P = {"project_id": project_id}
    W = {**P, "work_item_id": work_item_id}
    return [
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


def classify(tool: str, text: str) -> str:
    if not text.startswith("Error:"):
        return "ok"
    if tool in NEEDS_PARENT and "requires:" in text:
        return "skip"
    marker = KNOWN_BROKEN.get(tool)
    if marker and marker in text:
        return "known_broken"
    if any(m in text.lower() for m in FEATURE_MARKERS):
        return "feature_gated"
    return "fail"


def test_every_v2_tool_answers_a_read():
    """No tool may fail for a reason attributable to consolidation."""
    os.environ["PLANE_API_KEY"] = API_KEY
    os.environ["PLANE_WORKSPACE_SLUG"] = WORKSPACE
    from plane_mcp.server import get_stdio_mcp

    async def go():
        results: dict[str, list[str]] = {}
        async with Client(get_stdio_mcp(surface="v2")) as client:
            first = await client.call_tool("work_item", {"action": "list", "project_id": PROJECT_ID})
            text = "\n".join(getattr(c, "text", "") for c in (first.content or []))
            import json

            data = json.loads(text)
            rows = data.get("results", data) if isinstance(data, dict) else data
            work_item_id = rows[0]["id"] if rows else ""

            for tool, args in read_probes(PROJECT_ID, work_item_id):
                try:
                    res = await client.call_tool(tool, args)
                    out = "\n".join(getattr(c, "text", "") for c in (res.content or []))
                except Exception as e:  # noqa: BLE001
                    out = f"Error: {type(e).__name__}: {e}"
                results.setdefault(classify(tool, out), []).append(f"{tool}: {out[:120]}")
        return results

    results = asyncio.run(go())
    failures = results.get("fail", [])
    assert not failures, "tools failed for non-feature reasons:\n" + "\n".join(failures)
    assert len(results.get("ok", [])) >= 15, f"suspiciously few successes: {results}"
