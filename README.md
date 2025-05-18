# Plane MCP Server

The Plane MCP Server brings the power of Model Context Protocol (MCP) to Plane, allowing AI agents and developer tools to interact programmatically with your Plane workspace.

Whether you're building intelligent assistants, automation scripts, or workflow-driven tools, this server provides a seamless bridge to Plane’s API—so you can create projects, manage issues, assign tasks, and keep your work in sync with AI-powered tools.

## What can you do with it?
This server unlocks all sorts of useful capabilities for anyone working with Plane:

- Spin up projects and work items directly from your AI or app interface.

- Update progress, assign team members, set properties, or add comments—all programmatically.

- Move issues through workflows and update their states on the fly.

- Organize work with labels, modules, and cycles.

- Analyze data about your team’s work across projects.

- Build smart apps that interact naturally with Plane—whether it’s an AI agent logging work, or a bot keeping projects tidy.


## Tools

### Users

- `get_user`  
  - Get the current user's information
  - No parameters required

### Projects

- `get_projects`
  - Get all projects for the current user
  - No parameters required

- `create_project`   
  - Create a new project
  - Parameters:
    - `name` (string, required): Project name 

### Issue Types

- `list_issue_types`  
    - Get all issue types for a specific project
    - Parameters: 
      - `project_id` (string, required): UUID of the project

- `get_issue_type`
  - Get details of a specific issue type
  - Parameters: 
      - `project_id` (string, required): UUID of the project
      - `type_id` (string, required): UUID of the issue type

- `create_issue_type`
  - Create a new issue type in a project
  - Parameters: 
    - `project_id` (string, required): UUID of the project 
    - `issue_type_data`: Object containing:
       - `name` (string, required): Name of the issue type 
        - `description` (string, required): Description of the issue type 

- `update_issue_type` 
  - Update an existing issue type
  - Parameters: 
    - `project_id` (string, required): UUID of the project 
    - `type_id` (string, required): UUID of the issue type
    - `issue_type_data` (object): Fields to update on the issue type

- `delete_issue_type` 
  - Delete an issue type
  - Parameters:
    - `project_id` (string, required): UUID of the project
    - `type_id`  (string, required): UUID of the issue type

### States

- `list_states` 
  - Get all states for a specific project
  - Parameters:
    - `project_id` (string, required): UUID of the project

- `get_state` 
  - Get details of a specific state
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `state_id` (string, required): UUID of the state 

- `create_state` 
  - Create a new state in a project
   - Parameters:
     - `project_id` (string, required): UUID of the project
     - `state_data`: Object containing:
        - `name` (string, required): Name of the state 
        - `color` (string, required): Color code for the state 

- `update_state`
   - Update an existing state
   - Parameters:
      - `project_id` (string, required): UUID of the project 
      - `state_id` (string, required): UUID of the state 
      - `state_data` (object): Fields to update on the state 

- `delete_state` 
  - Delete a state
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `state_id` (string, required): UUID of the state 

### Labels

- `list_labels` 
  - Get all labels for a specific project
  - Parameters:
    - `project_id` (string, required): UUID of the project

- `get_label` 
  - Get details of a specific label
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `label_id` (string, required): UUID of the label 

- `create_label` 
  - Create a new label in a project
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `label_data`: Object containing:
      - `name` (string, required): Name of the label 
      - `color` (string, required): Color code for the label 

- `update_label` 
  - Update an existing label
  - Parameters:
    - `project_id` (string, required): UUID of the project
    - `label_id`  (string, required): UUID of the label
    - `label_data` (object): Fields to update on the label 

- `delete_label`
  - Delete a label
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `label_id` (string, required): UUID of the label 

### Issues

- `get_issue_using_readable_identifier` 
  - Get issue details using readable identifier (e.g., PROJ-123)
  - Parameters:
    - `project_identifier` (string, required)
    - `issue_identifier` (string, required): Issue numbe: Project identifier (e.g., "PROJ") r (e.g., "123") 

- `get_issue_comments` 
  - Get all comments for a specific issue
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `issue_id` (string, required): UUID of the issue 

- `add_issue_comment` 
  - Add a comment to an issue
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `issue_id` (string, required): UUID of the issue 
    - `comment_html` (string, required): HTML content of the comment 

- `create_issue` 
  - Create a new issue
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `issue_data`: Object containing:
     - `name` (string, required): Title of the issue 
      - `description_html` (string, required): HTML description of the issue 

- `update_issue` 
  - Update an existing issue
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `issue_id` (string, required): UUID of the issue 
    - `issue_data` (object): Fields to update on the issue 

### Modules

- `list_modules` 
  - Get all modules for a specific project
  - Parameters:
    - `project_id` (string, required): UUID of the project 

- `get_module` 
  - Get details of a specific module
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `module_id` (string, required): UUID of the module 

