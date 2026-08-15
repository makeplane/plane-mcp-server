"""Seeding a plan-gated capability skips the task instead of failing the run.

`DESIGN.md` states a plan gate is not rewritten as an agent task failure. Before this,
only the work item type seeder honoured it; a gate while seeding a release or customer
raised, became `infra_seed`, and killed the task-rep. That is what made a flag server
answering everything "on" a hard prerequisite.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from plane.errors.errors import HttpError

from evals.evidence import TARGET_ENTITY_EVIDENCE
from evals.seed import seed_customer, seed_release
from evals.tasks.skip import TaskSkipped

PLAN_REFUSAL = HttpError("Payment required", 402, {"error": "Payment required", "error_code": 1999})
RBAC_REFUSAL = HttpError("Forbidden", 403, {"detail": "You don't have permission to do this"})


def _raising_client(exc: Exception) -> SimpleNamespace:
    def _boom(**_kwargs: Any):
        raise exc

    return SimpleNamespace(
        releases=SimpleNamespace(create=_boom, changelog=SimpleNamespace(update=_boom)),
        customers=SimpleNamespace(create=_boom, requests=SimpleNamespace(create=_boom)),
    )


@pytest.mark.parametrize(
    ("seeder", "feature"),
    [(seed_release, "releases"), (seed_customer, "customers")],
)
def test_plan_gate_becomes_a_skip_with_a_reason(seeder, feature):
    context: dict[str, Any] = {"workspace_objects": []}
    with pytest.raises(TaskSkipped) as caught:
        seeder(_raising_client(PLAN_REFUSAL), "ws", context)
    assert caught.value.reason == f"env:plan-gated:{feature}"


@pytest.mark.parametrize("seeder", [seed_release, seed_customer])
def test_a_non_gate_failure_still_raises(seeder):
    """Only plan limits are excused. A permission or transport failure is a real error.

    Swallowing these would let the harness report a clean battery while the fixtures it
    graded against were never built.
    """
    context: dict[str, Any] = {"workspace_objects": []}
    with pytest.raises(HttpError):
        seeder(_raising_client(RBAC_REFUSAL), "ws", context)


@pytest.mark.parametrize("seeder", [seed_release, seed_customer])
def test_transport_failures_are_not_excused(seeder):
    context: dict[str, Any] = {"workspace_objects": []}
    with pytest.raises(RuntimeError):
        seeder(_raising_client(RuntimeError("connection reset")), "ws", context)


def test_customer_gate_does_not_leave_a_half_built_fixture():
    """A gate on the follow-up request must not leave a customer recorded as seeded.

    The customer is created, then its request is refused. Recording the customer while
    the task skips would leave a verifier reading a fixture that was never finished.
    """
    created: list[str] = []

    def _create_customer(**_kwargs: Any):
        created.append("customer")
        return SimpleNamespace(id="cust-1")

    def _refuse(**_kwargs: Any):
        raise PLAN_REFUSAL

    plane = SimpleNamespace(
        customers=SimpleNamespace(
            create=_create_customer,
            requests=SimpleNamespace(create=_refuse),
        )
    )
    context: dict[str, Any] = {"workspace_objects": []}
    with pytest.raises(TaskSkipped):
        seed_customer(plane, "ws", context)

    assert created == ["customer"]
    assert "customer_request" not in context


def test_release_changelog_write_behaviours():
    cases = (
        ("write failure is fatal", RuntimeError("connection reset"), RuntimeError, None),
        ("plan gate remains a skip", PLAN_REFUSAL, TaskSkipped, "env:plan-gated:releases"),
    )
    for label, error, expected_error, expected_reason in cases:
        with pytest.MonkeyPatch.context():
            plane = SimpleNamespace(
                releases=SimpleNamespace(
                    create=lambda **kw: SimpleNamespace(id="release-1"),
                    changelog=SimpleNamespace(
                        update=lambda error=error, **kw: (_ for _ in ()).throw(error),
                    ),
                )
            )
            context: dict[str, Any] = {"workspace_objects": []}
            with pytest.raises(expected_error) as caught:
                seed_release(plane, "ws", context)

            if expected_reason is not None:
                assert caught.value.reason == expected_reason, label
            assert context["release"]["id"] == "release-1", label
            assert context["workspace_objects"] == [{"kind": "release", "id": "release-1"}], label
            assert "release_changelog_text" not in context, label


def test_release_changelog_readback_sets_the_api_confirmed_baseline():
    plane = SimpleNamespace(
        releases=SimpleNamespace(
            create=lambda **kw: SimpleNamespace(id="release-1"),
            changelog=SimpleNamespace(
                update=lambda **kw: None,
                retrieve=lambda **kw: SimpleNamespace(
                    description_html="<p> Changelog entry one: API-confirmed fact. </p>",
                ),
            ),
        )
    )
    context: dict[str, Any] = {"workspace_objects": []}

    seed_release(plane, "ws", context)

    assert context["release_changelog_text"] == "Changelog entry one: API-confirmed fact."


def test_c2_release_truth_is_randomized_and_api_confirmed():
    contexts: list[dict[str, Any]] = []
    for run_id in ("c2000000aaaaaaaa", "c2000000bbbbbbbb"):
        stored: dict[str, str] = {}

        def create(*, data, _stored=stored, _run_id=run_id, **kwargs):
            _stored["name"] = data.name
            return SimpleNamespace(id=f"release-{_run_id[-4:]}", name=data.name)

        def update(*, data, _stored=stored, **kwargs):
            _stored["html"] = data.description_html

        def retrieve(*, _stored=stored, **kwargs):
            return SimpleNamespace(description_html=_stored["html"])

        plane = SimpleNamespace(
            releases=SimpleNamespace(
                create=create,
                changelog=SimpleNamespace(
                    update=update,
                    retrieve=retrieve,
                ),
            )
        )
        context: dict[str, Any] = {
            "run_id": run_id,
            "task_id": "C2",
            "workspace_objects": [],
            "randomized_truth": {},
        }
        seed_release(plane, "ws", context)
        contexts.append(context)

        assert context["release"]["name"] == stored["name"]
        assert context["randomized_truth"]["C2.release"]["confirmed"]["changelog"] == context["release_changelog_text"]
        assert context["evidence_sentinels"][TARGET_ENTITY_EVIDENCE]

    assert contexts[0]["release_name"] != contexts[1]["release_name"]
    assert contexts[0]["release_changelog_text"] != contexts[1]["release_changelog_text"]


@pytest.mark.parametrize(
    ("error", "expected_error", "expected_reason"),
    [
        (RuntimeError("readback failed"), RuntimeError, None),
        (PLAN_REFUSAL, TaskSkipped, "env:plan-gated:releases"),
    ],
)
def test_release_changelog_readback_failures(error, expected_error, expected_reason):
    plane = SimpleNamespace(
        releases=SimpleNamespace(
            create=lambda **kw: SimpleNamespace(id="release-1"),
            changelog=SimpleNamespace(
                update=lambda **kw: None,
                retrieve=lambda **kw: (_ for _ in ()).throw(error),
            ),
        )
    )
    context: dict[str, Any] = {"workspace_objects": []}

    with pytest.raises(expected_error) as caught:
        seed_release(plane, "ws", context)

    if expected_reason is not None:
        assert caught.value.reason == expected_reason
    assert "release_changelog_text" not in context


def test_empty_release_changelog_readback_is_a_seed_failure():
    plane = SimpleNamespace(
        releases=SimpleNamespace(
            create=lambda **kw: SimpleNamespace(id="release-1"),
            changelog=SimpleNamespace(
                update=lambda **kw: None,
                retrieve=lambda **kw: SimpleNamespace(description_html="<p> </p>"),
            ),
        )
    )

    with pytest.raises(RuntimeError, match="readback was empty after seeding"):
        seed_release(plane, "ws", {"workspace_objects": []})
