"""HTML sanitization utilities for Plane MCP Server.

Provides safe HTML cleaning to prevent stored XSS attacks when accepting
user-provided HTML content (descriptions, comments) before sending to the
Plane API.
"""

import nh3

ALLOWED_TAGS = {
    "p", "br", "strong", "b", "em", "i", "u", "s", "del",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "blockquote", "pre", "code",
    "a", "img",
    "table", "thead", "tbody", "tr", "th", "td",
    "hr", "span", "div", "sub", "sup",
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


def sanitize_html(html: str | None) -> str | None:
    if html is None:
        return None
    if not html:
        return html

    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        link_rel="noopener noreferrer",
        url_schemes={"http", "https", "mailto"},
    )
