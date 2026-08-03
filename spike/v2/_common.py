"""Shared plumbing for the consolidated (v2) tool surface.

Every v2 module follows the same shape:

    from spike.v2._common import bad_action, json_out, missing

    ACTIONS = ["list", "retrieve", "create", "update", "delete"]

    DOC = '''Manage <resource>. Actions:
    list (<required params>);
    ...'''

    def _dispatch(action, ...):
        '''Returns a model / list / None, or a str on validation error.'''

    def register_typed(mcp):   # variant C/BD -- Pydantic return, schema kept
    def register_str(mcp):     # variant D    -- JSON string, no schema

Conventions that matter for schema size -- keep them:

* Plain typed defaults (`= ""`, `= 0`, `= False`) instead of `| None = None`.
  Pydantic renders every `X | None` as a verbose anyOf-with-null block.
* `-> str` on the str variant. That is what suppresses `outputSchema`.
* Required-field enforcement lives in `_dispatch` via `missing()`, and is
  documented per action in DOC. There is no schema-level `required` beyond
  `action`, so the docstring is the model's only hint -- keep it accurate.
"""

from __future__ import annotations

import json
from typing import Any


def missing(action: str, *names: str) -> str:
    """Uniform 'you forgot a param' error."""
    return f"Error: action '{action}' requires: {', '.join(names)}."


def bad_action(action: str, allowed: list[str]) -> str:
    """Uniform 'no such action' error."""
    return f"Error: unknown action '{action}'. Must be one of: {', '.join(allowed)}."


def json_out(obj: Any) -> str:
    """Serialise a model / list of models / plain value to a JSON string."""
    if obj is None:
        return "Done"
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        return json.dumps([_plain(o) for o in obj], indent=2, default=str)
    return json.dumps(_plain(obj), indent=2, default=str)


def _plain(o: Any) -> Any:
    return o.model_dump(mode="json") if hasattr(o, "model_dump") else o


def page_params(cursor: str = "", per_page: int = 0, **extra: Any) -> dict[str, Any] | None:
    """Build a query-param dict, dropping unset values. None if nothing set."""
    params: dict[str, Any] = {}
    if cursor:
        params["cursor"] = cursor
    if per_page:
        params["per_page"] = per_page
    for k, v in extra.items():
        if v not in ("", 0, None, False):
            params[k] = v
    return params or None


def opt(value: Any) -> Any:
    """Normalise a sentinel default ("" / 0) back to None for SDK payloads."""
    return value if value not in ("", 0) else None
