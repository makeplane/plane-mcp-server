"""AI Journey tools for Plane MCP Server. Designed for semantic compression and intent-based operations."""

import re

from fastmcp import FastMCP
from plane.models.query_params import PaginatedQueryParams, RetrieveQueryParams
from plane.models.work_items import CreateWorkItem, CreateWorkItemComment, UpdateWorkItem

from plane_mcp.client import get_plane_client_context


class EntityResolver:
    """Helper to resolve human-readable identifiers to Plane UUIDs and minify responses."""
    def __init__(self, client, workspace_slug):
        self.client = client
        self.workspace_slug = workspace_slug
        self._project_cache = {}
        self._state_cache = {} # project_id -> { group: state_id }
        self._me = None

    def get_me_id(self):
        if not self._me:
            self._me = self.client.users.get_me()
        return self._me.id

    def get_project_id(self, identifier: str) -> str:
        identifier = identifier.upper()
        if identifier in self._project_cache:
            return self._project_cache[identifier]
        
        projects_resp = self.client.projects.list(workspace_slug=self.workspace_slug, params=PaginatedQueryParams(per_page=100))
        for p in projects_resp.results:
            self._project_cache[p.identifier.upper()] = p.id
            if p.identifier.upper() == identifier:
                return p.id
        raise ValueError(f"Project with identifier '{identifier}' not found in workspace '{self.workspace_slug}'.")

    def get_state_id(self, project_id: str, group: str) -> str:
        group = group.lower()
        if project_id not in self._state_cache:
            self._state_cache[project_id] = {}
            states_resp = self.client.states.list(workspace_slug=self.workspace_slug, project_id=project_id)
            for s in states_resp.results:
                if s.group not in self._state_cache[project_id]:
                    self._state_cache[project_id][s.group] = s.id
        
        if group not in self._state_cache[project_id]:
            raise ValueError(f"State group '{group}' not found in project.")
        return self._state_cache[project_id][group]

    def parse_issue_id(self, ticket_id: str):
        match = re.match(r"^([A-Z0-9]+)-(\d+)$", ticket_id, re.IGNORECASE)
        if not match:
            raise ValueError(f"Invalid ticket format: {ticket_id}. Expected format like 'PLANE-123'")
        return match.group(1).upper(), int(match.group(2))

    def get_issue_uuid(self, project_identifier: str, issue_sequence: int):
        try:
            item = self.client.work_items.retrieve_by_identifier(
                workspace_slug=self.workspace_slug,
                project_identifier=project_identifier,
                issue_identifier=issue_sequence,
                params=RetrieveQueryParams(expand="assignees,state")
            )
            return item.id, item
        except Exception as e:
            raise ValueError(f"Could not find ticket {project_identifier}-{issue_sequence}: {str(e)}")

    def minify_issue(self, item: dict, project_identifier: str) -> dict:
        """Compress a work item to save AI tokens."""
        if hasattr(item, 'model_dump'):
            item = item.model_dump()
        elif hasattr(item, 'dict'):
            item = item.dict()
        elif hasattr(item, '__dict__'):
            item = item.__dict__
            
        assignees = []
        if item.get("assignees"):
            for a in item.get("assignees"):
                if isinstance(a, dict):
                    assignees.append(a.get("display_name", a.get("id")))
                else:
                    assignees.append(str(a))
            
        return {
            "ticket_id": f"{project_identifier}-{item.get('sequence_id')}",
            "title": item.get("name"),
            "state": item.get("state_detail", {}).get("group") if isinstance(item.get("state_detail"), dict) else item.get("state"),
            "priority": item.get("priority"),
            "assignees": assignees
        }

