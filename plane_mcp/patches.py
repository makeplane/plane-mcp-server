"""Patches for plane-sdk Pydantic models to match the actual Plane API responses.

These patches fix type mismatches between the SDK models and what the API returns:

1. State.sequence: The Plane API uses a FloatField for sequence, but the SDK
   model declares it as ``int | None``. This causes validation errors when
   listing states.

2. WorkItem.state: When the ``expand`` query parameter is used (e.g.,
   ``expand=state``), the API returns an expanded state object instead of a
   plain UUID string. The SDK ``WorkItem`` model only declares ``state`` as
   ``str | None``, causing validation failures.

See: https://github.com/makeplane/plane-mcp-server/issues/83

TODO: Remove this module once plane-sdk includes the fixes from
https://github.com/makeplane/plane-python-sdk/pull/23
"""

from __future__ import annotations

from typing import Any

from plane.models.states import (
    CreateState,
    PaginatedStateResponse,
    State,
    StateLite,
    UpdateState,
)
from plane.models.work_items import PaginatedWorkItemResponse, WorkItem
from pydantic import BaseModel
from pydantic.fields import FieldInfo


def _rebuild_field(model: type[BaseModel], field_name: str, annotation: Any) -> None:
    """Update a Pydantic model field's type annotation and rebuild the validator."""
    model.__annotations__[field_name] = annotation
    current = model.model_fields[field_name]
    model.model_fields[field_name] = FieldInfo(
        annotation=annotation,
        default=current.default,
    )
    model.model_rebuild(force=True)


def _patch_state_sequence() -> None:
    """Change State.sequence from int to float to match the API's FloatField."""
    for model in (State, CreateState, UpdateState):
        _rebuild_field(model, "sequence", float | None)
    # Rebuild paginated response so it picks up the patched State model
    PaginatedStateResponse.model_rebuild(force=True)


def _patch_work_item_expandable_fields() -> None:
    """Allow WorkItem.state to accept expanded state objects.

    When ``expand=state`` is passed, the API returns a nested state object
    instead of a UUID string. The ``WorkItemDetail`` model already handles
    this (``str | StateLite | None``), but ``WorkItem`` (used by
    ``PaginatedWorkItemResponse``) does not.
    """
    _rebuild_field(WorkItem, "state", str | StateLite | None)
    # Rebuild paginated response so it picks up the patched WorkItem model
    PaginatedWorkItemResponse.model_rebuild(force=True)


def apply_patches() -> None:
    """Apply all SDK model patches. Must be called before any tool registration."""
    _patch_state_sequence()
    _patch_work_item_expandable_fields()
