"""Regression tests for plane-sdk model patches.

See: https://github.com/makeplane/plane-mcp-server/issues/83
"""

from plane.models.states import PaginatedStateResponse, State
from plane.models.work_items import PaginatedWorkItemResponse, WorkItem

import plane_mcp  # noqa: F401 — triggers apply_patches()

# -- Bug 1: State.sequence should accept float values --


def test_state_accepts_float_sequence():
    state = State.model_validate({"name": "In Progress", "color": "#f2c94c", "sequence": 8482.5})
    assert state.sequence == 8482.5


def test_state_accepts_negative_float_sequence():
    state = State.model_validate({"name": "Done", "color": "#09a953", "sequence": -57052.5})
    assert state.sequence == -57052.5


def test_state_accepts_int_sequence():
    state = State.model_validate({"name": "Backlog", "color": "#bec2c8", "sequence": 15000})
    assert state.sequence == 15000


def test_paginated_state_response_with_float_sequences():
    response = PaginatedStateResponse.model_validate(
        {
            "results": [
                {
                    "name": "Todo",
                    "color": "#ccc",
                    "sequence": 8482.5,
                    "group": "unstarted",
                },
                {
                    "name": "Done",
                    "color": "#0f0",
                    "sequence": -57052.5,
                    "group": "completed",
                },
            ],
            "total_count": 2,
            "total_pages": 1,
            "next_cursor": "",
            "prev_cursor": "",
            "next_page_results": False,
            "prev_page_results": False,
            "count": 2,
            "total_results": 2,
            "extra_stats": None,
        }
    )
    assert len(response.results) == 2
    assert response.results[0].sequence == 8482.5
    assert response.results[1].sequence == -57052.5


# -- Bug 2: WorkItem.state should accept expanded state objects --


def test_work_item_accepts_expanded_state():
    work_item = WorkItem.model_validate(
        {
            "name": "Test Item",
            "state": {
                "id": "fe22aafd-1234",
                "name": "In Progress",
                "color": "#f00",
                "group": "started",
            },
        }
    )
    assert work_item.state is not None
    assert work_item.state.id == "fe22aafd-1234"  # type: ignore[union-attr]


def test_work_item_accepts_string_state():
    work_item = WorkItem.model_validate(
        {
            "name": "Test Item",
            "state": "fe22aafd-1234-5678-9012-abcdef123456",
        }
    )
    assert work_item.state == "fe22aafd-1234-5678-9012-abcdef123456"


def test_work_item_accepts_null_state():
    work_item = WorkItem.model_validate({"name": "Test Item", "state": None})
    assert work_item.state is None


def test_paginated_work_item_response_with_expanded_state():
    response = PaginatedWorkItemResponse.model_validate(
        {
            "results": [
                {
                    "name": "Test",
                    "state": {
                        "id": "abc",
                        "name": "Done",
                        "color": "#0f0",
                        "group": "completed",
                    },
                }
            ],
            "total_count": 1,
            "total_pages": 1,
            "next_cursor": "",
            "prev_cursor": "",
            "next_page_results": False,
            "prev_page_results": False,
            "count": 1,
            "total_results": 1,
            "extra_stats": None,
        }
    )
    assert len(response.results) == 1
    state = response.results[0].state
    assert state is not None
    assert not isinstance(state, str)
    assert state.id == "abc"  # type: ignore[union-attr]
