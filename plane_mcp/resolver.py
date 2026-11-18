"""Stateless EntityResolver for mapping human-readable identifiers to Plane UUIDs.

Supports projects, workflow states, work items, pages/docs, collections, and templates
with per-workspace isolation, input validation, and actionable resolution errors.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from plane import PlaneClient
from plane.errors.errors import HttpError
from plane.models.query_params import PaginatedQueryParams


class EntityResolutionError(ValueError):
    """Exception raised when an entity cannot be resolved, containing actionable options."""

    def __init__(self, message: str, available_options: list[str] | None = None):
        """Initialize the resolution error with a message and optional list of valid identifiers."""
        super().__init__(message)
        self.available_options = available_options or []


_GLOBAL_PROJECT_CACHE: dict[str, dict[str, str]] = {}
_GLOBAL_STATE_CACHE: dict[str, dict[str, dict[str, str]]] = {}
_GLOBAL_WORK_ITEM_CACHE: dict[str, dict[str, str]] = {}
_GLOBAL_TICKET_CONTEXT_CACHE: dict[str, dict[str, tuple[str, str, str | None]]] = {}
_GLOBAL_PAGE_CACHE: dict[str, dict[str, tuple[str, str | None]]] = {}
_GLOBAL_COLLECTION_CACHE: dict[str, dict[str, str]] = {}
_GLOBAL_TEMPLATE_CACHE: dict[str, dict[str, str]] = {}
_GLOBAL_TEMPLATE_BODY_CACHE: dict[str, dict[str, str | None]] = {}

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


def _extract_template_body(template_obj: Any) -> str | None:
    """Extract HTML or text body content from a template model or dictionary."""
    if isinstance(template_obj, dict):
        for field in ("description_html", "body", "content", "description"):
            val = template_obj.get(field)
            if isinstance(val, str) and val.strip():
                return val
        return None

    for field in ("description_html", "body", "content", "description"):
        val = getattr(template_obj, field, None)
        if isinstance(val, str) and val.strip():
            return val
    return None


class EntityResolver:
    """Stateless resolver for mapping human-readable identifiers to Plane UUIDs."""

    def __init__(self, client: PlaneClient, workspace_slug: str):
        """Initialize the resolver for a Plane client and validated workspace slug."""
        self.client = client
        self.workspace_slug = validate_identifier(workspace_slug, "workspace_slug", max_len=64)

        _GLOBAL_PROJECT_CACHE.setdefault(self.workspace_slug, {})
        _GLOBAL_STATE_CACHE.setdefault(self.workspace_slug, {})
        _GLOBAL_WORK_ITEM_CACHE.setdefault(self.workspace_slug, {})
        _GLOBAL_TICKET_CONTEXT_CACHE.setdefault(self.workspace_slug, {})
        _GLOBAL_PAGE_CACHE.setdefault(self.workspace_slug, {})
        _GLOBAL_COLLECTION_CACHE.setdefault(self.workspace_slug, {})
        _GLOBAL_TEMPLATE_CACHE.setdefault(self.workspace_slug, {})
        _GLOBAL_TEMPLATE_BODY_CACHE.setdefault(self.workspace_slug, {})
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
        self._ticket_context_cache = _GLOBAL_TICKET_CONTEXT_CACHE[self.workspace_slug]
        self._page_cache = _GLOBAL_PAGE_CACHE[self.workspace_slug]
        self._collection_cache = _GLOBAL_COLLECTION_CACHE[self.workspace_slug]
        self._template_cache = _GLOBAL_TEMPLATE_CACHE[self.workspace_slug]
        self._template_body_cache = _GLOBAL_TEMPLATE_BODY_CACHE[self.workspace_slug]
        self._ts = _CACHE_LAST_UPDATED[self.workspace_slug]

    def _bound_cache(self, cache: dict[str, Any], ts_prefix: str | None = None) -> None:
        """Evict oldest quarter of entries when a cache exceeds _MAX_CACHE_ENTRIES."""
        if len(cache) > _MAX_CACHE_ENTRIES:
            evict_count = len(cache) // 4
            for _ in range(evict_count):
                if cache:
                    oldest_key = next(iter(cache))
                    cache.pop(oldest_key)
                    if ts_prefix:
                        self._ts.pop(f"{ts_prefix}:{oldest_key}", None)

    def _fetch_all_projects(self) -> list[Any]:
        """Fetch all projects across paginated responses from the Plane API."""
        all_projects: list[Any] = []
        seen_cursors: set[str] = set()
        cursor: str | None = None

        while True:
            params = (
                PaginatedQueryParams(per_page=100)
                if cursor is None
                else PaginatedQueryParams(per_page=100, cursor=cursor)
            )
            projects_resp = self.client.projects.list(
                workspace_slug=self.workspace_slug,
                params=params,
            )
            results = getattr(projects_resp, "results", None)
            if results:
                all_projects.extend(results)

            next_cursor = getattr(projects_resp, "next_cursor", None)
            next_page_results = getattr(projects_resp, "next_page_results", True)
            if (
                next_page_results is not False
                and isinstance(next_cursor, str)
                and bool(next_cursor.strip())
                and next_cursor not in seen_cursors
            ):
                seen_cursors.add(next_cursor)
                cursor = next_cursor
            else:
                break

        return all_projects

    def resolve_project(self, identifier_or_slug: str) -> str:
        """Resolve a project identifier, name, slug, or UUID to its canonical project UUID."""
        val = validate_identifier(identifier_or_slug, "project_identifier", max_len=64)
        if is_valid_uuid(val):
            return val

        key = val.upper()
        if key in self._project_cache and (time.time() - self._ts["projects"] < _CACHE_TTL_SECONDS):
            return self._project_cache[key]

        try:
            projects = self._fetch_all_projects()
            refreshed_cache: dict[str, str] = {}
            identifier_map: dict[str, str] = {}
            available: list[str] = []
            for p in projects:
                p_id = str(p.id)
                identifier = getattr(p, "identifier", None)
                if isinstance(identifier, str) and identifier:
                    identifier_map[identifier.upper()] = p_id
                    available.append(identifier)

                name = getattr(p, "name", None)
                if isinstance(name, str) and name:
                    refreshed_cache.setdefault(name.upper(), p_id)

                slug = getattr(p, "slug", None)
                if isinstance(slug, str) and slug:
                    refreshed_cache.setdefault(slug.upper(), p_id)

            refreshed_cache.update(identifier_map)
            self._project_cache.clear()
            self._project_cache.update(refreshed_cache)
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
        """Resolve a workflow state name or UUID for a given project to its state UUID."""
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
        """Resolve a human-readable ticket sequence ID (e.g. ENG-123) or UUID to its work item UUID."""
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

        project_id = self.resolve_project(project_identifier)

        try:
            response = self.client.work_items._get(
                f"{self.workspace_slug}/work-items/{project_identifier}-{issue_sequence}"
            )
            work_item_id = response.get("id") if isinstance(response, dict) else getattr(response, "id", None)
            if not work_item_id:
                raise EntityResolutionError(f"Ticket '{ticket_id}' not found.")

            resolved_id = str(work_item_id)
            now = time.time()
            self._work_item_cache[key] = resolved_id
            self._ts["work_items"] = now
            self._bound_cache(self._work_item_cache)

            ctx_key = resolved_id.lower()
            self._ticket_context_cache[ctx_key] = (resolved_id, project_id, project_identifier)
            self._ts[f"ticket_ctx:{ctx_key}"] = now
            self._bound_cache(self._ticket_context_cache, ts_prefix="ticket_ctx")
            return resolved_id
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

    def _extract_project_from_work_item(self, resp: Any) -> tuple[str | None, str | None]:
        """Extract project UUID and optional project identifier from a work item response."""
        if resp is None:
            return None, None

        if isinstance(resp, dict):
            raw_proj_id = resp.get("project_id") or resp.get("project")
            proj_detail = resp.get("project_detail")
            if isinstance(raw_proj_id, dict):
                proj_detail = proj_detail or raw_proj_id
                raw_proj_id = raw_proj_id.get("id")
            if not isinstance(raw_proj_id, str) and isinstance(proj_detail, dict):
                raw_proj_id = proj_detail.get("id")
            raw_ident = resp.get("project_identifier") or (
                proj_detail.get("identifier") if isinstance(proj_detail, dict) else None
            )
        else:
            raw_proj_id = getattr(resp, "project_id", None)
            if not isinstance(raw_proj_id, str):
                proj_attr = getattr(resp, "project", None)
                raw_proj_id = proj_attr if isinstance(proj_attr, str) else getattr(proj_attr, "id", None)
            proj_detail = getattr(resp, "project_detail", None)
            if not isinstance(raw_proj_id, str) and proj_detail is not None:
                raw_proj_id = getattr(proj_detail, "id", None)
            raw_ident = getattr(resp, "project_identifier", None) or getattr(proj_detail, "identifier", None)

        if not isinstance(raw_proj_id, str) or not raw_proj_id.strip():
            return None, None

        proj_id = raw_proj_id.strip()
        proj_ident = raw_ident.strip().upper() if isinstance(raw_ident, str) and raw_ident.strip() else None
        return proj_id, proj_ident

    def resolve_ticket_context(
        self,
        ticket_id: str,
        project_slug: str | None = None,
    ) -> tuple[str, str, str | None]:
        """Resolve a ticket sequence ID or UUID into (work_item_id, project_id, project_identifier)."""
        val = validate_identifier(ticket_id, "ticket_id", max_len=64)
        if not is_valid_uuid(val):
            key = val.upper()
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

            project_id = self.resolve_project(project_identifier)
            if project_slug:
                explicit_project_id = self.resolve_project(project_slug)
                if explicit_project_id != project_id:
                    raise EntityResolutionError(
                        f"Ticket '{ticket_id}' belongs to project '{project_identifier}', "
                        f"which does not match project_slug '{project_slug}'."
                    )
            work_item_id = self.resolve_ticket(val)
            ctx = (work_item_id, project_id, project_identifier)
            ctx_key = work_item_id.lower()
            self._ticket_context_cache[ctx_key] = ctx
            self._ts[f"ticket_ctx:{ctx_key}"] = time.time()
            self._bound_cache(self._ticket_context_cache, ts_prefix="ticket_ctx")
            return ctx

        if project_slug:
            project_id = self.resolve_project(project_slug)
            slug_clean = project_slug.strip()
            proj_ident = None if is_valid_uuid(slug_clean) else slug_clean.upper()
            ctx = (val, project_id, proj_ident)
            ctx_key = val.lower()
            self._ticket_context_cache[ctx_key] = ctx
            self._ts[f"ticket_ctx:{ctx_key}"] = time.time()
            self._bound_cache(self._ticket_context_cache, ts_prefix="ticket_ctx")
            return ctx

        cache_key = val.lower()
        ts_key = f"ticket_ctx:{cache_key}"
        if cache_key in self._ticket_context_cache:
            if time.time() - self._ts.get(ts_key, 0.0) < _CACHE_TTL_SECONDS:
                return self._ticket_context_cache[cache_key]
            self._ticket_context_cache.pop(cache_key, None)
            self._ts.pop(ts_key, None)

        try:
            resp = self.client.work_items._get(f"{self.workspace_slug}/work-items/{val}")
            proj_id, proj_ident = self._extract_project_from_work_item(resp)
            if proj_id:
                ctx = (val, proj_id, proj_ident)
                self._ticket_context_cache[cache_key] = ctx
                self._ts[ts_key] = time.time()
                self._bound_cache(self._ticket_context_cache, ts_prefix="ticket_ctx")
                return ctx
        except Exception:
            pass

        try:
            projects = self._fetch_all_projects()
        except Exception as e:
            raise RuntimeError(f"Failed to fetch projects while resolving ticket '{ticket_id}': {e!s}") from e

        for p in projects:
            p_id_raw = getattr(p, "id", None)
            if p_id_raw is None:
                continue
            p_id = str(p_id_raw)
            p_ident_raw = getattr(p, "identifier", None)
            p_ident = p_ident_raw.upper() if isinstance(p_ident_raw, str) and p_ident_raw.strip() else None
            try:
                item = self.client.work_items.retrieve(
                    workspace_slug=self.workspace_slug,
                    project_id=p_id,
                    work_item_id=val,
                )
            except HttpError as e:
                if getattr(e, "status_code", None) == 404 or "404" in str(e):
                    continue
                raise RuntimeError(f"Failed to retrieve ticket '{ticket_id}': {e!s}") from e
            except Exception:
                continue

            if not item:
                continue

            item_id = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
            if not isinstance(item_id, (str, uuid.UUID)) or str(item_id).strip().lower() != val.lower():
                continue

            ctx = (val, p_id, p_ident)
            self._ticket_context_cache[cache_key] = ctx
            self._ts[ts_key] = time.time()
            self._bound_cache(self._ticket_context_cache, ts_prefix="ticket_ctx")
            return ctx

        raise EntityResolutionError(
            f"Ticket '{ticket_id}' not found.",
            available_options=[],
        )

    def resolve_page(
        self,
        identifier: str,
        project_slug: str | None = None,
        collection: str | None = None,
    ) -> tuple[str, str | None]:
        """Resolve a page title or UUID into (page_id, project_id_or_none)."""
        val = validate_identifier(identifier, "page", max_len=255)
        project_id = self.resolve_project(project_slug) if project_slug else None

        if is_valid_uuid(val):
            return val, project_id

        cache_key = f"{project_id or 'workspace'}:{val.lower()}"
        ts_key = f"page:{cache_key}"
        if cache_key in self._page_cache:
            if time.time() - self._ts.get(ts_key, 0.0) < _CACHE_TTL_SECONDS:
                return self._page_cache[cache_key]
            self._page_cache.pop(cache_key, None)
            self._ts.pop(ts_key, None)

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

            if matched_id:
                now = time.time()
                res = (matched_id, project_id)
                self._page_cache[cache_key] = res
                self._ts[ts_key] = now
                self._ts["pages"] = now
                self._bound_cache(self._page_cache, ts_prefix="page")
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
        """Resolve a collection name or UUID to its canonical collection UUID."""
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

    def resolve_template_with_body(
        self,
        identifier: str,
        kind: str = "page",
        project_slug: str | None = None,
    ) -> tuple[str, str | None]:
        """Resolve a template name or UUID into (template_id, template_body_html_or_none)."""
        val = validate_identifier(identifier, "template", max_len=128)
        project_id = self.resolve_project(project_slug) if project_slug else None
        key = f"{project_id or 'workspace'}:{kind}:{val.lower()}"

        if (
            key in self._template_cache
            and key in self._template_body_cache
            and (time.time() - self._ts["templates"] < _CACHE_TTL_SECONDS)
        ):
            return self._template_cache[key], self._template_body_cache.get(key)

        try:
            available: list[str] = []
            matched_id: str | None = None
            matched_body: str | None = None
            if project_id:
                templates = self.client.project_templates.page_templates.list(
                    workspace_slug=self.workspace_slug, project_id=project_id
                )
            else:
                templates = self.client.workspace_templates.pages.list(workspace_slug=self.workspace_slug)

            items = getattr(templates, "results", templates if isinstance(templates, list) else [])
            for t in items:
                if isinstance(t, dict):
                    name = str(t.get("name") or "")
                    t_id = str(t.get("id") or "")
                else:
                    name = str(getattr(t, "name", "") or "")
                    t_id = str(getattr(t, "id", "") or "")
                available.append(name)
                if name.lower() == val.lower() or (is_valid_uuid(val) and t_id.lower() == val.lower()):
                    matched_id = t_id
                    matched_body = _extract_template_body(t)

            self._ts["templates"] = time.time()
            if matched_id:
                self._template_cache[key] = matched_id
                self._template_body_cache[key] = matched_body
                self._bound_cache(self._template_cache)
                self._bound_cache(self._template_body_cache)
                return matched_id, matched_body

            if is_valid_uuid(val):
                self._template_cache[key] = val
                self._template_body_cache[key] = None
                self._bound_cache(self._template_cache)
                self._bound_cache(self._template_body_cache)
                return val, None

            raise EntityResolutionError(
                f"Template '{identifier}' not found. Available templates: {', '.join(available)}",
                available_options=available,
            )
        except Exception as e:
            if isinstance(e, EntityResolutionError):
                raise
            if is_valid_uuid(val):
                return val, None
            raise RuntimeError(f"Failed to resolve template '{identifier}': {e!s}") from e

    def resolve_template(self, identifier: str, kind: str = "page", project_slug: str | None = None) -> str:
        """Resolve a template name or UUID to its canonical template UUID."""
        val = validate_identifier(identifier, "template", max_len=128)
        if is_valid_uuid(val):
            return val

        project_id = self.resolve_project(project_slug) if project_slug else None
        key = f"{project_id or 'workspace'}:{kind}:{val.lower()}"
        if key in self._template_cache and (time.time() - self._ts["templates"] < _CACHE_TTL_SECONDS):
            return self._template_cache[key]

        template_id, _ = self.resolve_template_with_body(identifier, kind=kind, project_slug=project_slug)
        return template_id
