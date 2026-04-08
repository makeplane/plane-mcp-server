"""Create and Update tools for Journey Endpoint."""

from fastmcp import FastMCP
from plane_mcp.journey.cache import get_cached_project_slugs_docstring
from plane_mcp.client import get_plane_client_context
from plane_mcp.resolver import EntityResolver
from plane_mcp.journey.base import JourneyBase, mcp_error_boundary
from plane_mcp.journey.lod import LODProfile
from plane_mcp.sanitize import sanitize_html
from plane.models.work_items import CreateWorkItem, UpdateWorkItem
from plane.models.labels import CreateLabel
from plane.models.cycles import CreateCycle
from plane.errors.errors import HttpError
import logging

logger = logging.getLogger(__name__)

MAX_NEW_LABELS_PER_REQUEST = 3


class CreateUpdateJourney(JourneyBase):
    def _resolve_or_create_labels(self, project_id: str, label_names: list[str]) -> list[str]:
        client, workspace_slug = get_plane_client_context()
        existing = client.labels.list(workspace_slug=workspace_slug, project_id=project_id).results
        name_to_id = {label.name.lower(): label.id for label in existing if label.name}

        new_labels_needed = [n for n in label_names if n.lower() not in name_to_id]
        if len(new_labels_needed) > MAX_NEW_LABELS_PER_REQUEST:
            raise ValueError(
                f"Too many new labels requested ({len(new_labels_needed)}). "
                f"Maximum {MAX_NEW_LABELS_PER_REQUEST} new labels can be auto-created per request. "
                f"Unknown labels: {new_labels_needed}"
            )

        label_ids = []
        for name in label_names:
            key = name.lower()
            if key in name_to_id:
                label_ids.append(name_to_id[key])
            else:
                new_label = client.labels.create(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    data=CreateLabel(name=name, color="#000000")
                )
                label_ids.append(new_label.id)
                name_to_id[key] = new_label.id
        return label_ids

    def _resolve_or_create_cycle(self, project_id: str, cycle_name: str) -> str | None:
        client, workspace_slug = get_plane_client_context()
        try:
            existing = client.cycles.list(workspace_slug=workspace_slug, project_id=project_id).results
            for c in existing:
                if c.name and c.name.lower() == cycle_name.lower():
                    return c.id

            # Create missing cycle
            me = client.users.get_me()
            user_id = me.id if hasattr(me, "id") else None

            import datetime
            start_dt = datetime.date.today().isoformat()
            end_dt = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()
            new_cycle = client.cycles.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                data=CreateCycle(name=cycle_name, project_id=project_id, owned_by=user_id, start_date=start_dt, end_date=end_dt)
            )
            return new_cycle.id
        except HttpError as e:
            if getattr(e, "status_code", getattr(getattr(e, "response", None), "status_code", None)) == 400:
                logger.warning(f"Cycles appear to be disabled for project {project_id}. Skipping cycle lookup/creation.")
                return None
            raise

    def create_ticket(
        self, 
        title: str, 
        project_slug: str, 
        description: str | None = None,
        state_name: str | None = None, 
        labels: list[str] | None = None, 
        cycle_name: str | None = None
    ) -> dict:
        
        if project_slug.lower() == 'help':
            from plane_mcp.journey.cache import get_cached_workspace_context
            return {"status": "help", "message": "Here are the valid options for this workspace.", "options": get_cached_workspace_context(0)}

        project_id = self.resolver.resolve_project(project_slug)
        client, workspace_slug = get_plane_client_context()
        
        state_id = self.resolver.resolve_state(project_slug, state_name) if state_name else None
        label_ids = self._resolve_or_create_labels(project_id, labels) if labels else None
        
        cycle_id = None
        if cycle_name:
            cycle_id = self._resolve_or_create_cycle(project_id, cycle_name)
            
        data = CreateWorkItem(
            name=title,
            description_html=sanitize_html(description),
            state=state_id,
            labels=label_ids
        )
        
        new_ticket = client.work_items.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=data
        )
        
        key = f"{project_slug}-{new_ticket.sequence_id}"

        if cycle_id and new_ticket.id:
            try:
                client.cycles.add_work_items(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    cycle_id=cycle_id,
                    issue_ids=[new_ticket.id]
                )
            except Exception as e:
                logger.warning("Created %s but failed to add it to cycle %s: %s", key, cycle_id, e)
                return {
                    "key": key,
                    "status": "warning",
                    "message": "Ticket created successfully, but adding it to the cycle failed. The ticket exists.",
                }

        return {"key": key}

    def update_ticket(
        self,
        ticket_id: str,
        new_title: str | None = None,
        append_text: str | None = None,
        append_after_snippet: str | None = None,
        replace_text: str | None = None,
        replace_target_snippet: str | None = None,
        comment: str | None = None
    ) -> dict:
        work_item_id = self.resolver.resolve_ticket(ticket_id)
        project_identifier, _ = self.parse_ticket_id(ticket_id)
        project_id = self.resolver.resolve_project(project_identifier)
        
        client, workspace_slug = get_plane_client_context()
        
        # Retrieve using internal _get to bypass Pydantic ValidationError when
        # the API returns label UUIDs as strings instead of Label objects
        current = client.work_items._get(
            f"{workspace_slug}/projects/{project_id}/work-items/{work_item_id}"
        )
        
        title_changed = False
        final_title = current.get("name", "")
        if new_title and new_title != current.get("name"):
            final_title = new_title
            title_changed = True
                
        desc_changed = False
        final_desc = current.get("description_html") or ""
        
        if replace_text is not None:
            if not replace_target_snippet:
                return {"status": "error", "message": "You must provide 'replace_target_snippet' to specify exactly which text to replace."}
            
            occurrences = final_desc.count(replace_target_snippet)
            if occurrences == 0:
                return {"status": "error", "message": f"The snippet '{replace_target_snippet}' was not found in the description. Ensure you matched the exact text, spaces, and casing."}
            if occurrences > 1:
                return {"status": "error", "message": f"The snippet '{replace_target_snippet}' matched multiple times. Please provide a longer, more specific snippet to uniquely identify the text to replace."}
                
            final_desc = final_desc.replace(replace_target_snippet, replace_text)
            desc_changed = True

        if append_text is not None:
            if append_after_snippet:
                occurrences = final_desc.count(append_after_snippet)
                if occurrences == 0:
                    return {"status": "error", "message": f"The snippet '{append_after_snippet}' was not found in the description. Ensure you matched the exact text."}
                if occurrences > 1:
                    return {"status": "error", "message": f"The snippet '{append_after_snippet}' matched multiple times. Please provide a longer snippet."}
                
                final_desc = final_desc.replace(append_after_snippet, f"{append_after_snippet}<br><br>{append_text}")
            else:
                if final_desc:
                    final_desc = f"{final_desc}<br><br>{append_text}"
                else:
                    final_desc = append_text
            desc_changed = True
                
        if title_changed or desc_changed:
            data = UpdateWorkItem(
                name=final_title,
                description_html=sanitize_html(final_desc)
            )
            
            client.work_items.update(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
                data=data
            )

        if comment:
            from plane.models.work_items import CreateWorkItemComment
            safe_comment = sanitize_html(f"<p>{comment}</p>")
            client.work_items.comments.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
                data=CreateWorkItemComment(comment_html=safe_comment)
            )
        
        if not title_changed and not desc_changed and not comment:
            return {"status": "warning", "message": "No changes were provided to update_ticket."}
            
        return {"key": ticket_id, "status": "success", "message": "Ticket updated successfully."}

