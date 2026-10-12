"""Client instructions and a credential-free standard-library upload example."""

from plane_mcp.attachments import UPLOAD_SIZE_LIMIT

# Keep this example executable. Regression tests run it with a mocked connection.
# HTTPSConnection does not follow redirects or use proxy/netrc authentication.
PYTHON_UPLOAD_EXAMPLE = (
    f"LIMIT = {UPLOAD_SIZE_LIMIT}\n"
    + """import http.client
import json
import os
import stat
import sys
from urllib.parse import urlsplit
from uuid import UUID, uuid4


def quote(value):
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError()
    return value.replace("\\\\", "\\\\\\\\").replace('"', '\\\\"')


def upload():
    raw = sys.stdin.read(65537)
    if len(raw) > 65536:
        raise ValueError()
    result = json.loads(raw)
    size = result["size"]
    if type(size) is not int or not 1 <= size <= LIMIT:
        raise ValueError()
    attachment_id = str(UUID(result["attachment_id"]))
    url = urlsplit(result["upload_data"]["url"])
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError()
    name, content_type = result["name"], result["content_type"]
    quote(name)
    quote(content_type)
    fields = result["upload_data"]["fields"]
    if not isinstance(fields, dict) or not fields or "file" in fields:
        raise ValueError()
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(sys.argv[1], flags), "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != size:
            raise ValueError()
        payload = source.read(LIMIT + 1)
    if len(payload) != size:
        raise ValueError()
    boundary = "plane-" + uuid4().hex
    parts = []
    for key, value in fields.items():
        if not isinstance(value, str) or key.lower() in ("authorization", "x-api-key", "x-workspace-slug"):
            raise ValueError()
        header = '--' + boundary + '\\r\\nContent-Disposition: form-data; name="' + quote(key) + '"\\r\\n\\r\\n'
        parts.append(header.encode("utf-8") + value.encode("utf-8") + b"\\r\\n")
    header = '--' + boundary + '\\r\\nContent-Disposition: form-data; name="file"; filename="' + quote(name)
    header += '"\\r\\nContent-Type: ' + content_type + '\\r\\n\\r\\n'
    parts.append(header.encode("utf-8") + payload + b"\\r\\n")
    parts.append(("--" + boundary + "--\\r\\n").encode("ascii"))
    connection = http.client.HTTPSConnection(url.hostname, url.port or 443, timeout=10)
    try:
        connection.connect()
        connection.sock.settimeout(60)
        target = (url.path or "/") + ("?" + url.query if url.query else "")
        connection.request("POST", target, body=b"".join(parts),
                           headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
        status = connection.getresponse().status
    finally:
        connection.close()
    if not 200 <= status < 300:
        print("Storage transfer failed: HTTP " + str(status) + ". Do not complete.", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps({"attachment_id": attachment_id, "transfer_succeeded": True, "http_status": status}))


try:
    upload()
except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, http.client.HTTPException):
    print("Transfer failed or is uncertain. Do not complete. Notify the user.", file=sys.stderr)
    raise SystemExit(1) from None
"""
)


