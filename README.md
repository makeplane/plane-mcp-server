# Plane MCP Server

The Plane MCP Server is a Model Context Protocol (MCP) server that provides seamless integration with Plane APIs, enabling projects, work items, and automations capabilities for develops and AI interfaces.

## Use Cases

- Create, update Projects and Work items.
- Assign People, Properties, and Write Comments to progress on the Work.
- Move and observe with various Work items in States.
- Add Labels to the Work items.
- Extracting and analyzing data from Projects and Members inside Plane.
- Building AI powered tools and applications that interact with Plane's ecosystem.

## Tools 

### Users

-  get_me - Get details of the authenticated user

    -  No parameters required

### Projects

- create_project - Create Project within the Workspace

    - name: Project title (string, required)
    - description: Project description (string, required)

### Issues

- get_issue - Gets the contents of an issue within a Project
    
    - user: Current user (string, required)
    - issue_number: Issue number (number, required)

- get_issue_comments - Get comments for a GitHub issue

    - user: Current user (string, required)
    - issue_number: Issue number (number, required)

- create_issue - Create a new issue in the Project

    - user: Current user (string, required)
    - project: Project name (string, required)
    - title: Issue title (string, required)
    - body: Issue body content (string, optional)
    - collaborates: Usernames to assign to this issue (string[], optional)
    - labels: Labels to apply to this issue (string[], optional)

- add_issue_comment - Add a comment to an issue

    - user: Current user (string, required)
    - body: Comment text (string, required)
    - issue_number: Issue number (number, required)
    - body: Comment text (string, required)

- list_issues - List and filter Project issues
    
    - user: Current user (string, required)
    - project: Project name (string, required)
    - state: Filter by state ('open', 'closed', 'all') (string, optional)
    - labels: Labels to filter by (string[], optional)
    - sort: Sort by ('created', 'updated', 'comments') (string, optional)
    - direction: Sort direction ('asc', 'desc') (string, optional)
    - since: Filter by date (ISO 8601 timestamp) (string, optional)
    - page: Page number (number, optional)
    - perPage: Results per page (number, optional)

- update_issue - Update an existing issue in a GitHub Project

    - user: Current user (string, required)
    - issue_number: Issue number to update (number, required)
    - title: New title (string, optional)
    - body: New description (string, optional)
    - state: New state ('open' or 'closed') (string, optional)
    - labels: New labels (string[], optional)
    - assignees: New assignee (string, optional)
    - properties: New properties (string[], optional)

- search_issues - Search for issues and pull requests

    - query: Search query (string, required)
    - sort: Sort field (string, optional)
    - order: Sort order (string, optional)
    - page: Page number (number, optional)
    - perPage: Results per page (number, optional)

## License

This project is licensed under the terms of the MIT open source license.