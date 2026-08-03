"""Probe: what do our MCP tools actually cost the *model*?

Everything measured so far is the MCP wire payload (server -> client). This
probe measures the other half of the question against the real Anthropic API:

  Q1. Does an Anthropic tool definition accept an output-schema field at all?
      If it 400s, an MCP `outputSchema` has nowhere to go and is necessarily
      dropped before the model ever sees it.

  Q2. What do the tool definitions really cost, per the actual tokenizer?
      Replaces the chars/4 approximation used in docs/tool-consolidation-plan.md
      with measured counts for baseline (177 tools) vs v2 (29 tools).

  Q3. What if a client inlines outputSchema into the description instead of
      dropping it? That is the pessimistic upper bound.

CREDENTIALS — the probe reads them the standard way, so any of these works:
  * export ANTHROPIC_API_KEY=sk-ant-...
  * ant auth login          (profile is picked up by a bare Anthropic() client)
Nothing is hardcoded and no key is written to disk by this script.

RUN:
  .venv/bin/pip install anthropic      # if not already present
  .venv/bin/python spike/probe_model_tokens.py

COST: count_tokens is free; the single Q1 probe is one ~20-token request.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PLANE_API_KEY", "x")
os.environ.setdefault("PLANE_WORKSPACE_SLUG", "x")

try:
    import anthropic
except ImportError:
    sys.exit("Install the SDK first:  .venv/bin/pip install anthropic")

from benchmarks.measure_all import build_v2, dds_of  # noqa: E402
from plane_mcp.server import get_stdio_mcp  # noqa: E402

MODEL = os.environ.get("PROBE_MODEL", "claude-opus-5")
J = dict(separators=(",", ":"))
PROBE_MSG = [{"role": "user", "content": "hi"}]


def to_api_tool(dd: dict, inline_output: bool = False) -> dict:
    """Convert an MCP tool descriptor to an Anthropic tool definition.

    This is the conversion an MCP client performs. Note what it can carry:
    name, description, input_schema -- and nothing for outputSchema.
    """
    desc = dd.get("description", "") or ""
    if inline_output and dd.get("outputSchema"):
        desc = f"{desc}\n\nReturns JSON matching: {json.dumps(dd['outputSchema'], **J)}"
    return {
        "name": dd["name"],
        "description": desc,
        "input_schema": dd.get("inputSchema") or {"type": "object", "properties": {}},
    }


def count(client, tools: list[dict]) -> int:
    return client.messages.count_tokens(
        model=MODEL, messages=PROBE_MSG, tools=tools
    ).input_tokens


def approx(dds: list[dict], inline_output: bool = False) -> int:
    """The chars/4 estimate used in the plan doc, for comparison."""
    payload = [to_api_tool(d, inline_output) for d in dds]
    return len(json.dumps(payload, **J)) // 4


async def collect() -> tuple[list[dict], list[dict]]:
    baseline = await dds_of(get_stdio_mcp())
    typed_mcp, failed = build_v2("typed")
    if failed:
        print("WARN: v2 modules failed to build:", failed, file=sys.stderr)
    return baseline, await dds_of(typed_mcp)


def main() -> None:
    baseline, v2 = asyncio.run(collect())
    client = anthropic.Anthropic()

    # ---- Q1 -----------------------------------------------------------
    print("\n" + "=" * 78)
    print("Q1  Does an Anthropic tool definition accept an output-schema field?")
    print("=" * 78)
    probe_tool = {
        "name": "probe",
        "description": "probe",
        "input_schema": {"type": "object", "properties": {}},
    }
    with_out = {**probe_tool, "output_schema": {"type": "object", "properties": {}}}
    try:
        n = count(client, [with_out])
        base_n = count(client, [probe_tool])
        if n == base_n:
            print(f"  ACCEPTED but IGNORED  (same count: {n}) -> field is discarded")
        else:
            print(f"  ACCEPTED and COUNTED  ({base_n} -> {n}) -> field reaches the model")
    except anthropic.BadRequestError as e:
        print("  REJECTED (400) -> no such field. MCP outputSchema cannot be forwarded.")
        print(f"  {str(e)[:180]}")

    # ---- Q2 -----------------------------------------------------------
    print("\n" + "=" * 78)
    print("Q2  Real token cost of the tool definitions (measured, not chars/4)")
    print("=" * 78)
    rows = [("A  baseline", baseline, False), ("BD/D  v2 (29 tools)", v2, False)]
    measured = {}
    print(f"{'variant':26s} {'tools':>6s} {'measured':>10s} {'chars/4':>10s} {'drift':>8s}")
    print("-" * 66)
    for label, dds, inline in rows:
        tools = [to_api_tool(d, inline) for d in dds]
        m = count(client, tools)
        a = approx(dds, inline)
        measured[label] = m
        print(f"{label:26s} {len(tools):6d} {m:10,d} {a:10,d} {(a/m-1)*100:+7.1f}%")
    a_tok = measured["A  baseline"]
    v_tok = measured["BD/D  v2 (29 tools)"]
    print("-" * 66)
    print(f"  consolidation cut: {a_tok:,} -> {v_tok:,} tok  ({(1 - v_tok/a_tok)*100:.1f}%)")

    # ---- Q3 -----------------------------------------------------------
    print("\n" + "=" * 78)
    print("Q3  Upper bound if a client inlines outputSchema into the description")
    print("=" * 78)
    for label, dds in (("A  baseline", baseline), ("BD/D  v2 (29 tools)", v2)):
        tools = [to_api_tool(d, inline_output=True) for d in dds]
        m = count(client, tools)
        plain = measured[label]
        print(f"{label:26s} {plain:10,d} -> {m:10,d} tok  ({m/plain:.1f}x)")

    print("\nInterpretation:")
    print("  Q1 REJECTED/IGNORED + Q2 ~= the model-facing figures in the plan doc")
    print("  => output schemas never reach the model; BD and D are equivalent for")
    print("     context cost, so choose BD (non-breaking) and lose nothing.")
    print("  Q1 COUNTED, or a client that inlines (Q3) => the full payload is real")
    print("     and the BD-vs-D tradeoff stands as originally stated.")


if __name__ == "__main__":
    main()
