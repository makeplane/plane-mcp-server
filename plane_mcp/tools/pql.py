"""
On-demand PQL syntax reference.
"""

from typing import Literal

from fastmcp import FastMCP

from plane_mcp.tools.pql_reference import PQL_FIELD_DESCRIPTION, PQL_FULL_REFERENCE


def register_pql_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    def get_pql_reference(detail: Literal["brief", "full"] = "full") -> dict:
        """
        Return the Plane Query Language (PQL) syntax reference.

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
            (the PQL syntax text).
        """
        if detail == "brief":
            return {"detail": "brief", "reference": PQL_FIELD_DESCRIPTION}
        return {"detail": "full", "reference": PQL_FULL_REFERENCE}
