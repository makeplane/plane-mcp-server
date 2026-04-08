"""Base classes and utilities for Journey-based AI tools."""

from functools import wraps
from typing import Any, Callable, TypeVar, cast
from plane_mcp.resolver import EntityResolver
from plane_mcp.journey.lod import apply_lod, LODProfile

T = TypeVar('T', bound=Callable[..., Any])


class JourneyBase:
    """
    Base class for Journey tools. Provides EntityResolver wiring
    and utilities to aggressively filter Level of Detail.
    """

    def __init__(self, resolver: EntityResolver):
        self.resolver = resolver

    def apply_lod(self, data: Any, profile: LODProfile = LODProfile.SUMMARY, project_identifier: str | None = None) -> Any:
        """
        Applies LOD profile, returning clean minimized data for AI context.
        """
        return apply_lod(data, profile=profile, project_identifier=project_identifier)

    def parse_ticket_id(self, ticket_id: str) -> tuple[str, int]:
        """
        Parses a typical sequence ID (ENG-123) into project_identifier and sequence_id.
        """
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
    """
    Decorator for Journey tools to automatically apply an LOD profile to the output.
    Can be applied to methods of JourneyBase subclasses or any function returning dict/list.
    """
    def decorator(func: T) -> T:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
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
    """
    Decorator to wrap MCP tool executions and catch unhandled exceptions,
    returning them as a formatted string or dict to prevent 500 errors.
    """
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            try:
                import logging
                import inspect
                import traceback
                
                error_details = traceback.format_exc()
                logging.error(f"Error in {func.__name__}:\n{error_details}")
                
                # Determine if this is a "handled" error that shouldn't pollute LLM context with stack traces
                is_handled = isinstance(e, ValueError)
                
                try:
                    from pydantic import ValidationError as PydanticValidationError
                    if isinstance(e, PydanticValidationError):
                        is_handled = True
                except ImportError:
                    pass
                    
                try:
                    from pydantic_core import ValidationError as PydanticCoreValidationError
                    if isinstance(e, PydanticCoreValidationError):
                        is_handled = True
                except ImportError:
                    pass
                
                try:
                    from plane.errors.errors import HttpError
                    if isinstance(e, HttpError):
                        is_handled = True
                except ImportError:
                    pass
                
                error_msg = f"Error executing tool '{func.__name__}': {str(e)}"
                if not is_handled:
                    error_msg += f"\n\nDetails: {error_details}"
                
                # Try to determine return type safely
                try:
                    sig = inspect.signature(func)
                    if sig.return_annotation is list or str(sig.return_annotation).startswith("list"):
                        return [{"error": error_msg}]
                except Exception:
                    pass
                    
                return {"error": error_msg}
            except Exception as inner_e:
                # Absolute fallback if error handling itself fails
                return {"error": f"CRITICAL: Tool {func.__name__} failed, and error handler also crashed: {str(inner_e)}"}
    return cast(T, wrapper)
