"""`workitem read_description_image`, the action the dispatch smoke test cannot reach.

It needs an image embedded in the description and an outbound fetch to get past
its own preconditions. The image channel is the reason it has its own file: the
action must hand back an `Image`, and a regression that collapsed it to a string
would pass every other test in the suite.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import requests
from fastmcp.utilities.types import Image
from plane.errors.errors import HttpError

from plane_mcp.tools import workitem as module

PROJECT = "project-1"
WORK_ITEM = "work-item-1"
ASSET = "0b1f6d1e-6f0e-4f0a-9d63-2f3c1f6f2a11"
OTHER_ASSET = "9c2a7e44-1d55-4b0e-8a31-5e6d7f8a9b00"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
SIGNED_URL = "https://bucket.s3.example.com/signed?X-Amz-Signature=abc"


class _Response:
    """A `requests` streamed response: a context manager yielding fixed chunks."""

    def __init__(self, chunks: list[bytes], status_code: int = 200) -> None:
        self._chunks = chunks
        self.status_code = status_code

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Client Error")

    def iter_content(self, chunk_size: int):
        return iter(self._chunks)


def _html(*asset_ids: str) -> str:
    images = "".join(f'<image-component src="{a}" width="100%"></image-component>' for a in asset_ids)
    return f"<p>Steps to reproduce</p>{images}"


@pytest.fixture
def image_tool(registered, spy, monkeypatch):
    """The tool, with a work item that embeds ASSET and the network stubbed out."""
    monkeypatch.setattr(module, "get_plane_client_context", lambda: (spy, "acme"))
    spy.returns["work_items.retrieve"] = SimpleNamespace(description_html=_html(ASSET))
    spy.returns["workspaces.get_asset"] = SimpleNamespace(
        asset_id=ASSET, asset_url=SIGNED_URL, asset_name="screenshot.png", asset_type="image/png"
    )
    monkeypatch.setattr(module.requests, "get", lambda url, **kwargs: _Response([PNG]))

    def call(asset_id: str = ASSET):
        return registered["workitem"].fn(
            action="read_description_image", project_id=PROJECT, workitem_id=WORK_ITEM, asset_id=asset_id
        )

    return call, spy


def test_returns_an_image_for_an_embedded_png(image_tool):
    call, _ = image_tool

    result = call()

    assert isinstance(result, Image)
    assert result._mime_type == "image/png"
    assert result.data == PNG


@pytest.mark.parametrize("content_type", ["image/jpeg", "image/gif", "image/webp"])
def test_supports_the_other_readable_image_types(image_tool, content_type):
    call, spy = image_tool
    spy.returns["workspaces.get_asset"].asset_type = content_type

    assert call()._mime_type == content_type


def test_media_type_parameters_and_case_are_ignored(image_tool):
    call, spy = image_tool
    spy.returns["workspaces.get_asset"].asset_type = "Image/PNG; charset=binary"

    assert call()._mime_type == "image/png"


def test_type_falls_back_to_the_file_extension(image_tool):
    call, spy = image_tool
    spy.returns["workspaces.get_asset"].asset_type = ""

    assert call()._mime_type == "image/png"


def test_looks_up_the_asset_in_the_callers_workspace(image_tool):
    call, spy = image_tool

    call()

    lookup = next(c for c in spy.recorder.calls if c.method == "workspaces.get_asset")
    assert lookup.kwargs == {"workspace_slug": "acme", "asset_id": ASSET}


def test_refuses_an_asset_the_description_does_not_embed(image_tool):
    """The asset endpoint authorises on the workspace; the work item is what scopes it."""
    call, spy = image_tool

    with pytest.raises(ValueError, match="not an image in the description") as excinfo:
        call(OTHER_ASSET)

    assert ASSET in str(excinfo.value)
    assert "workspaces.get_asset" not in spy.recorder.methods


def test_refuses_when_the_description_has_no_images(image_tool):
    call, spy = image_tool
    spy.returns["work_items.retrieve"] = SimpleNamespace(description_html="<p>no pictures</p>")

    with pytest.raises(ValueError, match="Images in the description: none"):
        call()


def test_refuses_when_the_description_is_empty(image_tool):
    call, spy = image_tool
    spy.returns["work_items.retrieve"] = SimpleNamespace(description_html=None)

    with pytest.raises(ValueError, match="not an image in the description"):
        call()


def test_only_image_components_count(image_tool):
    """An attachment-component carrying the same id is a file, not an inline image."""
    call, spy = image_tool
    spy.returns["work_items.retrieve"] = SimpleNamespace(
        description_html=f'<attachment-component src="{ASSET}"></attachment-component>'
    )

    with pytest.raises(ValueError, match="not an image in the description"):
        call()


def test_a_self_closing_image_component_counts(image_tool):
    call, spy = image_tool
    spy.returns["work_items.retrieve"] = SimpleNamespace(description_html=f'<image-component src="{ASSET}" />')

    assert isinstance(call(), Image)


def test_a_missing_work_item_is_reported(image_tool):
    call, spy = image_tool

    spy.returns["work_items.retrieve"] = HttpError("HTTP 404: Not Found", 404, {"error": "Issue not found"})

    with pytest.raises(ValueError, match="Failed to fetch work item.*404"):
        call()


@pytest.mark.parametrize(
    "status_code, body",
    [(404, {"error": "Asset not found"}), (400, {"error": "Asset not yet uploaded"})],
)
def test_a_missing_or_unfinished_asset_is_reported(image_tool, status_code, body):
    call, spy = image_tool

    spy.returns["workspaces.get_asset"] = HttpError(f"HTTP {status_code}", status_code, body)

    with pytest.raises(ValueError, match=f"Failed to get image.*{status_code}"):
        call()


def test_a_null_download_url_is_reported(image_tool):
    """Plane answers 200 with a null url when it cannot presign the stored file."""
    call, spy = image_tool
    spy.returns["workspaces.get_asset"].asset_url = None

    with pytest.raises(ValueError, match="no download URL"):
        call()


@pytest.mark.parametrize("content_type", ["image/svg+xml", "application/pdf", "text/plain"])
def test_refuses_an_unsupported_type(image_tool, content_type):
    call, spy = image_tool
    spy.returns["workspaces.get_asset"].asset_type = content_type

    with pytest.raises(ValueError, match="Unsupported content type"):
        call()


def test_refuses_an_image_over_the_limit(image_tool, monkeypatch):
    call, _ = image_tool
    chunk = b"\x00" * (module.IMAGE_READ_LIMIT // 2 + 1)
    monkeypatch.setattr(module.requests, "get", lambda url, **kwargs: _Response([chunk, chunk, chunk]))

    with pytest.raises(ValueError, match="exceeds the 5 MB"):
        call()


def test_an_image_exactly_at_the_limit_is_read(image_tool, monkeypatch):
    call, _ = image_tool
    payload = b"\x00" * module.IMAGE_READ_LIMIT
    monkeypatch.setattr(module.requests, "get", lambda url, **kwargs: _Response([payload]))

    assert call().data == payload


def test_the_download_is_streamed_with_a_timeout(image_tool, monkeypatch):
    call, _ = image_tool
    seen: dict = {}

    def capture(url, **kwargs):
        seen.update(url=url, **kwargs)
        return _Response([PNG])

    monkeypatch.setattr(module.requests, "get", capture)

    call()

    assert seen == {"url": SIGNED_URL, "timeout": module.HTTP_TIMEOUT, "stream": True}


def test_chunks_are_joined_in_order(image_tool, monkeypatch):
    call, _ = image_tool
    monkeypatch.setattr(module.requests, "get", lambda url, **kwargs: _Response([PNG[:10], PNG[10:]]))

    assert call().data == PNG


@pytest.mark.parametrize("status_code", [403, 404])
def test_a_failed_download_is_reported(image_tool, monkeypatch, status_code):
    call, _ = image_tool
    monkeypatch.setattr(module.requests, "get", lambda url, **kwargs: _Response([], status_code))

    with pytest.raises(ValueError, match=f"Failed to fetch image.*{status_code}"):
        call()


def test_an_unreachable_storage_host_is_reported(image_tool, monkeypatch):
    call, _ = image_tool

    def refuse(url, **kwargs):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(module.requests, "get", refuse)

    with pytest.raises(ValueError, match="Failed to fetch image.*connection refused"):
        call()


def test_missing_asset_id_is_reported_without_calling_the_sdk(image_tool, registered, spy):
    result = registered["workitem"].fn(action="read_description_image", project_id=PROJECT, workitem_id=WORK_ITEM)

    assert isinstance(result, str) and result.startswith("Error:")
    assert "asset_id" in result
    assert not spy.recorder.calls
