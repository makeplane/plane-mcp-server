"""Stateless EntityResolver for mapping human-readable identifiers to Plane UUIDs."""

import time

from plane import PlaneClient
from plane.errors.errors import HttpError
from plane.models.query_params import PaginatedQueryParams


class EntityResolutionError(ValueError):
    """Exception raised when an entity cannot be resolved, containing actionable options."""
    def __init__(self, message: str, available_options: list[str] | None = None):
        super().__init__(message)
        self.available_options = available_options or []


# Global caches to prevent N+1 queries across tool invocations in the same process
_GLOBAL_PROJECT_CACHE: dict[str, str] = {}
_GLOBAL_STATE_CACHE: dict[str, dict[str, str]] = {}
_GLOBAL_WORK_ITEM_CACHE: dict[str, str] = {}
_CACHE_LAST_UPDATED: dict[str, float] = {"projects": 0.0, "states": 0.0, "work_items": 0.0}
_CACHE_TTL_SECONDS = 300  # 5 minutes

class EntityResolver:
    """
    Stateless resolver for mapping human-readable identifiers to Plane UUIDs.
    Implements global caching to prevent N+1 API calls and returns Actionable Errors
    when an entity cannot be found.
    """

    def __init__(self, client: PlaneClient, workspace_slug: str):
        self.client = client
        self.workspace_slug = workspace_slug
        
        # We now use the global caches
        self._project_cache = _GLOBAL_PROJECT_CACHE
        self._state_cache = _GLOBAL_STATE_CACHE
        self._work_item_cache = _GLOBAL_WORK_ITEM_CACHE

    def resolve_project(self, identifier_or_slug: str) -> str:
        """
        Resolve a project identifier (e.g. 'ENG') or slug to its UUID.
        """
        key = identifier_or_slug.upper()
        if key in self._project_cache and (time.time() - _CACHE_LAST_UPDATED["projects"] < _CACHE_TTL_SECONDS):
            return self._project_cache[key]

        # Fetch all projects to cache and find
        try:
            projects_resp = self.client.projects.list(
                workspace_slug=self.workspace_slug, 
                params=PaginatedQueryParams(per_page=100)
            )
            available = []
            for p in projects_resp.results:
                self._project_cache[p.identifier.upper()] = p.id
                self._project_cache[p.name.upper()] = p.id
                available.append(p.identifier)
                if hasattr(p, 'slug') and p.slug:
                    self._project_cache[p.slug.upper()] = p.id

            _CACHE_LAST_UPDATED["projects"] = time.time()

            if key in self._project_cache:
                return self._project_cache[key]

            raise EntityResolutionError(
                f"Project '{identifier_or_slug}' not found. Available projects: {', '.join(available)}",
                available_options=available
            )
        except Exception as e:
            if isinstance(e, EntityResolutionError):
                raise
            raise RuntimeError(f"Failed to fetch projects: {e!s}") from e

    def resolve_state(self, project_identifier: str, state_name: str) -> str:
        """
        Resolve a state name (e.g. 'In Progress') to its UUID for a specific project.
        """
        project_id = self.resolve_project(project_identifier)
        key = state_name.lower()

        if project_id in self._state_cache and key in self._state_cache[project_id] and (time.time() - _CACHE_LAST_UPDATED["states"] < _CACHE_TTL_SECONDS):
            return self._state_cache[project_id][key]

        try:
            # We don't have the states in cache for this project, fetch them
            states_resp = self.client.states.list(
                workspace_slug=self.workspace_slug, 
                project_id=project_id
            )
            
            if project_id not in self._state_cache:
                self._state_cache[project_id] = {}
            
            available = []
            for s in states_resp.results:
                self._state_cache[project_id][s.name.lower()] = s.id
                available.append(s.name)

            _CACHE_LAST_UPDATED["states"] = time.time()

            if key in self._state_cache[project_id]:
                return self._state_cache[project_id][key]

            raise EntityResolutionError(
                f"State '{state_name}' not found for project '{project_identifier}'. Available states: {', '.join(available)}",
                available_options=available
            )
        except Exception as e:
            if isinstance(e, EntityResolutionError):
                raise
            raise RuntimeError(f"Failed to fetch states for project {project_identifier}: {e!s}") from e

    def resolve_ticket(self, ticket_id: str) -> str:
        """
        Resolve a ticket ID (e.g. 'ENG-123') to its work_item UUID.
        """
        key = ticket_id.upper()
        if key in self._work_item_cache and (time.time() - _CACHE_LAST_UPDATED["work_items"] < _CACHE_TTL_SECONDS):
            return self._work_item_cache[key]
        
        parts = key.split('-')
        if len(parts) != 2:
            raise ValueError(f"Invalid ticket ID format: '{ticket_id}'. Expected format like 'ENG-123'.")
        
        project_identifier = parts[0]
        try:
            issue_sequence = int(parts[1])
        except ValueError:
            raise ValueError(f"Invalid ticket sequence in '{ticket_id}'. Must be an integer (e.g., 123).")

        # Resolve project to ensure the project exists and is valid
        self.resolve_project(project_identifier)
        
        try:
            # Bypass Pydantic validation by using internal _get, as the WorkItemDetail 
            # model currently has an issue with assignees parsing (list of str vs UserLite).
            response = self.client.work_items._get(
                f"{self.workspace_slug}/work-items/{project_identifier}-{issue_sequence}"
            )
            work_item_id = response.get('id')
            if not work_item_id:
                raise EntityResolutionError(f"Ticket '{ticket_id}' not found.")
                
            self._work_item_cache[key] = work_item_id
            _CACHE_LAST_UPDATED["work_items"] = time.time()
            return work_item_id
        except HttpError as e:
            if getattr(e, "status_code", None) == 404:
                raise EntityResolutionError(
                    f"Ticket '{ticket_id}' not found.",
                    available_options=[]
                ) from e
            raise RuntimeError(f"Failed to retrieve ticket '{ticket_id}': {e!s}") from e
        except Exception as e:
            if isinstance(e, EntityResolutionError):
                raise
            raise RuntimeError(f"Failed to retrieve ticket '{ticket_id}': {e!s}") from e
