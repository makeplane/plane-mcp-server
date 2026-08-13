"""Reading Plane's two refusal shapes for a workspace-owned resource."""

from __future__ import annotations

import pytest
from plane.errors.errors import HttpError

from plane_mcp.toolkit.governance import MIGRATION_IN_PROGRESS, WORKSPACE_MANAGED, migration_in_progress, workspace_owns


def _error(status: int, body) -> HttpError:
    return HttpError("refused", status_code=status, response=body)


def test_the_code_shape_is_recognised_without_naming_a_field():
    """States, workflows and labels refuse with `code`, which needs no per-resource wiring."""
    assert workspace_owns(_error(400, {"error": "managed at the workspace level", "code": WORKSPACE_MANAGED}))


def test_the_field_shape_is_recognised_for_the_resource_that_declares_it():
    """A serializer validation keys on the field instead, so the caller names it."""
    refusal = _error(400, {"work_item_types": ["Cannot enable project-level work item types"]})
    assert workspace_owns(refusal, "work_item_types")
    assert not workspace_owns(refusal, "states"), "matched a field it was not asked about"


@pytest.mark.parametrize(
    "exc",
    [
        _error(403, {"code": WORKSPACE_MANAGED}),
        _error(400, {"code": "something_else"}),
        _error(400, {"detail": "nope"}),
        _error(400, "not a dict"),
        _error(500, {}),
    ],
)
def test_anything_else_is_not_a_governance_refusal(exc):
    """Only the documented 400 may reroute a write; the rest belong to the caller."""
    assert not workspace_owns(exc, "work_item_types")


def test_a_migration_in_progress_is_told_apart_from_ownership():
    """Mid-migration the write may be retried, so it must not read as a permanent lockout."""
    exc = _error(400, {"code": MIGRATION_IN_PROGRESS})
    assert migration_in_progress(exc)
    assert not workspace_owns(exc, "work_item_types")
    assert not migration_in_progress(_error(400, {"code": WORKSPACE_MANAGED}))
