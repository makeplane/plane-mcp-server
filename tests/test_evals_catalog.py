"""Offline tests for the full eval task catalog + seed plan + verifiers."""

from __future__ import annotations

import inspect

import pytest

from evals import seed as seed_mod
from evals import tasks as tasks_mod
from evals.cli import cmd_dry_run, cmd_list, parse_args
from evals.seed import seed_plan
from evals.tasks import TASKS, TASKS_BY_ID, get_tasks

# DESIGN.md catalog ids (stable) + extras added for uncovered tool families.
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
EXTRA_IDS = {"W9", "W10", "R7", "S5"}  # bulk, pages, transitions, features
# WS3 de-biasing classes
ID_IN_HAND_IDS = {"I1", "I2", "I3", "I4", "I5"}
LONG_TAIL_IDS = {"L1", "L2", "L3", "L4", "L5"}
# Workspace-scoped prompts that omit {project}
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


@pytest.fixture(autouse=True)
def _eval_creds(monkeypatch):
    monkeypatch.setenv("EVAL_PLANE_API_KEY", "test-key")
    monkeypatch.setenv("EVAL_PLANE_WORKSPACE_SLUG", "test-ws")
    monkeypatch.delenv("EVAL_PLANE_BASE_URL", raising=False)
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.delenv("REDIS_PORT", raising=False)


def test_catalog_includes_design_and_extras():
    ids = {t["id"] for t in TASKS}
    assert DESIGN_IDS.issubset(ids), f"missing DESIGN ids: {DESIGN_IDS - ids}"
    assert EXTRA_IDS.issubset(ids), f"missing extra ids: {EXTRA_IDS - ids}"
    assert ID_IN_HAND_IDS.issubset(ids), f"missing I-class: {ID_IN_HAND_IDS - ids}"
    assert LONG_TAIL_IDS.issubset(ids), f"missing L-class: {LONG_TAIL_IDS - ids}"
    assert len(TASKS) >= 20


def test_catalog_id_order_is_pinned():
    assert tuple(task["id"] for task in TASKS) == CATALOG_ID_ORDER


def test_get_tasks_all_and_filter():
    all_t = get_tasks(None)
    assert len(all_t) == len(TASKS)
    subset = get_tasks(["R1", "W9", "C2"])
    assert [t["id"] for t in subset] == ["R1", "W9", "C2"]


def test_get_tasks_unknown_exits():
    with pytest.raises(SystemExit):
        get_tasks(["NOPE"])


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


def test_debias_tasks_author():
    from evals.tasks import task_author

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


def test_seed_plan_covers_all_groups():
    groups = {
        "items",
        "labels",
        "bug_type",
        "cycles",
        "module",
        "intake",
        "customer",
        "release",
        "second_project",
    }
    lines = seed_plan(groups)
    blob = "\n".join(lines)
    for g in groups:
        assert (
            g.split("_")[0] in blob or g in blob or g.replace("_", " ") in blob or any(g in line for line in lines)
        ), f"seed_plan missing {g}: {lines}"
    # Specific fixtures named
    assert "Sprint 12" in blob
    assert "Checkout revamp" in blob
    assert "1.2.0" in blob
    assert "Acme Corp" in blob


def test_seed_plan_empty_needs_only_project():
    lines = seed_plan(set())
    assert any("project" in line for line in lines)
    # project line + default workspace customers enable note
    assert any("customers" in line for line in lines)
    assert len(lines) == 2


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


def test_cmd_list_prints_all_task_ids(capsys):
    rc = cmd_list()
    assert rc == 0
    out = capsys.readouterr().out
    for tid in DESIGN_IDS | EXTRA_IDS:
        assert tid in out


def test_cmd_dry_run_all_tasks(capsys):
    rc = cmd_dry_run(list(TASKS))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Seed plan:" in out
    for tid in ("R1", "W9", "S4", "C2", "R7"):
        assert f"=== {tid} ===" in out


def test_parse_args_list():
    a = parse_args(["--list", "--label", "candidate-build"])
    assert a.list is True
    assert a.label == "candidate-build"
    assert parse_args(["--list"]).label == "local"


