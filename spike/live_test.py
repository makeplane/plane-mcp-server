"""Live A/D equivalence test for the consolidated intake tool.

Drives BOTH tool surfaces through the real FastMCP client against a live Plane
workspace and compares results:

  A -- the 5 current intake tools (plane_mcp.tools.intake)
  D -- the 1 consolidated intake tool (spike.intake_v2)

Credentials come from .env.test.local (gitignored). Creates and then deletes
its own intake work items; nothing is left behind on success.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ENV = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.test.local")
for _line in open(_ENV):
    if "=" in _line and not _line.strip().startswith("#"):
        _k, _v = _line.strip().split("=", 1)
        os.environ[_k] = _v

from fastmcp import Client, FastMCP  # noqa: E402

from plane_mcp.client import get_plane_client_context  # noqa: E402
from plane_mcp.server import get_stdio_mcp  # noqa: E402
from spike.intake_v2 import register_variant_d  # noqa: E402

PROJECT_ID = os.environ.get("TEST_PROJECT_ID", "311cc73d-551b-4ef8-95fd-a0748996a4b5")

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    results.append((ok, label))
    print(f"  [{PASS if ok else FAIL}] {label}{('  -- ' + detail) if detail else ''}")
    return ok


def text_of(res) -> str:
    if getattr(res, "content", None):
        return "\n".join(getattr(c, "text", "") for c in res.content)
    return str(res)


def payload(res):
    """Structured data if the tool provides it, else parsed JSON text.

    Note: FastMCP still emits structuredContent for a `-> str` tool, but as
    {"result": "<json string>"} -- an opaque blob rather than typed data. So the
    unwrapped value may itself be a JSON string and needs a second parse.
    """
    sc = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
    val = (sc.get("result", sc) if isinstance(sc, dict) else sc) if sc is not None else text_of(res)
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


def sweep() -> int:
    """Delete any leftover spike-* intake items from an interrupted run."""
    client, ws = get_plane_client_context()
    try:
        items = client.intake.list(workspace_slug=ws, project_id=PROJECT_ID).results
    except Exception:
        return 0
    n = 0
    for it in items:
        name = getattr(getattr(it, "issue_detail", None), "name", "") or ""
        if name.startswith("spike-"):
            try:
                client.intake.delete(
                    workspace_slug=ws, project_id=PROJECT_ID, work_item_id=it.issue
                )
                n += 1
            except Exception as e:  # noqa: BLE001
                print(f"  sweep: could not delete {name}: {type(e).__name__}")
    return n


def ensure_intake_enabled() -> bool:
    client, ws = get_plane_client_context()
    feats = client.projects.get_features(workspace_slug=ws, project_id=PROJECT_ID)
    if getattr(feats, "intakes", False):
        print("intake already enabled")
        return False
    from plane.models.projects import ProjectFeature

    client.projects.update_features(
        workspace_slug=ws, project_id=PROJECT_ID, data=ProjectFeature(intakes=True)
    )
    print("intake ENABLED on project (was disabled) -- revert if unwanted")
    return True


async def run_surface(name: str, client: Client, call) -> dict:
    """Exercise create -> list -> retrieve -> update -> delete on one surface."""
    print(f"\n--- {name} ---")
    out: dict = {}
    title = f"spike-{name.lower()}-probe"

    created = payload(await call(client, "create", data={"issue": {"name": title, "priority": "medium"}}))
    wid = created.get("issue") if isinstance(created, dict) else None
    out["created_id"] = wid
    check(bool(wid), "create returns an intake item with an issue id", str(wid))

    listed = payload(await call(client, "list"))
    ids = [i.get("issue") for i in listed] if isinstance(listed, list) else []
    out["list_count"] = len(ids)
    check(wid in ids, "created item appears in list", f"{len(ids)} item(s)")

    got = payload(await call(client, "retrieve", work_item_id=wid))
    out["retrieved_name"] = (got.get("issue_detail") or {}).get("name") if isinstance(got, dict) else None
    check(out["retrieved_name"] == title, "retrieve returns the same item", str(out["retrieved_name"]))

    upd = payload(await call(client, "update", work_item_id=wid, status=-1))
    out["status"] = upd.get("status") if isinstance(upd, dict) else None
    check(out["status"] == -1, "update sets status to -1 (declined)", str(out["status"]))

    await call(client, "delete", work_item_id=wid)
    after = payload(await call(client, "list"))
    gone = wid not in [i.get("issue") for i in after] if isinstance(after, list) else False
    out["deleted"] = gone
    check(gone, "delete removes the item from list")
    return out


async def call_a(client: Client, action: str, **kw):
    """Variant A: five separate tools."""
    names = {
        "list": "list_intake_work_items",
        "create": "create_intake_work_item",
        "retrieve": "retrieve_intake_work_item",
        "update": "update_intake_work_item",
        "delete": "delete_intake_work_item",
    }
    return await client.call_tool(names[action], {"project_id": PROJECT_ID, **kw})


async def call_d(client: Client, action: str, **kw):
    """Variant D: one consolidated tool."""
    return await client.call_tool("intake", {"action": action, "project_id": PROJECT_ID, **kw})


async def main() -> None:
    print(f"workspace={os.environ['PLANE_WORKSPACE_SLUG']}  project={PROJECT_ID}\n")
    toggled = ensure_intake_enabled()
    pre = sweep()
    if pre:
        print(f"swept {pre} leftover spike-* item(s) from a previous run")

    md = FastMCP("spike-d")
    register_variant_d(md)

    async with Client(get_stdio_mcp()) as ca:
        a = await run_surface("A", ca, call_a)
    async with Client(md) as cd:
        d = await run_surface("D", cd, call_d)

    print("\n--- equivalence ---")
    check(a["list_count"] == d["list_count"], "same list count after own create",
          f"A={a['list_count']} D={d['list_count']}")
    check(a["retrieved_name"] != d["retrieved_name"], "each surface saw its own item (distinct probes)",
          f"A={a['retrieved_name']} D={d['retrieved_name']}")
    check(a["status"] == d["status"] == -1, "both applied status=-1")
    check(a["deleted"] and d["deleted"], "both cleaned up")

    print("\n--- variant D error handling (live client) ---")
    async with Client(md) as cd:
        for args, expect in [
            ({"action": "bogus"}, "unknown action"),
            ({"action": "retrieve", "project_id": PROJECT_ID}, "requires: work_item_id"),
        ]:
            t = text_of(await cd.call_tool("intake", args))
            check(expect in t, f"{args} -> readable error", t[:70])

    if toggled:
        print("\nNOTE: intake was enabled by this run. Revert with update_project_features "
              "intakes=False if it should stay off.")

    left = sweep()
    print(f"\nfinal sweep: {left} stray item(s) removed "
          f"({'clean' if left == 0 else 'had leftovers'})")

    bad = [lbl for ok, lbl in results if not ok]
    print(f"\n{'=' * 70}\n{len(results) - len(bad)}/{len(results)} checks passed")
    if bad:
        print("FAILED: " + "; ".join(bad))
        sys.exit(1)


asyncio.run(main())
