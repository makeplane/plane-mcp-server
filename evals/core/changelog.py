"""Shared release changelog response normalization."""

from __future__ import annotations

import re
from html import unescape
from typing import Any


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def normalize_changelog_text(value: Any) -> str:
    """Extract normalized text from a changelog API response or stored text."""
    nested = _field(value, "changelog")
    candidates = (
        value if isinstance(value, str) else _field(value, "description_html"),
        nested if isinstance(nested, str) else _field(nested, "description_html"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            without_tags = re.sub(r"<[^>]*>", " ", candidate)
            return " ".join(unescape(without_tags).split())
    return ""


def changelog_items(value: Any) -> list[str]:
    """Extract exact item text following each ``Changelog entry …:`` label."""
    text = normalize_changelog_text(value)
    markers = list(re.finditer(r"Changelog entry\s+[^:]+:\s*", text, flags=re.IGNORECASE))
    items: list[str] = []
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        item = text[marker.end() : end].strip().rstrip(".").strip()
        if item:
            items.append(item)
    return items


__all__ = ["changelog_items", "normalize_changelog_text"]
