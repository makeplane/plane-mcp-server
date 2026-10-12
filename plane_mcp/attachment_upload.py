"""Adapter for the upload lifecycle in plane-sdk 0.3.1.

The public create method parses an upload envelope as attachment metadata.
The public update method parses a body after a 204 response. Use the same
raw calls as upload_from_bytes, with the connection's authenticated resource.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlsplit
from uuid import UUID

import requests
from plane.errors.errors import HttpError
from plane.models.work_items import WorkItemAttachmentUploadRequest

from plane_mcp.attachments import UPLOAD_SIZE_LIMIT

_TOKEN = r"[A-Za-z0-9!#$%&'*+.^_`|~-]+"
_MIME = re.compile(rf"{_TOKEN}/{_TOKEN}")


def validate_metadata(name: str, content_type: str, size: int) -> None:
    """Check metadata before an API call or a local file read."""
    if type(size) is not int or not 1 <= size <= UPLOAD_SIZE_LIMIT:
        raise ValueError(f"size must be an integer from 1 to {UPLOAD_SIZE_LIMIT} bytes.")
    try:
        name_bytes = len(name.encode("utf-8"))
    except UnicodeError:
        raise ValueError("name must contain valid UTF-8 characters.") from None
    if (
        not name.strip()
        or name in (".", "..")
        or name_bytes > 255
        or any(c in "/\\" or ord(c) < 32 or ord(c) == 127 for c in name)
    ):
        raise ValueError("name must be a filename without a path or control characters, up to 255 UTF-8 bytes.")
    if len(content_type) > 255 or not _MIME.fullmatch(content_type):
        raise ValueError("content_type must be a MIME type without parameters, such as image/png.")


def validate_upload_data(upload_data: dict) -> None:
    """Accept signed HTTPS POST instructions without Plane API credentials."""
    url, fields = upload_data["url"], upload_data["fields"]
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.port == 0
    ):
        raise ValueError("Invalid storage URL")
    if not isinstance(fields, dict) or not fields or "file" in fields:
        raise ValueError("Invalid signed fields")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in fields.items()):
        raise ValueError("Invalid signed fields")
    if any(k.lower() in {"authorization", "x-api-key", "x-workspace-slug"} for k in fields):
        raise ValueError("Plane credentials are not storage instructions")


def _path(workspace_slug: str, project_id: str, workitem_id: str, attachment_id: str = "") -> str:
    # New operations must not let path components escape their authorized scope.
    if not workspace_slug or any(c in workspace_slug for c in "/\\?#") or workspace_slug in (".", ".."):
        raise ValueError("Invalid workspace context.")
    ids = []
    for value in (project_id, workitem_id, *([attachment_id] if attachment_id else [])):
        try:
            ids.append(str(UUID(value)))
        except (ValueError, AttributeError, TypeError):
            raise ValueError("Project, work item, and attachment IDs must be UUIDs.") from None
    path = f"{quote(workspace_slug, safe='')}/projects/{ids[0]}/work-items/{ids[1]}/attachments"
    return f"{path}/{ids[2]}" if attachment_id else path


def begin_upload(resource, workspace_slug: str, project_id: str, workitem_id: str, **metadata) -> dict[str, Any]:
    """Create a pending attachment. Return Plane's signed POST instructions."""
    validate_metadata(**metadata)
    path = _path(workspace_slug, project_id, workitem_id)
    data = WorkItemAttachmentUploadRequest(
        name=metadata["name"], type=metadata["content_type"], size=metadata["size"]
    ).model_dump(exclude_none=True)
    attachment_id = ""
    try:
        raw = resource._post(path, data)
        attachment_id = str(UUID(raw["asset_id"]))
        upload_data = raw["upload_data"]
        validate_upload_data(upload_data)
    except HttpError as exc:
        raise ValueError(f"Upload initiation failed: HTTP {exc.status_code}. No transfer was confirmed.") from None
    except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError):
        if attachment_id:
            raise ValueError(
                f"Upload instructions are invalid. Pending attachment_id: {attachment_id}. "
                "Delete this attachment before retrying. No transfer or completion was attempted."
            ) from None
        raise ValueError(
            "Upload initiation is uncertain. A pending record can remain. No transfer or completion was attempted."
        ) from None
    return {
        "attachment_id": attachment_id,
        "upload_data": upload_data,
        "name": metadata["name"],
        "content_type": metadata["content_type"],
        "size": metadata["size"],
        "instructions": {
            "next_step": (
                "The attachment is pending. If the bundled plane_mcp.local_upload helper is available, "
                "use it to upload the local file. Otherwise, use curl, Python, Node.js, or another available "
                "client that can read the file and send a multipart HTTPS request. "
                "Follow the direct_upload instructions. If no suitable client is available, stop and notify "
                "the user that the upload cannot proceed. Call complete_upload only after a successful "
                "storage transfer. Do not complete after a failed or uncertain transfer."
            ),
            "helper": {
                "optional": True,
                "command": "python -m plane_mcp.local_upload /local/path/to/file < upload.json",
                "input": (
                    "Use Python from an environment with plane-mcp-server installed. "
                    "Save this entire result object as UTF-8 JSON in upload.json with mode 0600. "
                    "Do not save the MCP content wrapper. The helper needs no Plane API key. "
                    "Exit status 0 and transfer_succeeded=true confirm the storage transfer."
                ),
            },
            "direct_upload": {
                "clients": "Use curl, your own Python or Node.js script, or another multipart HTTP client.",
                "method": "POST",
                "url_from": "upload_data.url",
                "form_fields_from": "upload_data.fields",
                "file_part": {"field": "file", "filename_from": "name", "content_type_from": "content_type"},
                "steps": [
                    "Check the local file size before reading its bytes. "
                    f"The size must equal the returned size and be from 1 to {UPLOAD_SIZE_LIMIT} bytes. "
                    "Bound the read to this limit plus one guard byte.",
                    "Send an HTTPS multipart/form-data POST to upload_data.url. "
                    "Copy every upload_data.fields entry unchanged into a separate form field.",
                    "Add the binary file as a separate part named file. "
                    "Use name as its filename and content_type as its MIME type. "
                    "Let the HTTP client generate the multipart boundary.",
                    "Use a fresh HTTP client without Plane API credentials, cookies, or implicit authentication. "
                    "Disable automatic redirects. Set connection and transfer timeouts.",
                    "Confirm an HTTP 2xx storage response. A redirect, error response, or timeout is not success.",
                ],
            },
            "after_transfer": {
                "condition": "Call this only after storage returns HTTP 2xx, or the helper confirms transfer success.",
                "tool": "workitem_attachment",
                "arguments": {
                    "action": "complete_upload",
                    "project_id": str(UUID(project_id)),
                    "workitem_id": str(UUID(workitem_id)),
                    "attachment_id": attachment_id,
                    "transfer_succeeded": True,
                },
            },
            "failure_handling": [
                "If no suitable upload client is available, notify the user. Do not call complete_upload. "
                "Report the pending attachment ID.",
                "Do not call complete_upload after a failed or uncertain transfer. "
                "Notify the user of the failure or uncertainty.",
                "If storage rejects expired instructions, delete this pending attachment in the same scope. "
                "Then call begin_upload for new instructions.",
                "If completion is uncertain, check the same attachment before retrying completion. "
                "Do not create a duplicate attachment.",
            ],
            "verification": "Use list and download_url. Download and compare bytes when integrity matters. "
            "Completion confirms metadata. It does not prove byte integrity.",
            "privacy": "Keep the Plane API key in the MCP server. Do not send file paths or bytes to MCP. "
            "Do not log file bytes, signed URLs, or signed fields. Remove upload.json after use.",
        },
    }


def complete_upload(resource, workspace_slug: str, project_id: str, workitem_id: str, attachment_id: str):
    """Confirm completion. The caller must first confirm a successful transfer."""
    path = _path(workspace_slug, project_id, workitem_id, attachment_id)
    try:
        resource._patch(path, {"is_uploaded": True})
    except HttpError as exc:
        raise ValueError(
            f"Upload completion failed: HTTP {exc.status_code}. Check this attachment before retrying completion."
        ) from None
    except requests.RequestException:
        raise ValueError("Upload completion is uncertain. Check this attachment before retrying completion.") from None
    return {"attachment_id": attachment_id, "completion_confirmed": True}
