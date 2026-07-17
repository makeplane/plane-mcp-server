"""Shared helpers for the release tool modules."""

from typing import Any


def page_params(cursor: str | None, per_page: int | None) -> dict[str, Any]:
    """Build query params for a paginated release endpoint, dropping unset ones."""
    params: dict[str, Any] = {}
    if cursor:
        params["cursor"] = cursor
    if per_page:
        params["per_page"] = per_page
    return params