def build_upload_instructions(project_id: str, workitem_id: str, attachment_id: str) -> dict:
    """Put client selection, secret handling, and completion conditions first."""
    return {
        "next_step": (
            "Follow these steps in order.\n"
            "1. Keep upload_data secret. Never print its URL or field values, even for debugging.\n"
            "2. If the bundled plane_mcp.local_upload helper is available, use helper.command.\n"
            "3. Otherwise, use direct_upload.python_stdlib or another client with all direct_upload settings.\n"
            "4. If no suitable client is available, stop. Send no_client.user_message to the user. "
            "Do not call complete_upload.\n"
            "5. Call after_transfer only after storage returns HTTP 2xx or the helper confirms success.\n"
            "6. Report the client, transfer result, completion result, and any blocker to the user."
        ),
        "required_rules": [
            "Inspect only metadata and field names. Never display upload_data, complete result JSON, "
            "signed download URLs, or file bytes.",
            "Read signed values inside the local upload process. Do not paste them into commands or source code.",
            "Disable redirects before transfer. Reject every HTTP 3xx response. "
            "Never enable redirects to fix an error.",
            "Keep Plane credentials in MCP. "
            "Use no API key, cookies, netrc authentication, or proxy credentials for storage.",
            "On a failed or uncertain transfer, stop without completion. Notify the user. Do not claim success.",
        ],
        "helper": {
            "optional": True,
            "command": "python -m plane_mcp.local_upload /local/path/to/file < upload.json",
            "input": "Use Python with plane-mcp-server installed. "
            "Save this entire result object as private UTF-8 JSON. "
            "Use mode 0600 for upload.json. Do not save the MCP content wrapper. Never display the file. "
            "The helper needs no Plane API key.",
            "success": "Require exit status 0 and transfer_succeeded=true. Then use after_transfer.",
        },
        "direct_upload": {
            "clients": "Prefer the supplied Python standard-library example when Python is available. "
            "You can use curl, Node.js, or another client if it satisfies every required rule.",
            "method": "POST",
            "url_from": "upload_data.url",
            "form_fields_from": "upload_data.fields",
            "file_part": {"field": "file", "filename_from": "name", "content_type_from": "content_type"},
            "follow_redirects": False,
            "use_environment_credentials": False,
            "max_size_bytes": UPLOAD_SIZE_LIMIT,
            "success_http_status": "200-299 only",
            "steps": [
                "Do not print signed values. Read upload_data only inside the transfer process.",
                f"Check the local file size before reading. Require size to match and be from 1 to {UPLOAD_SIZE_LIMIT} "
                "bytes. Bound the read to the limit plus one guard byte.",
                "Copy every upload_data.fields entry unchanged into a form field. "
                "Add a separate binary part named file. Use name and content_type for that part.",
                "Generate a multipart boundary. Send an HTTPS multipart/form-data POST to upload_data.url.",
                "Use a fresh client without credentials or implicit authentication. Set timeouts. Disable redirects.",
                "Require an HTTP 2xx response. On HTTP 3xx, error, or timeout, stop without completion. "
                "Notify the user.",
            ],
            "python_stdlib": {
                "usage": "Save code as upload.py. Use the command below. Do not insert signed values into the code. "
                "HTTPSConnection sends one request. It does not follow redirects or read proxy/netrc settings.",
                "command": "python3 upload.py /local/path/to/file < upload.json",
                "code": PYTHON_UPLOAD_EXAMPLE,
            },
            "other_clients": {
                "python_requests": "Use a new Session with trust_env=False and allow_redirects=False.",
                "python_urllib": "Install a redirect handler that rejects redirects. Use ProxyHandler({}). "
                "The default urllib opener follows redirects and must not be used.",
                "node_fetch": 'Set redirect="error". Send no authorization headers or cookies.',
                "curl": "Use -q as the first option. Disable proxies and implicit authentication. "
                "Use --proto '=https', --max-redirs 0, --connect-timeout 10, and --max-time 60. "
                "Do not use -L, --location, --location-trusted, -v, --verbose, or --trace. "
                "Read signed values from private input. Use --form-string for signed fields. Check the HTTP status.",
            },
        },
        "no_client": {
            "action": "Stop without transfer or completion. Notify the user with this message.",
            "user_message": "Upload cannot proceed: no suitable local upload client is available. "
            f"Attachment {attachment_id} is pending. No completion was attempted.",
        },
        "after_transfer": {
            "condition": "Call this only after an HTTP 2xx storage response "
            "or helper exit 0 with transfer_succeeded=true.",
            "tool": "workitem_attachment",
            "arguments": {
                "action": "complete_upload",
                "project_id": project_id,
                "workitem_id": workitem_id,
                "attachment_id": attachment_id,
                "transfer_succeeded": True,
            },
        },
        "failure_handling": [
            "If no suitable client is available, stop. Send no_client.user_message to the user.",
            "After a failed or uncertain transfer, do not complete. Notify the user. Report the pending attachment ID.",
            "If instructions expired, delete this pending attachment in the same scope. Then begin a new upload.",
            "If completion is uncertain, check the same attachment before retrying completion. "
            "Do not create a duplicate.",
        ],
        "verification": "Use list and download_url. Keep each download URL paired with its own attachment ID. "
        "Download privately and compare bytes when integrity matters. Completion does not prove byte integrity.",
        "privacy": "Never print signed upload or download values or file bytes. Keep credentials in MCP. "
        "Remove private instruction files after use.",
        "user_report": "Report the selected client, transfer evidence, completion status, byte comparison, "
        "and blockers. "
        "For no-client cases, send no_client.user_message. Do not omit the failure from the report.",
    }