def register_create_update_tools(mcp: FastMCP) -> None:
    def create_ticket(
        title: str,
        project_slug: str,
        description: str | None = None,
        labels: list[str] | None = None,
        cycle_name: str | None = None,
    ) -> dict:
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)
        journey = CreateUpdateJourney(resolver)
        return journey.create_ticket(title=title, project_slug=project_slug, description=description, labels=labels, cycle_name=cycle_name)

    create_ticket.__doc__ = """
        Create a new ticket with automatic resolution of labels and cycles.
        Missing labels or cycles will be automatically created. 
        The ticket is automatically placed in the default Backlog state.

        Args:
            title: Title of the ticket.
            project_slug: The Plane project identifier (e.g., 'PLANE'). To discover valid project slugs, call this tool with project_slug='help'.
            description: Optional detailed markdown description of the ticket.
            labels: List of label names.
            cycle_name: Name of the cycle to add this ticket to.
        """
    create_ticket = mcp.tool()(mcp_error_boundary(create_ticket))

    @mcp.tool()
    @mcp_error_boundary
    def update_ticket(
        ticket_id: str,
        new_title: str | None = None,
        append_text: str | None = None,
        append_after_snippet: str | None = None,
        replace_text: str | None = None,
        replace_target_snippet: str | None = None,
        comment: str | None = None
    ) -> dict:
        """
        Update a ticket's title, description, or add a comment. Features smart targeting to avoid JSON escaping errors.
        
        Args:
            ticket_id: The globally unique, human-readable identifier (e.g., ENG-123).
            new_title: Completely replaces the ticket title.
            append_text: Text to append to the description.
            append_after_snippet: If populated, 'append_text' will be inserted immediately after this exact snippet. If blank, 'append_text' is added to the very end of the description.
            replace_text: The new text that will replace a snippet.
            replace_target_snippet: The exact existing text to replace. Required if 'replace_text' is used.
            comment: Adds a new comment to the ticket thread.
        """
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)
        journey = CreateUpdateJourney(resolver)
        return journey.update_ticket(ticket_id, new_title, append_text, append_after_snippet, replace_text, replace_target_snippet, comment)
