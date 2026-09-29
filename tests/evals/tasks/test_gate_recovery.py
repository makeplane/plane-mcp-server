"""W11: the agent must clear a disabled project feature before it can do the work.

W8 logs two hours against a project where time tracking is already on. W11 is the same
end state reached from an obstacle — the worklog endpoints refuse until the feature is
enabled, which the prompt explicitly permits. The verifier separates the ways it can go
wrong, because "no work log" alone does not say whether the agent gave up, half-finished,
or claimed a success it never earned.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from plane.errors.errors import HttpError

from evals.tasks.catalog import TASKS_BY_ID
from evals.tasks.verification import VerifierReadError
from evals.tasks.write import W11_TITLE, verify_w11

WORKLOG_DISABLED = HttpError("Not found", 404, {"message": "Worklog is not enabled for the project"})


class _Page:
    """The paginated envelope the SDK returns from a list endpoint."""

    def __init__(self, results: list[Any] | None = None, next_page_results: bool = False) -> None:
        self.results = results or []
        self.next_page_results = next_page_results
        self.next_cursor = None


def _plane(*, logs: Any, time_tracking: bool | None = True, item: bool = True) -> SimpleNamespace:
    def _list_items(**_kwargs: Any):
        return _Page([SimpleNamespace(id="item-1", name=W11_TITLE)] if item else [])

    def _list_logs(**_kwargs: Any):
        if isinstance(logs, Exception):
            raise logs
        return logs

    def _retrieve_project(**_kwargs: Any):
        return SimpleNamespace(id="proj-1", is_time_tracking_enabled=time_tracking)

    return SimpleNamespace(
        work_items=SimpleNamespace(
            list=_list_items,
            work_logs=SimpleNamespace(list=_list_logs),
        ),
        projects=SimpleNamespace(retrieve=_retrieve_project),
    )


CTX = {"workspace_slug": "ws", "project_id": "proj-1"}


def test_passes_when_the_work_log_landed():
    plane = _plane(logs=[SimpleNamespace(duration=120)])
    ok, note = asyncio.run(verify_w11(plane, dict(CTX), {"final_text": "done"}))
    assert ok is True
    assert "120" in note


def test_still_refused_means_the_feature_was_never_enabled():
    plane = _plane(logs=WORKLOG_DISABLED, time_tracking=False)
    ok, note = asyncio.run(verify_w11(plane, dict(CTX), {"final_text": "I could not log the time."}))
    assert ok is False
    assert "never enabled" in note


def test_enabled_but_nothing_logged_is_reported_as_half_finished():
    plane = _plane(logs=[], time_tracking=True)
    ok, note = asyncio.run(verify_w11(plane, dict(CTX), {"final_text": "Enabled time tracking."}))
    assert ok is False
    assert "enabled but no 120-minute log" in note


def test_reporting_the_limitation_is_distinguished_from_giving_up_silently():
    """Both fail, but they are different behaviours and the report should say which."""
    reported = _plane(logs=[], time_tracking=False)
    ok, note = asyncio.run(
        verify_w11(reported, dict(CTX), {"final_text": "Time tracking is not enabled for this project."})
    )
    assert ok is False
    assert "rather than enabling it" in note

    silent = _plane(logs=[], time_tracking=False)
    ok, note = asyncio.run(verify_w11(silent, dict(CTX), {"final_text": "All done!"}))
    assert ok is False
    assert "never mentions" in note


def test_a_do_nothing_agent_fails():
    """The canary asserts this for every verifier; a gate task must not pass vacuously."""
    plane = _plane(logs=[], time_tracking=False)
    ok, _ = asyncio.run(verify_w11(plane, dict(CTX), {"final_text": "", "calls": []}))
    assert ok is False


def test_an_unexpected_error_is_not_swallowed_as_a_disabled_feature():
    """Only the 'worklog disabled' 404 is read as the obstacle; anything else is a bug."""
    plane = _plane(logs=HttpError("Server error", 500, {"error": "boom"}))
    with pytest.raises(VerifierReadError, match="W11 verifier read failed while listing work logs"):
        asyncio.run(verify_w11(plane, dict(CTX), {"final_text": ""}))


def test_task_seeds_time_tracking_off_and_authorises_turning_it_on():
    task = TASKS_BY_ID["W11"]
    assert "leave_worklogs_off" in task["needs"], "the obstacle must actually be seeded"
    assert "items" in task["needs"]
    # Without explicit permission, an agent that declines to change project-wide config is
    # arguably behaving better, and scoring the enable as success would reward overreach.
    assert "permission" in task["prompt"].lower()
