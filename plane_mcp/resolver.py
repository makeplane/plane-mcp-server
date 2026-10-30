"""Stateless EntityResolver for mapping human-readable identifiers to Plane UUIDs.

Supports projects, workflow states, work items, pages/docs, collections, and templates
with per-workspace isolation, input validation, and actionable resolution errors.
"""

from __future__ import annotations

import time
import uuid

from plane import PlaneClient
from plane.errors.errors import HttpError
from plane.models.query_params import PaginatedQueryParams


class EntityResolutionError(ValueError):
    """Exception raised when an entity cannot be resolved, containing actionable options."""

    def __init__(self, message: str, available_options: list[str] | None = None):
        super().__init__(message)
        self.available_options = available_options or []


_GLOBAL_PROJECT_CACHE: dict[str, dict[str, str]] = {}
_GLOBAL_STATE_CACHE: dict[str, dict[str, dict[str, str]]] = {}
_GLOBAL_WORK_ITEM_CACHE: dict[str, dict[str, str]] = {}
_GLOBAL_PAGE_CACHE: dict[str, dict[str, tuple[str, str | None]]] = {}
_GLOBAL_COLLECTION_CACHE: dict[str, dict[str, str]] = {}
_GLOBAL_TEMPLATE_CACHE: dict[str, dict[str, str]] = {}

