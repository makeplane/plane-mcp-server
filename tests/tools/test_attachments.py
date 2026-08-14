"""The attachment actions the dispatch smoke test cannot reach.

read, download_url and upload_from_url all need attachment metadata to exist and
an outbound fetch to succeed, so they get explicit stubs here. The image channel
is the reason: `read` must return an Image for an image and a str for text, and
a regression that collapsed both to str would pass every other test in this
suite.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastmcp.utilities.types import Image

from plane_mcp.tools import workitem_attachment as module

PROJECT = "project-1"
WORK_ITEM = "work-item-1"
ATTACHMENT = "attachment-1"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class _Response:
    def __init__(self, content: bytes, headers: dict[str, str] | None = None) -> None:
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        return None


def _attachment(name: str, content_type: str):
    return SimpleNamespace(id=ATTACHMENT, attributes={"name": name, "type": content_type})


@pytest.fixture
def attachment_tool(registered, spy, monkeypatch):
    """The tool, with attachment metadata present and the network stubbed out."""
    spy.returns["work_items.attachments.get_download_url"] = "https://files.example.com/a"
    monkeypatch.setattr(module, "get_plane_client_context", lambda: (spy, "acme"))

    def stub(name: str, content_type: str, payload: bytes = PNG):
        spy.returns["work_items.attachments.list"] = [_attachment(name, content_type)]
        monkeypatch.setattr(module.requests, "get", lambda *a, **k: _Response(payload, {"Content-Type": content_type}))

    return registered["workitem_attachment"].fn, spy, stub


def test_read_returns_an_image_for_an_image(attachment_tool):
    tool, _, stub = attachment_tool
    stub("diagram.png", "image/png")

    result = tool(action="read", project_id=PROJECT, workitem_id=WORK_ITEM, attachment_id=ATTACHMENT)

    assert isinstance(result, Image)
    assert result._mime_type == "image/png"


def test_read_returns_text_for_a_text_file(attachment_tool):
    tool, _, stub = attachment_tool
    stub("notes.md", "text/markdown", b"# Notes\nbody")

    result = tool(action="read", project_id=PROJECT, workitem_id=WORK_ITEM, attachment_id=ATTACHMENT)

    assert result == "# Notes\nbody"


def test_read_refuses_an_unsupported_type_and_points_at_download_url(attachment_tool):
    tool, _, stub = attachment_tool
    stub("spec.pdf", "application/pdf")

    with pytest.raises(ValueError, match="download_url"):
        tool(action="read", project_id=PROJECT, workitem_id=WORK_ITEM, attachment_id=ATTACHMENT)


def test_read_refuses_a_file_over_the_limit(attachment_tool):
    tool, _, stub = attachment_tool
    stub("huge.png", "image/png", b"\x00" * (module.IMAGE_READ_LIMIT + 1))

    with pytest.raises(ValueError, match="exceeds"):
        tool(action="read", project_id=PROJECT, workitem_id=WORK_ITEM, attachment_id=ATTACHMENT)


def test_download_url_returns_the_link_and_the_name(attachment_tool):
    tool, _, stub = attachment_tool
    stub("spec.pdf", "application/pdf")

    result = tool(action="download_url", project_id=PROJECT, workitem_id=WORK_ITEM, attachment_id=ATTACHMENT)

    assert result == {
        "download_url": "https://files.example.com/a",
        "attachment_id": ATTACHMENT,
        "name": "spec.pdf",
    }


def test_upload_from_url_rejects_a_private_address(attachment_tool):
    """The SSRF guard: the server performs this fetch, so it must not reach the LAN."""
    tool, spy, _ = attachment_tool

    with pytest.raises(ValueError):
        tool(
            action="upload_from_url",
            project_id=PROJECT,
            workitem_id=WORK_ITEM,
            url="http://169.254.169.254/latest/meta-data/",
        )
    assert not spy.recorder.calls


def test_upload_from_url_sends_the_fetched_bytes(attachment_tool, monkeypatch):
    tool, spy, _ = attachment_tool
    monkeypatch.setattr(module.requests, "get", lambda *a, **k: _Response(PNG, {"Content-Type": "image/png"}))
    monkeypatch.setattr(module, "attachment_to_dict", lambda attachment, slug: {"id": ATTACHMENT})

    result = tool(
        action="upload_from_url",
        project_id=PROJECT,
        workitem_id=WORK_ITEM,
        url="https://example.com/diagram.png",
    )

    upload = next(c for c in spy.recorder.calls if c.method.endswith("upload_from_bytes"))
    assert upload.kwargs["file_bytes"] == PNG
    assert upload.kwargs["name"] == "diagram.png"
    assert upload.kwargs["content_type"] == "image/png"
    assert result == {"id": ATTACHMENT}
