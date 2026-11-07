"""Read tools for Journey Endpoint."""

from typing import Literal

from fastmcp import FastMCP
from plane.models.query_params import RetrieveQueryParams

from plane_mcp.client import get_plane_client_context
from plane_mcp.journey.base import JourneyBase, mcp_error_boundary
from plane_mcp.journey.lod import LODProfile
from plane_mcp.resolver import EntityResolver, is_valid_uuid
from plane_mcp.yaml_formatter import with_yaml


def _extract_member_list(resp: object) -> list:
    """Extract a list of member records from a Plane SDK response or raw list."""
    if isinstance(resp, list):
        return resp
    results = getattr(resp, "results", None)
    if isinstance(results, list):
        return results
    return []


def _match_member_id(members: list, target: str) -> str | None:
    """Match a target username/display_name/email against workspace/project members and return user UUID."""
    target_lower = target.strip().lower()
    if not target_lower:
        return None

    for m in members:
        nested = m.get("member") if isinstance(m, dict) else getattr(m, "member", None)
        candidates = [c for c in (nested, m) if c is not None]
        for candidate in candidates:
            for field in ("username", "display_name", "email", "first_name"):
                val = candidate.get(field) if isinstance(candidate, dict) else getattr(candidate, field, None)
                if isinstance(val, str) and val.strip().lower() == target_lower:
                    for id_source in candidates:
                        raw_id = id_source.get("id") if isinstance(id_source, dict) else getattr(id_source, "id", None)
                        if isinstance(raw_id, str) and raw_id.strip():
                            return raw_id.strip()
    return None


