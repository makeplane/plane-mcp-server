"""Read an MCP tool name: whose tool it is, and what to call it.

Agent CLIs expose MCP tools under a vendor prefix (``mcp__plane__list_work_items``)
and mix them with their own built-ins (``Bash``, ``ToolSearch``). Drivers and the
result mapper both have to tell those apart before anything is classified or
counted, so this sits beside the result schema rather than inside one driver
package.
"""

from __future__ import annotations

import re
from typing import Any

# mcp__plane__list_work_items  → list_work_items
# mcp__plane-mcp-server__foo  → foo
_MCP_PREFIX_RE = re.compile(r"^mcp__[^_]+(?:_[^_]+)*__(.+)$")
# Alternate: mcp__server__tool with multi-segment server names
_MCP_PREFIX_RE2 = re.compile(r"^mcp__.+?__(.+)$")


def strip_mcp_prefix(name: str) -> str:
    """Strip Claude/Codex MCP tool name prefixes for classification.

    Examples:
      mcp__plane__list_work_items → list_work_items
      mcp__plane-mcp-server__find_work_items → find_work_items
    """
    if not name:
        return name
    m = _MCP_PREFIX_RE2.match(name)
    if m:
        return m.group(1)
    return name


def is_plane_mcp_tool(name: str) -> bool:
    """True when the raw tool name is from our Plane MCP server (pre-strip).

    Claude surfaces MCP tools as ``mcp__<server>__<tool>``. Our config registers
    the server as ``plane``, so names look like ``mcp__plane__find_work_items``.
    Built-ins (``ToolSearch``, ``Bash``, …) have no ``mcp__`` prefix.
    """
    if not name:
        return False
    # mcp__plane__tool  or  mcp__plane-foo__tool
    return name.startswith("mcp__plane__") or name.startswith("mcp__plane-")


def normalize_tool_call(name: str, args: Any) -> dict[str, Any]:
    """Tag a tool call as plane (classifiable) or client (excluded from mispicks)."""
    raw = str(name or "")
    if not isinstance(args, dict):
        args = {"_raw": args}
    if is_plane_mcp_tool(raw):
        return {
            "tool": strip_mcp_prefix(raw),
            "args": args,
            "origin": "plane",
            "raw_tool": raw,
        }
    return {
        "tool": raw,  # keep built-in name as-is (ToolSearch, Bash, …)
        "args": args,
        "origin": "client",
        "raw_tool": raw,
    }


def split_plane_and_client_calls(
    calls: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition tagged calls into plane vs client lists.

    Prefer explicit ``origin`` from ``normalize_tool_call``. Untagged calls
    (API path) default to plane so existing harness behavior is unchanged.
    """
    plane: list[dict[str, Any]] = []
    client: list[dict[str, Any]] = []
    for c in calls:
        origin = c.get("origin")
        if origin is None:
            raw = str(c.get("raw_tool") or c.get("tool") or "")
            if is_plane_mcp_tool(raw):
                origin = "plane"
            elif raw.startswith("mcp__"):
                origin = "client"  # other MCP server
            else:
                origin = "plane"  # bare name → assume plane (API)
        if origin == "client":
            client.append(c)
        else:
            plane.append(c)
    return plane, client


__all__ = [
    "is_plane_mcp_tool",
    "normalize_tool_call",
    "split_plane_and_client_calls",
    "strip_mcp_prefix",
]
