"""Level of Detail (LOD) transformations for Plane Documentation entities."""

from __future__ import annotations

from enum import Enum
from typing import Any

from plane_mcp.sanitize import html_to_clean_markdown


class DocsLOD(str, Enum):
    """Supported Level of Detail tiers for Plane documentation payloads."""

    SUMMARY = "summary"
    STANDARD = "standard"
    FULL = "full"


def _clean_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Strip keys with None or empty container values from a dictionary."""
    return {k: v for k, v in data.items() if v not in (None, "", [], {})}


def apply_doc_lod(page: Any, lod: DocsLOD | str = DocsLOD.STANDARD, project_slug: str | None = None) -> dict[str, Any]:
    """Transform a Page model or dict into an intent-focused LOD payload."""
    data = None
    if hasattr(page, "model_dump") and callable(page.model_dump):
        try:
            dump = page.model_dump(mode="json")
            if isinstance(dump, dict):
                data = dump
        except Exception:
            pass
    if data is None and hasattr(page, "dict") and callable(page.dict):
        try:
            dump = page.dict()
            if isinstance(dump, dict):
                data = dump
        except Exception:
            pass
    if data is None and isinstance(page, dict):
        data = dict(page)
    if data is None:
        inner_page = getattr(page, "page", None)
        if isinstance(inner_page, dict) and not isinstance(getattr(page, "id", None), str):
            data = dict(inner_page)
            for k in ("collection_id", "parent_id", "page_collection_id"):
                val = getattr(page, k, None)
                if isinstance(val, str) and val and data.get(k) is None:
                    data[k] = val
        else:
            data = {
                "id": getattr(page, "id", None),
                "name": getattr(page, "name", None),
                "description_html": getattr(page, "description_html", None),
                "parent_id": getattr(page, "parent_id", None),
                "collection_id": getattr(page, "collection_id", None),
                "is_locked": getattr(page, "is_locked", None),
                "updated_at": getattr(page, "updated_at", None),
                "access": getattr(page, "access", None),
                "created_at": getattr(page, "created_at", None),
                "created_by": getattr(page, "created_by", None),
            }

    if isinstance(data.get("page"), dict) and not data.get("id"):
        nested_page = dict(data["page"])
        for k, v in data.items():
            if k != "page" and v is not None and nested_page.get(k) is None:
                nested_page[k] = v
        data = nested_page

    lod_str = str(getattr(lod, "value", lod)).lower()
    page_id = str(data.get("id") or "")
    name = str(data.get("name") or "")
    scope = project_slug or "workspace"

    if lod_str == "summary":
        res = {
            "id": page_id,
            "name": name,
            "scope": scope,
            "parent_id": data.get("parent_id"),
            "collection_id": data.get("collection_id"),
            "is_locked": data.get("is_locked"),
            "updated_at": data.get("updated_at"),
        }
        return _clean_dict(res)

    # Standard and Full: convert description_html to Markdown content
    description_html = data.get("description_html") or ""
    content_md = html_to_clean_markdown(description_html)

    if lod_str == "standard":
        res = {
            "id": page_id,
            "name": name,
            "scope": scope,
            "content": content_md,
            "parent_id": data.get("parent_id"),
            "collection_id": data.get("collection_id"),
            "is_locked": data.get("is_locked"),
            "updated_at": data.get("updated_at"),
        }
        return _clean_dict(res)

    # Full: preserve all attributes with markdown content
    full_data = dict(data)
    full_data["scope"] = scope
    full_data["content"] = content_md
    if "description_html" in full_data:
        del full_data["description_html"]
    return _clean_dict(full_data)
