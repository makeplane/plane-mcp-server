"""Shape-tolerance tests for shared verifier lookups."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from evals.tasks.lookups import state_group


@pytest.mark.parametrize(
    "response",
    [
        [SimpleNamespace(id="state-1", group="started")],
        SimpleNamespace(results=[SimpleNamespace(id="state-1", group="started")]),
    ],
    ids=["raw-list", "paginated-page"],
)
def test_state_group_accepts_raw_and_paginated_list_shapes(response):
    plane = SimpleNamespace(states=SimpleNamespace(list=lambda **kwargs: response))

    assert state_group(plane, "ws", "project", "state-1") == "started"
