# Plane MCP Server

A [Model Context Protocol](https://modelcontextprotocol.io) server for
[Plane](https://plane.so). Gives an AI agent tools to read and manage projects,
work items, cycles, modules, releases, customers and more.

Built on [FastMCP](https://github.com/jlowin/fastmcp) and the official
[`plane-sdk`](https://pypi.org/project/plane-sdk/).

- **30 tools**, one per Plane resource, covering 209 operations
- **Local or remote** — stdio, streamable HTTP, SSE
- **OAuth or API key** authentication

## Quick start

Get an API key from Plane: **Workspace Settings → API tokens**.

Add this to your MCP client's configuration:

```json
{
  "mcpServers": {
    "plane": {
      "command": "uvx",
      "args": ["plane-mcp-server", "stdio"],
      "env": {
        "PLANE_API_KEY": "<your-api-key>",
        "PLANE_WORKSPACE_SLUG": "<your-workspace-slug>"
      }
    }
  }
}
```

`uvx` needs no install step. Requires Python 3.10+.

For a self-hosted Plane, add `"PLANE_BASE_URL": "https://plane.example.com"`.

## Transports

### stdio — local

Runs as a subprocess of your MCP client. Configuration as shown above; needs
`PLANE_API_KEY` and `PLANE_WORKSPACE_SLUG`.

```bash
PLANE_API_KEY=... PLANE_WORKSPACE_SLUG=... uvx plane-mcp-server stdio
```

### HTTP with OAuth — hosted

`https://mcp.plane.so/http/mcp`

The OAuth flow is handled on connect; no credentials in your config. For clients
without native remote MCP support, bridge with `mcp-remote`:

```json
{
  "mcpServers": {
    "plane": {
      "command": "npx",
      "args": ["mcp-remote@latest", "https://mcp.plane.so/http/mcp"]
    }
  }
}
```

Requires Node.js 22+.

### HTTP with a personal access token — hosted

`https://mcp.plane.so/http/api-key/mcp`

| Header | Value |
|---|---|
| `Authorization` | `Bearer <PAT>` |
| `X-Workspace-slug` | `<workspace-slug>` |

```json
{
  "mcpServers": {
    "plane": {
      "command": "npx",
      "args": ["mcp-remote@latest", "https://mcp.plane.so/http/api-key/mcp"],
      "headers": {
        "Authorization": "Bearer <PAT>",
        "X-Workspace-slug": "<workspace-slug>"
      }
    }
  }
}
```

### SSE — deprecated

`https://mcp.plane.so/sse` is maintained for backward compatibility only. Use an
HTTP transport instead.

## Tools

The server advertises 30 tools, one per resource. Each takes an `action`
parameter that selects the operation:

```python
workitem(action="create", project_id=..., name="Fix login")
workitem(action="list", project_id=..., pql='state__group = "started"')
cycle(action="archive", project_id=..., cycle_id=...)
```

Every tool's description lists its actions with their required and optional
parameters, so the catalogue is self-documenting at call time.

**→ [Full tool and action reference](plane_mcp/tools/README.md)**

### Querying work items

List, count and search accept **PQL**, Plane's query language:

```python
workitem(action="list", project_id=..., pql='state__group = "started" AND priority = "urgent"')
workitem(action="count", pql='assignees__id = "<member id>"', group_by="state_id")
```

Call `get_pql_reference` for the full syntax, operators and worked examples.

### Upgrading from the per-operation tools

Earlier releases exposed one tool per API operation. **Existing integrations keep
working**: 169 of those 177 names still resolve to the consolidated tool, so a
saved prompt or script calling `create_work_item` or `list_cycles` needs no
change. They are no longer advertised, and they keep the parameter names they
shipped with (`work_item_id`, not `workitem_id`).

Seven names chose between two operations with a parameter
(`manage_project_archive(archive=False)`), which one tool-and-action pair cannot
reproduce; calling one tells you its replacement. `get_pql_reference` is
unchanged.

### Attach a local artifact

Use `begin_upload` and `complete_upload` on `workitem_attachment` to attach a local file.
The client sends the file directly to Plane storage with signed upload instructions.
Use curl, a Python or Node.js script, or another multipart HTTP client.
The bundled helper is optional. The MCP server receives metadata. It does not
read a local path or receive file bytes. Plane Cloud continues to store your project data.

This flow works with a local or remote MCP server. The client must be able to
read the file and send HTTP requests. A client with only MCP tool access needs
a separate file-transfer capability to complete the upload.

Install and configure this checkout instead of the published `uvx` package:

```bash
uv sync --python 3.12 --extra dev
```

```json
{
  "mcpServers": {
    "plane-local": {
      "command": "/absolute/path/plane-mcp-server/.venv/bin/python",
      "args": ["-m", "plane_mcp", "stdio"],
      "env": {
        "PLANE_API_KEY": "<your-api-key>",
        "PLANE_WORKSPACE_SLUG": "<your-workspace-slug>",
        "PLANE_BASE_URL": "https://api.plane.so",
        "LOG_PAYLOADS": "false"
      }
    }
  }
}
```

Use Python 3.12 for this SDK version. The full suite passes on Python 3.12.
Python 3.14 has SDK annotation failures in other resource operations.

Keep the API key in the server configuration. Do not place it in upload
instructions or pass it to the storage upload client.

1. Get the local file size in bytes. Call the tool with its metadata:

   ```python
   workitem_attachment(
       action="begin_upload",
       project_id="<project-uuid>",
       workitem_id="<work-item-uuid>",
       name="screenshot.png",
       content_type="image/png",
       size=12345,
   )
   ```

   The result contains `attachment_id`, `upload_data` (`url` and `fields`),
   `name`, `content_type`, `size`, and `instructions`. The attachment is pending. The size must
   be from 1 byte to 5 MB (5,242,880 bytes). Use a filename without a path.
   Plane also applies its file-type, storage, plan, and permission checks.

   Follow the returned `instructions` during execution. They include the optional
   helper command and the direct multipart request for curl or your own script.
   The `after_transfer` section includes the scoped `complete_upload` arguments.
   Use those arguments only after the stated transfer condition is satisfied.
   The result also includes failure, verification, and privacy steps.

2. Keep the result object in client memory or save it as a private JSON file.
   Use the result object, not the MCP content wrapper. If you save a file,
   use mode `0600`, such as `upload.json`. Treat signed instructions as temporary
   secrets. Do not commit or log them.

3. Send the file from the computer that has the screenshot. Follow the
   [client-side storage request](#client-side-storage-request) below.
   You can use curl or your own Python or Node.js script. Alternatively,
   run the optional bundled helper with the private JSON file:

   ```bash
   /absolute/path/plane-mcp-server/.venv/bin/python -m plane_mcp.local_upload \
     /local/path/screenshot.png < upload.json
   ```

   The helper reads at most 5 MB plus one guard byte. It checks the file size.
   It uses an HTTPS multipart POST with all Plane-issued form fields.
   It disables redirects, netrc authentication, and environment proxies.
   It sends no Plane API headers and does not read the storage response body.
   Its output contains only the attachment ID and `transfer_succeeded: true`.
   A failed transfer exits with status 1.

4. Only after storage returns an HTTP 2xx response, call:

   ```python
   workitem_attachment(
       action="complete_upload",
       project_id="<project-uuid>",
       workitem_id="<work-item-uuid>",
       attachment_id="<attachment-id-from-begin-upload>",
       transfer_succeeded=True,
   )
   ```

   Completion uses the current MCP authentication and workspace context.
   Plane checks the project, work item, and attachment scope. The server does
   not accept a workspace override. Completion returns `completion_confirmed`.
   The transfer flag is a caller assertion. It is not independent byte validation.

5. Use `list` and `download_url` to check the attachment. Download and compare
   bytes when integrity matters. Use `read` only when you want the model to
   receive the image or text. Remove the private instruction file.

#### Client-side storage request

An agent can implement this request without installing the bundled helper:

1. Check that the actual file size equals the returned `size` and is from
   1 to 5,242,880 bytes. Bound the local read to this limit plus one guard byte.
2. Send an HTTPS `POST` to `upload_data.url` with `multipart/form-data`.
3. Copy every `upload_data.fields` entry unchanged into a form field.
   Do not select only some fields or calculate replacement signatures.
4. Add the binary file as a separate form part named `file`.
   Set its filename to the returned `name` and its MIME type to `content_type`.
   Let the HTTP client generate the multipart boundary.
5. Use a fresh HTTP client without Plane API credentials, cookies, or implicit
   authentication. Disable automatic redirects. Do not log file bytes,
   signed URLs, or signed fields.
6. Confirm an HTTP 2xx storage response before calling `complete_upload`.
   A redirect, error response, or timeout must not produce a success receipt.

For curl, use multipart form options and a file part. A Python script can use
`requests` with `data` and `files`. A Node.js script can use `FormData` and
`fetch` with redirects disabled. These clients must follow all steps above.
The signed URL and form fields provide storage authorization. They contain
no Plane API key. Keep the API key in the MCP server.

If storage rejects expired instructions (usually HTTP 403), do not complete.
Delete the pending attachment with `delete`, then begin a new upload.
If initiation times out, no file transfer was attempted. Plane can still have
created a pending record. Pending records do not necessarily appear in `list`.
If Plane returned a valid attachment ID but its instructions were rejected,
the safe error includes `Pending attachment_id: <UUID>`. Use that ID with the
same project and work item to delete the pending record before retrying.
Signed instructions are omitted from the error. If no valid ID was received,
the error reports uncertain initiation. Do not assume an empty list proves
that no record was created.

A storage timeout leaves the transfer uncertain. Do not complete based on a timeout.
Check storage, or delete the pending attachment and restart the flow.

If completion times out, retain the attachment ID. Check `list` and download
its content before retrying `complete_upload` for the same ID. Do not begin
another upload merely because the completion response was lost. Plane's PATCH
returns 204 without a body. That response or an `is_uploaded` flag does not
prove that the stored file matches your local bytes.

Attachment call payloads and error details are omitted from structured server
logs, even when `LOG_PAYLOADS=true`. Do not enable HTTP wire debug logs in the
helper or write signed results to shared logs.

The lifecycle adapter follows the installed `plane-sdk==0.3.1`
`upload_from_bytes` implementation and Plane's
[upload instructions](https://developers.plane.so/api-reference/issue-attachments/upload-file)
and [completion contract](https://developers.plane.so/api-reference/issue-attachments/complete-upload).
It uses the SDK resource's authenticated raw POST and PATCH methods because
public `create` parses the upload envelope as a model, and public `update`
expects a model after the empty 204 response. Regression tests pin both wire
contracts. Review the adapter when you update the SDK.

## Configuration

### Authentication

| Variable | Required for | Purpose |
|---|---|---|
| `PLANE_API_KEY` | stdio | API key |
| `PLANE_WORKSPACE_SLUG` | stdio | Target workspace |
| `PLANE_BASE_URL` | optional | Plane API URL (default `https://api.plane.so`) |

The remote transports carry credentials in the connection — the OAuth flow or the
PAT headers — and need none of these.

### Outbound proxy for attachment uploads

`HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY` (and their lowercase forms) are honored
for the attachment-upload-from-URL fetch (`upload_from_url`). When a proxy
applies to a given request, **the proxy — not this server — is that request's
SSRF control**: the server does not pin or validate the target's resolved
address itself, since the proxy connects to and resolves the target
independently. Scope `NO_PROXY` and proxy egress policy accordingly.

Self-hosting the server itself:

| Variable | Purpose |
|---|---|
| `PLANE_INTERNAL_BASE_URL` | Internal URL for server-to-server calls, preferred over `PLANE_BASE_URL` |
| `REDIS_URL` | OAuth token storage as one connection URL (`redis://` or `rediss://` for TLS); wins over host/port |
| `REDIS_HOST` / `REDIS_PORT` | OAuth token storage; falls back to in-memory |
| `PLANE_OAUTH_PROVIDER_*` | OAuth client credentials and base URL |
| `MCP_PATH_PREFIX` | Path prefix for the HTTP routes, when mounted behind a proxy — `/plane` serves `/plane/http/mcp` |

### OAuth redirect URIs

The OAuth transports validate each client's redirect URI against an allowlist.
Common clients (Cursor, VS Code, Claude.ai, ChatGPT connectors, localhost) are
allowed by default.

To onboard a new client without a release, append patterns:

```bash
export PLANE_OAUTH_ALLOWED_REDIRECT_URIS="https://newclient.com/cb,https://other.app/oauth/*"
```

`*` matches any port, path segment or subdomain. Keep the host pinned and
wildcard only the port or path.

### Logging

Structured JSON. Each tool call logs its name, duration, status and — when
available — an opaque user id and the workspace slug.

```bash
export LOG_USER_INFO=false    # also log the display name (PII);
export LOG_PAYLOADS=false    # keep request payloads out of logs; default true
```

Only the OAuth and PAT transports carry a display name; stdio is unaffected.

## Development

```bash
git clone https://github.com/makeplane/plane-mcp-server
cd plane-mcp-server
uv pip install -e ".[dev]"
```

Run the server against a workspace:

```bash
PLANE_API_KEY=... PLANE_WORKSPACE_SLUG=... python -m plane_mcp stdio
python -m plane_mcp http            # port 8211
```

Tests, format, lint:

```bash
pytest                              # no network or credentials needed
ruff format plane_mcp/ tests/       # line length 120
ruff check plane_mcp/ tests/        # rules E, F, I, UP, B
```

The suite runs fully offline — every action of every resource is
executed against a stand-in that binds each call against the genuine `plane-sdk`
signature. See [`plane_mcp/tools/README.md`](plane_mcp/tools/README.md#tests).

Live integration tests are skipped unless you point them at a running server:

```bash
export PLANE_TEST_API_KEY=... PLANE_TEST_WORKSPACE_SLUG=...
export PLANE_TEST_MCP_URL=http://localhost:8211    # optional; this is the default
pytest tests/test_integration.py -v
```

They write real data to that workspace.

### Repository layout

| Path | Contents |
|---|---|
| `plane_mcp/__main__.py` | entry point; picks the transport from `argv[1]` |
| `plane_mcp/server.py` | one factory per transport |
| `plane_mcp/client.py` | resolves credentials into a `plane-sdk` client |
| `plane_mcp/auth/` | OAuth provider and header auth |
| `plane_mcp/tools/` | the tool surface: one module per Plane resource |
| `plane_mcp/toolkit/` | shared building blocks for the tool surface |
| `plane_mcp/pql_reference.py` | PQL syntax reference served to models |

## Contributing

Pull requests welcome. Please run `pytest` and `ruff check` before submitting; new
tools should come with the invariants described in
[`plane_mcp/tools/README.md`](plane_mcp/tools/README.md).

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Migrating from the Node.js server

`@makeplane/plane-mcp-server` (Node.js) is deprecated and unmaintained. This
Python implementation replaces it.

| Node.js | Python |
|---|---|
| `PLANE_API_KEY` | `PLANE_API_KEY` |
| `PLANE_API_HOST_URL` | `PLANE_BASE_URL` |
| `PLANE_WORKSPACE_SLUG` | `PLANE_WORKSPACE_SLUG` |

Replace the `command` and `args` with the stdio configuration in
[Quick start](#quick-start).

## License

MIT — see [LICENSE](LICENSE).
