"""Shared helpers for the release tool modules."""

from html import escape
from typing import Any


def resolve_description_html(description_html: str | None, description_stripped: str | None) -> str | None:
    """Resolve the description_html to persist."""
    if description_html is not None:
        return description_html
    if description_stripped is not None:
        return "<p>" + escape(description_stripped).replace("\n", "<br/>") + "</p>"
    return None


def plain_text_to_prosemirror(text: str) -> dict[str, Any]:
    """Build a minimal ProseMirror doc from plain text (one paragraph per line)."""
    paragraphs: list[dict[str, Any]] = []
    for line in text.split("\n"):
        paragraph: dict[str, Any] = {"type": "paragraph"}
        if line:
            paragraph["content"] = [{"type": "text", "text": line}]
        paragraphs.append(paragraph)
    return {"type": "doc", "content": paragraphs}


def page_params(cursor: str | None, per_page: int | None) -> dict[str, Any]:
    """Build query params for a paginated release endpoint, dropping unset ones."""
    params: dict[str, Any] = {}
    if cursor:
        params["cursor"] = cursor
    if per_page:
        params["per_page"] = per_page
    return params
