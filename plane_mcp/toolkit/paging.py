"""Pagination envelopes and PQL failure handling for Plane list endpoints.

Several resources list work items -- workitem, cycle and module -- and all of
them must return the same envelope shape and handle an invalid PQL filter the
same way. Keeping that here is what makes them consistent.

This is Plane's pagination contract, not one tool surface's: any surface talking
to these endpoints needs the same envelope and the same 400-on-bad-PQL answer.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from typing import Any
from urllib.parse import urlparse

from fastmcp.exceptions import ToolError
from fastmcp.utilities.logging import get_logger
from plane.errors.errors import HttpError
from plane.models.query_params import WorkItemQueryParams

from plane_mcp.pql_reference import PQL_FULL_REFERENCE
from plane_mcp.toolkit.runtime import opt

logger = get_logger(__name__)


def dump_results(items: Any, fields: str | None) -> list[Any]:
    """Serialise a page, honouring `fields` as a sparse fieldset.

    Written as a loop rather than a nested ternary: the previous one-liner read
    as though `hasattr` guarded both branches, when it guarded only the second,
    so a page of plain dicts raised AttributeError as soon as `fields` was set.
    """
    requested = {name.strip() for name in fields.split(",")} - {""} if fields else None
    dumped: list[Any] = []
    for item in items or []:
        if not hasattr(item, "model_dump"):
            dumped.append(item)
        elif requested:
            dumped.append(item.model_dump(include=requested))
        else:
            dumped.append(item.model_dump())
    return dumped


ENVELOPE_FIELDS = (
    "total_count",
    "count",
    "next_cursor",
    "prev_cursor",
    "next_page_results",
    "prev_page_results",
)


def envelope(response: Any, fields: str | None = None) -> dict[str, Any]:
    """Keep the full pagination envelope so paging stays discoverable.

    An action that takes a `cursor` must return one. Handing back only
    `response.results` lets a caller page in but never page on, and makes a
    truncated first page look like the whole set.

    A few Plane responses carry `results` and nothing else. Those are refused
    rather than defaulted to None: a null `next_cursor` reads as "last page",
    which is the same lie by another route.
    """
    absent = [name for name in ENVELOPE_FIELDS if not hasattr(response, name)]
    if absent:
        raise TypeError(
            f"{type(response).__name__} has no {', '.join(absent)}, so it cannot be enveloped: "
            "this endpoint does not paginate. Return response.results directly, leave cursor and "
            "per_page out of the action's optional params, and record it in NOT_PAGINATED in "
            "tests/tools/test_pagination.py."
        )
    return {
        "results": dump_results(response.results, fields),
        **{name: getattr(response, name) for name in ENVELOPE_FIELDS},
    }


def reject_ce_pql(pql: str, reason: str = "") -> None:
    """Explicitly refuse a PQL query on installations or endpoints that do not support PQL filtering."""
    msg = reason or "PQL filtering is not supported on this Plane installation."
    display_pql = pql if len(pql) <= 80 else f"{pql[:77]}..."
    raise ToolError(
        f"{msg} No filtered result was returned. Failed PQL: {display_pql!r}. Remove pql or use client-side filtering."
    )


_MAX_CACHE_SIZE = 256
_CACHE_TTL_SECONDS = 1800  # 30 minutes
_PQL_CAPABILITY_CACHE_LOCK = threading.Lock()
# Bounded FIFO cache: cache_key -> (result: bool, timestamp: float)
# Evicts oldest inserted entry when capacity is reached.
_PQL_CAPABILITY_CACHE: dict[str, tuple[bool, float]] = {}


def reset_pql_capability_cache() -> None:
    """Clear cached PQL capability (used for test isolation)."""
    with _PQL_CAPABILITY_CACHE_LOCK:
        _PQL_CAPABILITY_CACHE.clear()


def _set_capability_cache(key: str, value: bool) -> None:
    """Record capability status with insertion-order FIFO eviction when capacity is reached."""
    with _PQL_CAPABILITY_CACHE_LOCK:
        if len(_PQL_CAPABILITY_CACHE) >= _MAX_CACHE_SIZE:
            oldest_key = next(iter(_PQL_CAPABILITY_CACHE), None)
            if oldest_key is not None:
                _PQL_CAPABILITY_CACHE.pop(oldest_key, None)
        _PQL_CAPABILITY_CACHE[key] = (value, time.monotonic())


def _is_plane_cloud(client: Any) -> bool:
    """Exact hostname check for official Plane Cloud (api.plane.so).

    Note: Skipping probe round-trips for api.plane.so relies on the documented
    service-level capability assumption that Plane Cloud implements PQL on all supported surfaces.
    """
    cfg = getattr(client, "config", None)
    for attr in ("api_root", "base_path"):
        val = getattr(cfg, attr, None)
        if isinstance(val, str) and val.strip():
            try:
                host = urlparse(val).hostname
                if host == "api.plane.so":
                    return True
            except Exception:
                pass
    return False


def _operator_override() -> bool | None:
    """Operator configuration assertion via environment variable.

    Note: PLANE_SUPPORTS_PQL represents an explicit operator configuration assertion,
    not an independently verified backend capability or proof of filtering.
    """
    for var in ("PLANE_SUPPORTS_PQL", "PLANE_CAPABILITY_PQL"):
        val = os.getenv(var)
        if val is not None and val.strip():
            norm = val.strip().lower()
            if norm in ("true", "1", "yes"):
                return True
            if norm in ("false", "0", "no"):
                return False
            logger.warning(
                "Unrecognized %s=%r: must be 'true'/'1'/'yes' or 'false'/'0'/'no'. Treating as unasserted.",
                var,
                val,
            )
    return None


def _auth_fingerprint(client: Any) -> str:
    cfg = getattr(client, "config", None)
    api_key = getattr(cfg, "api_key", None)
    if not isinstance(api_key, str):
        api_key = None
    access_token = getattr(cfg, "access_token", None)
    if not isinstance(access_token, str):
        access_token = None
    token = api_key or access_token
    if isinstance(token, str) and token:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    return "anon"


def check_pql_capability(
    client: Any,
    workspace_slug: str,
    project_id: str | None = None,
    surface: str = "workitem.list",
    probe_fn: Any = None,
) -> bool:
    """Determine whether the connected Plane backend evaluates PQL queries on this surface.

    Evaluation semantics:
    - Operator override (PLANE_SUPPORTS_PQL): Returns the operator's explicit assertion.
    - Official Plane Cloud (api.plane.so): Returns True based on the documented service capability.
    - Self-hosted probe (sending malformed PQL):
      * HTTP 200: Parameter was silently ignored (Plane CE <= 1.3.1) -> returns False.
      * HTTP 400 with edition refusal: Explicitly unsupported on CE (Plane CE >= 1.4.1) -> returns False.
      * HTTP 400 with PQL syntax complaint: Confirms backend PQL lexer/parser is active on this route -> returns True.
        (Note: Confirms syntactic parser presence, not complete semantic execution of all arbitrary operators.)
      * HTTP 404 on workspace-wide routes: Route is absent on this edition -> returns False.
      * HTTP 401/403 (auth), HTTP 404 (missing project), 429, 5xx, or network timeouts: Propagated unmasked.
    """
    env_override = _operator_override()
    if env_override is not None:
        return env_override

    if _is_plane_cloud(client):
        return True

    api_root = getattr(getattr(client, "config", None), "api_root", None)
    endpoint_id = api_root if isinstance(api_root, str) and api_root else str(id(client))
    auth_fp = _auth_fingerprint(client)
    cache_key = f"{endpoint_id}:{workspace_slug}:{surface}:{auth_fp}"

    now = time.monotonic()
    with _PQL_CAPABILITY_CACHE_LOCK:
        entry = _PQL_CAPABILITY_CACHE.get(cache_key)
        if entry is not None:
            val, ts = entry
            if now - ts < _CACHE_TTL_SECONDS:
                return val
            _PQL_CAPABILITY_CACHE.pop(cache_key, None)

    if probe_fn is None:
        if not hasattr(client, "work_items"):
            return False
        if project_id:

            def probe_fn():
                return client.work_items.list(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    params=WorkItemQueryParams(pql="(((pql_probe_syntax_check", per_page=1),
                )

        else:

            def probe_fn():
                return client.work_items.list_workspace(
                    workspace_slug=workspace_slug,
                    params=WorkItemQueryParams(pql="(((pql_probe_syntax_check", per_page=1),
                )

    try:
        probe_fn()
        # If the probe returns HTTP 200 without error, the backend SILENTLY IGNORED the malformed PQL!
        _set_capability_cache(cache_key, False)
        return False
    except HttpError as exc:
        if exc.status_code == 400:
            resp = exc.response if isinstance(exc.response, dict) else {}
            resp_str = str(resp).lower()
            # Explicit CE edition refusal (Plane CE >= 1.4.1)
            if "not supported on this plane edition" in resp_str or "unsupported_parameters" in resp:
                _set_capability_cache(cache_key, False)
                return False
            # Check if backend explicitly complained about the broken probe PQL syntax
            complaint = _filter_complaint(resp)
            if complaint is not None:
                _set_capability_cache(cache_key, True)
                return True
            # Unrelated 400 (e.g. invalid project UUID format, bad per_page)
            # MUST NOT be treated as proof of PQL support!
            raise
        if exc.status_code == 404:
            if surface in ("workitem.list_workspace", "workitem.count"):
                # Workspace-wide route does not exist on this Plane edition
                _set_capability_cache(cache_key, False)
                return False
            # For project-scoped resources, 404 indicates parent record not found
            raise
        # Auth (401/403), rate-limit (429), server errors (5xx) must propagate unmasked
        raise


def workitem_page(
    tool: str,
    action: str,
    method: Any,
    *,
    pql: str = "",
    order_by: str = "",
    cursor: str = "",
    per_page: int = 0,
    expand: str = "",
    fields: str = "",
    client: Any = None,
    **target: Any,
) -> dict[str, Any]:
    """One page of the work items belonging to a parent record."""
    if tool in ("cycle", "module") and pql and pql.strip():
        plane_client = client or getattr(getattr(method, "__self__", None), "_client", None)
        workspace_slug = target.get("workspace_slug", "")
        project_id = target.get("project_id", "")
        if not check_pql_capability(
            plane_client,
            workspace_slug,
            project_id=project_id,
            surface=f"{tool}.{action}",
            probe_fn=lambda: method(
                **target,
                params=WorkItemQueryParams(pql="(((pql_probe_syntax_check", per_page=1),
            ),
        ):
            reject_ce_pql(pql, reason=f"PQL filtering is not supported on {tool}.{action} in Plane CE.")
    try:
        response = method(
            **target,
            params=WorkItemQueryParams(
                pql=opt(pql),
                order_by=opt(order_by),
                per_page=opt(per_page),
                cursor=opt(cursor),
                expand=opt(expand),
                fields=opt(fields),
            ),
        )
    except HttpError as exc:
        failure = pql_failure(tool, action, pql, exc)
        if failure is None:
            raise
        raise ToolError(f"{failure['error']} Failed PQL: {failure['failed_pql']!r}. {failure['hint']}") from exc
    return envelope(response, opt(fields))


def pql_failure(tool: str, action: str, pql: str, exc: HttpError) -> dict[str, Any] | None:
    """Turn an invalid-PQL 400 into a correctable answer instead of an exception."""
    if not (pql and exc.status_code == 400 and isinstance(exc.response, dict)):
        return None
    detail = _filter_complaint(exc.response)
    if detail is None:
        return None
    logger.warning("%s %s: invalid PQL %r -> %s", tool, action, pql, exc.response)
    return {
        "error": detail,
        "failed_pql": pql,
        "pql_reference": PQL_FULL_REFERENCE,
        "hint": f"The PQL above failed. Fix it using the reference and retry {tool} {action}.",
    }


def _filter_complaint(body: dict[str, Any]) -> Any | None:
    """What a 400 said about the filter, or None when it was about something else."""
    if "pql" in body:
        return body["pql"]
    if not any(word in str(body).lower() for word in ("pql", "filter")):
        return None
    return body.get("message") or body.get("error") or body.get("detail") or body
