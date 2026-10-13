"""Files attached to a work item.

`read` returns an image or text inline for the model; `download_url` returns a
presigned link for everything else. The return union must stay intact -- the
image channel is lost if this ever collapses to a string.
"""

from __future__ import annotations

import mimetypes
import os
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

import requests
from fastmcp import FastMCP
from fastmcp.utilities.types import Image
from plane.errors.errors import HttpError
from pydantic import Field

from plane_mcp.attachment_upload import begin_upload, complete_upload
from plane_mcp.attachments import (
    HTTP_TIMEOUT,
    IMAGE_READ_LIMIT,
    READABLE_IMAGE_TYPES,
    READABLE_TEXT_TYPES,
    TEXT_READ_LIMIT,
    UPLOAD_SIZE_LIMIT,
    UploadSizeLimitError,
    attachment_to_dict,
    fetch_validated_get,
)
from plane_mcp.client import get_plane_client_context
from plane_mcp.toolkit import Action, build_annotations, build_description, missing, needs

NAME = "workitem_attachment"
TITLE = "Work item attachments"

ACTIONS = (
    Action("list", ("project_id", "workitem_id"), read=True),
    Action(
        "read",
        ("project_id", "workitem_id", "attachment_id"),
        note="returns images and text inline; use download_url for anything else",
        read=True,
    ),
    Action("download_url", ("project_id", "workitem_id", "attachment_id"), read=True),
    Action("upload_from_url", ("project_id", "workitem_id", "url"), ("name",), open_world=True),
    Action("delete", ("project_id", "workitem_id", "attachment_id"), destructive=True),
    Action("begin_upload", ("project_id", "workitem_id", "name", "content_type", "size"), open_world=True),
    Action("complete_upload", ("project_id", "workitem_id", "attachment_id", "transfer_succeeded")),
)

FOOTER = (
    f"read supports PNG/JPEG/GIF/WEBP up to {IMAGE_READ_LIMIT // 1024 // 1024} MB and "
    f"TXT/MD/CSV/HTML/XML/YAML/JSON up to {TEXT_READ_LIMIT // 1024 // 1024} MB. "
    "Get attachment_id from the list action. upload_from_url fetches the file server-side, so "
    "the URL must be reachable without authentication and must not resolve to a private address. "
    "Local files: begin_upload creates a pending attachment and returns attachment_id, upload_data.url, "
    "upload_data.fields, name, content_type, size, and instructions. Follow the returned instructions for "
    "helper or direct uploads, failure handling, and the conditional completion call. "
    "The bundled local_upload helper is optional. "
    "The client can use curl, Python, Node.js, or any HTTP client that supports multipart uploads. "
    "Read the file on the client computer. Check that its actual size equals size and is from 1 to "
    f"{UPLOAD_SIZE_LIMIT} bytes. POST to upload_data.url over HTTPS with multipart/form-data. "
    "Copy every upload_data.fields entry unchanged into a form field. Add the binary file as a separate "
    "form part named file, with filename=name and MIME type=content_type. Use a fresh HTTP client without "
    "Plane API credentials, cookies, or implicit authentication. Do not follow redirects. "
    "Do not log file bytes, signed URLs, or signed form fields. Call complete_upload with "
    "transfer_succeeded=true only after an HTTP 2xx storage response. On failure or timeout, do not complete. "
    "For expired instructions, delete the pending attachment and begin again. If completion is uncertain, "
    "check the existing attachment before retrying completion for the same ID. Completion is a caller assertion. "
    "use download_url and compare bytes when integrity matters. The MCP server can be local or remote, "
    "but the client must be able to read files and send HTTP requests. "
    "Never send local paths or file bytes to this tool."
)

LEGACY = {
    "list_work_item_attachments": "list",
    "read_work_item_attachment": "read",
    "get_work_item_attachment_download_url": "download_url",
    "upload_work_item_attachment_from_url": "upload_from_url",
    "delete_work_item_attachment": "delete",
}


def _find(client, workspace_slug: str, project_id: str, workitem_id: str, attachment_id: str):
    """Locate attachment metadata. The retrieve endpoint returns bytes, so list instead."""
    try:
        attachments = client.work_items.attachments.list(
            workspace_slug=workspace_slug, project_id=project_id, work_item_id=workitem_id
        )
    except HttpError as exc:
        raise ValueError(f"Failed to fetch attachment metadata: HTTP {exc.status_code}.") from None
    attachment = next((a for a in attachments if a.id == attachment_id), None)
    if attachment is None:
        raise ValueError(f"Attachment {attachment_id!r} not found on work item {workitem_id!r}")
    return attachment


