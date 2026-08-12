"""Pagination envelopes and PQL failure handling for Plane list endpoints.

Several resources list work items -- work_item, cycle and module -- and all of
them must return the same envelope shape and handle an invalid PQL filter the
same way. Keeping that here is what makes them consistent.

This is Plane's pagination contract, not one tool surface's: any surface talking
to these endpoints needs the same envelope and the same 400-on-bad-PQL answer.
"""

from __future__ import annotations

from typing import Any

from fastmcp.utilities.logging import get_logger
from plane.errors.errors import HttpError

from plane_mcp.pql_reference import PQL_FULL_REFERENCE

logger = get_logger(__name__)


def dump_results(items: Any, fields: str | None) -> list[Any]:
    """Serialise a page, honouring `fields` as a sparse fieldset.

    Written as a loop rather than a nested ternary: the previous one-liner read
    as though `hasattr` guarded both branches, when it guarded only the second,
    so a page of plain dicts raised AttributeError as soon as `fields` was set.
    """
    requested = {name.strip() for name in fields.split(",")} - {""} if fields else None
    dumped: list[Any] = []
    for item in items or []:
        if not hasattr(item, "model_dump"):
            dumped.append(item)
        elif requested:
            dumped.append(item.model_dump(include=requested))
        else:
            dumped.append(item.model_dump())
    return dumped


def envelope(response: Any, fields: str | None = None) -> dict[str, Any]:
    """Keep the full pagination envelope so paging stays discoverable.

    An action that takes a `cursor` must return one. Handing back only
    `response.results` lets a caller page in but never page on, and makes a
    truncated first page look like the whole set.
    """
    return {
        "results": dump_results(response.results, fields),
        "total_count": response.total_count,
        "count": response.count,
        "next_cursor": response.next_cursor,
        "prev_cursor": response.prev_cursor,
        "next_page_results": response.next_page_results,
        "prev_page_results": response.prev_page_results,
    }


def pql_failure(tool: str, action: str, pql: str, exc: HttpError) -> dict[str, Any] | None:
    """Turn an invalid-PQL 400 into a correctable answer instead of an exception."""
    if not (pql and exc.status_code == 400 and isinstance(exc.response, dict) and "pql" in exc.response):
        return None
    logger.warning("%s %s: invalid PQL %r -> %s", tool, action, pql, exc.response)
    return {
        "error": exc.response["pql"],
        "failed_pql": pql,
        "pql_reference": PQL_FULL_REFERENCE,
        "hint": f"The PQL above failed. Fix it using the reference and retry {tool} {action}.",
    }
