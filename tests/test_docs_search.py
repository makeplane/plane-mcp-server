"""Unit tests for the search_docs documentation tool.

These run without network access: the llms-full.txt parser and ranker are pure
functions exercised against fixtures, and the search flow is tested by seeding
the in-process corpus cache directly.
"""

from __future__ import annotations

import time

import pytest

from plane_mcp.tools import docs

# Mirrors the real docs.plane.so/llms-full.txt structure: pages are frontmatter
# blocks (first key ``url:``) separated by a ``---`` rule. Includes both a YAML
# block-scalar description and an inline one.
HELP_FIXTURE = """---
url: 'https://docs.plane.so/cycles/overview.html'
description: >-
  Plan sprints with cycles. Group work items into time-boxed cycles to track
  progress.
---

# Cycles overview

Cycles are time-boxed sprints. To create a cycle, open a project and click New Cycle.

---

---
url: 'https://docs.plane.so/modules/overview.html'
description: Organize work into modules.
---

# Modules

Modules group related work items across a project.

---

---
url: 'https://docs.plane.so/intro/home.html'
description: Welcome to Plane.
---

# Introduction

Plane is project management software for teams.
"""

DEV_FIXTURE = """---
url: 'https://developers.plane.so/api-reference/work-items/create.html'
description: Create a work item via the REST API.
---

# Create work item

POST to the work items endpoint to create a work item programmatically.
"""

# Mirrors API-reference pages, which encode the long URL as a YAML block scalar
# and include HTML entities in the heading.
BLOCK_SCALAR_FIXTURE = """---
url: >-
  https://developers.plane.so/api-reference/issue/add-issue.html
description: >-
  Create a work item via the REST API.
---

# Create a work item&#x20;

POST to the endpoint to create a work item.
"""


def _seed_cache() -> None:
    now = time.time()
    docs._CACHE["help"] = (now, docs._parse_llms_full(HELP_FIXTURE, "help", "docs.plane.so"))
    docs._CACHE["developer"] = (now, docs._parse_llms_full(DEV_FIXTURE, "developer", "developers.plane.so"))


@pytest.fixture(autouse=True)
def _clear_cache():
    docs._CACHE.clear()
    yield
    docs._CACHE.clear()


# --- parsing -------------------------------------------------------------------


def test_parse_splits_pages_and_strips_html_suffix():
    pages = docs._parse_llms_full(HELP_FIXTURE, "help", "docs.plane.so")
    assert [p.url for p in pages] == [
        "https://docs.plane.so/cycles/overview",
        "https://docs.plane.so/modules/overview",
        "https://docs.plane.so/intro/home",
    ]
    assert all(p.source == "help" and p.label == "docs.plane.so" for p in pages)


def test_parse_extracts_titles():
    pages = docs._parse_llms_full(HELP_FIXTURE, "help", "docs.plane.so")
    assert [p.title for p in pages] == ["Cycles overview", "Modules", "Introduction"]


def test_parse_handles_block_scalar_and_inline_descriptions():
    pages = docs._parse_llms_full(HELP_FIXTURE, "help", "docs.plane.so")
    assert "time-boxed cycles" in pages[0].description
    assert "\n" not in pages[0].description  # continuation lines joined
    assert pages[1].description == "Organize work into modules."


def test_parse_strips_trailing_separator_rule_from_body():
    pages = docs._parse_llms_full(HELP_FIXTURE, "help", "docs.plane.so")
    assert "New Cycle" in pages[0].content
    assert not pages[0].content.rstrip().endswith("---")


def test_parse_handles_block_scalar_url():
    pages = docs._parse_llms_full(BLOCK_SCALAR_FIXTURE, "developer", "developers.plane.so")
    assert len(pages) == 1
    assert pages[0].url == "https://developers.plane.so/api-reference/issue/add-issue"


def test_parse_unescapes_html_entities_in_title():
    pages = docs._parse_llms_full(BLOCK_SCALAR_FIXTURE, "developer", "developers.plane.so")
    assert pages[0].title == "Create a work item"


# --- query tokenization --------------------------------------------------------


def test_query_tokens_drop_stopwords_and_single_chars():
    assert docs._query_tokens("How to create a cycle") == ["create", "cycle"]


def test_query_tokens_dedupe():
    assert docs._query_tokens("cycle cycle Cycle") == ["cycle"]


# --- search --------------------------------------------------------------------


def test_search_ranks_relevant_page_first_with_snippet():
    _seed_cache()
    result = docs._search("how to create a cycle", "all", 5)
    assert result["query"] == "how to create a cycle"
    assert result["results"], "expected at least one match"
    top = result["results"][0]
    assert top["url"] == "https://docs.plane.so/cycles/overview"
    assert top["source"] == "help"
    assert "cycle" in top["snippet"].lower()
    assert top["score"] > 0


def test_search_source_filter_restricts_corpus():
    _seed_cache()
    result = docs._search("create work item", "developer", 5)
    assert result["results"]
    assert {r["source"] for r in result["results"]} == {"developer"}
    assert result["results"][0]["url"].endswith("/work-items/create")


def test_search_empty_or_stopword_only_query_returns_no_results():
    _seed_cache()
    assert docs._search("   ", "all", 5)["results"] == []
    assert docs._search("how to the", "all", 5)["results"] == []


def test_search_respects_limit():
    _seed_cache()
    result = docs._search("plane work cycle module project", "help", 1)
    assert len(result["results"]) == 1


def test_search_no_match_returns_empty_results_without_error():
    _seed_cache()
    result = docs._search("kubernetes helm chart xyzzy", "help", 5)
    assert result["results"] == []
    assert "error" not in result


def test_search_reports_fetch_error(monkeypatch):
    def boom(_source):
        raise RuntimeError("network down")

    monkeypatch.setattr(docs, "_fetch_corpus", boom)
    result = docs._search("cycle", "help", 5)
    assert result["results"] == []
    assert "error" in result and "network down" in result["error"]


def test_search_partial_fetch_error_is_a_warning(monkeypatch):
    # help corpus seeded; developer corpus fetch fails -> results plus a warning.
    docs._CACHE["help"] = (time.time(), docs._parse_llms_full(HELP_FIXTURE, "help", "docs.plane.so"))

    def boom(_source):
        raise RuntimeError("dev docs unreachable")

    monkeypatch.setattr(docs, "_fetch_corpus", boom)
    result = docs._search("create a cycle", "all", 5)
    assert result["results"]
    assert "warnings" in result and "dev docs unreachable" in result["warnings"]
    assert "error" not in result


# --- full_text ------------------------------------------------------------------


def test_search_full_text_returns_full_body_not_snippet():
    _seed_cache()
    result = docs._search("how to create a cycle", "help", 1, full_text=True)
    top = result["results"][0]
    assert top["url"] == "https://docs.plane.so/cycles/overview"
    assert "content" in top and "snippet" not in top
    assert "New Cycle" in top["content"]  # full body, not just a snippet


def test_search_full_text_unescapes_html_entities():
    docs._CACHE["developer"] = (
        time.time(),
        docs._parse_llms_full(BLOCK_SCALAR_FIXTURE, "developer", "developers.plane.so"),
    )
    result = docs._search("create work item", "developer", 1, full_text=True)
    assert "&#x20;" not in result["results"][0]["content"]  # entities decoded


def test_search_default_returns_snippet_not_content():
    _seed_cache()
    top = docs._search("how to create a cycle", "help", 1)["results"][0]
    assert "snippet" in top and "content" not in top
