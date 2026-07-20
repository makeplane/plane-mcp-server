"""Unit tests for release changelog tools (offline, monkeypatched client)."""

import asyncio

from fastmcp import Client, FastMCP
from plane.models.releases import ReleaseChangelog

from plane_mcp.tools.releases import changelog as changelog_tools
from plane_mcp.tools.releases._helpers import plain_text_to_prosemirror


class FakeChangelog:
    def __init__(self):
        self.data = None

    def update(self, workspace_slug, release_id, data):
        self.data = data
        return ReleaseChangelog.model_validate({"id": "c1", "release": release_id})


class FakeReleases:
    def __init__(self):
        self.changelog = FakeChangelog()


class FakeClient:
    def __init__(self):
        self.releases = FakeReleases()


def _call(monkeypatch, client, args):
    monkeypatch.setattr(changelog_tools, "get_plane_client_context", lambda: (client, "ws"))

    async def run():
        mcp = FastMCP("test")
        changelog_tools.register_release_changelog_tools(mcp)
        async with Client(mcp) as c:
            return await c.call_tool("update_release_changelog", args)

    return asyncio.run(run())


def test_plain_text_to_prosemirror_single_line():
    assert plain_text_to_prosemirror("hello") == {
        "type": "doc",
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": "hello"}]}],
    }


def test_plain_text_to_prosemirror_multiline_with_blank():
    doc = plain_text_to_prosemirror("a\n\nb")
    assert doc["content"] == [
        {"type": "paragraph", "content": [{"type": "text", "text": "a"}]},
        {"type": "paragraph"},
        {"type": "paragraph", "content": [{"type": "text", "text": "b"}]},
    ]


def test_stripped_emits_both_html_and_matching_json(monkeypatch):
    """Plain text wraps into <p> HTML and a matching ProseMirror doc so the
    document-based changelog editor actually renders it."""
    client = FakeClient()
    _call(monkeypatch, client, {"release_id": "r", "description_stripped": "qwerty"})
    data = client.releases.changelog.data
    assert data.description_html == "<p>qwerty</p>"
    assert data.description_json == plain_text_to_prosemirror("qwerty")


def test_explicit_html_does_not_generate_json(monkeypatch):
    """Raw HTML can't be safely converted to a doc, so no json is fabricated."""
    client = FakeClient()
    _call(monkeypatch, client, {"release_id": "r", "description_html": "<h1>hi</h1>"})
    data = client.releases.changelog.data
    assert data.description_html == "<h1>hi</h1>"
    assert data.description_json is None


def test_explicit_json_wins_over_stripped(monkeypatch):
    """An explicit description_json is passed through unchanged."""
    client = FakeClient()
    explicit = {"type": "doc", "content": []}
    _call(
        monkeypatch,
        client,
        {"release_id": "r", "description_stripped": "x", "description_json": explicit},
    )
    data = client.releases.changelog.data
    assert data.description_html == "<p>x</p>"
    assert data.description_json == explicit
