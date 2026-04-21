"""Workflow tools for Journey Endpoint."""

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from fastmcp import FastMCP
from plane.errors.errors import HttpError
from plane.models.cycles import CreateCycle
from plane.models.work_items import CreateWorkItemComment, UpdateWorkItem

from plane_mcp.client import get_plane_client_context
from plane_mcp.journey.base import JourneyBase, mcp_error_boundary
from plane_mcp.journey.lod import LODProfile
from plane_mcp.journey.yaml_formatter import with_yaml
from plane_mcp.resolver import EntityResolutionError, EntityResolver
from plane_mcp.sanitize import sanitize_html

logger = logging.getLogger(__name__)

class WorkflowJourney(JourneyBase):
    def _resolve_or_create_cycle(self, project_id: str, cycle_name: str) -> str | None:
        client, workspace_slug = get_plane_client_context()
        try:
            existing = client.cycles.list(workspace_slug=workspace_slug, project_id=project_id).results
            for c in existing:
                if c.name and c.name.lower() == cycle_name.lower():
                    return c.id
            
            me = client.users.get_me()
            user_id = me.id if hasattr(me, "id") else None
            
            start_date = datetime.now()
            end_date = start_date + timedelta(days=14)
            
            new_cycle = client.cycles.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                data=CreateCycle(
                    name=cycle_name, 
                    project_id=project_id, 
                    owned_by=user_id,
                    start_date=start_date.strftime("%Y-%m-%d"),
                    end_date=end_date.strftime("%Y-%m-%d")
                )
            )
            return new_cycle.id
        except HttpError as e:
            # If the project has the Cycles module disabled, the API returns 400 Bad Request.
            # We catch this so the rest of the workflow (like status transitions) can still proceed.
            if getattr(e, "status_code", getattr(getattr(e, "response", None), "status_code", None)) == 400:
                logger.warning(f"Cycles appear to be disabled for project {project_id}. Skipping cycle creation.")
                return None
            raise

    def transition_ticket(self, ticket_id: str, state_name: str) -> dict:
        work_item_id = self.resolver.resolve_ticket(ticket_id)
        project_identifier, _ = self.parse_ticket_id(ticket_id)
        project_id = self.resolver.resolve_project(project_identifier)
        state_id = self.resolver.resolve_state(project_identifier, state_name)
        
        client, workspace_slug = get_plane_client_context()
        
        updated = client.work_items.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=UpdateWorkItem(state=state_id)
        )
        
        return self.apply_lod(updated, profile=LODProfile.SUMMARY, project_identifier=project_identifier)

    def begin_work(self, ticket_ids: list[str], cycle_name: str) -> dict:
        """Batch operation to add multiple tickets to a cycle and potentially transition them."""
        client, workspace_slug = get_plane_client_context()
        
        # Group tickets by project
        project_to_tickets = defaultdict(list)
        for t_id in ticket_ids:
            proj_id_str, _ = self.parse_ticket_id(t_id)
            w_id = self.resolver.resolve_ticket(t_id)
            project_to_tickets[proj_id_str].append(w_id)
            
        results = {}
        for proj_identifier, w_ids in project_to_tickets.items():
            proj_id = self.resolver.resolve_project(proj_identifier)
            cycle_id = self._resolve_or_create_cycle(proj_id, cycle_name)
            
            msg = f"Processed {len(w_ids)} tickets."
            
            if cycle_id:
                # Add all tickets in this project to the cycle; non-fatal on failure
                try:
                    client.cycles.add_work_items(
                        workspace_slug=workspace_slug,
                        project_id=proj_id,
                        cycle_id=cycle_id,
                        issue_ids=w_ids
                    )
                    msg += f" Added to cycle '{cycle_name}'."
                except Exception as e:
                    logger.warning(
                        "Failed to add tickets to cycle '%s' for project %s: %s", cycle_name, proj_identifier, e
                    )
                    msg += f" Warning: tickets processed but could not be added to cycle '{cycle_name}'."
            else:
                msg += " Note: Cycles are disabled for this project, skipped cycle assignment."

            # Optionally transition them to In Progress if such state exists
            try:
                state_id = self.resolver.resolve_state(proj_identifier, "In Progress")
                for w_id in w_ids:
                    client.work_items.update(
                        workspace_slug=workspace_slug,
                        project_id=proj_id,
                        work_item_id=w_id,
                        data=UpdateWorkItem(state=state_id)
                    )
            except EntityResolutionError:
                # "In Progress" state not found in this project — skip silently, it's optional
                pass
            except Exception as e:
                # Unexpected API error — log but don't fail the whole batch
                logger.warning("State transition to 'In Progress' failed for project %s: %s", proj_identifier, e)
                
            results[proj_identifier] = msg
            
        return {"status": "success", "details": results}

    def complete_work(self, ticket_id: str, comment: str) -> dict:
        work_item_id = self.resolver.resolve_ticket(ticket_id)
        project_identifier, _ = self.parse_ticket_id(ticket_id)
        project_id = self.resolver.resolve_project(project_identifier)
        
        client, workspace_slug = get_plane_client_context()
        
        # Add comment
        safe_comment = sanitize_html(f"<p>{comment}</p>")
        client.work_items.comments.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=work_item_id,
            data=CreateWorkItemComment(comment_html=safe_comment)
        )
        
        # Try to transition to Done or Completed
        state_id = None
        for state_name in ["Done", "Completed"]:
            try:
                state_id = self.resolver.resolve_state(project_identifier, state_name)
                break
            except ValueError:
                continue
                
        if state_id:
            updated = client.work_items.update(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
                data=UpdateWorkItem(state=state_id)
            )

            return self.apply_lod(updated, profile=LODProfile.SUMMARY, project_identifier=project_identifier)
            
        return {
            "status": "partial",
            "message": (
                "No workflow states found indicating a 'Done' or 'Completed' state found. "
                "Call transition_ticket explicitly to close this ticket."
            )
        }


