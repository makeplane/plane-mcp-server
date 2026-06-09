"""Work item attachment-related tools for Plane MCP Server."""

from typing import Any

from fastmcp import FastMCP
from plane.models.work_items import (
    UpdateWorkItemAttachment,
    WorkItemAttachment,
    WorkItemAttachmentUploadRequest,
)
from pydantic import BaseModel, ConfigDict

from plane_mcp.client import get_plane_client_context


class WorkItemAttachmentCreated(BaseModel):
    """Wrapper response from create_work_item_attachment.

    Plane's POST .../issue-attachments/ returns a wrapper containing both the
    attachment record and the S3 multipart-POST policy needed to upload the
    file bytes. The caller posts the file as multipart/form-data to
    ``upload_data["url"]`` with the ``upload_data["fields"]`` plus a ``file``
    part, then calls ``update_work_item_attachment`` with ``is_uploaded=True``.
    """

    model_config = ConfigDict(extra="allow")

    attachment: WorkItemAttachment
    upload_data: dict[str, Any]
    asset_id: str | None = None
    asset_url: str | None = None


def register_work_item_attachment_tools(mcp: FastMCP) -> None:
    """Register all work item attachment-related tools with the MCP server."""

    @mcp.tool()
    def list_work_item_attachments(
        project_id: str,
        work_item_id: str,
        params: dict[str, Any] | None = None,
    ) -> list[WorkItemAttachment]:
        """
        List attachments for a work item.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            params: Optional query parameters as a dictionary

        Returns:
            List of WorkItemAttachment objects
        """
        client, workspace_slug = get_plane_client_context()
        return client.work_items.attachments.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            params=params,
        )

    @mcp.tool()
    def create_work_item_attachment(
        project_id: str,
        work_item_id: str,
        name: str,
        size: int,
        mime_type: str | None = None,
        external_id: str | None = None,
        external_source: str | None = None,
    ) -> WorkItemAttachmentCreated:
        """
        Register an attachment for a work item and get a presigned upload URL.

        Plane attachments use a two-step asset flow. This tool creates the
        attachment record on the server and returns:

        * ``attachment`` — the created ``WorkItemAttachment`` record (with
          ``id``, ``asset`` storage path, ``is_uploaded=False``, etc.)
        * ``upload_data`` — an S3 multipart-POST policy. The caller posts the
          file as ``multipart/form-data`` to ``upload_data["url"]`` with the
          ``upload_data["fields"]`` plus a ``file`` part.
        * ``asset_id`` and ``asset_url`` — convenience identifiers.

        After the upload completes, call ``update_work_item_attachment`` with
        ``is_uploaded=True`` to mark the attachment ready.

        Note: this tool calls the underlying ``_post`` directly because the
        plane-sdk ``WorkItemAttachments.create()`` method incorrectly
        validates the wrapper response as ``WorkItemAttachment``. See
        plane-python-sdk for the upstream fix.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            name: Original filename of the asset
            size: File size in bytes
            mime_type: MIME type of the file
            external_id: External identifier for the asset
            external_source: External source system

        Returns:
            WorkItemAttachmentCreated wrapper with ``attachment`` record and
            ``upload_data`` S3 policy.
        """
        client, workspace_slug = get_plane_client_context()

        data = WorkItemAttachmentUploadRequest(
            name=name,
            size=size,
            type=mime_type,
            external_id=external_id,
            external_source=external_source,
        )

        raw = client.work_items.attachments._post(
            f"{workspace_slug}/projects/{project_id}/work-items/{work_item_id}/attachments",
            data.model_dump(exclude_none=True),
        )
        return WorkItemAttachmentCreated.model_validate(raw)

    @mcp.tool()
    def update_work_item_attachment(
        project_id: str,
        work_item_id: str,
        attachment_id: str,
        is_uploaded: bool,
    ) -> WorkItemAttachment:
        """
        Update an attachment for a work item.

        Typically used to confirm a successful binary upload by setting
        ``is_uploaded=True`` after the caller has POSTed the file bytes to
        the presigned URL returned by ``create_work_item_attachment``.

        Note: Plane responds to attachment PATCH with ``204 No Content`` and
        exposes no metadata-by-id endpoint (the GET on a single attachment
        URL is a download redirect). To return the updated record this tool
        follows the PATCH with a list call and filters by id, mirroring
        plane-python-sdk PR #34. Once that PR lands and the SDK pin is
        bumped here, this can switch to calling
        ``client.work_items.attachments.update`` directly.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            attachment_id: UUID of the attachment
            is_uploaded: Mark attachment as uploaded

        Returns:
            Updated WorkItemAttachment object.
        """
        if is_uploaded is not True:
            raise ValueError(
                "Only is_uploaded=True is currently supported (Plane lists/exposes only uploaded attachments)."
            )

        client, workspace_slug = get_plane_client_context()

        data = UpdateWorkItemAttachment(is_uploaded=True)

        client.work_items.attachments._patch(
            f"{workspace_slug}/projects/{project_id}/work-items/{work_item_id}/attachments/{attachment_id}",
            data.model_dump(exclude_none=True),
        )
        for attachment in client.work_items.attachments.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
        ):
            if attachment.id == attachment_id:
                return attachment
        raise ValueError(
            f"Attachment {attachment_id} not found after update; Plane only lists attachments with is_uploaded=True."
        )

    @mcp.tool()
    def delete_work_item_attachment(
        project_id: str,
        work_item_id: str,
        attachment_id: str,
    ) -> None:
        """
        Delete an attachment from a work item.

        Args:
            project_id: UUID of the project
            work_item_id: UUID of the work item
            attachment_id: UUID of the attachment

        Returns:
            None on success.
        """
        client, workspace_slug = get_plane_client_context()
        client.work_items.attachments.delete(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            attachment_id=attachment_id,
        )
