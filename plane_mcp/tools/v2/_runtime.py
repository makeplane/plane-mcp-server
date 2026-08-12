"""Runtime helpers shared by every resource module.

Validation failures are returned as strings rather than raised. The MCP spec
designates tool execution errors as the model's self-correction channel, so the
message must name exactly what to supply.
"""

from __future__ import annotations

import json
from typing import Any


def missing(action: str, *names: str) -> str:
    return f"Error: action '{action}' requires: {', '.join(names)}."


def bad_action(action: str, allowed: tuple[str, ...]) -> str:
    return f"Error: unknown action '{action}'. Must be one of: {', '.join(allowed)}."


def require(actions: Any, action: str, **supplied: Any) -> str | None:
    """Check an action's declared required params up front, from the declaration.

    Most resources guard inline, sharing a prefix check between actions. Use this
    where validation has to happen before anything else -- a resource that makes a
    preflight call should not spend it on a request that was never going to work.
    """
    declared = next((a.requires for a in actions if a.name == action), ())
    absent = [name for name in declared if not supplied.get(name)]
    return missing(action, *absent) if absent else None


def opt(value: Any) -> Any:
    """Normalise a sentinel default ("" / 0) back to None for SDK payloads."""
    return value if value not in ("", 0) else None


def coerce_list(value: Any) -> list[Any] | None:
    """Accept a JSON-encoded string where a list is expected.

    Models routinely send '["uuid"]' for a list parameter. Absorbing it here
    removes a failure class that is not worth surfacing to the caller.
    """
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except ValueError:
                return [text]
            return parsed if isinstance(parsed, list) else [parsed]
        return [part.strip() for part in text.split(",") if part.strip()]
    return [value]


def page_params(cursor: str = "", per_page: int = 0, **extra: Any) -> dict[str, Any] | None:
    """Build a query-param dict, dropping unset values. None when nothing is set.

    Only for endpoints annotated `Mapping[str, Any]`. The rest of the SDK types
    `params` as a Pydantic model and calls `.model_dump()` on it, so a dict there
    raises AttributeError at call time -- use `as_params` for those.
    """
    params: dict[str, Any] = {}
    if cursor:
        params["cursor"] = cursor
    if per_page:
        params["per_page"] = per_page
    params.update({k: v for k, v in extra.items() if v not in ("", 0, None, False)})
    return params or None


def as_params(model: type[Any], **values: Any) -> Any | None:
    """Build a typed SDK query-param model, dropping unset values.

    Returns None when nothing was supplied so the SDK keeps its own defaults.
    """
    supplied = {k: v for k, v in values.items() if v not in ("", 0, None)}
    return model(**supplied) if supplied else None


def ids_of(items: Any) -> list[str]:
    """Extract ids from a field that may hold bare id strings or model objects."""
    out: list[str] = []
    for item in items or []:
        value = item if isinstance(item, str) else getattr(item, "id", None)
        if value:
            out.append(str(value))
    return out
