"""`get_pql_reference` -- kept 1:1 in the v2 surface.

This is the one tool that does NOT get an `action` parameter: it is already a
single self-contained lookup with no sibling verbs to collapse. It keeps its
original name and signature; only the two register_* variants are added so it
composes with the rest of the v2 package.
"""

from __future__ import annotations

from typing import Literal

from fastmcp import FastMCP

from plane_mcp.tools.pql_reference import PQL_FIELD_DESCRIPTION, PQL_FULL_REFERENCE
from plane_mcp.tools_v2._common import json_out

DOC = """Return the Plane Query Language (PQL) syntax reference.

Call this when composing the `pql` filter for `list_work_items`,
`list_archived_work_items`, `list_cycle_work_items`, `list_module_work_items`,
or `count_work_items`.

Args:
    detail: "full" (default) returns the comprehensive reference with
        all operators, functions, common mistakes, and worked examples.
        "brief" returns the compact field/operator/function quick
        reference (lighter payload for simple queries).

Returns:
    Dict with `detail` (which version was returned) and `reference`
    (the PQL syntax text)."""


def _reference(detail: str) -> dict:
    if detail == "brief":
        return {"detail": "brief", "reference": PQL_FIELD_DESCRIPTION}
    return {"detail": "full", "reference": PQL_FULL_REFERENCE}


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="get_pql_reference", description=DOC)
    def _get_pql_reference(detail: Literal["brief", "full"] = "full") -> dict:
        return _reference(detail)


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="get_pql_reference", description=DOC)
    def _get_pql_reference(detail: Literal["brief", "full"] = "full") -> str:
        try:
            return json_out(_reference(detail))
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
