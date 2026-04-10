"""Read tools for Journey Endpoint."""

from typing import Literal
from fastmcp import FastMCP
from plane_mcp.journey.cache import get_cached_project_slugs_docstring
from plane_mcp.client import get_plane_client_context
from plane_mcp.resolver import EntityResolver
from plane_mcp.journey.base import JourneyBase, mcp_error_boundary
from plane_mcp.journey.lod import LODProfile
from plane.models.query_params import RetrieveQueryParams

class ReadJourney(JourneyBase):
    def search_tickets(
        self, 
        project_slug: str, 
        query: str | None = None, 
        labels: list[str] | None = None, 
        priority: list[str] | None = None, 
        states: list[str] | None = None, 
        assignees: list[str] | None = None, 
        limit: int = 50, 
        cursor: str | None = None, 
        lod: Literal["summary", "standard", "full"] = "standard"
    ) -> dict:
        import json
        
        
        if project_slug.lower() == 'help':
            from plane_mcp.journey.cache import get_cached_workspace_context
            opts = get_cached_workspace_context(0).copy()
            llm_content = {"projects": opts.get("projects", []), "priorities": opts.get("priorities", [])}
            display_lines = []
            for p in llm_content.get("projects", []):
                slug = p.get("project_slug", "UNKNOWN")
                desc = p.get("description", "").strip() or p.get("name", "")
                display_lines.append(f"{slug} - {desc}")
            display_str = "\n".join(display_lines) if display_lines else "No projects found."
            
            return {
                "llmContent": llm_content,
                "returnDisplay": display_str
            }

        project_id = self.resolver.resolve_project(project_slug)
        client, workspace_slug = get_plane_client_context()

        if limit <= 0:
            return {"results": [], "next_cursor": None, "prev_cursor": None}
        limit = min(limit, 100)
        per_page = limit
        query_params = {
            "per_page": per_page,
            "expand": "assignees,labels,state",
        }
        
        if cursor:
            query_params["cursor"] = cursor
        if priority:
            query_params["priority"] = ",".join([p.lower() for p in priority])
            
        if states:
            state_ids = [self.resolver.resolve_state(project_slug, s) for s in states]
            query_params["state"] = ",".join(state_ids)
            
        unresolved_labels: list[str] = []
        if labels:
            try:
                existing_labels = client.labels.list(workspace_slug=workspace_slug, project_id=project_id).results
                name_to_id = {label.name.lower(): label.id for label in existing_labels if label.name}
                label_ids = []
                for name in labels:
                    if name.lower() in name_to_id:
                        label_ids.append(name_to_id[name.lower()])
                    else:
                        unresolved_labels.append(name)
                if label_ids:
                    query_params["labels"] = ",".join(label_ids)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Label resolution failed, returning empty results: {e}")
                return {
                    "results": [],
                    "next_cursor": None,
                    "prev_cursor": None,
                    "warnings": [f"Label filter could not be applied due to an error: {e}. Please retry or check your label names."],
                }
                
        if assignees:
            assignee_ids = []
            for a in assignees:
                if a.lower() == "me":
                    try:
                        me = client.users.get_me()
                        if hasattr(me, "id"):
                            assignee_ids.append(me.id)
                    except Exception:
                        pass
            if assignee_ids:
                query_params["assignees"] = ",".join(assignee_ids)
                
        from plane.models.work_items import PaginatedWorkItemResponse
        
        matched_results = []
        current_cursor = cursor
        next_cursor_to_return = None
        
        query_lower = query.lower() if query else None
        
        max_pages_to_fetch = 5
        pages_fetched = 0


        # Loop to pull pages and deep-search until we hit the requested limit or run out of pages
        while len(matched_results) < limit and pages_fetched < max_pages_to_fetch:
            if current_cursor:
                query_params["cursor"] = current_cursor
                
            response = client.work_items._get(
                f"{workspace_slug}/projects/{project_id}/work-items", 
                params=query_params
            )
            pages_fetched += 1
            
            paginated = PaginatedWorkItemResponse.model_validate(response)
            
            for item in paginated.results:
                if query_lower:
                    # Deep JSON string match
                    item_json = json.dumps(item.model_dump(), default=str).lower()
                    if query_lower in item_json:
                        matched_results.append(item)
                else:
                    matched_results.append(item)

            # Advance cursor only after a full page is consumed — keeps page boundaries aligned
            next_cursor_to_return = getattr(paginated, "next_cursor", None)
            if not next_cursor_to_return:
                break
            current_cursor = next_cursor_to_return

        try:
            profile = LODProfile(lod)
        except ValueError:
            profile = LODProfile.STANDARD
            
        transformed_results = self.apply_lod(matched_results[:limit], profile=profile, project_identifier=project_slug)
        
        result = {
            "results": transformed_results,
            "next_cursor": next_cursor_to_return,
            "prev_cursor": paginated.prev_cursor if hasattr(paginated, "prev_cursor") else None
        }
        if unresolved_labels:
            result["warnings"] = [f"Label not found and filter was skipped: {', '.join(unresolved_labels)}"]
        return result

    def read_ticket(self, ticket_id: str, lod: Literal["summary", "standard", "full"] = "standard", comments: bool = False) -> dict:
        work_item_id = self.resolver.resolve_ticket(ticket_id)
        project_identifier, _issue_sequence = self.parse_ticket_id(ticket_id)
        project_id = self.resolver.resolve_project(project_identifier)
        
        client, workspace_slug = get_plane_client_context()
        
        params = RetrieveQueryParams(expand="assignees,labels,state")
        
        result = client.work_items.retrieve(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            params=params
        )
        
        try:
            profile = LODProfile(lod)
        except ValueError:
            profile = LODProfile.STANDARD
            
        transformed = self.apply_lod(result, profile=profile, project_identifier=project_identifier)
        
        if comments:
            try:
                comments_resp = client.work_items.comments.list(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    work_item_id=work_item_id
                )
                formatted_comments = []
                for c in comments_resp.results:
                    date_str = str(c.created_at)[:10] if hasattr(c, 'created_at') and c.created_at else "YYYY-MM-DD"
                    
                    username = "user"
                    # Try to extract the best available identifier
                    if hasattr(c, 'actor_detail') and c.actor_detail:
                        username = getattr(c.actor_detail, 'display_name', getattr(c.actor_detail, 'username', username))
                    elif hasattr(c, 'actor') and c.actor:
                        username = str(c.actor)
                        
                    text = getattr(c, 'comment_stripped', '') or getattr(c, 'comment_html', '')
                    formatted_comments.append(f"\n{date_str}-@{username}:\n{text}")
                
                if formatted_comments:
                    transformed["comments"] = "".join(formatted_comments)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(f"Failed to fetch comments for {ticket_id}: {e}")
                
        return transformed


