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
from plane_mcp.upload_instructions import build_upload_instructions

_TOKEN = r"[A-Za-z0-9!#$%&'*+.^_`|~-]+"
_MIME = re.compile(rf"{_TOKEN}/{_TOKEN}")


def validate_metadata(name: str, content_type: str, size: int) -> None:
    """Check metadata before an API call or a local file read.

    Args:
        name: Nonempty filename, up to 255 UTF-8 bytes. Paths, control
            characters, and the names . and .. are invalid.
        content_type: MIME type without parameters, up to 255 characters.
        size: Integer byte count from 1 to 5,242,880. Booleans are invalid.

    Returns:
        None when the metadata is valid.

    Raises:
        ValueError: The supplied metadata violates these limits.
    """
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
    """Check the structure of signed storage instructions.

    This check does not verify the signature or its expiry.

    Args:
        upload_data: Dictionary with an HTTPS url and a nonempty fields
            dictionary of string keys and values. The URL must have a host
            and no user information, fragment, or zero port. Fields must
            exclude file, authorization, x-api-key, and x-workspace-slug.

    Returns:
        None when the instruction structure is valid.

    Raises:
        ValueError: The URL or form fields violate the storage contract.
        KeyError: A required dictionary key is missing.
        TypeError: A supplied value has an unsupported type.
        AttributeError: An unsupported URL value cannot be parsed.
    """
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
    """Validate scope components and normalize UUIDs for a relative SDK endpoint.

    An attachment ID selects one record; otherwise the endpoint selects the
    work item's attachment collection. Invalid scope components raise ValueError.
    """
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
    """Create a pending attachment without transferring or completing a file.

    Args:
        resource: Attachment SDK resource with the current connection's auth.
        workspace_slug: Workspace from that authenticated connection.
        project_id: Project UUID within the workspace.
        workitem_id: Work-item UUID within the project.
        **metadata: Required name, content_type, and integer size in bytes.
            validate_metadata checks these before the API request.

    Returns:
        Dictionary with attachment_id, upload_data, name, content_type, size,
        and client instructions. upload_data contains temporary signed values;
        keep it private and do not log it.

    Raises:
        ValueError: Metadata or scope is invalid, Plane rejects initiation, or
            initiation is uncertain. A pending record can remain after an
            uncertain response. If returned instructions are invalid after a
            valid attachment ID is known, the safe error retains that ID.
            Delete that pending record in the same scope before retrying.
    """
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
        "instructions": build_upload_instructions(str(UUID(project_id)), str(UUID(workitem_id)), attachment_id),
    }


def complete_upload(resource, workspace_slug: str, project_id: str, workitem_id: str, attachment_id: str):
    """Confirm an attachment after the caller verifies storage transfer success.

    This helper does not enforce transfer evidence. The tool checks the caller's
    transfer_succeeded assertion before it calls this helper.

    Args:
        resource: Attachment SDK resource with the current connection's auth.
        workspace_slug: Workspace from that authenticated connection.
        project_id: Project UUID within the workspace.
        workitem_id: Work-item UUID within the project.
        attachment_id: Pending attachment UUID in this work-item scope.

    Returns:
        Dictionary with attachment_id and completion_confirmed=True. This
        confirms the API request, not file integrity. Compare downloaded bytes
        with the source when independent transfer proof is required.

    Raises:
        ValueError: Scope is invalid, Plane rejects completion, or completion
            is uncertain. Check this same attachment before retrying its
            completion; do not create another record to resolve uncertainty.
    """
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
