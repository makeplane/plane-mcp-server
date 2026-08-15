"""Offline eval tests for catalog."""

from __future__ import annotations

import inspect

import pytest

from evals import tasks as tasks_mod
from evals.tasks.catalog import TASKS, TASKS_BY_ID, battery_fingerprint, get_tasks, task_author
from evals.tasks.debias import (
    I1_TITLE,
)

DESIGN_IDS = {
    "R1",
    "R2",
    "R3",
    "R4",
    "R5",
    "R6",
    "W1",
    "W2",
    "W3",
    "W4",
    "W5",
    "W6",
    "W7",
    "W8",
    "S1",
    "S2",
    "S3",
    "S4",
    "C1",
    "C2",
}

EXTRA_IDS = {"W9", "W10", "R7", "S5", "W11"}  # bulk, pages, state inventory, features, gate recovery

ID_IN_HAND_IDS = {"I1", "I2", "I3", "I4", "I5"}

LONG_TAIL_IDS = {"L1", "L2", "L3", "L4", "L5"}

NO_PROJECT_PROMPT_IDS = {"C2", "L3", "L4"}

CATALOG_ID_ORDER = (
    "R1",
    "R2",
    "R3",
    "R4",
    "R5",
    "R6",
    "W1",
    "W2",
    "W3",
    "W4",
    "W5",
    "W6",
    "W7",
    "W8",
    "W9",
    "W10",
    "W11",
    "S1",
    "S2",
    "S3",
    "S4",
    "S5",
    "C1",
    "C2",
    "R7",
    "I1",
    "I2",
    "I3",
    "I4",
    "I5",
    "L1",
    "L2",
    "L3",
    "L4",
    "L5",
)

# "eea5abf36382" before CATALOG_REVISION entered the payload; "232036625e00" at revision 1;
# "9c148461e674" at revision 3 before fixture names joined the payload.
# "013109dc1c4c" at revision 3 after fixture names joined the payload.
# "9f2c2feb2e24" at revision 4 before read provenance and randomised truth.
# "7b8dc6bd2f8f" at revision 5 before unverifiable W8/W9 asks were removed.
# "77230f96962d" at revision 6 before target-entity response evidence.
# This pin moves with every deliberate revision bump, and must not move otherwise — an
# unexplained change means the serialization drifted, which is what the pin exists to catch.
PINNED_SYNTHETIC_BATTERY = "e059523c9f3d"


@pytest.mark.parametrize("case", ["design-and-extras", "id-order"])
def test_catalog_behaviours(case):
    if case == "id-order":
        assert tuple(task["id"] for task in TASKS) == CATALOG_ID_ORDER
        return

    ids = {task["id"] for task in TASKS}
    for expected, label in (
        (DESIGN_IDS, "DESIGN"),
        (EXTRA_IDS, "extra"),
        (ID_IN_HAND_IDS, "I-class"),
        (LONG_TAIL_IDS, "L-class"),
    ):
        assert expected.issubset(ids), f"missing {label}: {expected - ids}"
    assert len(TASKS) >= 20


@pytest.mark.parametrize("case", ["all-and-filter", "unknown-id"])
def test_get_tasks_behaviours(case):
    if case == "unknown-id":
        with pytest.raises(SystemExit):
            get_tasks(["NOPE"])
        return

    assert len(get_tasks(None)) == len(TASKS)
    assert [task["id"] for task in get_tasks(["R1", "W9", "C2"])] == ["R1", "W9", "C2"]


@pytest.mark.parametrize("case", ["schema-invariants", "author-default"])
def test_task_behaviours(case):
    if case == "author-default":
        assert task_author({}) == "claude"
        assert task_author({"author": "alice"}) == "alice"
        return

    for task in TASKS:
        assert task["id"]
        assert isinstance(task["tags"], set)
        assert "{project}" in task["prompt"] or task["id"] in NO_PROJECT_PROMPT_IDS
        assert callable(task["verify"])
        assert isinstance(task.get("needs"), set)


def test_debias_tasks_author():
    from evals.tasks.catalog import task_author

    for tid in ID_IN_HAND_IDS | LONG_TAIL_IDS:
        t = TASKS_BY_ID[tid]
        assert task_author(t) == "post-hoc-debias"


def test_w6_seeds_an_open_cycle():
    """W6 asks the agent to close Sprint 12, so the seed must leave it open.

    Plane rejects every edit to an ended cycle, so a pre-closed fixture makes the
    task unachievable by design.
    """
    assert "cycles_open_past" in TASKS_BY_ID["W6"]["needs"]
    assert "cycles" in TASKS_BY_ID["W6"]["needs"]


def test_prompts_do_not_ask_for_unverifiable_w8_date_or_w9_batching():
    from plane.models.work_items import CreateWorkItemWorkLog, WorkItemWorkLog

    w8_prompt = str(TASKS_BY_ID["W8"]["prompt"])
    w9_prompt = str(TASKS_BY_ID["W9"]["prompt"])
    authoritative_date_fields = {"logged_at", "logged_date", "work_date", "date"}
    assert not authoritative_date_fields.intersection(CreateWorkItemWorkLog.model_fields)
    assert not authoritative_date_fields.intersection(WorkItemWorkLog.model_fields)
    assert "yesterday" not in w8_prompt.casefold()
    assert "in one batch" not in w9_prompt.casefold()


