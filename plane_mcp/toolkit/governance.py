"""Reading Plane's refusal when a workspace, not a project, owns a resource.

The refusal is the signal rather than a feature read: the flag is cached and the
lockout outlives it being toggled off. A new governed resource passes its field name.
"""

from __future__ import annotations

from typing import Any

from plane.errors.errors import HttpError

WORKSPACE_MANAGED = "workspace_managed"
MIGRATION_IN_PROGRESS = "governance_migration_in_progress"


def _body(exc: HttpError) -> dict[str, Any]:
    """The 400's body, or empty for anything else."""
    return exc.response if exc.status_code == 400 and isinstance(exc.response, dict) else {}


def workspace_owns(exc: HttpError, *fields: str) -> bool:
    """Whether a refusal means the workspace catalogue owns this resource."""
    body = _body(exc)
    return body.get("code") == WORKSPACE_MANAGED or any(field in body for field in fields)


def migration_in_progress(exc: HttpError) -> bool:
    """Whether a refusal means a governance migration is running, so the write may be retried."""
    return _body(exc).get("code") == MIGRATION_IN_PROGRESS