class ReadJourney(JourneyBase):
    """Journey handler for reading and searching Plane work items."""

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
        lod: Literal["summary", "standard", "full"] = "standard",
    ) -> dict:
        """Search and filter tickets within a project with deep text matching and pagination."""
        import json

        if project_slug.lower() == "help":
            return self.get_workspace_help_context()

        project_id = self.resolver.resolve_project(project_slug)
        client, workspace_slug = get_plane_client_context()

        if limit <= 0:
            return {"results": [], "next_cursor": None, "prev_cursor": None}
        limit = min(limit, 100)
        per_page = limit
        query_params: dict[str, object] = {
            "per_page": per_page,
            "expand": "assignees,labels,state",
        }

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
                if not label_ids:
                    return {
                        "status": "error",
                        "error": f"None of the requested labels could be resolved: {', '.join(unresolved_labels)}",
                        "results": [],
                        "next_cursor": None,
                        "prev_cursor": None,
                    }
                query_params["labels"] = ",".join(label_ids)
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(f"Label resolution failed, returning empty results: {e}")
                return {
                    "results": [],
                    "next_cursor": None,
                    "prev_cursor": None,
                    "warnings": [
                        f"Label filter could not be applied due to an error: {e}. "
                        "Please retry or check your label names."
                    ],
                }

        if assignees:
            assignee_ids: list[str] = []
            unresolved_assignees: list[str] = []
            members_cache: list | None = None

            for a in assignees:
                a_clean = a.strip() if isinstance(a, str) else ""
                if not a_clean:
                    unresolved_assignees.append(str(a))
                    continue

                if a_clean.lower() == "me":
                    resolved_me = False
                    try:
                        me = client.users.get_me()
                        me_id = getattr(me, "id", None)
                        if isinstance(me_id, str) and me_id:
                            assignee_ids.append(me_id)
                            resolved_me = True
                    except Exception:
                        pass
                    if not resolved_me:
                        unresolved_assignees.append(a)
                elif is_valid_uuid(a_clean):
                    assignee_ids.append(a_clean)
                else:
                    if members_cache is None:
                        members_cache = []
                        for getter in (
                            lambda: client.projects.get_members(workspace_slug=workspace_slug, project_id=project_id),
                            lambda: client.workspaces.get_members_lite(workspace_slug=workspace_slug),
                            lambda: client.workspaces.get_members(workspace_slug=workspace_slug),
                        ):
                            try:
                                extracted = _extract_member_list(getter())
                                if extracted:
                                    members_cache.extend(extracted)
                            except Exception:
                                continue

                    matched_id = _match_member_id(members_cache, a_clean)
                    if matched_id:
                        assignee_ids.append(matched_id)
                    else:
                        unresolved_assignees.append(a)

            if unresolved_assignees or not assignee_ids:
                return {
                    "status": "error",
                    "error": (
                        f"Assignee filter could not be resolved for: {', '.join(unresolved_assignees)}. "
                        "Pass 'me', a user UUID, or a valid workspace member username/display_name."
                    ),
                    "results": [],
                    "next_cursor": None,
                    "prev_cursor": None,
                }
            query_params["assignees"] = ",".join(assignee_ids)

        from plane.models.work_items import PaginatedWorkItemResponse

        matched_results = []
        skip_matches = 0
        current_cursor = cursor
        if cursor and "|" in cursor:
            raw_cursor, offset_str = cursor.rsplit("|", 1)
            if offset_str.isdigit():
                skip_matches = int(offset_str)
                current_cursor = None if raw_cursor == ":initial" else raw_cursor

        next_cursor_to_return = None
        paginated = None

        query_lower = query.lower() if query else None

        max_pages_to_fetch = 5
        pages_fetched = 0

        # Loop to pull pages and deep-search until we hit the requested limit or run out of pages
        while len(matched_results) < limit and pages_fetched < max_pages_to_fetch:
            page_cursor = current_cursor
            if current_cursor:
                query_params["cursor"] = current_cursor
            else:
                query_params.pop("cursor", None)

            response = client.work_items._get(f"{workspace_slug}/projects/{project_id}/work-items", params=query_params)
            pages_fetched += 1

            paginated = PaginatedWorkItemResponse.model_validate(response)

            page_matches = []
            for item in paginated.results:
                if query_lower:
                    # Deep JSON string match
                    item_json = json.dumps(item.model_dump(), default=str).lower()
                    if query_lower in item_json:
                        page_matches.append(item)
                else:
                    page_matches.append(item)

            page_skip = skip_matches
            skip_matches = 0
            available_matches = page_matches[page_skip:]
            needed = limit - len(matched_results)

            if len(available_matches) > needed:
                matched_results.extend(available_matches[:needed])
                next_cursor_to_return = f"{page_cursor or ':initial'}|{page_skip + needed}"
                break

            matched_results.extend(available_matches)
            next_cursor_to_return = getattr(paginated, "next_cursor", None)
            if (
                not next_cursor_to_return
                or not isinstance(next_cursor_to_return, str)
                or not next_cursor_to_return.strip()
            ):
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
            "prev_cursor": paginated.prev_cursor if paginated and hasattr(paginated, "prev_cursor") else None,
        }
        if unresolved_labels:
            result["warnings"] = [f"Label not found and filter was skipped: {', '.join(unresolved_labels)}"]
        return result

    def read_ticket(
        self, ticket_id: str, lod: Literal["summary", "standard", "full"] = "standard", comments: bool = False
    ) -> dict:
        """Retrieve a single ticket by identifier with optional comments and LOD formatting."""
        work_item_id = self.resolver.resolve_ticket(ticket_id)
        project_identifier, _issue_sequence = self.parse_ticket_id(ticket_id)
        project_id = self.resolver.resolve_project(project_identifier)

        client, workspace_slug = get_plane_client_context()

        params = RetrieveQueryParams(expand="assignees,labels,state")

        result = client.work_items.retrieve(
            workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id, params=params
        )

        try:
            profile = LODProfile(lod)
        except ValueError:
            profile = LODProfile.STANDARD

        transformed = self.apply_lod(result, profile=profile, project_identifier=project_identifier)

        if comments:
            try:
                comments_resp = client.work_items.comments.list(
                    workspace_slug=workspace_slug, project_id=project_id, work_item_id=work_item_id
                )
                formatted_comments = []
                for c in comments_resp.results:
                    date_str = str(c.created_at)[:10] if hasattr(c, "created_at") and c.created_at else "YYYY-MM-DD"

                    username = "user"
                    # Try to extract the best available identifier
                    if hasattr(c, "actor_detail") and c.actor_detail:
                        username = getattr(
                            c.actor_detail, "display_name", getattr(c.actor_detail, "username", username)
                        )
                    elif hasattr(c, "actor") and c.actor:
                        username = str(c.actor)

                    text = getattr(c, "comment_stripped", "") or getattr(c, "comment_html", "")
                    formatted_comments.append(f"\n{date_str}-@{username}:\n{text}")

                if formatted_comments:
                    transformed["comments"] = "".join(formatted_comments)
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(f"Failed to fetch comments for {ticket_id}: {e}")

        return transformed


