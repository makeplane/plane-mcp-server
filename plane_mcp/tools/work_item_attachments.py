"""Work item attachment tools for Plane MCP Server."""

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


def register_work_item_attachment_tools(mcp: FastMCP) -> None:
    """Register all work item attachment tools with the MCP server."""

    @mcp.tool()
    def list_work_item_attachments(
        project_id: str,
        work_item_id: str,
    ) -> list[dict[str, Any]]:
        """List all attachments for a work item.

        Returns metadata for each attachment. To get a downloadable link
        for a specific attachment, use get_work_item_attachment_download_url.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item

        Returns:
            List of attachments, each with: id, name, size, content_type,
            created_at, created_by.
        """
        client, workspace_slug = get_plane_client_context()
        try:
            attachments = client.work_items.attachments.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
            )
        except HttpError as e:
            raise ValueError(f"Failed to list attachments: HTTP {e.status_code} — {e.response}") from e
        return [_attachment_to_dict(a, workspace_slug) for a in attachments]

    @mcp.tool()
    def get_work_item_attachment_download_url(
        project_id: str,
        work_item_id: str,
        attachment_id: str,
    ) -> dict[str, Any]:
        """Get a presigned download URL for a work item attachment.

        Returns a time-limited URL (typically valid ~1 hour) that can be
        opened directly in a browser or downloaded with any HTTP client —
        no Plane authentication required on the URL itself.

        Use list_work_item_attachments first to get attachment IDs and names.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            attachment_id: UUID of the attachment

        Returns:
            Dict with: download_url (presigned S3 URL), attachment_id, name.
        """
        client, workspace_slug = get_plane_client_context()

        # Retrieve endpoint returns raw bytes, not JSON — use list for metadata.
        try:
            attachments = client.work_items.attachments.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
            )
        except HttpError as e:
            raise ValueError(f"Failed to fetch attachment metadata: HTTP {e.status_code} — {e.response}") from e

        attachment = next((a for a in attachments if a.id == attachment_id), None)
        if attachment is None:
            raise ValueError(f"Attachment {attachment_id!r} not found on work item {work_item_id!r}")

        attrs = attachment.attributes or {}
        name = attrs.get("name") or attachment_id

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
            "name": name,
        }

    @mcp.tool()
    def upload_work_item_attachment_from_url(
        project_id: str,
        work_item_id: str,
        url: str,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Fetch a file from a public URL and attach it to a work item.

        The MCP server downloads the file server-side and uploads it to Plane
        via the standard three-step presigned S3 flow. The source URL must be
        publicly accessible without authentication and must not resolve to a
        private/internal network address.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            url: Publicly accessible URL of the file to attach
                (e.g. a GitHub raw URL, public S3 link, or direct download link)
            name: Override the filename. Defaults to the filename in the URL path.

        Returns:
            Created attachment metadata: id, name, size, content_type.
        """
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

        if name:
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

    @mcp.tool()
    def delete_work_item_attachment(
        project_id: str,
        work_item_id: str,
        attachment_id: str,
    ) -> None:
        """Delete an attachment from a work item.

        Use list_work_item_attachments to get the attachment_id.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            attachment_id: UUID of the attachment to delete
        """
        client, workspace_slug = get_plane_client_context()
        try:
            client.work_items.attachments.delete(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
                attachment_id=attachment_id,
            )
        except HttpError as e:
            raise ValueError(f"Failed to delete attachment: HTTP {e.status_code} — {e.response}") from e

    @mcp.tool()
    def read_work_item_attachment(
        project_id: str,
        work_item_id: str,
        attachment_id: str,
    ) -> Image | str:
        """Fetch an attachment's content so the LLM can read or analyze it.

        Supported file types:
          Images (returned as vision-readable image, max 5 MB):
            PNG, JPEG, GIF, WEBP
          Text (returned as a string, max 1 MB):
            TXT, MD, CSV, HTML, XML, YAML, JSON

        Not supported (use get_work_item_attachment_download_url instead):
          PDF            — requires a text-extraction library (not installed)
          DOCX/XLSX/PPTX — binary Office formats, require extraction
          Audio / Video  — non-textual binary formats
          Generic binary — executables, archives, etc.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            attachment_id: UUID of the attachment

        Returns:
            Image object for image files, plain string for text files.

        Raises:
            ValueError: If the file type is unsupported or the file exceeds
                        the size limit for its category.
        """
        client, workspace_slug = get_plane_client_context()

        # Retrieve endpoint returns raw bytes — use list for name + content_type.
        try:
            attachments = client.work_items.attachments.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
            )
        except HttpError as e:
            raise ValueError(f"Failed to fetch attachment metadata: HTTP {e.status_code} — {e.response}") from e

        attachment = next((a for a in attachments if a.id == attachment_id), None)
        if attachment is None:
            raise ValueError(f"Attachment {attachment_id!r} not found on work item {work_item_id!r}")

        attrs = attachment.attributes or {}
        name = attrs.get("name") or attachment_id
        content_type = attrs.get("type") or ""

        # Fall back to guessing from the filename when the stored type is absent.
        if not content_type or content_type == "application/octet-stream":
            content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"

        is_image = content_type in _READABLE_IMAGE_TYPES
        is_text = content_type in _READABLE_TEXT_TYPES

        if not is_image and not is_text:
            raise ValueError(
                f"Unsupported content type {content_type!r} for file {name!r}. "
                "Supported: PNG/JPEG/GIF/WEBP (images) and "
                "TXT/MD/CSV/HTML/XML/YAML/JSON (text). "
                "For PDFs and Office documents use get_work_item_attachment_download_url."
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
                    f"Image {name!r} is {size / 1024 / 1024:.1f} MB, "
                    f"which exceeds the {_IMAGE_READ_LIMIT // 1024 // 1024} MB limit. "
                    "Use get_work_item_attachment_download_url to get a direct link instead."
                )
            # FastMCP Image(format=X) sets MIME to "image/X", so strip the prefix.
            fmt = content_type.removeprefix("image/")
            return Image(data=file_bytes, format=fmt)

        # Text path
        if size > _TEXT_READ_LIMIT:
            raise ValueError(
                f"Text file {name!r} is {size / 1024 / 1024:.1f} MB, "
                f"which exceeds the {_TEXT_READ_LIMIT // 1024 // 1024} MB limit. "
                "Use get_work_item_attachment_download_url to get a direct link instead."
            )
        return file_bytes.decode("utf-8", errors="replace")
