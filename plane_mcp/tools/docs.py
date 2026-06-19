"""Documentation search tool for Plane MCP Server.

Performs local full-text search over Plane's two public documentation sites:

- ``docs.plane.so`` — product / usage help docs.
- ``developers.plane.so`` — API reference, self-hosting, and app-building docs.

Both sites publish a Mintlify ``llms-full.txt`` content dump. We fetch those
dumps, cache them in-process, split each into per-page records, and rank the
pages against the query locally. The docs are public, so no Plane
authentication is required.
"""

from __future__ import annotations

import html
import re
import threading
import time
from typing import Literal, NamedTuple

import httpx
from fastmcp import FastMCP

# Source key -> (human-readable site label, llms-full.txt URL).
_SOURCES: dict[str, tuple[str, str]] = {
    "help": ("docs.plane.so", "https://docs.plane.so/llms-full.txt"),
    "developer": ("developers.plane.so", "https://developers.plane.so/llms-full.txt"),
}

_CACHE_TTL = 3600.0  # seconds; refresh a cached corpus at most hourly
_REQUEST_TIMEOUT = 20.0
_MAX_LIMIT = 20
_SNIPPET_RADIUS = 160  # characters of context on each side of the first match

# Each page in llms-full.txt begins with a frontmatter block whose first key is
# ``url:``. The block is delimited by ``---`` lines; the body follows until the
# next such block. Page-separator ``---`` rules between bodies do not start with
# ``url:``, so they never match here.
_FRONTMATTER_RE = re.compile(r"(?ms)^---\nurl:.*?\n---\n")
_TITLE_RE = re.compile(r"(?m)^#\s+(.+?)\s*$")
_YAML_KEY_RE = re.compile(r"([A-Za-z_][\w-]*):\s*(.*)$")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_WS_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_BLOCK_SCALAR_INDICATORS = {">", ">-", ">+", "|", "|-", "|+"}

# Dropped from queries so common words don't dominate substring scoring.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "how",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "the",
        "to",
        "what",
        "when",
        "where",
        "which",
        "why",
        "with",
        "you",
        "your",
    }
)


class DocPage(NamedTuple):
    """A single documentation page parsed from llms-full.txt."""

    source: str  # "help" or "developer"
    label: str  # site label, e.g. "docs.plane.so"
    url: str  # clean browsable URL (".html" stripped)
    title: str
    description: str
    content: str  # body markdown


# Parsed-corpus cache: source key -> (fetched_at_epoch, pages).
_CACHE: dict[str, tuple[float, list[DocPage]]] = {}
_CACHE_LOCK = threading.Lock()


def _normalize_url(url: str) -> str:
    """Return the clean browsable URL (drop the ``.html`` suffix)."""
    url = url.strip()
    if url.endswith(".html"):
        url = url[: -len(".html")]
    return url


