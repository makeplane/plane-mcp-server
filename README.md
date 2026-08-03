# Plane MCP Server

A Model Context Protocol (MCP) server for Plane integration. This server provides tools and resources for interacting with Plane through AI agents.

## Features

* 🔧 **Plane Integration**: Interact with Plane APIs and services
* 🔌 **Multiple Transports**: Supports stdio, SSE, and streamable HTTP transports
* 🌐 **Remote & Local**: Works both locally and as a remote service
* 🛠️ **Extensible**: Easy to add new tools and resources
* 📦 **Two tool surfaces**: the default 177-tool surface, or an opt-in consolidated 29-tool surface (`--v2`)

## Tool surfaces

The server ships two tool surfaces. They expose the same capabilities — the difference is how many tools those capabilities are spread across.

| | Tools | Select with | Status |
|---|---:|---|---|
| **v1** | 177 | *(default — nothing to add)* | Stable |
| **v2** | 29 | `--v2` | Experimental |

v2 collapses each resource's tools into a single action-dispatch tool — `list_labels`, `create_label`, `update_label`… all become `label` with an `action` parameter. This cuts the tool-list payload sent on every request from ~125k tokens to ~15–60k depending on configuration, and keeps the surface under the tool-count caps some MCP clients enforce.

**v1 remains the default**; adding `--v2` is the only way to change surfaces, so existing configurations are unaffected.

```bash
plane-mcp-server stdio           # 177 tools (default)
plane-mcp-server stdio --v2      #  29 tools
plane-mcp-server http --v2       # works with every transport
```