def test_tasks_module_has_no_hardcoded_uuids():
    """Regression: verifiers must resolve expected values at verify time."""
    src = inspect.getsource(tasks_mod)
    # Crude: no UUID-shaped literals in tasks module.
    assert not any(len(part) == 36 and part.count("-") == 4 for part in src.replace('"', " ").replace("'", " ").split())


def test_seed_module_ast_has_all_group_handlers():
    """seed() dispatches every documented fixture group."""
    src = inspect.getsource(seed_mod.seed)
    for group in (
        "labels",
        "items",
        "bug_type",
        "cycles",
        "module",
        "intake",
        "customer",
        "release",
        "second_project",
    ):
        assert f'"{group}"' in src or f"'{group}'" in src, group


def test_seed_enables_project_features_immediately_after_create(monkeypatch):
    """Fresh projects ship with cycles/modules/intake/worklogs off — seed must enable them.

    Sequence: create → workspace features (customers) → project update → project features.
    """
    from types import SimpleNamespace

    from plane.models.projects import ProjectFeature, UpdateProject
    from plane.models.workspaces import WorkspaceFeature

    monkeypatch.setenv("EVAL_PLANE_WORKSPACE_SLUG", "test-ws")
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.delenv("REDIS_PORT", raising=False)

    calls: list[tuple] = []

    class _Projects:
        def create(self, workspace_slug, data):
            calls.append(("create", workspace_slug, getattr(data, "name", None)))
            return SimpleNamespace(id="proj-main")

        def update(self, workspace_slug, project_id, data):
            assert isinstance(data, UpdateProject)
            calls.append(("update", project_id, data.model_dump(exclude_none=True)))
            return SimpleNamespace(id=project_id)

        def update_features(self, workspace_slug, project_id, data):
            assert isinstance(data, ProjectFeature)
            calls.append(("update_features", project_id, data.model_dump(exclude_none=True)))
            return data

    class _Workspaces:
        def update_features(self, workspace_slug, data):
            assert isinstance(data, WorkspaceFeature)
            calls.append(("ws_update_features", data.model_dump(exclude_none=True)))
            return data

    plane = SimpleNamespace(projects=_Projects(), workspaces=_Workspaces())
    ctx: dict = {}
    seed_mod.seed(plane, run_id="deadbeefcafebabe", needs=set(), ctx=ctx)

    assert ctx["project_id"] == "proj-main"
    kinds = [c[0] for c in calls]
    assert kinds == ["create", "ws_update_features", "update", "update_features"]
    # Workspace customers enabled for C1 preconditions
    assert calls[1][1].get("customers") is True
    assert "work_item_types" not in calls[1][1]
    # Project enable calls target the created id
    assert calls[2][1] == "proj-main"
    assert calls[3][1] == "proj-main"
    upd = calls[2][2]
    assert upd.get("cycle_view") is True
    assert upd.get("is_time_tracking_enabled") is True
    feat = calls[3][2]
    assert feat.get("cycles") is True


def test_seed_s5_leaves_cycles_worklogs_and_customers_off(monkeypatch):
    """S5 needs leave_cycles_worklogs_off — project cycles/worklogs + workspace customers OFF."""
    from types import SimpleNamespace

    monkeypatch.setenv("EVAL_PLANE_WORKSPACE_SLUG", "test-ws")
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.delenv("REDIS_PORT", raising=False)

    calls: list[tuple] = []

    class _Projects:
        def create(self, workspace_slug, data):
            return SimpleNamespace(id="proj-s5")

        def update(self, workspace_slug, project_id, data):
            calls.append(("update", data.model_dump(exclude_none=True)))
            return SimpleNamespace(id=project_id)

        def update_features(self, workspace_slug, project_id, data):
            calls.append(("features", data.model_dump(exclude_none=True)))
            return data

    class _Workspaces:
        def update_features(self, workspace_slug, data):
            calls.append(("ws_features", data.model_dump(exclude_none=True)))
            return data

    plane = SimpleNamespace(projects=_Projects(), workspaces=_Workspaces())
    ctx: dict = {}
    seed_mod.seed(plane, run_id="s5s5s5s5s5s5s5s5", needs={"leave_cycles_worklogs_off"}, ctx=ctx)
    assert ctx["feature_exclude"] == ["cycles", "worklogs"]
    assert ctx["ws_feature_exclude"] == ["customers"]
    assert ctx["s5_left_customers_off"] is True
    # No workspace customers enable call (excluded → _enable_workspace_features no-ops)
    assert not any(c[0] == "ws_features" for c in calls)
    upd = next(c[1] for c in calls if c[0] == "update")
    assert "cycle_view" not in upd
    assert "is_time_tracking_enabled" not in upd
    assert upd.get("module_view") is True
    feat = next(c[1] for c in calls if c[0] == "features")
    assert "cycles" not in feat
    assert feat.get("modules") is True


