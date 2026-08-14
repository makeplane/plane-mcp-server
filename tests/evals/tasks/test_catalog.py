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

EXTRA_IDS = {"W9", "W10", "R7", "S5", "W11"}  # bulk, pages, transitions, features, gate recovery

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

# "eea5abf36382" before CATALOG_REVISION entered the payload; "232036625e00" at revision 1.
# This pin moves with every deliberate revision bump, and must not move otherwise — an
# unexplained change means the serialization drifted, which is what the pin exists to catch.
PINNED_SYNTHETIC_BATTERY = "3e9194740d73"


def test_catalog_behaviours():
    def test_catalog_includes_design_and_extras():
        ids = {t["id"] for t in TASKS}
        assert DESIGN_IDS.issubset(ids), f"missing DESIGN ids: {DESIGN_IDS - ids}"
        assert EXTRA_IDS.issubset(ids), f"missing extra ids: {EXTRA_IDS - ids}"
        assert ID_IN_HAND_IDS.issubset(ids), f"missing I-class: {ID_IN_HAND_IDS - ids}"
        assert LONG_TAIL_IDS.issubset(ids), f"missing L-class: {LONG_TAIL_IDS - ids}"
        assert len(TASKS) >= 20

    def test_catalog_id_order_is_pinned():
        assert tuple(task["id"] for task in TASKS) == CATALOG_ID_ORDER

    test_catalog_includes_design_and_extras()
    test_catalog_id_order_is_pinned()


def test_get_tasks_behaviours():
    def test_get_tasks_all_and_filter():
        all_t = get_tasks(None)
        assert len(all_t) == len(TASKS)
        subset = get_tasks(["R1", "W9", "C2"])
        assert [t["id"] for t in subset] == ["R1", "W9", "C2"]

    def test_get_tasks_unknown_exits():
        with pytest.raises(SystemExit):
            get_tasks(["NOPE"])

    test_get_tasks_all_and_filter()
    test_get_tasks_unknown_exits()


def test_task_behaviours():
    def test_task_schema_invariants():
        for t in TASKS:
            assert t["id"]
            assert isinstance(t["tags"], set)
            assert "{project}" in t["prompt"] or t["id"] in NO_PROJECT_PROMPT_IDS
            assert isinstance(t["optimal_tools"], set) and t["optimal_tools"]
            assert isinstance(t["alternate_tools"], set)
            assert t["optimal_tools"].isdisjoint(t["alternate_tools"]), t["id"]
            assert callable(t["verify"])
            assert isinstance(t.get("needs"), set)

    def test_task_author_default():
        assert task_author({}) == "claude"
        assert task_author({"author": "alice"}) == "alice"

    test_task_schema_invariants()
    test_task_author_default()


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


def test_prompt_bind_behaviours():
    def test_prompt_bind_strict_empty_raises():
        from evals.tasks.catalog import TASKS_BY_ID
        from evals.tasks.prompts import PromptBindError, format_task_prompt

        t = TASKS_BY_ID["I1"]
        with pytest.raises(PromptBindError):
            format_task_prompt(t, {"project_name": "P", "items": {}}, strict=True)

    def test_prompt_bind_strict_exception_raises():
        from evals.tasks.prompts import PromptBindError, format_task_prompt

        def boom(_ctx):
            raise RuntimeError("seed broken")

        task = {
            "id": "X",
            "prompt": "do {work_item_id}",
            "prompt_bind": boom,
        }
        with pytest.raises(PromptBindError, match="prompt_bind failed"):
            format_task_prompt(task, {"project_name": "P"}, strict=True)

    def test_prompt_bind_dry_run_markers():
        from evals.tasks.catalog import TASKS_BY_ID
        from evals.tasks.prompts import format_task_prompt

        t = TASKS_BY_ID["I1"]
        text = format_task_prompt(t, {"project_name": "EVAL x"}, strict=False)
        assert "<work_item_id>" in text
        assert "EVAL x" in text

    def test_prompt_bind_strict_success():
        from evals.tasks.catalog import TASKS_BY_ID
        from evals.tasks.prompts import format_task_prompt

        t = TASKS_BY_ID["I1"]
        text = format_task_prompt(
            t,
            {"project_name": "P", "items": {I1_TITLE: "uuid-abc"}},
            strict=True,
        )
        assert "uuid-abc" in text
        assert "<" not in text

    test_prompt_bind_strict_empty_raises()
    test_prompt_bind_strict_exception_raises()
    test_prompt_bind_dry_run_markers()
    test_prompt_bind_strict_success()


def test_battery_fingerprint_behaviours():
    def test_battery_fingerprint_stable_and_sensitive():
        t1 = {
            "id": "A",
            "prompt": "p1 {project}",
            "optimal_tools": {"b", "a"},
            "alternate_tools": {"c"},
            "optimal_calls": 2,
        }
        t2 = {
            "id": "B",
            "prompt": "p2",
            "optimal_tools": {"x"},
            "alternate_tools": set(),
            "optimal_calls": 1,
        }
        # Order of list must not matter (sorted by id).
        h1 = battery_fingerprint([t2, t1])
        h2 = battery_fingerprint([t1, t2])
        assert h1 == h2 == PINNED_SYNTHETIC_BATTERY
        assert len(h1) == 12

        t1_edit = {**t1, "prompt": "p1 edited {project}"}
        assert battery_fingerprint([t1_edit, t2]) != PINNED_SYNTHETIC_BATTERY

        # Subset of selected tasks → different fingerprint (documented ceiling).
        assert battery_fingerprint([t1]) != PINNED_SYNTHETIC_BATTERY

    def test_battery_fingerprint_catalog_is_nonempty():
        from evals.tasks.catalog import TASKS

        fp = battery_fingerprint()
        assert len(fp) == 12
        assert battery_fingerprint(list(TASKS)) == fp

    def test_battery_fingerprint_changes_with_new_debias_tasks():
        from evals.tasks.catalog import TASKS, TASKS_BY_ID

        full = battery_fingerprint()
        without_debias = [t for t in TASKS if not str(t.get("id", "")).startswith(("I", "L"))]
        assert without_debias, "pre-debias catalog should be non-empty"
        reduced = battery_fingerprint(without_debias)
        assert reduced != full
        # Single new task also moves the hash relative to a reduced set.
        assert battery_fingerprint(without_debias + [TASKS_BY_ID["I1"]]) != reduced

    test_battery_fingerprint_stable_and_sensitive()
    test_battery_fingerprint_catalog_is_nonempty()
    test_battery_fingerprint_changes_with_new_debias_tasks()


def test_revision_bump_changes_the_fingerprint_for_an_unchanged_catalog():
    """A fixture/verifier correction is expressible even though the hash ignores them.

    The per-task payload deliberately omits ``needs`` and verifier bodies, so without
    the revision a corrected seeder would keep the old fingerprint and go on asserting
    that results answering a different question are comparable.
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
    Revision 2 is the feature-exclusion correction, which redefines what S5 asks without
    touching any prompt or tool set — exactly the change the hash could not otherwise see.
    Asserting the constant rather than merely 'it changed' is what makes an unexplained
    future move visible.
    """
    from evals.tasks.catalog import CATALOG_REVISION

    assert CATALOG_REVISION == 2
    assert battery_fingerprint() == "4fb3a34a7231"
