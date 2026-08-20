"""Classifying what kind of "no" a tool call received, and what counts as friction.

Every payload below is one that a real battery produced. One errored-call count
answered three unrelated questions at once: on an unpatched 28-tool surface, 31 of
40 errors were the schema correcting a malformed call, one was a genuine tool-design
defect, and one was a fair question fairly answered. Reading them as one number is
what made the defect invisible.
"""

from __future__ import annotations

import pytest

from evals.core.error_class import (
    DENIED,
    FAILED,
    NOT_FOUND,
    REFUSED,
    REJECTED,
    UNCLASSIFIED,
    classify_error,
)
from evals.core.results import CallRecord
from evals.report.schema_friction import split_errors

# --- the classifier, on payloads observed in real runs -----------------------

OBSERVED = [
    # This server's own refusals: no status, no pydantic shape, deliberate wording.
    ("Error: project requires an action. It takes: archive, create, delete, list.", REFUSED),
    ("Error: action 'create' does not take: points. It takes: description, name.", REFUSED),
    # FastMCP rejecting a call against the tool signature, before the body runs.
    (
        "1 validation error for call[project]\naction\n  Missing required argument "
        "[type=missing_argument, input_value={}, input_type=dict]",
        REFUSED,
    ),
    (
        "1 validation error for call[workspace]\naction\n  Input should be 'get_features' "
        "or 'update_features' [type=literal_error, input_value='list', input_type=str]",
        REFUSED,
    ),
    # The API answering a fair existence question.
    ("Error calling tool 'project_estimate': HTTP 404: Not Found: Estimate not found", NOT_FOUND),
    # The API refusing the meaning of a well-formed call -- the defect class.
    ("HTTP 400: Bad Request: The old cycle is not completed yet", REJECTED),
    ("HTTP 409: Conflict: name: The project name is already taken", REJECTED),
    # Plan and permission gates say nothing about the tool surface.
    ("HTTP 402: Payment Required: Upgrade your plan to access Initiatives", DENIED),
    ("HTTP 403: Forbidden: Customer feature is not enabled for this workspace", DENIED),
    ("HTTP 500: Internal Server Error", FAILED),
]


@pytest.mark.parametrize(("payload", "expected"), OBSERVED, ids=[e + ":" + p[:28] for p, e in OBSERVED])
def test_an_observed_payload_lands_in_its_class(payload: str, expected: str):
    assert classify_error(payload) == expected


def test_a_status_outranks_wording():
    """A 404 that happens to mention an argument is still an absent resource.

    Only a payload with no status at all can be a schema refusal, because that is
    exactly the case where the call never reached the API.
    """
    assert classify_error("HTTP 404: Not Found: missing required argument foo") == NOT_FOUND


def test_an_unrecognised_payload_is_never_guessed_into_a_class():
    """Silently sorting the unknown into `refused` would inflate the one number
    that is supposed to be attributable to our own schema."""
    assert classify_error("something went sideways") == UNCLASSIFIED
    assert classify_error("") == UNCLASSIFIED
    assert classify_error(None) == UNCLASSIFIED


def test_a_foreign_surface_still_classifies_by_status():
    """The battery scores servers it has never seen -- a 177-tool build, a future v2.

    Those emit neither this server's refusal wording nor its tool names, so status
    has to carry them. Coupling the classifier to `ACTIONS` would end that.
    """
    assert classify_error("HTTP 400: Bad Request: whatever a foreign server says") == REJECTED
    assert classify_error("HTTP 404: Not Found") == NOT_FOUND


# --- the split, including the rule that keeps a fair question out of friction ---


def err(tool: str, action: str | None, kind: str) -> CallRecord:
    return CallRecord(tool=tool, action=action, is_error=True, error_class=kind)


def test_an_unclassified_error_is_not_filed_beside_ones_we_chose_not_to_charge():
    """`other` means classified and deliberately not charged to tool design.
    `unclassified` means we do not know, which a reader must be able to tell apart.
    """
    counts = split_errors([err("project", "list", DENIED), err("project", "list", UNCLASSIFIED)])
    assert counts["other"] == 1
    assert counts["unclassified"] == 1