def test_seed_cycles_create_add_then_backdate(monkeypatch):
    """Sprint 12: create (active end) → add_work_items → update(end_date past).

    Plane rejects adds when end_date is already past; seed must not create past first.
    """
    from types import SimpleNamespace

    from plane.models.cycles import CreateCycle, UpdateCycle

    monkeypatch.setenv("EVAL_PLANE_WORKSPACE_SLUG", "test-ws")
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.delenv("REDIS_PORT", raising=False)

    calls: list[tuple] = []
    cycle_seq = {"n": 0}

    class _Cycles:
        def create(self, workspace_slug, project_id, data):
            assert isinstance(data, CreateCycle)
            cycle_seq["n"] += 1
            cid = f"cyc-{cycle_seq['n']}"
            calls.append(
                (
                    "create",
                    {
                        "name": data.name,
                        "start_date": data.start_date,
                        "end_date": data.end_date,
                        "id": cid,
                    },
                )
            )
            return SimpleNamespace(id=cid, name=data.name, end_date=data.end_date)

        def add_work_items(self, workspace_slug, project_id, cycle_id, issue_ids):
            calls.append(("add_work_items", {"cycle_id": cycle_id, "n": len(issue_ids)}))

        def update(self, workspace_slug, project_id, cycle_id, data):
            assert isinstance(data, UpdateCycle)
            calls.append(("update", {"cycle_id": cycle_id, "end_date": data.end_date}))
            return SimpleNamespace(id=cycle_id, end_date=data.end_date)

    class _Projects:
        def create(self, workspace_slug, data):
            return SimpleNamespace(id="proj-1")

        def update(self, workspace_slug, project_id, data):
            return SimpleNamespace(id=project_id)

        def update_features(self, workspace_slug, project_id, data):
            return data

    class _Workspaces:
        def update_features(self, workspace_slug, data):
            return data

        def get_features(self, workspace_slug):
            return SimpleNamespace(model_dump=lambda: {})

    class _Users:
        def get_me(self):
            return SimpleNamespace(id="user-1")

    class _States:
        def list(self, workspace_slug, project_id):
            return SimpleNamespace(
                results=[
                    SimpleNamespace(id="st-started", name="In Progress", group="started", default=False),
                    SimpleNamespace(id="st-todo", name="Todo", group="unstarted", default=True),
                ]
            )

    item_n = {"n": 0}

    class _WorkItems:
        def create(self, workspace_slug, project_id, data):
            item_n["n"] += 1
            return SimpleNamespace(
                id=f"wi-{item_n['n']}",
                name=data.name,
                state="st-started",
                created_at="2026-01-01",
            )

        def update(self, workspace_slug, project_id, work_item_id, data):
            return SimpleNamespace(id=work_item_id, name="x", state=getattr(data, "state", None))

        class comments:
            @staticmethod
            def create(**kw):
                return SimpleNamespace(id="c1")

    plane = SimpleNamespace(
        projects=_Projects(),
        workspaces=_Workspaces(),
        cycles=_Cycles(),
        users=_Users(),
        states=_States(),
        work_items=_WorkItems(),
    )
    ctx: dict = {}
    seed_mod.seed(plane, run_id="cycletestabcdef", needs={"items", "cycles"}, ctx=ctx)

    # Filter to Sprint-12-related create/add/update sequence (first cycle is past).
    past_id = ctx["cycle_past_id"]
    # Must create both cycles before any backdate update of past.
    create_idxs = [i for i, c in enumerate(calls) if c[0] == "create"]
    assert len(create_idxs) == 2
    past_create = next(c for c in calls if c[0] == "create" and c[1]["name"] == seed_mod.CYCLE_PAST)
    # Created with temporary *future* end_date (active), not the final past end.
    assert past_create[1]["end_date"] > past_create[1]["start_date"]
    # At least one add to past cycle before its update
    past_adds = [i for i, c in enumerate(calls) if c[0] == "add_work_items" and c[1]["cycle_id"] == past_id]
    past_updates = [i for i, c in enumerate(calls) if c[0] == "update" and c[1]["cycle_id"] == past_id]
    assert past_adds, "expected add_work_items on Sprint 12"
    assert past_updates, "expected backdate update on Sprint 12"
    assert max(past_adds) < min(past_updates), f"add must precede backdate; calls={calls}"
    # Backdated end matches W6 seed ctx; differs from create-time active end
    backdated_end = calls[past_updates[0]][1]["end_date"]
    assert ctx["cycle_past_seed_end_date"] == backdated_end
    assert backdated_end != past_create[1]["end_date"]
    assert ctx.get("cycle_past_end_date_before_backdate") == past_create[1]["end_date"]
    # Active cycle: create with future end; never backdated
    cur_create = next(c for c in calls if c[0] == "create" and c[1]["name"] == seed_mod.CYCLE_CURRENT)
    assert cur_create[1]["end_date"]
    cur_updates = [c for c in calls if c[0] == "update" and c[1]["cycle_id"] == ctx["cycle_current_id"]]
    assert cur_updates == []