See [Migrating from v1 to v2](#migrating-from-v1-to-v2) for the full tool mapping, and [`plane_mcp/tools_v2/README.md`](plane_mcp/tools_v2/README.md) for the calling convention and known issues.

## Usage

The server supports three transport methods. **We recommend using `uvx`** as it doesn't require installation.

**Requirements**:
- **Python 3.10+** (for stdio transport, via `uvx`)
- **Node.js 22+** (for remote transports, via `npx`)

### 1. Stdio Transport (for local use)

**MCP Client Configuration** (using uvx - recommended):

```json
{
  "mcpServers": {
    "plane": {
      "command": "uvx",
      "args": ["plane-mcp-server", "stdio"],
      "env": {
        "PLANE_API_KEY": "<your-api-key>",
        "PLANE_WORKSPACE_SLUG": "<your-workspace-slug>",
        "PLANE_BASE_URL": "https://api.plane.so"
      }
    }
  }
}
```

<details>
<summary>Consolidated 29-tool surface (<code>--v2</code>)</summary>

Add `"--v2"` to `args`:

```json
{
  "mcpServers": {
    "plane": {
      "command": "uvx",
      "args": ["plane-mcp-server", "stdio", "--v2"],
      "env": {
        "PLANE_API_KEY": "<your-api-key>",
        "PLANE_WORKSPACE_SLUG": "<your-workspace-slug>",
        "PLANE_BASE_URL": "https://api.plane.so"
      }
    }
  }
}
```

To run unreleased changes straight from the repository:

```json
{
  "mcpServers": {
    "plane": {
      "command": "uvx",
      "args": [
        "--from", "git+https://github.com/makeplane/plane-mcp-server",
        "plane-mcp-server", "stdio", "--v2"
      ],
      "env": {
        "PLANE_API_KEY": "<your-api-key>",
        "PLANE_WORKSPACE_SLUG": "<your-workspace-slug>",
        "PLANE_BASE_URL": "https://api.plane.so"
      }
    }
  }
}
```

`uvx` caches git installs — add `--refresh` to pick up new commits, or pin a tag for reproducibility.

</details>

### 2. Remote HTTP Transport with OAuth

Connect to the hosted Plane MCP server using OAuth authentication.

**URL**: `https://mcp.plane.so/http/mcp`

**MCP Client Configuration** (for tools like Claude Desktop without native remote MCP support):

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

**Note**: OAuth authentication will be handled automatically when connecting to the remote server.

### 3. Remote HTTP Transport using PAT Token

Connect to the hosted Plane MCP server using a Personal Access Token (PAT).

**URL**: `https://mcp.plane.so/http/api-key/mcp`

**Headers**:
- `Authorization: Bearer <PAT_TOKEN>`
- `X-Workspace-slug: <SLUG>`

**MCP Client Configuration** (for tools like Claude Desktop without native remote MCP support):

```json
{
  "mcpServers": {
    "plane": {
      "command": "npx",
      "args": ["mcp-remote@latest", "https://mcp.plane.so/http/api-key/mcp"],
      "headers": {
        "Authorization": "Bearer <PAT_TOKEN>",
        "X-Workspace-slug": "<SLUG>"
      }
    }
  }
}
```

### 4. SSE Transport (Legacy)

⚠️ **Legacy Transport**: SSE (Server-Sent Events) transport is maintained for backward compatibility. New implementations should use the HTTP transport (sections 2 or 3) instead.

Connect to the hosted Plane MCP server using OAuth authentication via Server-Sent Events.

**URL**: `https://mcp.plane.so/sse`

**MCP Client Configuration** (for tools that support SSE transport):

```json
{
  "mcpServers": {
    "plane": {
      "command": "npx",
      "args": ["mcp-remote@latest", "https://mcp.plane.so/sse"]
    }
  }
}
```

**Note**: OAuth authentication will be handled automatically when connecting to the remote server. This transport is deprecated in favor of the HTTP transport.


## Configuration

### Authentication

The server requires authentication via environment variables:

- `PLANE_BASE_URL`: Base URL for Plane API (default: `https://api.plane.so`) - Optional
- `PLANE_API_KEY`: API key for authentication (required for stdio transport)
- `PLANE_WORKSPACE_SLUG`: Workspace slug identifier (required for stdio transport)
- `PLANE_ACCESS_TOKEN`: Access token for authentication (alternative to API key)

**Example** (for stdio transport):
```bash
export PLANE_BASE_URL="https://api.plane.so"
export PLANE_API_KEY="your-api-key"
export PLANE_WORKSPACE_SLUG="your-workspace-slug"
```

**Note**: For remote HTTP transports (OAuth or PAT), authentication is handled via the connection method (OAuth flow or PAT headers) and does not require these environment variables.

### OAuth redirect URIs

For the OAuth HTTP/SSE transports, the server validates each client's redirect URI against an allowlist. Common MCP clients (Cursor, VS Code, Claude.ai, ChatGPT connectors, localhost) are allowed by default.

To onboard a new client without a code change or release, append extra patterns via an environment variable:

- `PLANE_OAUTH_ALLOWED_REDIRECT_URIS`: Comma-separated redirect URI patterns appended to the built-in allowlist.

```bash
export PLANE_OAUTH_ALLOWED_REDIRECT_URIS="https://newclient.com/cb,https://other.app/oauth/*"
```

Patterns support glob matching (`*` matches any port, path segment, or subdomain). For security, keep the host pinned and wildcard only the port/path.

### Logging

The server emits structured JSON logs. Each tool call is logged with its tool name, duration, status, and (when available) the opaque user id and workspace slug.

- `LOG_USER_INFO`: When `true`, include user info (PII such as the display name) in logs alongside the opaque user id. Defaults to `false` so PII is never logged unless explicitly opted in. Only the OAuth and PAT (header) HTTP transports carry a display name; stdio is unaffected.

```bash
export LOG_USER_INFO="true"
```

## Available Tools

The server provides comprehensive tools for interacting with Plane. All tools use Pydantic models from the Plane SDK for type safety and validation.

> The tables below document the **default v1 surface (177 tools)**. Running with `--v2` replaces them with 29 consolidated tools covering the same capabilities — see [Migrating from v1 to v2](#migrating-from-v1-to-v2).

### Projects

| Tool Name | Description |
|-----------|-------------|
| `list_projects` | List all projects in a workspace with optional pagination and filtering |
| `create_project` | Create a new project with name, identifier, and optional configuration |
| `retrieve_project` | Retrieve a project by ID |
| `update_project` | Update a project with partial data |
| `delete_project` | Delete a project by ID |
| `get_project_worklog_summary` | Get work log summary for a project |
| `get_project_members` | Get all members of a project |
| `update_project_features` | Update features configuration of a project |

### Work Items

| Tool Name | Description |
|-----------|-------------|
| `list_work_items` | List all work items in a project with optional filtering and pagination |
| `create_work_item` | Create a new work item with name, assignees, labels, and other attributes |
| `retrieve_work_item` | Retrieve a work item by ID with optional field expansion |
| `retrieve_work_item_by_identifier` | Retrieve a work item by project identifier and issue sequence number |
| `update_work_item` | Update a work item with partial data |
| `delete_work_item` | Delete a work item by ID |
| `search_work_items` | Search work items across a workspace with query string |

### Cycles

| Tool Name | Description |
|-----------|-------------|
| `list_cycles` | List cycles in a project (set `archived=true` for archived) |
| `create_cycle` | Create a new cycle with name, dates, and owner |
| `retrieve_cycle` | Retrieve a cycle by ID |
| `update_cycle` | Update a cycle with partial data |
| `delete_cycle` | Delete a cycle by ID |
| `manage_cycle_work_items` | Add and/or remove work items on a cycle |
| `list_cycle_work_items` | List work items in a cycle |
| `transfer_cycle_work_items` | Transfer work items from one cycle to another |
| `manage_cycle_archive` | Archive or unarchive a cycle |

### Modules

| Tool Name | Description |
|-----------|-------------|
| `list_modules` | List modules in a project (set `archived=true` for archived) |
| `create_module` | Create a new module with name, dates, status, and members |
| `retrieve_module` | Retrieve a module by ID |
| `update_module` | Update a module with partial data |
| `delete_module` | Delete a module by ID |
| `manage_module_work_items` | Add and/or remove work items on a module |
| `list_module_work_items` | List work items in a module |
| `manage_module_archive` | Archive or unarchive a module |

### Initiatives

| Tool Name | Description |
|-----------|-------------|
| `list_initiatives` | List all initiatives in a workspace |
| `create_initiative` | Create a new initiative with name, dates, state, and lead |
| `retrieve_initiative` | Retrieve an initiative by ID |
| `update_initiative` | Update an initiative with partial data |
| `delete_initiative` | Delete an initiative by ID |

### Intake Work Items

| Tool Name | Description |
|-----------|-------------|
| `list_intake_work_items` | List all intake work items in a project with optional pagination |
| `create_intake_work_item` | Create a new intake work item in a project |
| `retrieve_intake_work_item` | Retrieve an intake work item by work item ID with optional field expansion |
| `update_intake_work_item` | Update an intake work item with partial data |
| `delete_intake_work_item` | Delete an intake work item by work item ID |

### Work Item Properties

| Tool Name | Description |
|-----------|-------------|
| `list_work_item_properties` | List work item properties for a work item type |
| `create_work_item_property` | Create a new work item property with type, settings, and validation rules |
| `retrieve_work_item_property` | Retrieve a work item property by ID |
| `update_work_item_property` | Update a work item property with partial data |
| `delete_work_item_property` | Delete a work item property by ID |

### Milestones

| Tool Name | Description |
|-----------|-------------|
| `list_milestones` | List all milestones in a project |
| `create_milestone` | Create a new milestone |
| `retrieve_milestone` | Retrieve a milestone by ID |
| `update_milestone` | Update a milestone by ID |
| `delete_milestone` | Delete a milestone by ID |
| `manage_milestone_work_items` | Add and/or remove work items on a milestone |
| `list_milestone_work_items` | List work items in a milestone |

### Labels

| Tool Name | Description |
|-----------|-------------|
| `list_labels` | List all labels in a project |
| `create_label` | Create a new label |
| `retrieve_label` | Retrieve a label by ID |
| `update_label` | Update a label by ID |
| `delete_label` | Delete a label by ID |

### States

| Tool Name | Description |
|-----------|-------------|
| `list_states` | List all states in a project |
| `create_state` | Create a new state |
| `retrieve_state` | Retrieve a state by ID |
| `update_state` | Update a state by ID |
| `delete_state` | Delete a state by ID |

### Work Item Comments

| Tool Name | Description |
|-----------|-------------|
| `list_work_item_comments` | List comments for a work item |
| `retrieve_work_item_comment` | Retrieve a specific comment for a work item |
| `create_work_item_comment` | Create a comment for a work item |
| `update_work_item_comment` | Update a comment for a work item |
| `delete_work_item_comment` | Delete a comment for a work item |

### Work Item Links

| Tool Name | Description |
|-----------|-------------|
| `list_work_item_links` | List links for a work item |
| `retrieve_work_item_link` | Retrieve a specific link for a work item |
| `create_work_item_link` | Create a link for a work item |
| `update_work_item_link` | Update a link for a work item |
| `delete_work_item_link` | Delete a link for a work item |

### Work Item Types

| Tool Name | Description |
|-----------|-------------|
| `list_work_item_types` | List all work item types in a project |
| `create_work_item_type` | Create a new work item type |
| `retrieve_work_item_type` | Retrieve a work item type by ID |
| `update_work_item_type` | Update a work item type by ID |
| `delete_work_item_type` | Delete a work item type by ID |
| `import_work_item_types_to_project` | Bulk-link workspace-level work item types to a project |
| `resolve_work_item_type` | Find or create a named type for a project, auto-handling workspace vs project scope and import |

### Work Item Relations

| Tool Name | Description |
|-----------|-------------|
| `list_work_item_relations` | List relations for a work item |
| `create_work_item_relation` | Create relations for a work item |
| `remove_work_item_relation` | Remove a relation from a work item |

### Work Item Relation Definitions

| Tool Name | Description |
|-----------|-------------|
| `list_work_item_relation_definitions` | List workspace custom relation definitions |
| `create_work_item_relation_definition` | Create a workspace relation definition |
| `update_work_item_relation_definition` | Update a relation definition |
| `delete_work_item_relation_definition` | Delete a relation definition |

### Work Item Activities

| Tool Name | Description |
|-----------|-------------|
| `list_work_item_activities` | List activities for a work item |
| `retrieve_work_item_activity` | Retrieve a specific activity for a work item |

### Work Logs

| Tool Name | Description |
|-----------|-------------|
| `list_work_logs` | List work logs for a work item |
| `create_work_log` | Create a work log for a work item |
| `update_work_log` | Update a work log for a work item |
| `delete_work_log` | Delete a work log for a work item |

### Pages

| Tool Name | Description |
|-----------|-------------|
| `list_pages` | List pages (workspace, or a project's if `project_id` given) |
| `retrieve_page` | Retrieve a page by ID (workspace, or project's if `project_id` given) |
| `create_page` | Create a workspace or project page |

### Workspaces

| Tool Name | Description |
|-----------|-------------|
| `get_workspace_members` | Get all members of the current workspace |
| `get_features` | Get feature flags (workspace, or a project's if `project_id` given) |
| `update_workspace_features` | Update features of the current workspace |

### Users

| Tool Name | Description |
|-----------|-------------|
| `get_me` | Get current authenticated user information |

**Total Tools**: 177 tools across 20 categories

## Migrating from v1 to v2

v2 exposes the same capabilities through 29 tools instead of 177. Every v1 tool has a v2 equivalent — nothing was dropped.

### 1. Switch surfaces

Add `--v2` to your server arguments. Nothing else in your configuration changes:

```diff
- "args": ["plane-mcp-server", "stdio"]
+ "args": ["plane-mcp-server", "stdio", "--v2"]
```

### 2. Translate your calls

Each v1 tool becomes an `action` on its resource tool:

```jsonc
// v1
list_labels   { "project_id": "<uuid>" }
create_label  { "project_id": "<uuid>", "name": "bug", "color": "#ef4444" }
update_label  { "project_id": "<uuid>", "label_id": "<uuid>", "color": "#3b82f6" }

// v2 — one tool, an action parameter
label { "action": "list",   "project_id": "<uuid>" }
label { "action": "create", "project_id": "<uuid>", "name": "bug", "color": "#ef4444" }
label { "action": "update", "project_id": "<uuid>", "label_id": "<uuid>", "color": "#3b82f6" }
```

Each v2 tool's **description** lists every action with its required and optional parameters. That is the authoritative reference: JSON Schema cannot express "required for *this* action", so the required-parameter contract lives in the description and is enforced at runtime with readable errors:

```
Error: unknown action 'bogus'. Must be one of: list, retrieve, create, update, delete.
Error: action 'retrieve' requires: work_item_id.
```

### 3. Tool mapping

| v2 tool | replaces |
|---|---|
| `customer` | `list_customers`, `retrieve_customer`, `create_customer`, `update_customer`, `delete_customer`, `list_customer_work_items`, `manage_customer_work_items` |
| `customer_property` | `list_customer_properties`, `retrieve_customer_property`, `create_customer_property`, `update_customer_property`, `delete_customer_property`, `get_customer_property_values`, `set_customer_property_values` |
| `customer_request` | `list_customer_requests`, `retrieve_customer_request`, `create_customer_request`, `update_customer_request`, `delete_customer_request` |
| `cycle` | `list_cycles`, `retrieve_cycle`, `create_cycle`, `update_cycle`, `delete_cycle`, `complete_cycle`, `manage_cycle_archive`, `list_cycle_work_items`, `manage_cycle_work_items`, `transfer_cycle_work_items` |
| `get_pql_reference` | `get_pql_reference` *(unchanged — no `action` parameter)* |
| `initiative` | `list_initiatives`, `retrieve_initiative`, `create_initiative`, `update_initiative`, `delete_initiative`, `list_initiative_projects`, `manage_initiative_projects` |
| `intake` | `list_intake_work_items`, `retrieve_intake_work_item`, `create_intake_work_item`, `update_intake_work_item`, `delete_intake_work_item` |
| `label` | `list_labels`, `retrieve_label`, `create_label`, `update_label`, `delete_label` |
| `member` | `get_me`, `get_workspace_members`, `get_project_members`, `list_roles`, `retrieve_role` |
| `milestone` | `list_milestones`, `retrieve_milestone`, `create_milestone`, `update_milestone`, `delete_milestone`, `list_milestone_work_items`, `manage_milestone_work_items` |
| `module` | `list_modules`, `retrieve_module`, `create_module`, `update_module`, `delete_module`, `manage_module_archive`, `list_module_work_items`, `manage_module_work_items` |
| `page` | `list_pages`, `retrieve_page`, `create_page`, `list_work_item_pages`, `attach_page_to_work_item`, `detach_page_from_work_item` |
| `project` | `list_projects`, `retrieve_project`, `create_project`, `update_project`, `delete_project`, `manage_project_archive`, `update_project_features`, `get_project_worklog_summary` |
| `project_estimate` | `get_project_estimate`, `create_project_estimate`, `update_project_estimate`, `delete_project_estimate`, `list_project_estimate_points`, `create_project_estimate_points`, `update_project_estimate_point`, `delete_project_estimate_point`, `link_estimate_to_project` |
| `release` | `list_releases`, `retrieve_release`, `create_release`, `update_release`, `delete_release`, `get_release_changelog`, `update_release_changelog`, `list_release_work_items`, `manage_release_work_items` |
| `release_label` | `list_release_labels`, `create_release_label`, `update_release_label`, `delete_release_label`, `manage_release_labels` |
| `release_tag` | `list_release_tags`, `retrieve_release_tag`, `create_release_tag`, `update_release_tag`, `delete_release_tag` |
| `state` | `list_states`, `retrieve_state`, `create_state`, `update_state`, `delete_state` |
| `work_item` | `list_work_items`, `list_archived_work_items`, `retrieve_work_item`, `retrieve_work_item_by_identifier`, `search_work_items`, `count_work_items`, `create_work_item`, `update_work_item`, `delete_work_item`, `manage_work_item_archive`, `manage_work_item_assignee`, `manage_work_item_label` |
| `work_item_activity` | `list_work_item_activities`, `retrieve_work_item_activity` |
| `work_item_attachment` | `list_work_item_attachments`, `read_work_item_attachment`, `upload_work_item_attachment_from_url`, `get_work_item_attachment_download_url`, `delete_work_item_attachment` |
| `work_item_comment` | `list_work_item_comments`, `retrieve_work_item_comment`, `create_work_item_comment`, `update_work_item_comment`, `delete_work_item_comment` |
| `work_item_link` | `list_work_item_links`, `retrieve_work_item_link`, `create_work_item_link`, `update_work_item_link`, `delete_work_item_link` |
| `work_item_property` | `list_work_item_properties`, `retrieve_work_item_property`, `create_work_item_property`, `update_work_item_property`, `delete_work_item_property`, `list_work_item_property_options`, `retrieve_work_item_property_option`, `create_work_item_property_option`, `update_work_item_property_option`, `delete_work_item_property_option`, `manage_work_item_type_properties` |
| `work_item_property_value` | `get_work_item_property_value`, `set_work_item_property_value`, `delete_work_item_property_value` |
| `work_item_relation` | `list_work_item_relations`, `create_work_item_relation`, `remove_work_item_relation`, `list_work_item_relation_definitions`, `create_work_item_relation_definition`, `update_work_item_relation_definition`, `delete_work_item_relation_definition` |
| `work_item_type` | `list_work_item_types`, `retrieve_work_item_type`, `create_work_item_type`, `update_work_item_type`, `delete_work_item_type`, `resolve_work_item_type`, `import_work_item_types_to_project` |
| `work_log` | `list_work_logs`, `create_work_log`, `update_work_log`, `delete_work_log` |
| `workspace` | `get_features`, `update_workspace_features` |

**177 v1 tools → 29 v2 tools. All accounted for.**

### 4. Differences to be aware of

- **`action` is a reserved parameter name.** Four v1 tools had their own `action` parameter (`manage_release_work_items`, `manage_release_labels`, `manage_customer_work_items`, `manage_initiative_projects`). Those sub-verbs are now `operation` or `op` — check the tool description.
- **`manage_*_archive` splits into two actions**, `archive` and `unarchive`, rather than a boolean flag.
- **`manage_*_work_items` keeps its combined shape** as one `manage_work_items` action, because the v1 tool supports adding and removing in a single call.
- **Some `Literal` enums are now plain strings** (`release.status`, `cycle.list.status`, `member.namespace`). Valid values are in the tool description; invalid input fails at validation time rather than being rejected by the schema.
- **`release` and `release_tag` list actions return `.results`** rather than the full paginated envelope, so `next_cursor` is not exposed. Being addressed.

`--v2` is experimental. Write paths are only partly tested, and [`plane_mcp/tools_v2/README.md`](plane_mcp/tools_v2/README.md) lists the current known issues. Switching back is removing the flag.

## Development

### Running Tests

```bash
pytest
```

### Code Formatting

```bash
black plane_mcp/
ruff check plane_mcp/
```

## License

MIT License - see LICENSE for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Deprecation Notice

⚠️ **The Node.js-based `plane-mcp-server` is deprecated and no longer maintained.**

This repository represents the new Python+FastMCP based implementation of the Plane MCP server. If you were using the previous Node.js version, please migrate to this Python-based version for continued support and updates.

The new implementation offers:
- Better type safety with Pydantic models
- Improved performance with FastMCP
- Enhanced tool coverage
- Active maintenance and development

For migration assistance, please refer to the configuration examples in this README or open an issue for support.

**Old Node.js Configuration (Deprecated):**

If you were using the previous Node.js-based `@makeplane/plane-mcp-server`, your configuration looked like this:

```json
{
  "mcpServers": {
    "plane": {
      "command": "npx",
      "args": [
        "-y",
        "@makeplane/plane-mcp-server"
      ],
      "env": {
        "PLANE_API_KEY": "<YOUR_API_KEY>",
        "PLANE_API_HOST_URL": "<HOST_URL_FOR_SELF_HOSTED>",
        "PLANE_WORKSPACE_SLUG": "<YOUR_WORKSPACE_SLUG>"
      }
    }
  }
}
```

**Please migrate to the new Python-based configuration shown in the Usage section above.**

