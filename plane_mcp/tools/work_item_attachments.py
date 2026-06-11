"""Work item attachment tools for Plane MCP Server."""

import mimetypes
import os
from typing import Any
from urllib.parse import urlparse

import requests as _requests
from fastmcp import FastMCP
from plane.errors.errors import HttpError

from plane_mcp.client import get_plane_client_context


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

        # Get filename from list (retrieve endpoint returns raw bytes, not JSON)
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
        publicly accessible without authentication.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            url: Publicly accessible URL of the file to attach
                (e.g. a GitHub raw URL, public S3 link, or direct download link)
            name: Override the filename. Defaults to the filename in the URL path.

        Returns:
            Created attachment metadata: id, name, size, content_type.
        """
        try:
            resp = _requests.get(url, timeout=60)
            resp.raise_for_status()
        except _requests.RequestException as e:
            raise ValueError(f"Failed to fetch file from {url!r}: {e}") from e

        file_bytes = resp.content

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
