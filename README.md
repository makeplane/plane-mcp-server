# Plane MCP Server

The Plane MCP Server is a Model Context Protocol (MCP) server that provides seamless integration with Plane APIs, enabling projects, work items, and automations capabilities for develops and AI interfaces.

<a href="https://glama.ai/mcp/servers/@makeplane/plane-mcp-server">
  <img width="380" height="200" src="https://glama.ai/mcp/servers/@makeplane/plane-mcp-server/badge" alt="Plane Server MCP server" />
</a>

## Use Cases

- Create, update Projects and Work items
- Assign People, Properties, and Write Comments to progress on the Work
- Move and observe with various Work items in States
- Add Labels to the Work items
- Extracting and analyzing data from Projects and Members inside Plane
- Building AI powered tools and applications that interact with Plane's ecosystem

## Configuration Parameters

1. `PLANE_API_KEY` - The user's API token. This can be obtained from the `/settings/api-tokens/` page in the UI.
2. `PLANE_WORKSPACE_SLUG` - The workspace slug for your Plane instance.
3. `PLANE_API_HOST_URL` (optional) - The host URL of the Plane API Server. Defaults to https://api.plane.so/

## Tools

### Users

- `get_user` - Get the current user's information
  - No parameters required

### Projects

- `get_projects` - Get all projects for the current user

  - No parameters required

- `create_project` - Create a new project
  - `name`: Project name (string, required)

### Issue Types

- `list_issue_types` - Get all issue types for a specific project

  - `project_id`: UUID of the project (string, required)

- `get_issue_type` - Get details of a specific issue type

  - `project_id`: UUID of the project (string, required)
  - `type_id`: UUID of the issue type (string, required)

- `create_issue_type` - Create a new issue type in a project

  - `project_id`: UUID of the project (string, required)
  - `issue_type_data`: Object containing:
    - `name`: Name of the issue type (string, required)
    - `description`: Description of the issue type (string, required)

- `update_issue_type` - Update an existing issue type

  - `project_id`: UUID of the project (string, required)
  - `type_id`: UUID of the issue type (string, required)
  - `issue_type_data`: Fields to update on the issue type (object)

- `delete_issue_type` - Delete an issue type
  - `project_id`: UUID of the project (string, required)
  - `type_id`: UUID of the issue type (string, required)

### States

- `list_states` - Get all states for a specific project

  - `project_id`: UUID of the project (string, required)

- `get_state` - Get details of a specific state

  - `project_id`: UUID of the project (string, required)
  - `state_id`: UUID of the state (string, required)

- `create_state` - Create a new state in a project

  - `project_id`: UUID of the project (string, required)
  - `state_data`: Object containing:
    - `name`: Name of the state (string, required)
    - `color`: Color code for the state (string, required)

- `update_state` - Update an existing state

  - `project_id`: UUID of the project (string, required)
  - `state_id`: UUID of the state (string, required)
  - `state_data`: Fields to update on the state (object)

- `delete_state` - Delete a state
  - `project_id`: UUID of the project (string, required)
  - `state_id`: UUID of the state (string, required)

### Labels

- `list_labels` - Get all labels for a specific project

  - `project_id`: UUID of the project (string, required)

- `get_label` - Get details of a specific label

  - `project_id`: UUID of the project (string, required)
  - `label_id`: UUID of the label (string, required)

- `create_label` - Create a new label in a project

  - `project_id`: UUID of the project (string, required)
  - `label_data`: Object containing:
    - `name`: Name of the label (string, required)
    - `color`: Color code for the label (string, required)

- `update_label` - Update an existing label

  - `project_id`: UUID of the project (string, required)
  - `label_id`: UUID of the label (string, required)
  - `label_data`: Fields to update on the label (object)

- `delete_label` - Delete a label
  - `project_id`: UUID of the project (string, required)
  - `label_id`: UUID of the label (string, required)

### Issues

- `get_issue_using_readable_identifier` - Get issue details using readable identifier (e.g., PROJ-123)

  - `project_identifier`: Project identifier (e.g., "PROJ") (string, required)
  - `issue_identifier`: Issue number (e.g., "123") (string, required)

- `get_issue_comments` - Get all comments for a specific issue

  - `project_id`: UUID of the project (string, required)
  - `issue_id`: UUID of the issue (string, required)

- `add_issue_comment` - Add a comment to an issue

  - `project_id`: UUID of the project (string, required)
  - `issue_id`: UUID of the issue (string, required)
  - `comment_html`: HTML content of the comment (string, required)

- `create_issue` - Create a new issue

  - `project_id`: UUID of the project (string, required)
  - `issue_data`: Object containing:
    - `name`: Title of the issue (string, required)
    - `description_html`: HTML description of the issue (string, required)

- `update_issue` - Update an existing issue
  - `project_id`: UUID of the project (string, required)
  - `issue_id`: UUID of the issue (string, required)
  - `issue_data`: Fields to update on the issue (object)

### Modules

- `list_modules` - Get all modules for a specific project

  - `project_id`: UUID of the project (string, required)

