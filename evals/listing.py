"""Measure MCP tool listing size: tool count and cl100k tokens.

``python -m evals.listing [--label local | --server-cmd '<cmd>' --server-env KEY=VAL]``
Reports wire tokens (with outputSchema), model-facing tokens (without), and the top-10
tools by size. Needs EVAL_PLANE_* credentials; tiktoken is a dev optional dependency.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys
from dataclasses import dataclass
from typing import Any

from evals.runner.live import stdio_server_env


@dataclass
class ToolTokenRow:
    name: str
    wire_tokens: int
    model_facing_tokens: int
    has_output_schema: bool


def tool_payload_wire(tool: Any) -> dict[str, Any]:
    """Full wire-shaped tool dict for token counting (includes outputSchema when present)."""
    name = getattr(tool, "name", None) or (tool.get("name") if isinstance(tool, dict) else "") or ""
    description = (getattr(tool, "description", None) if not isinstance(tool, dict) else tool.get("description")) or ""
    input_schema = (
        getattr(tool, "inputSchema", None)
        if not isinstance(tool, dict)
        else (tool.get("inputSchema") or tool.get("input_schema"))
    ) or {}
    output_schema = (
        getattr(tool, "outputSchema", None)
        if not isinstance(tool, dict)
        else (tool.get("outputSchema") or tool.get("output_schema"))
    )
    d: dict[str, Any] = {
        "name": name,
        "description": description,
        "input_schema": input_schema,
    }
    if output_schema is not None:
        d["output_schema"] = output_schema
    return d


def tool_payload_model_facing(tool: Any) -> dict[str, Any]:
    """Model-facing payload: name + description + input_schema only (no outputSchema)."""
    wire = tool_payload_wire(tool)
    return {
        "name": wire["name"],
        "description": wire["description"],
        "input_schema": wire["input_schema"],
    }


def count_tool_tokens(
    tools: list[Any],
    *,
    encode: Any | None = None,
) -> tuple[list[ToolTokenRow], int, int]:
    """Count cl100k tokens per tool for wire and model-facing serializations.

    ``encode`` is a callable ``str -> list[int]`` (tiktoken Encoding.encode). When
    None, imports tiktoken cl100k_base. Returns (per-tool rows sorted by wire
    tokens desc, total_wire, total_model_facing).
    """
    if encode is None:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        encode = enc.encode

    rows: list[ToolTokenRow] = []
    total_wire = 0
    total_model = 0
    for t in tools:
        wire = tool_payload_wire(t)
        model = tool_payload_model_facing(t)
        w_tok = len(encode(json.dumps(wire, separators=(",", ":"), ensure_ascii=False)))
        m_tok = len(encode(json.dumps(model, separators=(",", ":"), ensure_ascii=False)))
        has_out = "output_schema" in wire and wire["output_schema"] is not None
        rows.append(
            ToolTokenRow(
                name=str(wire["name"]),
                wire_tokens=w_tok,
                model_facing_tokens=m_tok,
                has_output_schema=has_out,
            )
        )
        total_wire += w_tok
        total_model += m_tok
    rows.sort(key=lambda r: r.wire_tokens, reverse=True)
    return rows, total_wire, total_model


def _listing_stdio_env(*, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build MCP stdio env from EVAL_* credentials via the shared runner helper."""
    if not os.environ.get("EVAL_PLANE_API_KEY") or not os.environ.get("EVAL_PLANE_WORKSPACE_SLUG"):
        raise RuntimeError("EVAL_PLANE_API_KEY and EVAL_PLANE_WORKSPACE_SLUG are required for listing measurement")
    return stdio_server_env(extra=extra)


async def list_tools_from_stdio(
    command: str,
    args: list[str],
    env: dict[str, str],
) -> list[Any]:
    """Connect to a stdio MCP server and return all tools (paginated)."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=command, args=args, env=env)
    tools: list[Any] = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            cursor = None
            while True:
                page = await session.list_tools(cursor=cursor) if cursor else await session.list_tools()
                tools.extend(page.tools or [])
                cursor = getattr(page, "nextCursor", None) or getattr(page, "next_cursor", None)
                if not cursor:
                    break
    return tools


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Measure MCP tool listing tokens (cl100k)")
    p.add_argument(
        "--label",
        type=str,
        default="local",
        help="Label printed with the listing measurement (default: local).",
    )
    p.add_argument(
        "--server-cmd",
        type=str,
        default=None,
        help="External MCP stdio launch command (shlex-split)",
    )
    p.add_argument(
        "--server-env",
        action="append",
        default=[],
        metavar="KEY=VAL",
        help="Extra env for the MCP server child; repeatable",
    )
    p.add_argument("--top", type=int, default=10, help="Top-N tools by wire tokens (default 10)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    extra: dict[str, str] = {}
    for pair in args.server_env:
        key, sep, val = pair.partition("=")
        if not sep or not key:
            print(f"error: --server-env expects KEY=VAL, got {pair!r}", file=sys.stderr)
            return 2
        extra[key] = val

    if args.server_cmd:
        parts = shlex.split(args.server_cmd)
        if not parts:
            print("error: --server-cmd is empty", file=sys.stderr)
            return 2
        command, cmd_args = parts[0], parts[1:]
        label = (args.label or "local").strip() or "local"
        try:
            env = _listing_stdio_env(extra=extra or None)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    else:
        command = sys.executable
        cmd_args = ["-m", "plane_mcp", "stdio"]
        label = (args.label or "local").strip() or "local"
        try:
            env = _listing_stdio_env(extra=extra or None)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    try:
        tools = asyncio.run(list_tools_from_stdio(command, cmd_args, env))
    except Exception as exc:
        print(f"error: failed to list tools: {exc}", file=sys.stderr)
        return 1

    try:
        rows, total_wire, total_model = count_tool_tokens(tools)
    except ImportError:
        print(
            "error: tiktoken is required (install with: uv pip install '.[dev]')",
            file=sys.stderr,
        )
        return 1

    with_out = sum(1 for r in rows if r.has_output_schema)
    print(
        f"label={label} tools={len(rows)} "
        f"listing_tokens_cl100k={total_wire} "
        f"model_facing(no_outputSchema)={total_model} "
        f"tools_with_outputSchema={with_out}"
    )
    top_n = max(0, int(args.top))
    if top_n and rows:
        print(f"top {min(top_n, len(rows))} by wire tokens:")
        for r in rows[:top_n]:
            flag = " +out" if r.has_output_schema else ""
            print(f"  {r.wire_tokens:6d}  {r.name}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
