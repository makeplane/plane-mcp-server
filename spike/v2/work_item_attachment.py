"""Consolidated `work_item_attachment` tool.

Collapses the 5 tools in plane_mcp/tools/work_item_attachments.py into a single
action-dispatch tool.

The upload_from_url path performs its own outbound HTTP fetch guarded by
`_assert_public_url`. That guard is a resolve-then-fetch check with a known
DNS-rebinding TOCTOU window (open SSRF advisory). It is ported here VERBATIM
and deliberately NOT fixed -- this spike measures schema size, not security.
"""

from __future__ import annotations

import ipaddress
import mimetypes
import os
import socket
from typing import Any
from urllib.parse import urlparse

import requests as _requests
from fastmcp import FastMCP
from fastmcp.utilities.types import Image
from plane.errors.errors import HttpError

from plane_mcp.client import get_plane_client_context
from spike.v2._common import bad_action, json_out, missing, opt

# ── Limits ────────────────────────────────────────────────────────────────────
_IMAGE_READ_LIMIT = 5 * 1024 * 1024  # 5 MB
_TEXT_READ_LIMIT = 1 * 1024 * 1024  # 1 MB
_UPLOAD_SIZE_LIMIT = 5 * 1024 * 1024  # 5 MB

# Connect timeout / read timeout tuple used for all outbound HTTP calls.
_HTTP_TIMEOUT = (10, 60)

# ── Supported MIME types ──────────────────────────────────────────────────────
_READABLE_IMAGE_TYPES: frozenset[str] = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
_READABLE_TEXT_TYPES: frozenset[str] = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/csv",
        "text/html",
        "text/xml",
        "text/yaml",
        "application/json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
    }
)

# ── Private / reserved network ranges (SSRF guard) ───────────────────────────
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),  # loopback
    ipaddress.ip_network("10.0.0.0/8"),  # RFC 1918
    ipaddress.ip_network("172.16.0.0/12"),  # RFC 1918
    ipaddress.ip_network("192.168.0.0/16"),  # RFC 1918
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / AWS metadata
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique-local
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
]

ACTIONS = ["list", "delete", "read", "upload_from_url", "get_download_url"]

DOC = """Manage work item attachments. Actions:
list (project_id, work_item_id) -> metadata for each attachment: id, name, size, content_type, created_at, created_by;
delete (project_id, work_item_id, attachment_id);
read (project_id, work_item_id, attachment_id) -> fetches the content so it can be read/analysed;
upload_from_url (project_id, work_item_id, url; optional name to override the filename);
get_download_url (project_id, work_item_id, attachment_id) -> time-limited presigned URL (~1 hour), no Plane auth needed on the URL.

Call list first to get attachment_id values.
read supports images (PNG/JPEG/GIF/WEBP, max 5 MB, returned as a vision-readable image) and text
(TXT/MD/CSV/HTML/XML/YAML/JSON, max 1 MB, returned as a string). PDFs, Office documents, audio,
video and generic binaries are NOT supported -- use get_download_url for those.
upload_from_url downloads the file server-side (max 5 MB); the source URL must be publicly
accessible without authentication and must not resolve to a private/internal network address."""


