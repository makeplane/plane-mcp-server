# Plane MCP Server

A Model Context Protocol (MCP) server for Plane integration. This server provides tools and resources for interacting with Plane through AI agents.

## Features

* 🔧 **Plane Integration**: Interact with Plane APIs and services
* 🔌 **Multiple Transports**: Supports stdio, SSE, and streamable HTTP transports
* 🌐 **Remote & Local**: Works both locally and as a remote service
* 🛠️ **Extensible**: Easy to add new tools and resources

## Usage

The server supports three transport methods. **We recommend using `uvx`** as it doesn't require installation.

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

**URL**: `https://mcp.plane.so/api-key/mcp`

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

## Available Tools

The server provides comprehensive tools for interacting with Plane. All tools use Pydantic models from the Plane SDK for type safety and validation.

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
| `get_project_features` | Get features configuration of a project |
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
| `list_cycles` | List all cycles in a project |
| `create_cycle` | Create a new cycle with name, dates, and owner |
| `retrieve_cycle` | Retrieve a cycle by ID |
| `update_cycle` | Update a cycle with partial data |
| `delete_cycle` | Delete a cycle by ID |
| `list_archived_cycles` | List archived cycles in a project |
| `add_work_items_to_cycle` | Add work items to a cycle |
| `remove_work_item_from_cycle` | Remove a work item from a cycle |
| `list_cycle_work_items` | List work items in a cycle |
| `transfer_cycle_work_items` | Transfer work items from one cycle to another |
| `archive_cycle` | Archive a cycle |
| `unarchive_cycle` | Unarchive a cycle |

### Modules

| Tool Name | Description |
|-----------|-------------|
| `list_modules` | List all modules in a project |
| `create_module` | Create a new module with name, dates, status, and members |
| `retrieve_module` | Retrieve a module by ID |
| `update_module` | Update a module with partial data |
| `delete_module` | Delete a module by ID |
| `list_archived_modules` | List archived modules in a project |
| `add_work_items_to_module` | Add work items to a module |
| `remove_work_item_from_module` | Remove a work item from a module |
| `list_module_work_items` | List work items in a module |
| `archive_module` | Archive a module |
| `unarchive_module` | Unarchive a module |

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

### Users

| Tool Name | Description |
|-----------|-------------|
| `get_me` | Get current authenticated user information |

**Total Tools**: 55+ tools across 8 categories

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

