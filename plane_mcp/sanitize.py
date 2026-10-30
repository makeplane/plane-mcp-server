"""HTML sanitization and snippet editing utilities for Plane MCP Server.

Provides safe HTML cleaning to prevent stored XSS attacks when accepting
user-provided HTML/Markdown content (descriptions, comments, docs) before sending
to the Plane API, and provides atomic snippet-based editing with collision detection.
"""

from __future__ import annotations

import html as html_lib
import re

import nh3
from markdownify import markdownify as md

ALLOWED_TAGS = {
    "p",
    "br",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "s",
    "del",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "ul",
    "ol",
    "li",
    "blockquote",
    "pre",
    "code",
    "a",
    "img",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "hr",
    "span",
    "div",
    "sub",
    "sup",
}

ALLOWED_ATTRIBUTES = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "span": {"class"},
    "div": {"class"},
    "code": {"class"},
    "pre": {"class"},
}


def sanitize_html(html_str: str | None) -> str | None:
    """Sanitize HTML string using nh3 with strict tag and attribute allowlists."""
    if html_str is None:
        return None
    if not html_str:
        return html_str

    return nh3.clean(
        html_str,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        link_rel="noopener noreferrer",
        url_schemes={"http", "https", "mailto"},
    )


def markdown_or_html_to_safe_html(content: str | None) -> str | None:
    """Convert Markdown or HTML text to safe, sanitized HTML for Plane description fields."""
    if content is None:
        return None
    stripped = content.strip()
    if not stripped:
        return ""

    # If already contains common HTML tags, sanitize directly
    if re.search(r"<(?:p|br|div|h[1-6]|ul|ol|li|table|pre|code|strong|em|b|i)[^>]*>", stripped, re.IGNORECASE):
        return sanitize_html(content)

    # Otherwise format basic markdown paragraphs/lines to HTML paragraphs
    paragraphs = re.split(r"\n\s*\n", stripped)
    html_paragraphs = []
    for p in paragraphs:
        p_clean = html_lib.escape(p.strip()).replace("\n", "<br/>")
        if p_clean:
            html_paragraphs.append(f"<p>{p_clean}</p>")
    html_result = "".join(html_paragraphs)
    return sanitize_html(html_result)


def html_to_clean_markdown(html_str: str | None) -> str:
    """Convert HTML into clean ATX Markdown."""
    if not html_str:
        return ""
    try:
        converted = md(html_str, heading_style="ATX", strip=["style", "script"])
        return converted.strip()
    except Exception:
        return html_str.strip()


def apply_snippet_edits(
    current_html: str,
    *,
    replace_target_snippet: str | None = None,
    replace_text: str | None = None,
    append_after_snippet: str | None = None,
    append_text: str | None = None,
) -> tuple[str | None, str | None]:
    """Safely apply snippet replacement or snippet append to current HTML.

    Returns:
        (updated_html, error_message): Exactly one of these is non-None.
    """
    working_html = current_html or ""

    # 1. Validation for replace_target_snippet
    if replace_target_snippet is not None:
        if not replace_target_snippet.strip():
            return None, "replace_target_snippet cannot be empty or whitespace only"
        if replace_text is None:
            return None, "replace_text is required when replace_target_snippet is provided"

        # Check occurrences in HTML
        occurrences = working_html.count(replace_target_snippet)
        if occurrences == 0:
            # Try matching with stripped text or unescaped HTML entities
            unescaped_snippet = html_lib.unescape(replace_target_snippet)
            if unescaped_snippet != replace_target_snippet and working_html.count(unescaped_snippet) == 1:
                replace_target_snippet = unescaped_snippet
                occurrences = 1

        if occurrences == 0:
            return None, (
                f"Target snippet not found in description: '{replace_target_snippet}'. "
                "Ensure the snippet matches exactly or retrieve the latest text."
            )
        if occurrences > 1:
            return None, (
                f"Ambiguous snippet: found {occurrences} occurrences in description. "
                "Target snippet must be unique to prevent unintended modifications."
            )

        # Sanitize replacement text
        safe_replace = sanitize_html(replace_text) or ""
        working_html = working_html.replace(replace_target_snippet, safe_replace, 1)

    # 2. Validation for append_after_snippet
    if append_text is not None:
        safe_append = sanitize_html(append_text) or ""
        if append_after_snippet is not None:
            if not append_after_snippet.strip():
                return None, "append_after_snippet cannot be empty or whitespace only"

            occurrences = working_html.count(append_after_snippet)
            if occurrences == 0:
                unescaped_snippet = html_lib.unescape(append_after_snippet)
                if unescaped_snippet != append_after_snippet and working_html.count(unescaped_snippet) == 1:
                    append_after_snippet = unescaped_snippet
                    occurrences = 1

            if occurrences == 0:
                return None, (
                    f"Anchor snippet not found in description: '{append_after_snippet}'. "
                    "Ensure the snippet matches exactly or retrieve the latest text."
                )
            if occurrences > 1:
                return None, (
                    f"Ambiguous anchor snippet: found {occurrences} occurrences in description. "
                    "Anchor snippet must be unique."
                )

            idx = working_html.find(append_after_snippet)
            insert_pos = idx + len(append_after_snippet)
            working_html = working_html[:insert_pos] + safe_append + working_html[insert_pos:]
        else:
            # Append to end
            if working_html:
                working_html = f"{working_html}<br><br>{safe_append}"
            else:
                working_html = safe_append

    return sanitize_html(working_html), None
