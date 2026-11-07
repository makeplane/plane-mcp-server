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


def _render_inline_markdown(text: str) -> str:
    """Render common inline Markdown formatting on HTML-escaped text."""
    escaped = html_lib.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"__(.+?)__", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(r"\b_(.+?)_\b", r"<em>\1</em>", escaped)
    escaped = re.sub(r"~~(.+?)~~", r"<del>\1</del>", escaped)
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^\s)]+|mailto:[^\s)]+)\)",
        r'<a href="\2">\1</a>',
        escaped,
    )
    return escaped


def _render_markdown_block(block: str) -> str:
    """Render a non-fenced Markdown block (headings, lists, blockquotes, paragraphs) into HTML."""
    lines = [line for line in block.strip().split("\n") if line.strip()]
    if not lines:
        return ""

    if len(lines) == 1:
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", lines[0].strip())
        if heading_match:
            level = len(heading_match.group(1))
            return f"<h{level}>{_render_inline_markdown(heading_match.group(2).strip())}</h{level}>"

    if all(re.match(r"^[-*]\s+(.+)$", line.strip()) for line in lines):
        ul_prefix = re.compile(r"^[-*]\s+")
        items = [f"<li>{_render_inline_markdown(ul_prefix.sub('', line.strip()))}</li>" for line in lines]
        return f"<ul>{''.join(items)}</ul>"

    if all(re.match(r"^\d+\.\s+(.+)$", line.strip()) for line in lines):
        ol_prefix = re.compile(r"^\d+\.\s+")
        items = [f"<li>{_render_inline_markdown(ol_prefix.sub('', line.strip()))}</li>" for line in lines]
        return f"<ol>{''.join(items)}</ol>"

    if all(re.match(r"^>\s?(.*)$", line.strip()) for line in lines):
        bq_prefix = re.compile(r"^>\s?")
        quote_lines = [_render_inline_markdown(bq_prefix.sub("", line.strip())) for line in lines]
        return f"<blockquote>{'<br/>'.join(quote_lines)}</blockquote>"

    rendered_lines = [_render_inline_markdown(line) for line in block.strip().split("\n")]
    p_clean = "<br/>".join(rendered_lines)
    return f"<p>{p_clean}</p>" if p_clean else ""


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

    # Render fenced code blocks and basic Markdown blocks/inline formatting to HTML
    fenced_pattern = re.compile(r"```(?:[a-zA-Z0-9_-]*\n)?(.*?)```", re.DOTALL)
    html_parts: list[str] = []
    last_idx = 0
    for match in fenced_pattern.finditer(stripped):
        before = stripped[last_idx : match.start()].strip()
        if before:
            for p in re.split(r"\n\s*\n", before):
                rendered = _render_markdown_block(p)
                if rendered:
                    html_parts.append(rendered)
        code_body = html_lib.escape(match.group(1).strip("\n"))
        html_parts.append(f"<pre><code>{code_body}</code></pre>")
        last_idx = match.end()

    remaining = stripped[last_idx:].strip()
    if remaining:
        for p in re.split(r"\n\s*\n", remaining):
            rendered = _render_markdown_block(p)
            if rendered:
                html_parts.append(rendered)

    html_result = "".join(html_parts)
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


def _resolve_unique_snippet(working_html: str, snippet: str) -> tuple[str, int]:
    """Resolve a target or anchor snippet against stored HTML across HTML-encoding levels."""
    direct_count = working_html.count(snippet)
    if direct_count > 0:
        return snippet, direct_count

    candidates: list[str] = []

    def _add_candidate(val: str) -> None:
        """Add a non-empty deduplicated candidate snippet variant."""
        if val and val not in candidates:
            candidates.append(val)

    _add_candidate(snippet)
    curr = snippet
    for _ in range(3):
        curr = html_lib.unescape(curr)
        _add_candidate(curr)

    raw = curr
    curr_esc = raw
    curr_esc_no_quote = raw
    for _ in range(3):
        curr_esc = html_lib.escape(curr_esc)
        curr_esc_no_quote = html_lib.escape(curr_esc_no_quote, quote=False)
        _add_candidate(curr_esc)
        _add_candidate(curr_esc_no_quote)

    matched_variants = [v for v in candidates if working_html.count(v) > 0]
    total = sum(working_html.count(v) for v in matched_variants)
    if total == 1:
        return matched_variants[0], 1
    if total > 1:
        return matched_variants[0], total
    return snippet, 0


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

    if replace_text is not None and replace_target_snippet is None:
        return None, "replace_target_snippet is required when replace_text is provided"

    # 1. Validation for replace_target_snippet
    if replace_target_snippet is not None:
        if not replace_target_snippet.strip():
            return None, "replace_target_snippet cannot be empty or whitespace only"
        if replace_text is None:
            return None, "replace_text is required when replace_target_snippet is provided"

        # Check occurrences in HTML across encoding variants
        resolved_target, occurrences = _resolve_unique_snippet(working_html, replace_target_snippet)

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
        working_html = working_html.replace(resolved_target, safe_replace, 1)

    # 2. Validation for append_after_snippet
    if append_text is not None:
        safe_append = sanitize_html(append_text) or ""
        if append_after_snippet is not None:
            if not append_after_snippet.strip():
                return None, "append_after_snippet cannot be empty or whitespace only"

            resolved_anchor, occurrences = _resolve_unique_snippet(working_html, append_after_snippet)

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

            idx = working_html.find(resolved_anchor)
            insert_pos = idx + len(resolved_anchor)
            working_html = working_html[:insert_pos] + safe_append + working_html[insert_pos:]
        else:
            # Append to end
            if working_html:
                working_html = f"{working_html}<br><br>{safe_append}"
            else:
                working_html = safe_append

    return sanitize_html(working_html), None
