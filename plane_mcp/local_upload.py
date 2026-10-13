"""Upload a local file with Plane-issued instructions. No Plane API key is used.

Run: python -m plane_mcp.local_upload screenshot.png < upload.json
The input is the JSON object returned by workitem_attachment begin_upload.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from uuid import UUID

import requests

from plane_mcp.attachment_upload import validate_metadata, validate_upload_data
from plane_mcp.attachments import HTTP_TIMEOUT, UPLOAD_SIZE_LIMIT


def upload_file(path: Path, instructions: dict) -> dict:
    """Read a bounded local file. Send its bytes only to the signed storage URL.

    Args:
        path: Local file selected by the caller.
        instructions: Result of the begin_upload action.

    Returns:
        Attachment ID and transfer_succeeded=True after an HTTP 2xx storage
        response. This function does not call complete_upload.

    Raises:
        ValueError: Instructions are invalid; the file is unreadable, not
            regular, the wrong size, or changed during the read; storage
            rejects the instructions or returns a non-2xx response; or the
            transfer outcome is uncertain. Do not complete after a failure.
            Rejected instructions can be expired: delete the pending record
            and request new instructions. Check storage after uncertainty.
    """
    try:
        name, content_type, size = (instructions[k] for k in ("name", "content_type", "size"))
        validate_metadata(name, content_type, size)
        attachment_id = str(UUID(instructions["attachment_id"]))
        upload_data = instructions["upload_data"]
        validate_upload_data(upload_data)
        url, fields = upload_data["url"], upload_data["fields"]
    except (KeyError, TypeError, AttributeError, ValueError):
        raise ValueError("Invalid upload instructions. Request new instructions from begin_upload.") from None

    # Check the opened file, then bound the read in case the file changes.
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size != size:
                raise ValueError("The local file size differs from the upload metadata, or the file is not regular.")
            payload = source.read(UPLOAD_SIZE_LIMIT + 1)
    except OSError:
        raise ValueError("Cannot read the local file.") from None
    if len(payload) != size:
        raise ValueError("The local file changed. Request new instructions for its current size.")

    # A fresh session has no Plane headers or cookies. Disable netrc and proxy
    # environment credentials. Never redirect signed fields to another host.
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(
                url,
                data=fields,
                files={"file": (name, payload, content_type)},
                timeout=HTTP_TIMEOUT,
                allow_redirects=False,
                stream=True,
            )
            status = response.status_code
            response.close()
    except requests.RequestException:
        raise ValueError(
            "Storage transfer is uncertain. Do not complete the attachment. Check storage before retrying."
        ) from None
    if status in (401, 403):
        raise ValueError(
            "Storage rejected the instructions. They can be expired. Do not complete. Delete the pending attachment "
            "and request new instructions."
        )
    if not 200 <= status < 300:
        raise ValueError(f"Storage transfer failed: HTTP {status}. Do not complete the attachment.")
    return {"attachment_id": attachment_id, "transfer_succeeded": True}


def main() -> None:
    """Transfer the positional file using private begin_upload JSON on stdin.

    Read at most 65,537 text characters and reject inputs over 65,536. Success
    writes only the attachment ID and transfer confirmation as JSON to stdout.
    Input and transfer failures write a sanitized message to stderr and exit 1.
    argparse handles invalid CLI arguments separately. No signed values or
    file bytes are printed, and this command does not complete the attachment.
    """
    parser = argparse.ArgumentParser(description="Upload a local file using begin_upload JSON from standard input.")
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    try:
        # Signed instructions are small. Do not buffer arbitrary stdin input.
        raw = sys.stdin.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise ValueError("Upload instructions exceed the 64 KB limit.")
        instructions = json.loads(raw)
        if not isinstance(instructions, dict):
            raise ValueError("Upload instructions must be a JSON object.")
        result = upload_file(args.file, instructions)
    except UnicodeError:
        print("Invalid text encoding in instructions. Do not complete the attachment.", file=sys.stderr)
        raise SystemExit(1) from None
    except json.JSONDecodeError:
        # JSON parser errors can quote signed input.
        print("Invalid JSON instructions. Do not complete the attachment.", file=sys.stderr)
        raise SystemExit(1) from None
    except ValueError as exc:
        # upload_file raises fixed messages without input or network details.
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from None
    except OSError:
        print("Cannot read instructions. Do not complete the attachment.", file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