def _assert_public_url(url: str) -> None:
    """Raise ValueError if the URL resolves to a private/reserved IP address."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"Invalid URL (no hostname): {url!r}")

    try:
        resolved_ip = socket.getaddrinfo(hostname, None)[0][4][0]
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve hostname {hostname!r}: {exc}") from exc

    addr = ipaddress.ip_address(resolved_ip)
    if any(addr in net for net in _PRIVATE_NETWORKS):
        raise ValueError(
            f"URL {url!r} resolves to a private/reserved address ({resolved_ip}) "
            "and cannot be fetched for security reasons."
        )


def _attachment_to_dict(attachment: Any, workspace_slug: str) -> dict[str, Any]:
    data = attachment.model_dump()
    attrs = data.get("attributes") or {}
    data["name"] = attrs.get("name")
    data["size"] = attrs.get("size") or data.get("size")
    data["content_type"] = attrs.get("type")
    return data


def _list_attachments(client: Any, workspace_slug: str, project_id: str, work_item_id: str, what: str) -> Any:
    try:
        return client.work_items.attachments.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
        )
    except HttpError as e:
        raise ValueError(f"{what}: HTTP {e.status_code} — {e.response}") from e


def _dispatch(  # noqa: PLR0911, PLR0912, PLR0915
    action: str,
    project_id: str,
    work_item_id: str,
    attachment_id: str,
    url: str,
    name: str,
):
    if action not in ACTIONS:
        return bad_action(action, ACTIONS)
    if not project_id:
        return missing(action, "project_id")
    if not work_item_id:
        return missing(action, "work_item_id")

    if action == "upload_from_url":
        if not url:
            return missing(action, "url")

        _assert_public_url(url)

        try:
            resp = _requests.get(url, timeout=_HTTP_TIMEOUT)
            resp.raise_for_status()
        except _requests.RequestException as e:
            raise ValueError(f"Failed to fetch file from {url!r}: {e}") from e

        content_length = resp.headers.get("Content-Length")
        if content_length and int(content_length) > _UPLOAD_SIZE_LIMIT:
            raise ValueError(
                f"File at {url!r} is too large "
                f"({int(content_length) // 1024 // 1024} MB). "
                f"Maximum allowed size is {_UPLOAD_SIZE_LIMIT // 1024 // 1024} MB."
            )

        file_bytes = resp.content
        if len(file_bytes) > _UPLOAD_SIZE_LIMIT:
            raise ValueError(
                f"File at {url!r} is too large "
                f"({len(file_bytes) // 1024 // 1024} MB). "
                f"Maximum allowed size is {_UPLOAD_SIZE_LIMIT // 1024 // 1024} MB."
            )

        if opt(name):
            filename = name
        else:
            path = urlparse(url).path
            filename = os.path.basename(path.rstrip("/")) or "attachment"

        raw_ct = resp.headers.get("Content-Type", "")
        content_type = raw_ct.split(";")[0].strip() if raw_ct else ""
        if not content_type or content_type == "application/octet-stream":
            content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

        client, workspace_slug = get_plane_client_context()
        try:
            attachment = client.work_items.attachments.upload_from_bytes(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
                file_bytes=file_bytes,
                name=filename,
                content_type=content_type,
            )
        except HttpError as e:
            raise ValueError(f"Failed to upload attachment: HTTP {e.status_code} — {e.response}") from e

        return _attachment_to_dict(attachment, workspace_slug)

    client, workspace_slug = get_plane_client_context()

    if action == "list":
        attachments = _list_attachments(
            client, workspace_slug, project_id, work_item_id, "Failed to list attachments"
        )
        return [_attachment_to_dict(a, workspace_slug) for a in attachments]

    if not attachment_id:
        return missing(action, "attachment_id")

    if action == "delete":
        try:
            client.work_items.attachments.delete(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
                attachment_id=attachment_id,
            )
        except HttpError as e:
            raise ValueError(f"Failed to delete attachment: HTTP {e.status_code} — {e.response}") from e
        return None

    # Retrieve endpoint returns raw bytes, not JSON — use list for metadata.
    attachments = _list_attachments(
        client, workspace_slug, project_id, work_item_id, "Failed to fetch attachment metadata"
    )
    attachment = next((a for a in attachments if a.id == attachment_id), None)
    if attachment is None:
        raise ValueError(f"Attachment {attachment_id!r} not found on work item {work_item_id!r}")

    attrs = attachment.attributes or {}
    attachment_name = attrs.get("name") or attachment_id

    if action == "get_download_url":
        try:
            presigned_url = client.work_items.attachments.get_download_url(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
                attachment_id=attachment_id,
            )
        except HttpError as e:
            raise ValueError(f"Failed to get download URL: HTTP {e.status_code} — {e.response}") from e
        return {
            "download_url": presigned_url,
            "attachment_id": attachment_id,
            "name": attachment_name,
        }

    # read
    content_type = attrs.get("type") or ""

    # Fall back to guessing from the filename when the stored type is absent.
    if not content_type or content_type == "application/octet-stream":
        content_type = mimetypes.guess_type(attachment_name)[0] or "application/octet-stream"

    is_image = content_type in _READABLE_IMAGE_TYPES
    is_text = content_type in _READABLE_TEXT_TYPES

    if not is_image and not is_text:
        raise ValueError(
            f"Unsupported content type {content_type!r} for file {attachment_name!r}. "
            "Supported: PNG/JPEG/GIF/WEBP (images) and "
            "TXT/MD/CSV/HTML/XML/YAML/JSON (text). "
            "For PDFs and Office documents use action='get_download_url'."
        )

    # Get a fresh presigned S3 URL and fetch the bytes (no Plane auth to S3).
    try:
        presigned_url = client.work_items.attachments.get_download_url(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            attachment_id=attachment_id,
        )
    except HttpError as e:
        raise ValueError(f"Failed to get download URL: HTTP {e.status_code} — {e.response}") from e

    try:
        resp = _requests.get(presigned_url, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
    except _requests.RequestException as e:
        raise ValueError(f"Failed to fetch attachment content: {e}") from e

    file_bytes = resp.content
    size = len(file_bytes)

    if is_image:
        if size > _IMAGE_READ_LIMIT:
            raise ValueError(
                f"Image {attachment_name!r} is {size / 1024 / 1024:.1f} MB, "
                f"which exceeds the {_IMAGE_READ_LIMIT // 1024 // 1024} MB limit. "
                "Use action='get_download_url' to get a direct link instead."
            )
        # FastMCP Image(format=X) sets MIME to "image/X", so strip the prefix.
        fmt = content_type.removeprefix("image/")
        return Image(data=file_bytes, format=fmt)

    # Text path
    if size > _TEXT_READ_LIMIT:
        raise ValueError(
            f"Text file {attachment_name!r} is {size / 1024 / 1024:.1f} MB, "
            f"which exceeds the {_TEXT_READ_LIMIT // 1024 // 1024} MB limit. "
            "Use action='get_download_url' to get a direct link instead."
        )
    return file_bytes.decode("utf-8", errors="replace")


def register_typed(mcp: FastMCP) -> None:
    @mcp.tool(name="work_item_attachment", description=DOC)
    def _work_item_attachment(
        action: str,
        project_id: str = "",
        work_item_id: str = "",
        attachment_id: str = "",
        url: str = "",
        name: str = "",
    ) -> Image | dict[str, Any] | list[dict[str, Any]] | str | None:
        return _dispatch(action, project_id, work_item_id, attachment_id, url, name)


def register_str(mcp: FastMCP) -> None:
    @mcp.tool(name="work_item_attachment", description=DOC)
    def _work_item_attachment(
        action: str,
        project_id: str = "",
        work_item_id: str = "",
        attachment_id: str = "",
        url: str = "",
        name: str = "",
    ) -> str:
        try:
            result = _dispatch(action, project_id, work_item_id, attachment_id, url, name)
            if isinstance(result, Image):
                # The str variant has no image channel; the typed variant does.
                return (
                    "Error: this attachment is an image and cannot be returned by the "
                    "string-return variant of work_item_attachment. Use "
                    "action='get_download_url' to get a direct link instead."
                )
            return json_out(result)
        except Exception as e:  # noqa: BLE001 - surface readable errors to the model
            return f"Error: {type(e).__name__}: {e}"