def _download_url(client, workspace_slug: str, project_id: str, workitem_id: str, attachment_id: str) -> str:
    """Return a signed download URL using the caller's authenticated scope.

    Keep the URL private. Plane HTTP failures raise ValueError with the status
    code and omit the response body.
    """
    try:
        return client.work_items.attachments.get_download_url(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=workitem_id,
            attachment_id=attachment_id,
        )
    except HttpError as exc:
        raise ValueError(f"Failed to get download URL: HTTP {exc.status_code}.") from None


def _read(client, workspace_slug: str, project_id: str, workitem_id: str, attachment_id: str):
    """Return an Image or decoded text after a scoped attachment lookup.

    Supported MIME types and image/text limits come from attachments.py.
    Unsupported types, oversized content, and HTTP fetch failures raise
    ValueError. Fetch errors omit signed URLs. This action returns file content
    to MCP; use download_url when only a local client needs the bytes.
    """
    attachment = _find(client, workspace_slug, project_id, workitem_id, attachment_id)
    attrs = attachment.attributes or {}
    name = attrs.get("name") or attachment_id
    content_type = attrs.get("type") or ""
    if not content_type or content_type == "application/octet-stream":
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"

    is_image = content_type in READABLE_IMAGE_TYPES
    is_text = content_type in READABLE_TEXT_TYPES
    if not is_image and not is_text:
        raise ValueError(
            f"Unsupported content type {content_type!r} for file {name!r}. Supported: "
            "PNG/JPEG/GIF/WEBP (images) and TXT/MD/CSV/HTML/XML/YAML/JSON (text). "
            "For PDFs and Office documents use the download_url action."
        )

    url = _download_url(client, workspace_slug, project_id, workitem_id, attachment_id)
    try:
        response = requests.get(url, timeout=HTTP_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException:
        raise ValueError("Failed to fetch attachment content. Request a new download URL and retry.") from None

    payload = response.content
    limit = IMAGE_READ_LIMIT if is_image else TEXT_READ_LIMIT
    if len(payload) > limit:
        raise ValueError(
            f"{name!r} is {len(payload) / 1024 / 1024:.1f} MB, which exceeds the "
            f"{limit // 1024 // 1024} MB limit. Use the download_url action instead."
        )
    if is_image:
        # FastMCP Image(format=X) sets the MIME type to "image/X", so strip the prefix.
        return Image(data=payload, format=content_type.removeprefix("image/"))
    return payload.decode("utf-8", errors="replace")


def _upload(client, workspace_slug: str, project_id: str, workitem_id: str, url: str, name: str):
    """Fetch a public source URL and return uploaded attachment metadata.

    The server fetch enforces address safety and the upload size limit. The
    scoped SDK call transfers and completes the attachment. Fetch and Plane
    HTTP failures raise sanitized ValueError messages. A storage or API request
    failure can leave the upload uncertain; check attachments before retrying.
    """
    try:
        response = fetch_validated_get(url, max_bytes=UPLOAD_SIZE_LIMIT)
        response.raise_for_status()
    except UploadSizeLimitError:
        raise ValueError("Source file exceeds the 5 MB upload limit.") from None
    except ValueError:
        raise ValueError(
            "Failed to fetch source file. Check URL access, address safety, size, and proxy settings."
        ) from None

    declared = response.headers.get("Content-Length")
    payload = response.content
    size = max(int(declared) if declared else 0, len(payload))
    if size > UPLOAD_SIZE_LIMIT:
        raise ValueError(
            f"Source file is too large ({size // 1024 // 1024} MB). Maximum allowed "
            f"size is {UPLOAD_SIZE_LIMIT // 1024 // 1024} MB."
        )

    filename = name or os.path.basename(urlparse(url).path.rstrip("/")) or "attachment"
    raw_type = response.headers.get("Content-Type", "")
    content_type = raw_type.split(";")[0].strip() if raw_type else ""
    if not content_type or content_type == "application/octet-stream":
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    try:
        attachment = client.work_items.attachments.upload_from_bytes(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=workitem_id,
            file_bytes=payload,
            name=filename,
            content_type=content_type,
        )
    except HttpError as exc:
        raise ValueError(f"Failed to upload attachment: HTTP {exc.status_code}.") from None
    except requests.RequestException:
        raise ValueError("Attachment upload is uncertain. Check attachments before retrying.") from None
    return attachment_to_dict(attachment, workspace_slug)


def register(mcp: FastMCP) -> None:
    @mcp.tool(
        name=NAME,
        description=build_description("Files attached to a work item.", ACTIONS, FOOTER),
        annotations=build_annotations(TITLE, ACTIONS),
    )
    def workitem_attachment(
        action: Literal["list", "read", "download_url", "upload_from_url", "delete", "begin_upload", "complete_upload"],
        project_id: str = "",
        workitem_id: str = "",
        attachment_id: str = "",
        url: str = "",
        name: str = "",
        content_type: str = "",
        size: Annotated[int, Field(strict=True)] = 0,
        transfer_succeeded: Annotated[bool, Field(strict=True)] = False,
    ) -> Image | list[dict[str, Any]] | dict[str, Any] | str | None:
        """Manage attachments in the current authenticated workspace.

        Args:
            action: Operation declared in ACTIONS.
            project_id: Project UUID; required for every action.
            workitem_id: Work-item UUID in the project; required for every action.
            attachment_id: Required for read, download_url, delete, and complete_upload.
            url: Public source URL required by upload_from_url.
            name: Required filename for begin_upload; optional display name for
                upload_from_url. begin_upload accepts no path or control characters.
            content_type: Parameter-free MIME type required by begin_upload.
            size: Integer byte count from 1 to 5,242,880, required by begin_upload.
            transfer_succeeded: True only after confirmed storage success;
                required by complete_upload. This is a caller assertion.

        Returns:
            list returns attachment metadata dictionaries. read returns an Image
            or decoded text. download_url returns a sensitive signed URL, ID,
            and name. upload_from_url returns uploaded attachment metadata.
            begin_upload returns pending identity, metadata, sensitive signed
            upload_data, and client instructions. complete_upload returns an ID
            and completion confirmation, not independent byte verification.
            delete returns None. Missing required arguments return an error string.

        Raises:
            ValueError: Metadata, scope, file access, transfer, or a Plane request
                fails. Lifecycle errors describe pending-record cleanup or
                checking the same attachment after uncertain completion.
        """
        client, workspace_slug = get_plane_client_context()

        if error := needs(action, project_id=project_id, workitem_id=workitem_id):
            return error

        if action == "list":
            try:
                attachments = client.work_items.attachments.list(
                    workspace_slug=workspace_slug, project_id=project_id, work_item_id=workitem_id
                )
            except HttpError as exc:
                raise ValueError(f"Failed to list attachments: HTTP {exc.status_code}.") from None
            return [attachment_to_dict(a, workspace_slug) for a in attachments]

        if action == "upload_from_url":
            if not url:
                return missing(action, "url")
            return _upload(client, workspace_slug, project_id, workitem_id, url, name)

        if action == "begin_upload":
            if error := needs(action, name=name, content_type=content_type, size=size):
                return error
            return begin_upload(
                client.work_items.attachments,
                workspace_slug,
                project_id,
                workitem_id,
                name=name,
                content_type=content_type,
                size=size,
            )

        if action == "complete_upload":
            if error := needs(action, attachment_id=attachment_id, transfer_succeeded=transfer_succeeded):
                return error
            if transfer_succeeded is not True:
                raise ValueError("transfer_succeeded must be true after a successful storage transfer.")
            return complete_upload(
                client.work_items.attachments, workspace_slug, project_id, workitem_id, attachment_id
            )

        if not attachment_id:
            return missing(action, "attachment_id")

        if action == "read":
            return _read(client, workspace_slug, project_id, workitem_id, attachment_id)

        if action == "download_url":
            attachment = _find(client, workspace_slug, project_id, workitem_id, attachment_id)
            attrs = attachment.attributes or {}
            return {
                "download_url": _download_url(client, workspace_slug, project_id, workitem_id, attachment_id),
                "attachment_id": attachment_id,
                "name": attrs.get("name") or attachment_id,
            }

        try:
            client.work_items.attachments.delete(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=workitem_id,
                attachment_id=attachment_id,
            )
        except HttpError as exc:
            raise ValueError(f"Failed to delete attachment: HTTP {exc.status_code}.") from None
        return None