def register_read_tools(mcp: FastMCP) -> None:
    """Register Journey ticket reading and searching tools on the FastMCP server."""

    def search_tickets(
        project_slug: str,
        query: str | None = None,
        labels: list[str] | None = None,
        priority: list[str] | None = None,
        states: list[str] | None = None,
        assignees: list[str] | None = None,
        limit: int = 50,
        cursor: str | None = None,
        lod: Literal["summary", "standard", "full"] = "standard",
    ) -> dict:
        """Search for issues using filters or a text query."""
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)
        journey = ReadJourney(resolver)
        raw_data = journey.search_tickets(project_slug, query, labels, priority, states, assignees, limit, cursor, lod)

        if project_slug.lower() == "help":
            return raw_data

        # Strip HTML tags from description fields in-place for clean human+machine output
        import re

        _html_re = re.compile(r"<[^>]+>")
        for item in raw_data.get("results", []):
            if item.get("description"):
                item["description"] = " ".join(_html_re.sub("", item["description"]).split()).strip()

        return raw_data

    search_tickets.__doc__ = """
        Search for issues. You can use standard filters or a text query.
        If the desired result is not in the current page, call again with the provided next_cursor.

        Args:
            project_slug: The Plane project identifier (e.g., 'PLANE' or 'TEST'). To discover valid project slugs, 
                states, and labels, call this tool with project_slug='help'.
            query: Free-form text search query.
            labels: List of label names to filter by (e.g., ['bug', 'feature']).
            priority: List of priorities to filter by (e.g., ['urgent', 'high', 'medium', 'low', 'none']).
            states: List of state names to filter by (e.g., ['In Progress', 'Backlog', 'Done']).
            assignees: List of usernames or 'me' to filter by.
            limit: Maximum number of results to return (max 100). Default is 50.
            cursor: Pagination cursor for getting the next set of results.
            lod: Level of Detail profile ("summary", "standard", or "full"). Default is "standard".
        """
    search_tickets = mcp.tool()(with_yaml(mcp_error_boundary(search_tickets)))

    @mcp.tool()
    @with_yaml
    @mcp_error_boundary
    def read_ticket(
        ticket_id: str, lod: Literal["summary", "standard", "full"] = "standard", comments: bool = False
    ) -> dict:
        """
        Read the details of a single ticket.

        Args:
            ticket_id: The globally unique, human-readable identifier (e.g., ENG-123). The system automatically
                resolves the project and issue routing from this prefix.
            lod: Level of Detail profile ("summary", "standard", "full"). Default is "standard".
            comments: If true, fetches and appends the ticket's comments to the result.
        """
        client, workspace_slug = get_plane_client_context()
        resolver = EntityResolver(client, workspace_slug)
        journey = ReadJourney(resolver)
        raw_data = journey.read_ticket(ticket_id, lod, comments)

        # Strip HTML tags from description, preserving newlines and Markdown structure
        import re

        if raw_data.get("description"):
            raw_data["description"] = re.sub(r"<[^>]+>", "", raw_data["description"]).strip()

        return raw_data
