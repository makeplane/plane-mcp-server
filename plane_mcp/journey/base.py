"""Base classes and utilities for Journey-based AI tools."""

import logging
import re
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar, cast

from pydantic import ValidationError

from plane_mcp.journey.lod import LODProfile, apply_lod
from plane_mcp.resolver import EntityResolutionError, EntityResolver

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=Callable[..., Any])

_SAFE_ERROR_TYPES = (ValueError, EntityResolutionError)
_PATH_PATTERN = re.compile(r"(?:/[\w.-]+)+(?::\d+)?")
_KV_PATTERN = re.compile(r"\b[\w.-]+=[^\s,;)]+")


def _sanitize_error_message(exc: Exception, func_name: str) -> str:
    """Return a client-safe error message for tool responses without leaking internal details."""
    if isinstance(exc, ValidationError):
        return f"Invalid arguments or response format in {func_name}."
    if isinstance(exc, _SAFE_ERROR_TYPES):
        sanitized = _PATH_PATTERN.sub("[redacted-path]", str(exc))
        sanitized = _KV_PATTERN.sub("[redacted]", sanitized)
        return sanitized
    return f"An unexpected error occurred in {func_name}."


def get_workspace_help_context(client: Any = None, workspace_slug: str | None = None) -> dict[str, Any]:
    """Fetch workspace projects, states, labels, and priorities directly in-memory without disk caching."""
    if client is None or workspace_slug is None:
        from plane_mcp.client import get_plane_client_context

        client, workspace_slug = get_plane_client_context()

    try:
        projects_resp = client.projects.list(workspace_slug=workspace_slug)
        p_items = getattr(projects_resp, "results", projects_resp if isinstance(projects_resp, list) else [])
        projects = []
        for p in p_items:
            proj_dict: dict[str, Any] = {
                "project_slug": getattr(p, "identifier", ""),
                "name": getattr(p, "name", ""),
                "description": getattr(p, "description", "") or "",
            }
            p_id = getattr(p, "id", None)
            if p_id:
                try:
                    s_res = client.states.list(workspace_slug=workspace_slug, project_id=str(p_id))
                    s_items = getattr(s_res, "results", s_res if isinstance(s_res, list) else [])
                    proj_dict["states"] = [s.name for s in s_items if getattr(s, "name", None)]
                except Exception:
                    proj_dict["states"] = []

                try:
                    l_res = client.labels.list(workspace_slug=workspace_slug, project_id=str(p_id))
                    l_items = getattr(l_res, "results", l_res if isinstance(l_res, list) else [])
                    proj_dict["labels"] = [lbl.name for lbl in l_items if getattr(lbl, "name", None)]
                except Exception:
                    proj_dict["labels"] = []
            else:
                proj_dict["states"] = []
                proj_dict["labels"] = []

            projects.append(proj_dict)

        return {
            "projects": projects,
            "priorities": ["urgent", "high", "medium", "low", "none"],
        }
    except Exception as e:
        logger.warning("Failed to fetch workspace help context: %s", e)
        return {
            "projects": [],
            "priorities": ["urgent", "high", "medium", "low", "none"],
            "error": f"Could not connect to Plane API: {e}",
        }


class JourneyBase:
    """Base class for Journey tools. Provides EntityResolver wiring
    and utilities to aggressively filter Level of Detail.
    """

    def __init__(self, resolver: EntityResolver):
        """Initialize the JourneyBase helper with an EntityResolver instance."""
        self.resolver = resolver

    def get_workspace_help_context(self) -> dict[str, Any]:
        """Fetch workspace projects, states, labels, and priorities directly in-memory."""
        client = getattr(self.resolver, "client", None)
        workspace_slug = getattr(self.resolver, "workspace_slug", None)
        return get_workspace_help_context(client=client, workspace_slug=workspace_slug)

    def apply_lod(
        self, data: Any, profile: LODProfile = LODProfile.SUMMARY, project_identifier: str | None = None
    ) -> Any:
        """Applies LOD profile, returning clean minimized data for AI context."""
        return apply_lod(data, profile=profile, project_identifier=project_identifier)

    def parse_ticket_id(self, ticket_id: str) -> tuple[str, int]:
        """Parses a typical sequence ID (ENG-123) into project_identifier and sequence_id."""
        if not isinstance(ticket_id, str):
            raise ValueError(f"Invalid ticket_id: expected string, got {type(ticket_id).__name__}")

        parts = ticket_id.split("-")
        if len(parts) != 2:
            raise ValueError(f"Invalid ticket ID format: '{ticket_id}'. Expected format like 'ENG-123'.")

        project_identifier = parts[0]
        try:
            issue_sequence = int(parts[1])
        except ValueError:
            raise ValueError(f"Invalid ticket sequence in '{ticket_id}'. Must be an integer (e.g., 123).") from None

        return project_identifier, issue_sequence


def with_lod(profile: LODProfile = LODProfile.SUMMARY) -> Callable[[T], T]:
    """Decorator for Journey tools to automatically apply an LOD profile to the output.
    Can be applied to methods of JourneyBase subclasses or any function returning dict/list.
    """

    def decorator(func: T) -> T:
        """Wrap the target function to apply LOD filtering to its return value."""

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            """Execute the wrapped function and filter its output using the configured LOD profile."""
            result = func(*args, **kwargs)
            # Try to get project_identifier from kwargs if available for sequence injection
            project_identifier = kwargs.get("project_identifier")

            # If not in kwargs but it's a typical ticket_id arg
            if not project_identifier and "ticket_id" in kwargs:
                try:
                    project_identifier = kwargs["ticket_id"].split("-")[0]
                except (AttributeError, IndexError):
                    pass  # ticket_id format doesn't match expected pattern

            # Extract self if it's a method
            if args and hasattr(args[0], "apply_lod"):
                # Let the base class handle it
                return args[0].apply_lod(result, profile=profile, project_identifier=project_identifier)
            else:
                return apply_lod(result, profile=profile, project_identifier=project_identifier)

        return cast(T, wrapper)

    return decorator


def mcp_error_boundary(func: T) -> T:
    """Decorator to wrap MCP tool executions and catch unhandled exceptions,
    returning a structured error dictionary while preventing raw stack trace disclosure (CWE-209).
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        """Execute the tool function within a sanitizing error boundary."""
        try:
            return func(*args, **kwargs)
        except Exception as e:
            try:
                import inspect

                logger.error("Error in %s: %s", func.__name__, e, exc_info=True)

                error_msg = _sanitize_error_message(e, func.__name__)
                result: dict[str, Any] = {
                    "tool": func.__name__,
                    "error": error_msg,
                }

                # Try to determine return type safely
                try:
                    sig = inspect.signature(func)
                    if sig.return_annotation is list or str(sig.return_annotation).startswith("list"):
                        return [result]
                except Exception:
                    pass

                return result
            except Exception as inner_e:
                logger.error("CRITICAL: Error boundary failed for %s: %s", func.__name__, inner_e, exc_info=True)
                return {"error": f"CRITICAL: Tool {func.__name__} failed, and error handler also crashed."}

    return cast(T, wrapper)