- `create_module` 
  - Create a new module in a project
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `module_data`: Object containing:
      - `name` (string, required): Name of the module 

- `update_module` 
  - Update an existing module
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `module_id` (string, required): UUID of the module 
    - `module_data` (object): Fields to update on the module 

- `delete_module` 
  - Delete a module
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `module_id` (string, required): UUID of the module 

### Module Issues

- `list_module_issues` 
  - Get all issues for a specific module
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `module_id` (string, required): UUID of the module 

- `add_module_issues` 
  - Add issues to a module
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `module_id` (string, required): UUID of the module 
    - `issues` (string[], required): Array of issue UUIDs to add 

- `delete_module_issue` 
  - Remove an issue from a module
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `module_id` (string, required): UUID of the module 
    - `issue_id` (string, required): UUID of the issue to remove 

### Cycles

- `list_cycles` 
  - Get all cycles for a specific project
  - Parameters:
    - `project_id` (string, required): UUID of the project 

- `get_cycle` 
  - Get details of a specific cycle
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `cycle_id` (string, required): UUID of the cycle 

- `create_cycle` 
  - Create a new cycle in a project
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `cycle_data`: Object containing:
     - `name` (string, required): Name of the cycle 
      - `start_date` (string, required): Start date (YYYY-MM-DD) 
      - `end_date` (string, required)
: End date (YYYY-MM-DD) 
- `update_cycle` 
  - Update an existing cycle
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `cycle_id` (string, required): UUID of the cycle 
    - `cycle_data` (object): Fields to update on the cycle 

- `delete_cycle` 
  - Delete a cycle
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `cycle_id` (string, required): UUID of the cycle 

### Cycle Issues

- `list_cycle_issues` 
  - Get all issues for a specific cycle
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `cycle_id` (string, required): UUID of the cycle 

- `add_cycle_issues` 
  - Add issues to a cycle
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `cycle_id` (string, required): UUID of the cycle 
    - `issues` (string[], required): Array of issue UUIDs to add 

- `delete_cycle_issue` 
  - Remove an issue from a cycle
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `cycle_id` (string, required): UUID of the cycle 
    - `issue_id` (string, required): UUID of the issue to remove 

### Work Logs

- `get_issue_worklogs` 
  - Get all worklogs for a specific issue
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `issue_id` (string, required): UUID of the issue 

- `get_total_worklogs` 
  - Get total logged time for a project
  - Parameters:
    - `project_id` (string, required): UUID of the project 

- `create_worklog` 
  - Create a new worklog for an issue
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `issue_id` (string, required): UUID of the issue 
    - `worklog_data`: Object containing:
      - `description` (string, required): Description of the work done 
      - `duration` (integer, required): Duration in minutes 

- `update_worklog` 
  - Update an existing worklog
  - Parameters:
    - `project_id` (string, required): UUID of the project
    - `issue_id` (string, required): UUID of the issue 
    - `worklog_id` (string, required): UUID of the worklog 
    - `worklog_data` (object): Fields to update on the worklog 

- `delete_worklog` 
  - Delete a worklog
  - Parameters:
    - `project_id` (string, required): UUID of the project 
    - `issue_id` (string, required): UUID of the issue 
    - `worklog_id` (string, required): UUID of the worklog 


## Configuration Parameters

- `PLANE_API_KEY` - Your Plane API token. You can generate one from the Workspace Settings > API Tokens page (`/settings/api-tokens/`) in the Plane app. 
- `PLANE_WORKSPACE_SLUG` - The workspace slug for your Plane instance. The workspace-slug represents the unique workspace identifier for a workspace in Plane. It can be found in the URL.
- `PLANE_API_HOST_URL` (optional) - The host URL of the Plane API Server. Defaults to https://api.plane.so/

## Usage

### Claude Desktop

You can add Plane to [Claude Desktop](https://modelcontextprotocol.io/quickstart/user) by updating your `claude_desktop_config.json`:

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

### VSCode

You can also connect Plane to [VSCode](https://code.visualstudio.com/docs/copilot/chat/mcp-servers#_add-an-mcp-server) by editing your `.vscode.json` or `mcp.json` file:

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
        "PLANE_API_HOST_URL": "<HOST_URL_FOR_SELF_HOSTED>",
        "PLANE_WORKSPACE_SLUG": "<YOUR_WORKSPACE_SLUG>"
      }
    }
  }
}

```



## Running evals

The evals package loads an mcp client that then runs the index.ts file, so there is no need to rebuild between tests. You can load environment variables by prefixing the npx command. Full documentation can be found [here](https://www.mcpevals.io/docs).

```bash
OPENAI_API_KEY=your-key  npx mcp-eval src/evals/evals.ts src/tools/work-log.ts
```
## License

This MCP server is licensed under the MIT License. This means you are free to use, modify, and distribute the software, subject to the terms and conditions of the MIT License. For more details, please see the LICENSE file in the project repository.