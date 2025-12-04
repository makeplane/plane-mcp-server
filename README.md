# Plane MCP Server

A Model Context Protocol (MCP) server for Plane integration. This server provides tools and resources for interacting with Plane through AI agents.

## Features

* 🔧 **Plane Integration**: Interact with Plane APIs and services
* 🔌 **Multiple Transports**: Supports stdio, SSE, and streamable HTTP transports
* 🌐 **Remote & Local**: Works both locally and as a remote service
* 🛠️ **Extensible**: Easy to add new tools and resources

## Installation

### Using uvx (Recommended - No Installation Required)

`uvx` allows you to run the server directly without installing it globally. This is the recommended method for MCP clients:

```bash
uvx plane-mcp-server stdio
```

No installation needed! `uvx` will automatically download and run the package from PyPI.

### Installing with uv (Optional)

If you prefer to install the package globally:

```bash
uv pip install plane-mcp-server
```

Then you can run it using:
```bash
plane-mcp-server stdio
# or
python -m plane_mcp stdio
```

### Installing with pip (Alternative)

```bash
pip install plane-mcp-server
```

### Development Installation

```bash
git clone <repository-url>
cd plane-mcp-server-py
uv pip install -e ".[dev]"
```

## Usage

The server supports three transport methods. **We recommend using `uvx`** as it doesn't require installation.

### 1. Stdio Transport (for local use)

**Recommended: Using uvx (no installation required)**
```bash
uvx plane-mcp-server stdio
```

**Alternative: If installed locally**
```bash
python -m plane_mcp stdio
# or
plane-mcp-server stdio
```

**MCP Client Configuration** (using uvx - recommended):

```json
{
  "mcpServers": {
    "plane": {
      "command": "uvx",
      "args": ["plane-mcp-server", "stdio"]
    }
  }
}
```

**MCP Client Configuration** (if installed globally):

```json
{
  "mcpServers": {
    "plane": {
      "command": "plane-mcp-server",
      "args": ["stdio"]
    }
  }
}
```

### 2. SSE Transport (Server-Sent Events - Deprecated)

```bash
uvx plane-mcp-server sse
```

**SSE transport connection**:

```json
{
  "mcpServers": {
    "plane": {
      "url": "http://localhost:8000/sse"
    }
  }
}
```

### 3. Streamable HTTP Transport (Recommended for remote connections)

```bash
uvx plane-mcp-server streamable-http
```

**Streamable HTTP transport connection**:

```json
{
  "mcpServers": {
    "plane": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

## Configuration

### Authentication

The server requires authentication via environment variables:

- `PLANE_BASE_URL`: Base URL for Plane API (default: `https://api.plane.so`)
- `PLANE_API_KEY`: API key for authentication (required if access_token not provided)
- `PLANE_ACCESS_TOKEN`: Access token for authentication (required if api_key not provided)

**Example**:
```bash
export PLANE_BASE_URL="https://api.plane.so"
export PLANE_API_KEY="your-api-key"
# or
export PLANE_ACCESS_TOKEN="your-access-token"
```

### Server Configuration

When running the server with **SSE or Streamable HTTP protocols**, you can set:

- `FASTMCP_PORT`: Port the server listens on (default: `8017`)

**Example (Windows PowerShell)**:
```powershell
$env:FASTMCP_PORT="8007"
uvx plane-mcp-server streamable-http
```

**Example (Linux/macOS)**:
```bash
FASTMCP_PORT=8007 uvx plane-mcp-server streamable-http
```

**Note**: When using the **stdio protocol**, no additional environment variables are required.

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

**Total Tools**: 50+ tools across 7 categories

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

