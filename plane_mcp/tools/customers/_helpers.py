"""Shared helpers for the customer tool modules."""

from typing import Any

from plane.models.customers import PropertySettings
from plane.models.work_item_property_configurations import (
    DateAttributeSettings,
    TextAttributeSettings,
)


def page_params(cursor: str | None, per_page: int | None, query: str | None = None) -> dict[str, Any]:
    """Build query params for a paginated customer endpoint, dropping unset ones."""
    params: dict[str, Any] = {}
    if cursor:
        params["cursor"] = cursor
    if per_page:
        params["per_page"] = per_page
    if query:
        params["query"] = query
    return params


def build_settings(property_type: str, settings: dict | None) -> PropertySettings:
    """Turn a raw settings dict into the typed settings model for its property type."""
    if not settings:
        return None
    if property_type == "TEXT":
        return TextAttributeSettings(**settings)
    if property_type == "DATETIME":
        return DateAttributeSettings(**settings)
    return None