def register_ai_journeys(mcp: FastMCP) -> None:
    """Register AI intent-based tools."""

    @mcp.tool()
    def create_ticket(
        title: str, 
        description: str, 
        project_slug: str = "PLANE", 
        priority: str = "none",
        is_epic: bool = False,
        child_tickets: list[dict] | None = None
    ) -> dict:
        """
        Create a new ticket in Plane.
        
        Args:
            title: The title of the ticket.
            description: Detailed description of the ticket.
            project_slug: The identifier of the project (e.g., 'PLANE', 'TEST'). Defaults to 'PLANE'.
            priority: Ticket priority ('urgent', 'high', 'medium', 'low', 'none'). Defaults to 'none'.
            is_epic: Set to True if this ticket is an Epic (a large feature with children).
            child_tickets: Optional list of dicts [{'title': '...', 'description': '...'}] to create as children.
            
        Returns:
            A token-optimized summary of the created ticket (and its children).
        """
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)
        
        project_id = resolver.get_project_id(project_slug)
        state_id = resolver.get_state_id(project_id, "backlog")
        
        data = CreateWorkItem(
            name=title,
            description_stripped=description,
            priority=priority if priority != "none" else None,
            state=state_id
        )
        
        result = client.work_items.create(workspace_slug=workspace_slug, project_id=project_id, data=data)
        parent_minified = resolver.minify_issue(result, project_slug)
        
        if child_tickets:
            parent_id = result.id
            children_results = []
            for child in child_tickets:
                child_data = CreateWorkItem(
                    name=child.get('title', 'Untitled Child'),
                    description_stripped=child.get('description', ''),
                    state=state_id,
                    parent=parent_id
                )
                child_res = client.work_items.create(workspace_slug=workspace_slug, project_id=project_id, data=child_data)
                children_results.append(resolver.minify_issue(child_res, project_slug))
            parent_minified['children'] = children_results
            
        return parent_minified

    @mcp.tool()
    def update_ticket(
        ticket_id: str, 
        title: str | None = None, 
        description: str | None = None, 
        priority: str | None = None
    ) -> dict:
        """
        Update an existing ticket's standard fields.
        
        Args:
            ticket_id: The human-readable ID, e.g., 'PLANE-123'.
            title: New title (optional).
            description: New description (optional).
            priority: New priority ('urgent', 'high', 'medium', 'low', 'none') (optional).
        """
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)
        
        proj_identifier, issue_seq = resolver.parse_issue_id(ticket_id)
        project_id = resolver.get_project_id(proj_identifier)
        issue_uuid, _ = resolver.get_issue_uuid(proj_identifier, issue_seq)
        
        data_dict = {}
        if title is not None:
            data_dict['name'] = title
        if description is not None:
            data_dict['description_stripped'] = description
        if priority is not None:
            data_dict['priority'] = priority
        update_model = UpdateWorkItem(**data_dict)
        
        result = client.work_items.update(
            workspace_slug=workspace_slug, 
            project_id=project_id, 
            work_item_id=issue_uuid, 
            data=update_model
        )
        return resolver.minify_issue(result, proj_identifier)

    @mcp.tool()
    def begin_work(ticket_id: str) -> dict:
        """
        Begin work on a ticket. This automatically:
        1. Assigns the ticket to you.
        2. Transitions the ticket to the 'In Progress' (started) state.
        
        Args:
            ticket_id: The human-readable ID, e.g., 'PLANE-123'.
        """
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)
        
        proj_identifier, issue_seq = resolver.parse_issue_id(ticket_id)
        project_id = resolver.get_project_id(proj_identifier)
        issue_uuid, existing_item = resolver.get_issue_uuid(proj_identifier, issue_seq)
        
        me_id = resolver.get_me_id()
        started_state_id = resolver.get_state_id(project_id, "started")
        
        # Merge existing assignees with 'me' to avoid overwriting
        existing_assignees = []
        if hasattr(existing_item, 'assignees') and existing_item.assignees:
            for a in existing_item.assignees:
                if isinstance(a, dict):
                    existing_assignees.append(a.get('id'))
                elif hasattr(a, 'id'):
                    existing_assignees.append(a.id)
        if me_id not in existing_assignees:
            existing_assignees.append(me_id)
            
        data = UpdateWorkItem(
            state=started_state_id,
            assignees=existing_assignees
        )
        
        result = client.work_items.update(
            workspace_slug=workspace_slug, 
            project_id=project_id, 
            work_item_id=issue_uuid, 
            data=data
        )
        return resolver.minify_issue(result, proj_identifier)

    @mcp.tool()
    def complete_work(ticket_id: str, comment: str | None = None) -> dict:
        """
        Complete work on a ticket. This automatically:
        1. Transitions the ticket to the 'Done' (completed) state.
        2. Optionally adds a comment to the ticket.
        
        Args:
            ticket_id: The human-readable ID, e.g., 'PLANE-123'.
            comment: A comment explaining the resolution (optional).
        """
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)
        
        proj_identifier, issue_seq = resolver.parse_issue_id(ticket_id)
        project_id = resolver.get_project_id(proj_identifier)
        issue_uuid, _ = resolver.get_issue_uuid(proj_identifier, issue_seq)
        
        completed_state_id = resolver.get_state_id(project_id, "completed")
        
        data = UpdateWorkItem(state=completed_state_id)
        result = client.work_items.update(
            workspace_slug=workspace_slug, 
            project_id=project_id, 
            work_item_id=issue_uuid, 
            data=data
        )
        
        if comment:
            # Plane client comments
            comment_data = CreateWorkItemComment(comment_stripped=comment)
            client.work_items.comments.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=issue_uuid,
                data=comment_data
            )
            
        return resolver.minify_issue(result, proj_identifier)

    @mcp.tool()
    def read_ticket(ticket_id: str) -> dict:
        """
        Read a ticket's current status and details.
        
        Args:
            ticket_id: The human-readable ID, e.g., 'PLANE-123'.
        """
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)
        
        proj_identifier, issue_seq = resolver.parse_issue_id(ticket_id)
        _, existing_item = resolver.get_issue_uuid(proj_identifier, issue_seq)
        
        return resolver.minify_issue(existing_item, proj_identifier)

    @mcp.tool()
    def search_tickets(query: str, project_slug: str = "PLANE") -> list[dict]:
        """
        Search for tickets using natural language query.
        
        Args:
            query: The text to search for.
            project_slug: The project to search within (e.g., 'PLANE').
        """
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)
        
        project_id = resolver.get_project_id(project_slug)
        results = client.work_items.search(workspace_slug=workspace_slug, query=query, params=RetrieveQueryParams())

        minified = []
        if hasattr(results, 'issues'):
            for item in results.issues:
                if hasattr(item, 'project') and item.project == project_id:
                    minified.append(resolver.minify_issue(item, project_slug))
                elif isinstance(item, dict) and item.get('project') == project_id:
                    minified.append(resolver.minify_issue(item, project_slug))
        return minified