def register_read_tools(mcp: FastMCP) -> None:
    
    def search_tickets(
        project_slug: str, 
        query: str | None = None, 
        labels: list[str] | None = None, 
        priority: list[str] | None = None, 
        states: list[str] | None = None, 
        assignees: list[str] | None = None, 
        limit: int = 50, 
        cursor: str | None = None, 
        lod: Literal["summary", "standard", "full"] = "standard"
    ) -> dict:
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)
        journey = ReadJourney(resolver)
        raw_data = journey.search_tickets(project_slug, query, labels, priority, states, assignees, limit, cursor, lod)
        
        if project_slug.lower() == 'help':
            return raw_data
            
        import re
        def strip_html_markdown(text: str) -> str:
            if not text:
                return ""
            text = re.sub(r'<[^>]+>', '', text)
            return " ".join(text.split())

        formatted_str = []
        for item in raw_data.get("results", []):
            slug = item.get("ticket_id", item.get("key", "UNKNOWN"))
            title = item.get("name", "Untitled")
            desc = item.get("description", "")
            
            clean_desc = strip_html_markdown(desc)
            if len(clean_desc) > 80:
                snippet = clean_desc[:80] + "..."
            else:
                snippet = clean_desc
            
            if snippet:
                formatted_str.append(f"{slug} {title}\n{snippet}")
            else:
                formatted_str.append(f"{slug} {title}")

        return {
            "llmContent": raw_data,
            "returnDisplay": "\n\n".join(formatted_str)
        }

    search_tickets.__doc__ = """
        Search for issues. You can use standard filters or a text query.
        If the desired result is not in the current page, call again with the provided next_cursor.

        Args:
            project_slug: The Plane project identifier (e.g., 'PLANE' or 'TEST'). To discover valid project slugs, states, and labels, call this tool with project_slug='help'.
            query: Free-form text search query.
            labels: List of label names to filter by (e.g., ['bug', 'feature']).
            priority: List of priorities to filter by (e.g., ['urgent', 'high', 'medium', 'low', 'none']).
            states: List of state names to filter by (e.g., ['In Progress', 'Backlog', 'Done']).
            assignees: List of usernames or 'me' to filter by.
            limit: Maximum number of results to return (max 100). Default is 50.
            cursor: Pagination cursor for getting the next set of results.
            lod: Level of Detail profile ("summary", "standard", or "full"). Default is "standard".
        """
    search_tickets = mcp.tool()(mcp_error_boundary(search_tickets))

    @mcp.tool()
    @mcp_error_boundary
    def read_ticket(ticket_id: str, lod: Literal["summary", "standard", "full"] = "standard", comments: bool = False) -> dict:
        """
        Read the details of a single ticket.

        Args:
            ticket_id: The globally unique, human-readable identifier (e.g., ENG-123). The system automatically resolves the project and issue routing from this prefix.
            lod: Level of Detail profile ("summary", "standard", "full"). Default is "standard".
            comments: If true, fetches and appends the ticket's comments to the result.
        """
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)
        journey = ReadJourney(resolver)
        raw_data = journey.read_ticket(ticket_id, lod, comments)

        import re
        def clean_description_for_read(text: str) -> str:
            if not text:
                return ""
            # Only strip HTML tags, preserve newlines and Markdown
            return re.sub(r'<[^>]+>', '', text).strip()

        slug = raw_data.get("ticket_id", raw_data.get("key", ticket_id))
        title = raw_data.get("name", "Untitled")
        desc = raw_data.get("description", "")

        clean_desc = clean_description_for_read(desc)

        returnDisplay = f"{slug} {title}\n\n{clean_desc}"
        if "comments" in raw_data:
            returnDisplay += "\n\nComments:\n" + raw_data["comments"]

        return {
            "llmContent": raw_data,
            "returnDisplay": returnDisplay.strip()
        }