def test_verifiers_are_async_and_importable():
    modules = {
        "R": "read",
        "W": "write",
        "S": "schema",
        "C": "cross",
        "I": "debias",
        "L": "debias",
    }
    for t in TASKS:
        fn = t["verify"]
        assert inspect.iscoroutinefunction(fn), t["id"]
        # Callables resolve without NameError
        assert fn.__module__ == f"evals.tasks.{modules[t['id'][0]]}"


def test_tasks_module_has_no_hardcoded_uuids():
    """Regression: verifiers must resolve expected values at verify time."""
    src = inspect.getsource(tasks_mod)
    # Crude: no UUID-shaped literals in tasks module.
    assert not any(len(part) == 36 and part.count("-") == 4 for part in src.replace('"', " ").replace("'", " ").split())


@pytest.mark.parametrize(
    "case",
    ["strict-empty", "strict-exception", "dry-run-markers", "strict-success"],
)
def test_prompt_bind_behaviours(case):
    from evals.tasks.prompts import PromptBindError, format_task_prompt

    task = TASKS_BY_ID["I1"]
    if case == "strict-empty":
        with pytest.raises(PromptBindError):
            format_task_prompt(task, {"project_name": "P", "items": {}}, strict=True)
    elif case == "strict-exception":

        def boom(_ctx):
            raise RuntimeError("seed broken")

        custom = {"id": "X", "prompt": "do {work_item_id}", "prompt_bind": boom}
        with pytest.raises(PromptBindError, match="prompt_bind failed"):
            format_task_prompt(custom, {"project_name": "P"}, strict=True)
    elif case == "dry-run-markers":
        text = format_task_prompt(task, {"project_name": "EVAL x"}, strict=False)
        assert "<work_item_id>" in text and "EVAL x" in text
    else:
        text = format_task_prompt(
            task,
            {"project_name": "P", "items": {I1_TITLE: "uuid-abc"}},
            strict=True,
        )
        assert "uuid-abc" in text and "<" not in text


@pytest.mark.parametrize(
    "case",
    ["stable-and-sensitive", "catalog-nonempty", "debias-tasks-change-hash"],
)
def test_battery_fingerprint_behaviours(case):
    if case == "catalog-nonempty":
        fingerprint = battery_fingerprint()
        assert len(fingerprint) == 12
        assert battery_fingerprint(list(TASKS)) == fingerprint
        return
    if case == "debias-tasks-change-hash":
        full = battery_fingerprint()
        without_debias = [task for task in TASKS if not str(task.get("id", "")).startswith(("I", "L"))]
        assert without_debias, "pre-debias catalog should be non-empty"
        reduced = battery_fingerprint(without_debias)
        assert reduced != full
        assert battery_fingerprint(without_debias + [TASKS_BY_ID["I1"]]) != reduced
        return

    task_a = {"id": "A", "prompt": "p1 {project}"}
    task_b = {"id": "B", "prompt": "p2"}
    assert battery_fingerprint([task_b, task_a]) == battery_fingerprint([task_a, task_b]) == PINNED_SYNTHETIC_BATTERY
    assert battery_fingerprint([{**task_a, "prompt": "p1 edited {project}"}, task_b]) != PINNED_SYNTHETIC_BATTERY
    assert battery_fingerprint([task_a]) != PINNED_SYNTHETIC_BATTERY


def test_revision_bump_changes_the_fingerprint_for_an_unchanged_catalog():
    """A fixture/verifier correction is expressible even though the hash ignores them.

    The per-task payload deliberately omits verifier bodies, so without the revision a
    corrected verifier could keep the old fingerprint and go on asserting that results
    graded against a different contract are comparable.
    """
    from evals.tasks import catalog

    tasks = list(catalog.TASKS)
    before = battery_fingerprint(tasks)
    original = catalog.CATALOG_REVISION
    try:
        catalog.CATALOG_REVISION = original + 1
        after = battery_fingerprint(tasks)
    finally:
        catalog.CATALOG_REVISION = original

    assert after != before, "bumping the revision must move the fingerprint"
    assert battery_fingerprint(tasks) == before, "restoring the revision must restore it"


def test_fingerprint_records_the_revision_transition():
    """Pin the current value, so a future change is read as intentional, not drift.

    ``d546d3181bdb`` was the fingerprint before the revision field existed (batteries 6-8).
    Revision 3 drops author-declared tool sets and call floors and adds fixture names, so
    the hash covers what the agent was asked and what it was given — never how anyone
    expected it to answer. Its final full-catalog value was ``d89173c744cc``. Revision 4
    rewrites R7 to replace its unconditional-pass transition question with an exact
    state-and-group listing; its full-catalog value was ``0c9b6fc0405e``. Revision 5
    adds successful Plane-call provenance and randomised API-confirmed seed truth to the
    read family; its full-catalog value was ``075bbd409f15``. Revision 6 removes W8's
    unverifiable logged-date ask and W9's unverifiable batching ask, and tightens the
    affected end-state verifiers; its full-catalog value was ``ccf39203f656``. Revision 7
    binds read provenance to target-entity response evidence and gives C2/R7 randomised,
    immutable seed-time oracles. Results across these transitions are not comparable.
    Asserting the constant rather than merely 'it changed' makes future drift visible.
    """
    from evals.tasks.catalog import CATALOG_REVISION

    assert CATALOG_REVISION == 7
    assert battery_fingerprint() == "9ea76bf22ba0"