def test_each_kind_lands_in_its_own_column():
    counts = split_errors(
        [
            err("project", None, REFUSED),
            err("cycle", "transfer_workitems", REJECTED),
            err("initiative", "list", DENIED),
            CallRecord(tool="project", action="list"),  # a success is not counted anywhere
        ]
    )
    assert counts == {"navigation": 1, "surface": 1, "answered": 0, "other": 1, "unclassified": 0}


def test_a_first_absent_read_is_an_answer_not_friction():
    """`project_estimate retrieve` -> 404 before creating one is the correct move.

    Three separate models made this exact call and each was charged for it. There is
    no cheaper way to ask whether something exists than to ask.
    """
    counts = split_errors([err("project_estimate", "retrieve", NOT_FOUND)])
    assert counts["answered"] == 1
    assert counts["surface"] == 0


def test_a_repeated_absent_read_is_friction():
    """Asking twice means the first answer did not land, which is the surface's problem."""
    counts = split_errors(
        [
            err("project_estimate", "retrieve", NOT_FOUND),
            err("project_estimate", "retrieve", NOT_FOUND),
            err("project_estimate", "retrieve", NOT_FOUND),
        ]
    )
    assert counts["answered"] == 1
    assert counts["surface"] == 2


def test_absent_reads_of_different_things_are_each_their_own_question():
    counts = split_errors(
        [
            err("project_estimate", "retrieve", NOT_FOUND),
            err("cycle", "retrieve", NOT_FOUND),
            err("project_estimate", "list_points", NOT_FOUND),
        ]
    )
    assert counts["answered"] == 3
    assert counts["surface"] == 0


def test_an_unclassified_error_is_never_counted_as_surface_friction():
    """Surface friction is the number someone will act on, so it may only hold
    calls we can actually attribute to tool design."""
    counts = split_errors([err("project", "list", UNCLASSIFIED), err("project", "list", FAILED)])
    assert counts["surface"] == 0
    assert counts["unclassified"] == 1
    assert counts["other"] == 1


def test_a_row_from_before_this_field_existed_does_not_crash_or_inflate():
    """Older result files carry is_error with no error_class."""
    counts = split_errors([CallRecord(tool="project", action="list", is_error=True)])
    assert counts["unclassified"] == 1
    assert counts["surface"] == 0
    assert counts["other"] == 0


# --- the whole path, because a key dropped anywhere on it reaches no report ----


def test_a_class_survives_every_hop_from_proxy_to_report(tmp_path):
    """The classifier ran and the report still said "unclassified", because the
    sidecar reader rebuilds each call from an explicit key list and did not copy the
    field. Every hop is asserted here: proxy row -> sidecar reader -> AgentRun ->
    TaskResult -> serialized row -> reloaded row.
    """
    import json

    from evals.core.results import AgentRun, TaskResult, Usage, agent_run_to_task_result
    from evals.drivers.cli.sidecar import load_proxy_sidecar

    sidecar = tmp_path / "proxy-sidecar.jsonl"
    rows = [
        {
            "tool": "cycle",
            "args": {"action": "transfer_workitems"},
            "is_error": True,
            "error_class": REJECTED,
            "result_chars": 145,
            "duration_ms": 115,
            "seq": 1,
        },
        {
            "row_type": "proxy_meta",
            "relayed_lines": 1,
            "unparsed_lines": 0,
            "unmatched_responses": 0,
            "notifications": 0,
            "pending_left": 0,
            "child_killed": False,
        },
    ]
    sidecar.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    calls, _status = load_proxy_sidecar(sidecar)
    assert calls and calls[0].get("error_class") == REJECTED, "the sidecar reader dropped it"

    agent = agent_run_to_task_result(
        AgentRun(calls=calls, final_text="done", usage=Usage(), stopped_reason="end_turn")
    )
    assert agent.calls[0].error_class == REJECTED, "agent_run_to_task_result dropped it"

    serialized = json.loads(json.dumps(agent.to_row()))
    assert serialized["calls"][0]["error_class"] == REJECTED, "serialization dropped it"

    reloaded = TaskResult.from_row(serialized)
    assert reloaded.calls[0].error_class == REJECTED, "reload dropped it"
    assert split_errors(reloaded.calls)["surface"] == 1, "the report did not see it"