def test_teardown_s5_reenables_workspace_customers(monkeypatch):
    from types import SimpleNamespace

    from plane.models.workspaces import WorkspaceFeature

    monkeypatch.setenv("EVAL_PLANE_WORKSPACE_SLUG", "test-ws")
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.delenv("REDIS_PORT", raising=False)

    calls: list = []

    class _Workspaces:
        def update_features(self, workspace_slug, data):
            assert isinstance(data, WorkspaceFeature)
            calls.append(data.model_dump(exclude_none=True))
            return data

    plane = SimpleNamespace(workspaces=_Workspaces(), projects=SimpleNamespace(delete=lambda **k: None))
    seed_mod.teardown(plane, {"workspace_slug": "test-ws", "s5_left_customers_off": True, "project_id": None})
    assert calls and calls[0].get("customers") is True


def test_seed_enables_features_on_second_project_too(monkeypatch):
    """R6 second project also gets feature enable after its create."""
    from types import SimpleNamespace

    monkeypatch.setenv("EVAL_PLANE_WORKSPACE_SLUG", "test-ws")
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.delenv("REDIS_PORT", raising=False)

    creates: list[str] = []
    enables: list[str] = []

    class _Projects:
        def create(self, workspace_slug, data):
            pid = f"p-{len(creates)}"
            creates.append(pid)
            return SimpleNamespace(id=pid)

        def update(self, workspace_slug, project_id, data):
            enables.append(("update", project_id))
            return SimpleNamespace(id=project_id)

        def update_features(self, workspace_slug, project_id, data):
            enables.append(("features", project_id))
            return data

    # Minimal stubs so second_project seed gets past bug_type + work items.
    class _Workspaces:
        def get_features(self, workspace_slug):
            return SimpleNamespace(model_dump=lambda: {"is_work_item_types_enabled": False})

        def update_features(self, workspace_slug, data):
            enables.append(("ws_features", workspace_slug))
            return data

    class _WorkItemTypes:
        def list(self, **kw):
            return [SimpleNamespace(id="bug-1", name="Bug")]

        def create(self, **kw):
            return SimpleNamespace(id="bug-1", name="Bug")

        def import_to_project(self, **kw):
            return None

    class _WorkItems:
        def create(self, **kw):
            return SimpleNamespace(id=f"wi-{id(kw)}", name=kw["data"].name)

    plane = SimpleNamespace(
        projects=_Projects(),
        workspaces=_Workspaces(),
        work_item_types=_WorkItemTypes(),
        work_items=_WorkItems(),
    )
    ctx: dict = {}
    # second_project path also seeds bug_type when missing
    seed_mod.seed(plane, run_id="aabbccdd11223344", needs={"second_project", "bug_type"}, ctx=ctx)

    assert len(creates) == 2
    # Each create followed by update + update_features for that project id
    assert ("update", creates[0]) in enables
    assert ("features", creates[0]) in enables
    assert ("update", creates[1]) in enables
    assert ("features", creates[1]) in enables