def register_workflow_tools(mcp: FastMCP) -> None:
    def transition_ticket(ticket_id: str, state_name: str) -> dict:
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)
        journey = WorkflowJourney(resolver)
        raw_data = journey.transition_ticket(ticket_id, state_name)

        return raw_data
        
    transition_ticket.__doc__ = """
        Transition a ticket to a new state.
        Use this primitive for granular edge-case routing, such as moving a ticket to Canceled, 
        Duplicate, or custom review states.
        
        Args:
            ticket_id: The globally unique, human-readable identifier (e.g., ENG-123).
            state_name: The name of the state to transition to (e.g. 'In Progress').
        """
    transition_ticket = mcp.tool()(with_yaml(mcp_error_boundary(transition_ticket)))

    @mcp.tool()
    @with_yaml
    @mcp_error_boundary
    def begin_work(ticket_ids: list[str], cycle_name: str) -> dict:
        """
        Add multiple tickets to a cycle (creating it if missing) and attempt to transition them to 'In Progress'.
        Supports batch operations across multiple tickets and potentially multiple projects.
        Use this macro as your primary method for standard workflow progression (starting work).
        
        Args:
            ticket_ids: List of globally unique, human-readable identifiers (e.g. ['ENG-123', 'ENG-124']). 
                The system automatically resolves the project and issue routing from these prefixes.
            cycle_name: The name of the cycle to add tickets to. If you are unsure, make your best logical guess.
        """
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)
        journey = WorkflowJourney(resolver)
        raw_data = journey.begin_work(ticket_ids, cycle_name)

        return raw_data
    @mcp.tool()
    @with_yaml
    @mcp_error_boundary
    def complete_work(ticket_id: str, comment: str) -> dict:
        """
        Add a completion comment to a ticket and attempt to transition it to a 'Done' or 'Completed' state.
        Use this macro as your primary method for standard workflow progression (finishing work).
        
        Args:
            ticket_id: The globally unique, human-readable identifier (e.g., ENG-123). The system 
                automatically resolves the project and issue routing from this prefix; no separate 
                project context is needed.
            comment: The text to add as a comment.
        """
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)
        journey = WorkflowJourney(resolver)
        raw_data = journey.complete_work(ticket_id, comment)

        return raw_data