def _title_from_url(url: str) -> str:
    """Derive a fallback title from the last path segment of a URL."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    slug = slug.replace("-", " ").replace("_", " ").strip()
    return slug.title() if slug else url


def _parse_frontmatter(fm: str) -> tuple[str, str]:
    """Extract ``(url, description)`` from a frontmatter block's text.

    Values may be inline (``url: '…'``) or YAML block scalars (``url: >-`` with
    the value on subsequent indented lines) — both the url and the description
    appear in either form in the real dumps.
    """
    fields = _parse_yaml_fields(fm)
    return fields.get("url", ""), fields.get("description", "")


def _parse_yaml_fields(fm: str) -> dict[str, str]:
    """Parse top-level ``key: value`` pairs from a frontmatter block.

    Inline scalars are returned verbatim (quotes stripped); block scalars
    (``>``/``|`` and folded variants) are joined from their indented
    continuation lines.
    """
    fields: dict[str, str] = {}
    lines = fm.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not line.strip() or line.strip() == "---":
            i += 1
            continue
        match = _YAML_KEY_RE.match(line)
        if not match:
            i += 1
            continue
        key, inline = match.group(1), match.group(2).strip()
        i += 1
        if inline and inline not in _BLOCK_SCALAR_INDICATORS:
            fields[key] = inline.strip("'\"")
            continue
        # Block scalar: value is the indented (or blank) lines that follow.
        parts: list[str] = []
        while i < n and (lines[i][:1] in (" ", "\t") or lines[i].strip() == ""):
            if lines[i].strip():
                parts.append(lines[i].strip())
            i += 1
        fields[key] = " ".join(parts)
    return fields


def _parse_llms_full(text: str, source: str, label: str) -> list[DocPage]:
    """Split an ``llms-full.txt`` document into per-page records."""
    pages: list[DocPage] = []
    matches = list(_FRONTMATTER_RE.finditer(text))
    for idx, match in enumerate(matches):
        url, description = _parse_frontmatter(match.group(0))
        if not url:
            continue
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[match.end() : body_end].strip()
        # Drop the trailing "---" page-separator rule, if present.
        if body.endswith("---"):
            body = body[:-3].rstrip()
        url = _normalize_url(url)
        title_match = _TITLE_RE.search(body)
        title = title_match.group(1).strip() if title_match else _title_from_url(url)
        title = html.unescape(title).strip()
        pages.append(
            DocPage(
                source=source,
                label=label,
                url=url,
                title=title,
                description=description,
                content=body,
            )
        )
    return pages


def _tokenize(text: str) -> list[str]:
    """Lowercase and split text into alphanumeric tokens."""
    return _TOKEN_RE.findall(text.lower())


def _query_tokens(query: str) -> list[str]:
    """Distinct, meaningful query terms (stopwords and single chars removed)."""
    tokens: list[str] = []
    for token in _tokenize(query):
        if len(token) < 2 or token in _STOPWORDS or token in tokens:
            continue
        tokens.append(token)
    return tokens


def _saturate(count: int, k: float = 1.5) -> float:
    """Diminishing-returns term frequency: maps a raw count into ``[0, 1)``.

    This keeps a long page that merely repeats one term from out-ranking a
    focused page that covers more of the query, so relevance rewards coverage
    over raw repetition.
    """
    return count / (count + k) if count else 0.0


def _score_page(query_tokens: list[str], page: DocPage) -> float:
    """Score a page by saturated, field-weighted query-term frequency.

    Matches in the title weigh most, then the description, then the body. Pages
    that match every distinct query term get a relevance boost.
    """
    if not query_tokens:
        return 0.0
    title_l = page.title.lower()
    desc_l = page.description.lower()
    body_l = page.content.lower()
    score = 0.0
    matched_terms = 0
    for token in query_tokens:
        title_hits = title_l.count(token)
        desc_hits = desc_l.count(token)
        body_hits = body_l.count(token)
        if title_hits or desc_hits or body_hits:
            matched_terms += 1
        score += 6.0 * _saturate(title_hits) + 3.0 * _saturate(desc_hits) + 2.0 * _saturate(body_hits)
    if len(query_tokens) > 1 and matched_terms == len(query_tokens):
        score *= 1.5
    return score


def _clean_snippet(text: str) -> str:
    """Strip image markdown, decode HTML entities, and collapse whitespace."""
    text = _IMAGE_RE.sub("", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _make_snippet(content: str, description: str, query_tokens: list[str]) -> str:
    """Build a short excerpt centered on the first query-term match.

    Falls back to the page description (then the body head) when no query term
    appears in the body — e.g. when the match came from the title alone.
    """
    body_l = content.lower()
    pos = -1
    for token in query_tokens:
        found = body_l.find(token)
        if found != -1 and (pos == -1 or found < pos):
            pos = found

    if pos == -1:
        base = description.strip() or content
        snippet = base[: _SNIPPET_RADIUS * 2]
        suffix = "…" if len(base) > _SNIPPET_RADIUS * 2 else ""
        return f"{_clean_snippet(snippet)}{suffix}".strip()

    start = max(0, pos - _SNIPPET_RADIUS)
    end = min(len(content), pos + _SNIPPET_RADIUS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(content) else ""
    return f"{prefix}{_clean_snippet(content[start:end])}{suffix}".strip()


def _fetch_corpus(source: str) -> list[DocPage]:
    """Fetch and parse the llms-full.txt dump for a single source."""
    label, url = _SOURCES[source]
    with httpx.Client(
        timeout=_REQUEST_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": "plane-mcp-server/search_docs"},
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        return _parse_llms_full(response.text, source, label)


def _get_corpus(source: str) -> list[DocPage]:
    """Return the cached corpus for a source, fetching it if stale or missing."""
    cached = _CACHE.get(source)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]
    with _CACHE_LOCK:
        # Re-check under the lock so concurrent callers don't double-fetch.
        cached = _CACHE.get(source)
        if cached and time.time() - cached[0] < _CACHE_TTL:
            return cached[1]
        pages = _fetch_corpus(source)
        _CACHE[source] = (time.time(), pages)
        return pages


def _search(query: str, source: str, limit: int) -> dict:
    """Core search routine shared by the tool and tests."""
    query_tokens = _query_tokens(query)
    limit = max(1, min(limit, _MAX_LIMIT))
    if not query_tokens:
        return {"query": query, "results": []}

    source_keys = ["help", "developer"] if source == "all" else [source]
    pages: list[DocPage] = []
    errors: list[str] = []
    for key in source_keys:
        try:
            pages.extend(_get_corpus(key))
        except Exception as exc:  # noqa: BLE001 - surface fetch failures, don't crash the tool
            errors.append(f"{_SOURCES[key][0]}: {exc}")

    scored = [(score, page) for page in pages if (score := _score_page(query_tokens, page)) > 0]
    scored.sort(key=lambda item: item[0], reverse=True)

    results = [
        {
            "title": page.title,
            "url": page.url,
            "source": page.source,
            "score": round(score, 2),
            "snippet": _make_snippet(page.content, page.description, query_tokens),
        }
        for score, page in scored[:limit]
    ]

    out: dict = {"query": query, "results": results}
    if errors:
        out["error" if not results else "warnings"] = "; ".join(errors)
    return out


def register_docs_tools(mcp: FastMCP) -> None:
    """Register documentation-search tools with the MCP server."""

    @mcp.tool()
    def search_docs(
        query: str,
        source: Literal["all", "help", "developer"] = "all",
        limit: int = 5,
    ) -> dict:
        """
        Search Plane's official documentation and return the most relevant pages.

        Use this to answer questions about how to use Plane (product/help docs)
        or how to build on Plane (REST API, self-hosting, OAuth, webhooks, MCP).
        It searches the public docs at docs.plane.so and developers.plane.so via
        local full-text ranking and returns the top matching pages, each with a
        short snippet and a URL you can open or fetch for full content.

        Args:
            query: Natural-language question or keywords, e.g. "how to create a
                cycle", "rest api create work item", "self-host docker compose".
            source: Which documentation set to search.
                - "all" (default): both sites.
                - "help": product / usage docs (docs.plane.so).
                - "developer": API reference, self-hosting, and app-building
                  docs (developers.plane.so).
            limit: Maximum number of results to return (1-20, default 5).

        Returns:
            A dict with:
                - "query": the original query string.
                - "results": ranked matches, each with "title", "url", "source"
                  ("help" or "developer"), "score", and "snippet". Empty if
                  nothing matched.
                - "error" (or "warnings"): present only if a docs site could not
                  be fetched.
        """
        return _search(query, source, limit)