_CACHE_LAST_UPDATED: dict[str, dict[str, float]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes
_MAX_CACHE_ENTRIES = 2048


def validate_identifier(value: str, field_name: str, max_len: int = 128) -> str:
    """Validate string identifier against null bytes, control characters, and length limits."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} cannot be empty or whitespace only")
    if len(stripped) > max_len:
        raise ValueError(f"{field_name} exceeds maximum length of {max_len} characters")
    if any(ord(c) < 32 or ord(c) == 127 or (0x80 <= ord(c) <= 0x9F) for c in stripped):
        raise ValueError(f"{field_name} contains invalid control characters or null bytes")
    if any(token in stripped for token in ("../", "..\\", "/..", "\\..")):
        raise ValueError(f"{field_name} contains path traversal sequence")
    return stripped


def is_valid_uuid(val: str) -> bool:
    """Check if a string is a valid UUID."""
    try:
        uuid.UUID(str(val))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


class EntityResolver:
    """Stateless resolver for mapping human-readable identifiers to Plane UUIDs."""

    def __init__(self, client: PlaneClient, workspace_slug: str):
        self.client = client
        self.workspace_slug = validate_identifier(workspace_slug, "workspace_slug", max_len=64)

        _GLOBAL_PROJECT_CACHE.setdefault(self.workspace_slug, {})
        _GLOBAL_STATE_CACHE.setdefault(self.workspace_slug, {})
        _GLOBAL_WORK_ITEM_CACHE.setdefault(self.workspace_slug, {})
        _GLOBAL_PAGE_CACHE.setdefault(self.workspace_slug, {})
        _GLOBAL_COLLECTION_CACHE.setdefault(self.workspace_slug, {})
        _GLOBAL_TEMPLATE_CACHE.setdefault(self.workspace_slug, {})
        _CACHE_LAST_UPDATED.setdefault(
            self.workspace_slug,
            {
                "projects": 0.0,
                "states": 0.0,
                "work_items": 0.0,
                "pages": 0.0,
                "collections": 0.0,
                "templates": 0.0,
            },
        )

        self._project_cache = _GLOBAL_PROJECT_CACHE[self.workspace_slug]
        self._state_cache = _GLOBAL_STATE_CACHE[self.workspace_slug]
        self._work_item_cache = _GLOBAL_WORK_ITEM_CACHE[self.workspace_slug]
        self._page_cache = _GLOBAL_PAGE_CACHE[self.workspace_slug]
        self._collection_cache = _GLOBAL_COLLECTION_CACHE[self.workspace_slug]
        self._template_cache = _GLOBAL_TEMPLATE_CACHE[self.workspace_slug]
        self._ts = _CACHE_LAST_UPDATED[self.workspace_slug]

    def _bound_cache(self, cache: dict) -> None:
        if len(cache) > _MAX_CACHE_ENTRIES:
            evict_count = len(cache) // 4
            for _ in range(evict_count):
                if cache:
                    cache.pop(next(iter(cache)))

    def resolve_project(self, identifier_or_slug: str) -> str:
        val = validate_identifier(identifier_or_slug, "project_identifier", max_len=64)
        if is_valid_uuid(val):
            return val

        key = val.upper()
        if key in self._project_cache and (time.time() - self._ts["projects"] < _CACHE_TTL_SECONDS):
            return self._project_cache[key]

        try:
            projects_resp = self.client.projects.list(
                workspace_slug=self.workspace_slug,
                params=PaginatedQueryParams(per_page=100),
            )
            available = []
            for p in projects_resp.results:
                p_id = str(p.id)
                self._project_cache[p.identifier.upper()] = p_id
                self._project_cache[p.name.upper()] = p_id
                available.append(p.identifier)
                if hasattr(p, "slug") and p.slug:
                    self._project_cache[p.slug.upper()] = p_id

            self._ts["projects"] = time.time()
            self._bound_cache(self._project_cache)

            if key in self._project_cache:
                return self._project_cache[key]

            raise EntityResolutionError(
                f"Project '{identifier_or_slug}' not found. Available projects: {', '.join(available)}",
                available_options=available,
            )
        except Exception as e:
            if isinstance(e, EntityResolutionError):
                raise
            raise RuntimeError(f"Failed to fetch projects: {e!s}") from e

    def resolve_state(self, project_identifier: str, state_name: str) -> str:
        val = validate_identifier(state_name, "state_name", max_len=128)
        if is_valid_uuid(val):
            return val

        project_id = self.resolve_project(project_identifier)
        key = val.lower()

        if (
            project_id in self._state_cache
            and key in self._state_cache[project_id]
            and (time.time() - self._ts["states"] < _CACHE_TTL_SECONDS)
        ):
            return self._state_cache[project_id][key]

        try:
            states_resp = self.client.states.list(
                workspace_slug=self.workspace_slug,
                project_id=project_id,
            )
            if project_id not in self._state_cache:
                self._state_cache[project_id] = {}

            available = []
            for s in states_resp.results:
                s_id = str(s.id)
                self._state_cache[project_id][s.name.lower()] = s_id
                available.append(s.name)

            self._ts["states"] = time.time()
            self._bound_cache(self._state_cache[project_id])

            if key in self._state_cache[project_id]:
                return self._state_cache[project_id][key]

            raise EntityResolutionError(
                f"State '{state_name}' not found for project '{project_identifier}'. "
                f"Available states: {', '.join(available)}",
                available_options=available,
            )
        except Exception as e:
            if isinstance(e, EntityResolutionError):
                raise
            raise RuntimeError(f"Failed to fetch states for project {project_identifier}: {e!s}") from e

    def resolve_ticket(self, ticket_id: str) -> str:
        val = validate_identifier(ticket_id, "ticket_id", max_len=64)
        if is_valid_uuid(val):
            return val

        key = val.upper()
        if key in self._work_item_cache and (time.time() - self._ts["work_items"] < _CACHE_TTL_SECONDS):
            return self._work_item_cache[key]

        parts = key.split("-")
        if len(parts) != 2:
            raise ValueError(f"Invalid ticket ID format: '{ticket_id}'. Expected format like 'ENG-123'.")

        project_identifier = parts[0]
        try:
            issue_sequence = int(parts[1])
            if issue_sequence <= 0:
                raise ValueError
        except ValueError:
            msg = f"Invalid ticket sequence in '{ticket_id}'. Must be a positive integer (e.g., 123)."
            raise ValueError(msg) from None

        self.resolve_project(project_identifier)

        try:
            response = self.client.work_items._get(
                f"{self.workspace_slug}/work-items/{project_identifier}-{issue_sequence}"
            )
            work_item_id = response.get("id") if isinstance(response, dict) else getattr(response, "id", None)
            if not work_item_id:
                raise EntityResolutionError(f"Ticket '{ticket_id}' not found.")

            self._work_item_cache[key] = str(work_item_id)
            self._ts["work_items"] = time.time()
            self._bound_cache(self._work_item_cache)
            return str(work_item_id)
        except HttpError as e:
            if getattr(e, "status_code", None) == 404 or "404" in str(e):
                raise EntityResolutionError(
                    f"Ticket '{ticket_id}' not found.",
                    available_options=[],
                ) from e
            raise RuntimeError(f"Failed to retrieve ticket '{ticket_id}': {e!s}") from e
        except Exception as e:
            if isinstance(e, EntityResolutionError):
                raise
            raise RuntimeError(f"Failed to retrieve ticket '{ticket_id}': {e!s}") from e

    def resolve_page(
        self,
        identifier: str,
        project_slug: str | None = None,
        collection: str | None = None,
    ) -> tuple[str, str | None]:
        val = validate_identifier(identifier, "page", max_len=255)
        project_id = self.resolve_project(project_slug) if project_slug else None

        if is_valid_uuid(val):
            return val, project_id

        cache_key = f"{project_id or 'workspace'}:{val.lower()}"
        if cache_key in self._page_cache and (time.time() - self._ts["pages"] < _CACHE_TTL_SECONDS):
            return self._page_cache[cache_key]

        try:
            available = []
            matched_id = None
            if project_id:
                pages_resp = self.client.pages.list_project_pages(
                    workspace_slug=self.workspace_slug,
                    project_id=project_id,
                )
                items = getattr(pages_resp, "results", pages_resp if isinstance(pages_resp, list) else [])
                for p in items:
                    name = str(getattr(p, "name", "") or "")
                    available.append(name)
                    if name.lower() == val.lower():
                        matched_id = str(p.id)
            else:
                pages_resp = self.client.pages.list_workspace_pages(
                    workspace_slug=self.workspace_slug,
                )
                items = getattr(pages_resp, "results", pages_resp if isinstance(pages_resp, list) else [])
                for p in items:
                    name = str(getattr(p, "name", "") or "")
                    available.append(name)
                    if name.lower() == val.lower():
                        matched_id = str(p.id)

            self._ts["pages"] = time.time()
            if matched_id:
                res = (matched_id, project_id)
                self._page_cache[cache_key] = res
                self._bound_cache(self._page_cache)
                return res

            scope_desc = f"project '{project_slug}'" if project_slug else "workspace"
            raise EntityResolutionError(
                f"Page '{identifier}' not found in {scope_desc}. Available pages: {', '.join(available[:20])}",
                available_options=available[:20],
            )
        except Exception as e:
            if isinstance(e, EntityResolutionError):
                raise
            raise RuntimeError(f"Failed to resolve page '{identifier}': {e!s}") from e

    def resolve_collection(self, identifier: str) -> str:
        val = validate_identifier(identifier, "collection", max_len=128)
        if is_valid_uuid(val):
            return val

        key = val.lower()
        if key in self._collection_cache and (time.time() - self._ts["collections"] < _CACHE_TTL_SECONDS):
            return self._collection_cache[key]

        try:
            collections_resp = self.client.collections.list(workspace_slug=self.workspace_slug)
            items = getattr(collections_resp, "results", collections_resp if isinstance(collections_resp, list) else [])
            available = []
            for c in items:
                name = str(getattr(c, "name", "") or "")
                c_id = str(c.id)
                self._collection_cache[name.lower()] = c_id
                available.append(name)

            self._ts["collections"] = time.time()
            self._bound_cache(self._collection_cache)

            if key in self._collection_cache:
                return self._collection_cache[key]

            raise EntityResolutionError(
                f"Collection '{identifier}' not found. Available collections: {', '.join(available)}",
                available_options=available,
            )
        except Exception as e:
            if isinstance(e, EntityResolutionError):
                raise
            raise RuntimeError(f"Failed to resolve collection '{identifier}': {e!s}") from e

    def resolve_template(self, identifier: str, kind: str = "page", project_slug: str | None = None) -> str:
        val = validate_identifier(identifier, "template", max_len=128)
        if is_valid_uuid(val):
            return val

        project_id = self.resolve_project(project_slug) if project_slug else None
        key = f"{project_id or 'workspace'}:{kind}:{val.lower()}"
        if key in self._template_cache and (time.time() - self._ts["templates"] < _CACHE_TTL_SECONDS):
            return self._template_cache[key]

        try:
            available = []
            matched_id = None
            if project_id:
                templates = self.client.project_templates.page_templates.list(
                    workspace_slug=self.workspace_slug, project_id=project_id
                )
            else:
                templates = self.client.workspace_templates.pages.list(workspace_slug=self.workspace_slug)

            items = getattr(templates, "results", templates if isinstance(templates, list) else [])
            for t in items:
                name = str(getattr(t, "name", "") or "")
                t_id = str(t.id)
                available.append(name)
                if name.lower() == val.lower():
                    matched_id = t_id

            self._ts["templates"] = time.time()
            if matched_id:
                self._template_cache[key] = matched_id
                self._bound_cache(self._template_cache)
                return matched_id

            raise EntityResolutionError(
                f"Template '{identifier}' not found. Available templates: {', '.join(available)}",
                available_options=available,
            )
        except Exception as e:
            if isinstance(e, EntityResolutionError):
                raise
            raise RuntimeError(f"Failed to resolve template '{identifier}': {e!s}") from e