- `get_module` - Get details of a specific module

  - `project_id`: UUID of the project (string, required)
  - `module_id`: UUID of the module (string, required)

- `create_module` - Create a new module in a project

  - `project_id`: UUID of the project (string, required)
  - `module_data`: Object containing:
    - `name`: Name of the module (string, required)

- `update_module` - Update an existing module

  - `project_id`: UUID of the project (string, required)
  - `module_id`: UUID of the module (string, required)
  - `module_data`: Fields to update on the module (object)

- `delete_module` - Delete a module
  - `project_id`: UUID of the project (string, required)
  - `module_id`: UUID of the module (string, required)

### Module Issues

- `list_module_issues` - Get all issues for a specific module

  - `project_id`: UUID of the project (string, required)
  - `module_id`: UUID of the module (string, required)

- `add_module_issues` - Add issues to a module

  - `project_id`: UUID of the project (string, required)
  - `module_id`: UUID of the module (string, required)
  - `issues`: Array of issue UUIDs to add (string[], required)

- `delete_module_issue` - Remove an issue from a module
  - `project_id`: UUID of the project (string, required)
  - `module_id`: UUID of the module (string, required)
  - `issue_id`: UUID of the issue to remove (string, required)

### Cycles

- `list_cycles` - Get all cycles for a specific project

  - `project_id`: UUID of the project (string, required)

- `get_cycle` - Get details of a specific cycle

  - `project_id`: UUID of the project (string, required)
  - `cycle_id`: UUID of the cycle (string, required)

- `create_cycle` - Create a new cycle in a project

  - `project_id`: UUID of the project (string, required)
  - `cycle_data`: Object containing:
    - `name`: Name of the cycle (string, required)
    - `start_date`: Start date (YYYY-MM-DD) (string, required)
    - `end_date`: End date (YYYY-MM-DD) (string, required)

- `update_cycle` - Update an existing cycle

  - `project_id`: UUID of the project (string, required)
  - `cycle_id`: UUID of the cycle (string, required)
  - `cycle_data`: Fields to update on the cycle (object)

- `delete_cycle` - Delete a cycle
  - `project_id`: UUID of the project (string, required)
  - `cycle_id`: UUID of the cycle (string, required)

### Cycle Issues

- `list_cycle_issues` - Get all issues for a specific cycle

  - `project_id`: UUID of the project (string, required)
  - `cycle_id`: UUID of the cycle (string, required)

- `add_cycle_issues` - Add issues to a cycle

  - `project_id`: UUID of the project (string, required)
  - `cycle_id`: UUID of the cycle (string, required)
  - `issues`: Array of issue UUIDs to add (string[], required)

- `delete_cycle_issue` - Remove an issue from a cycle
  - `project_id`: UUID of the project (string, required)
  - `cycle_id`: UUID of the cycle (string, required)
  - `issue_id`: UUID of the issue to remove (string, required)

### Work Logs

- `get_issue_worklogs` - Get all worklogs for a specific issue

  - `project_id`: UUID of the project (string, required)
  - `issue_id`: UUID of the issue (string, required)

- `get_total_worklogs` - Get total logged time for a project

  - `project_id`: UUID of the project (string, required)

- `create_worklog` - Create a new worklog for an issue

  - `project_id`: UUID of the project (string, required)
  - `issue_id`: UUID of the issue (string, required)
  - `worklog_data`: Object containing:
    - `description`: Description of the work done (string, required)
    - `duration`: Duration in minutes (integer, required)

- `update_worklog` - Update an existing worklog

  - `project_id`: UUID of the project (string, required)
  - `issue_id`: UUID of the issue (string, required)
  - `worklog_id`: UUID of the worklog (string, required)
  - `worklog_data`: Fields to update on the worklog (object)

- `delete_worklog` - Delete a worklog
  - `project_id`: UUID of the project (string, required)
  - `issue_id`: UUID of the issue (string, required)
  - `worklog_id`: UUID of the worklog (string, required)

## Usage

### Claude Desktop

Add Plane to [Claude Desktop](https://modelcontextprotocol.io/quickstart/user) by editing your `claude_desktop_config.json`.

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
        "PLANE_API_HOST_URL": "<HOST_URL_FOR_SELF_HOSTED",
        "PLANE_WORKSPACE_SLUG": "<YOUR_WORKSPACE_SLUG>"
      }
    }
  }
}
```

### VSCode

Add Plane to [VSCode](https://code.visualstudio.com/docs/copilot/chat/mcp-servers#_add-an-mcp-server) by editing your `.vscode.json/mcp.json`.

```json
{
  "servers": {
    "plane": {
      "command": "npx",
      "args": [
        "-y",
        "@makeplane/plane-mcp-server"
      ],
      "env": {
        "PLANE_API_KEY": "<YOUR_API_KEY>",
        "PLANE_API_HOST_URL": "<HOST_URL_FOR_SELF_HOSTED",
        "PLANE_WORKSPACE_SLUG": "<YOUR_WORKSPACE_SLUG>"
      }
    }
  }
}

```

## License

This project is licensed under the terms of the MIT open source license.