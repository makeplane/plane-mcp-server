"""Offline eval tests for seed."""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from plane.errors.errors import HttpError

from evals import cleanup as cleanup_mod
from evals import seed as seed_mod
from evals.errors import TaskSkipped
from evals.evidence import TARGET_ENTITY_EVIDENCE
from evals.seed import (
    R5_TITLE,
    create_project_with_identifier_retry,
    is_identifier_collision,
    seed_plan,
    seed_second_project,
)
from evals.tasks.debias import (
    L3_TAG_VERSION,
    L4_PROP_DISPLAY,
)
from evals.tasks.read import verify_r6
from tests.evals.conftest import case_params


class _Page:
    def __init__(self, results: list[Any] | None = None):
        self.results = results or []
        self.next_page_results = False
        self.next_cursor = None


class _TeardownPlane:
    def __init__(self):
        self.deleted: list[tuple[str, str]] = []
        self.releases = SimpleNamespace(
            tags=SimpleNamespace(
                list=lambda **kw: _Page([SimpleNamespace(id="tag-1", version=L3_TAG_VERSION)]),
                delete=lambda **kw: self.deleted.append(("release_tag", kw["tag_id"])),
            ),
            delete=lambda **kw: self.deleted.append(("release", kw.get("release_id"))),
        )
        self.customers = SimpleNamespace(
            properties=SimpleNamespace(
                list=lambda **kw: _Page(
                    [
                        SimpleNamespace(
                            id="prop-1",
                            display_name=L4_PROP_DISPLAY,
                            name="eval-industry",
                        )
                    ]
                ),
                delete=lambda **kw: self.deleted.append(("customer_property", kw["property_id"])),
            ),
            list=lambda **kw: _Page([]),
            delete=lambda **kw: None,
        )
        self.projects = SimpleNamespace(delete=lambda **kw: None)
        self.work_item_types = SimpleNamespace(list=lambda **kw: [], delete=lambda **kw: None)
        self.workspace_work_item_types = SimpleNamespace(delete=lambda **kw: None)
        self.workspace_work_item_properties = SimpleNamespace(delete=lambda **kw: None)


def test_r6_random_truth_is_per_seed_and_oracle_is_api_confirmed():
    class Projects:
        def __init__(self, main_id: str, main_name: str):
            self.names = {main_id: main_name}

        def create(self, workspace_slug, data):
            project_id = f"second-{len(self.names)}"
            self.names[project_id] = data.name
            return SimpleNamespace(id=project_id, name=data.name, identifier=data.identifier)

        def update(self, **kwargs):
            return None

        def update_features(self, **kwargs):
            return None

        def retrieve(self, *, project_id, **kwargs):
            return SimpleNamespace(id=project_id, name=self.names[project_id])

    class WorkItems:
        def __init__(self, *, mark_second_non_bug: int):
            self.rows: dict[str, SimpleNamespace] = {}
            self.project_ids: dict[str, list[str]] = {}
            self.mark_second_non_bug = mark_second_non_bug

        def create(self, *, project_id, data, **kwargs):
            project_rows = self.project_ids.setdefault(str(project_id), [])
            work_item_id = f"{project_id}-wi-{len(project_rows) + 1}"
            project_rows.append(work_item_id)
            self.rows[work_item_id] = SimpleNamespace(
                id=work_item_id,
                name=data.name,
                type_id=data.type_id,
                completed_at=None,
                archived_at=None,
            )
            return self.rows[work_item_id]

        def retrieve(self, *, project_id, work_item_id, **kwargs):
            row = self.rows[work_item_id]
            project_rows = self.project_ids[str(project_id)]
            if (
                self.mark_second_non_bug
                and str(project_id).startswith("second-")
                and work_item_id in project_rows[-self.mark_second_non_bug :]
            ):
                return SimpleNamespace(**{**vars(row), "type_id": "not-bug"})
            return row

    def seeded(run_id: str, *, mark_second_non_bug: int = 0):
        run8 = run_id[:8]
        main_id = f"main-{run8}"
        main_name = f"EVAL {run8}"
        work_items = WorkItems(mark_second_non_bug=mark_second_non_bug)
        plane = SimpleNamespace(
            projects=Projects(main_id, main_name),
            work_items=work_items,
        )
        ctx = {
            "run_id": run_id,
            "run8": run8,
            "task_id": "R6",
            "project_id": main_id,
            "project_name": main_name,
            "items": {},
            "item_ids": [],
            "bug_type": {"id": "bug-1", "name": "Bug"},
            "bug_type_workspace_level": False,
            "randomized_truth": {},
        }
        seed_second_project(plane, "ws", ctx)
        return plane, ctx

    first_plane, first = seeded("22222222cccccccc")  # deterministic intended counts 3 / 2
    second_plane, second = seeded("aabbccdd11223344", mark_second_non_bug=2)  # intended 4 / 5
    assert "second_project_ids" not in first
    assert "second_project_ids" not in second

    first_intended = first["randomized_truth"]["R6.open_bug_counts"]
    second_truth = second["randomized_truth"]["R6.open_bug_counts"]
    assert (first_intended["intended_main"], first_intended["intended_second"]) != (
        second_truth["intended_main"],
        second_truth["intended_second"],
    )
    assert (first["r6_main_bug_count"], first["r6_second_bug_count"]) == (3, 2)
    assert (second["r6_main_bug_count"], second["r6_second_bug_count"]) == (4, 3)
    # Intended counts say B wins 5-to-4. API readback says main wins 4-to-3;
    # the verifier must use the API-confirmed seed oracle.
    assert second["r6_more_bugs_project"] == second["project_name"]
    assert second_truth["confirmed"]["winner"] == second["project_name"]

    run = {
        "final_text": f"project: {second['project_name']}",
        "calls": [
            {
                "tool": "plane_call",
                "is_error": False,
                "observed_sentinels": [TARGET_ENTITY_EVIDENCE],
            }
        ],
        "call_source": "test",
        "evidence_trace_available": True,
    }
    ok, note = asyncio.run(verify_r6(second_plane, second, run))
    assert ok is True, note
    wrong_run = {**run, "final_text": f"project: {second['second_project_name']}"}
    wrong_ok, wrong_note = asyncio.run(verify_r6(second_plane, second, wrong_run))
    assert wrong_ok is False, wrong_note


def test_baseline_snapshot_failure_surfaces_before_workspace_mutation(monkeypatch):
    monkeypatch.setenv("EVAL_PLANE_WORKSPACE_SLUG", "ws")

    def fail_list(**kwargs):
        raise RuntimeError("customers unreadable")

    plane = SimpleNamespace(
        projects=SimpleNamespace(create=lambda **kwargs: SimpleNamespace(id="project-1", identifier="EVDEADBEEF")),
        customers=SimpleNamespace(
            list=fail_list,
            properties=SimpleNamespace(list=lambda **kwargs: _Page([])),
        ),
        releases=SimpleNamespace(tags=SimpleNamespace(list=lambda **kwargs: _Page([]))),
    )
    context: dict[str, Any] = {}

    with pytest.raises(RuntimeError, match="workspace baseline snapshot: list customers failed"):
        seed_mod.seed(plane, "deadbeefcafebabe", set(), context, task_id="W10")

    assert context["project_id"] == "project-1"


def test_workspace_feature_snapshot_failure_prevents_mutation():
    updates: list[Any] = []
    plane = SimpleNamespace(
        workspaces=SimpleNamespace(
            get_features=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("feature read failed")),
            update_features=lambda **kwargs: updates.append(kwargs),
        )
    )

    with pytest.raises(RuntimeError, match="workspace feature snapshot failed before mutation"):
        seed_mod.enable_workspace_features(plane, "ws")

    assert updates == []


@pytest.mark.parametrize(
    "context_key,context_value",
    [("second_project_id", "second-1"), ("second_project_ids", ["second-1"])],
)
def test_teardown_deletes_second_project_from_current_or_legacy_context_key(context_key, context_value):
    deleted: list[str] = []
    plane = SimpleNamespace(
        customers=SimpleNamespace(
            list=lambda **kwargs: _Page([]),
            properties=SimpleNamespace(list=lambda **kwargs: _Page([])),
        ),
        releases=SimpleNamespace(tags=SimpleNamespace(list=lambda **kwargs: _Page([]))),
        projects=SimpleNamespace(delete=lambda **kwargs: deleted.append(str(kwargs["project_id"]))),
        work_item_types=SimpleNamespace(list=lambda **kwargs: []),
    )
    context = {
        "workspace_slug": "ws",
        "project_id": "main-1",
        context_key: context_value,
        "workspace_baseline": {
            "customers": set(),
            "release_tags": set(),
            "customer_properties": set(),
        },
    }

    seed_mod.teardown(plane, context)

    assert deleted == ["second-1", "main-1"]


def _seed_plan_covers_all_groups(_monkeypatch):
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


def _seed_plan_empty_needs_only_project(_monkeypatch):
    lines = seed_plan(set())
    assert any("project" in line for line in lines)
    # project line + default workspace customers enable note
    assert any("customers" in line for line in lines)
    assert len(lines) == 2


def _seed_module_ast_has_all_group_handlers(_monkeypatch):
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


def _seed_enables_project_features_immediately_after_create(monkeypatch):
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
        def get_features(self, workspace_slug):
            return SimpleNamespace(model_dump=lambda: {"customers": False})

        def update_features(self, workspace_slug, data):
            assert isinstance(data, WorkspaceFeature)
            calls.append(("ws_update_features", data.model_dump(exclude_none=True)))
            return data

    plane = SimpleNamespace(
        projects=_Projects(),
        workspaces=_Workspaces(),
        customers=SimpleNamespace(
            list=lambda **kw: _Page([]),
            properties=SimpleNamespace(list=lambda **kw: _Page([]), delete=lambda **kw: None),
        ),
        releases=SimpleNamespace(
            tags=SimpleNamespace(
                list=lambda **kw: _Page([SimpleNamespace(id="tag-unrelated", version=L3_TAG_VERSION)]),
                delete=lambda **kw: None,
            )
        ),
    )
    ctx: dict = {}
    seed_mod.seed(plane, run_id="deadbeefcafebabe", needs=set(), ctx=ctx, task_id="W10")

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
    assert ctx["workspace_baseline"] == {
        "customers": set(),
        "release_tags": {"tag-unrelated"},
        "customer_properties": set(),
        "work_item_types": None,
        "work_item_properties": None,
    }


def _seed_collision_skips_before_create(monkeypatch):
    from evals.tasks.skip import TaskSkipped

    monkeypatch.setenv("EVAL_PLANE_WORKSPACE_SLUG", "test-ws")
    creates: list[Any] = []
    plane = SimpleNamespace(
        projects=SimpleNamespace(create=lambda **kw: creates.append(kw)),
        customers=SimpleNamespace(
            list=lambda **kw: _Page([]),
            properties=SimpleNamespace(list=lambda **kw: _Page([])),
        ),
        releases=SimpleNamespace(
            tags=SimpleNamespace(list=lambda **kw: _Page([SimpleNamespace(id="tag-collision", version=L3_TAG_VERSION)]))
        ),
    )
    ctx: dict[str, Any] = {}

    with pytest.raises(TaskSkipped, match=r"^env:fixture-collision:release_tags:eval-rc1"):
        seed_mod.seed(plane, run_id="collision123456", needs=set(), ctx=ctx, task_id="L3")

    assert creates == []
    assert ctx["project_id"] is None
    assert ctx["workspace_baseline"] == {
        "customers": None,
        "release_tags": None,
        "customer_properties": None,
        "work_item_types": None,
        "work_item_properties": None,
    }


def _seed_s5_leaves_cycles_worklogs_and_customers_off(monkeypatch):
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
        def get_features(self, workspace_slug):
            return SimpleNamespace(model_dump=lambda: {"customers": True})

        def update_features(self, workspace_slug, data):
            calls.append(("ws_features", data.model_dump(exclude_none=True)))
            return data

    plane = SimpleNamespace(projects=_Projects(), workspaces=_Workspaces())
    ctx: dict = {}
    seed_mod.seed(plane, run_id="s5s5s5s5s5s5s5s5", needs={"leave_cycles_worklogs_off"}, ctx=ctx)
    assert ctx["feature_exclude"] == ["cycles", "worklogs"]
    assert ctx["ws_feature_exclude"] == ["customers"]
    assert ctx["s5_left_customers_off"] is True
    # Excluded features are written OFF, not omitted. The workspace outlives the run, so
    # omitting the write leaves the previous rep's value and S5's precondition never holds.
    ws = next(c[1] for c in calls if c[0] == "ws_features")
    assert ws.get("customers") is False
    assert ctx["workspace_features_prior"] == {"customers": True}
    upd = next(c[1] for c in calls if c[0] == "update")
    assert upd.get("cycle_view") is False
    assert upd.get("is_time_tracking_enabled") is False
    assert upd.get("module_view") is True
    feat = next(c[1] for c in calls if c[0] == "features")
    assert feat.get("cycles") is False
    assert feat.get("modules") is True


def _seed_cycles_create_add_then_backdate(monkeypatch):
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


def _seed_enables_features_on_second_project_too(monkeypatch):
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


_SEED_CASES = case_params(
    _seed_plan_covers_all_groups,
    _seed_plan_empty_needs_only_project,
    _seed_module_ast_has_all_group_handlers,
    _seed_enables_project_features_immediately_after_create,
    _seed_collision_skips_before_create,
    _seed_s5_leaves_cycles_worklogs_and_customers_off,
    _seed_cycles_create_add_then_backdate,
    _seed_enables_features_on_second_project_too,
)


@pytest.mark.parametrize("case", _SEED_CASES)
def test_seed_behaviours(case, monkeypatch):
    case(monkeypatch)


def test_excluding_pages_turns_page_view_off_despite_its_true_default(monkeypatch):
    """``page_view`` defaults to True on a fresh project, so omission is not exclusion.

    The other excludable project features default false, which is why omitting the write
    happened to work for S5. Relying on that is unsound for any feature added later.
    """
    from types import SimpleNamespace

    monkeypatch.setenv("EVAL_PLANE_WORKSPACE_SLUG", "test-ws")
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.delenv("REDIS_PORT", raising=False)

    calls: list[tuple] = []

    class _Projects:
        def update(self, workspace_slug, project_id, data):
            calls.append(("update", data.model_dump(exclude_none=True)))
            return SimpleNamespace(id=project_id)

        def update_features(self, workspace_slug, project_id, data):
            calls.append(("features", data.model_dump(exclude_none=True)))
            return data

    plane = SimpleNamespace(projects=_Projects())
    seed_mod.enable_project_features(plane, "test-ws", "proj-1", exclude={"pages"})

    upd = next(c[1] for c in calls if c[0] == "update")
    assert upd.get("page_view") is False
    feat = next(c[1] for c in calls if c[0] == "features")
    assert feat.get("pages") is False
    assert feat.get("cycles") is True


@pytest.mark.parametrize("prior", [True, False])
def test_teardown_restores_the_workspace_value_it_found(monkeypatch, prior):
    """Teardown puts the toggle back, rather than forcing the value this run wanted.

    The harness runs against an instance it does not own. Forcing ``customers=True`` on
    the way out is configuration drift for anyone whose workspace had it off.
    """
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

    plane = SimpleNamespace(
        workspaces=_Workspaces(),
        projects=SimpleNamespace(delete=lambda **k: None),
        customers=SimpleNamespace(
            list=lambda **kw: _Page([]),
            properties=SimpleNamespace(list=lambda **kw: _Page([])),
        ),
        releases=SimpleNamespace(tags=SimpleNamespace(list=lambda **kw: _Page([]))),
    )
    seed_mod.teardown(
        plane,
        {
            "workspace_slug": "test-ws",
            "workspace_features_prior": {"customers": prior},
            "project_id": None,
        },
    )
    assert calls and calls[0].get("customers") is prior


def _teardown_leaves_workspace_alone_when_prior_unknown():
    from types import SimpleNamespace

    calls: list = []

    class _Workspaces:
        def update_features(self, workspace_slug, data):
            calls.append(data)
            return data

    plane = SimpleNamespace(
        workspaces=_Workspaces(),
        projects=SimpleNamespace(delete=lambda **k: None),
        customers=SimpleNamespace(
            list=lambda **kw: _Page([]),
            properties=SimpleNamespace(list=lambda **kw: _Page([])),
        ),
        releases=SimpleNamespace(tags=SimpleNamespace(list=lambda **kw: _Page([]))),
    )
    seed_mod.teardown(
        plane,
        {
            "workspace_slug": "test-ws",
            "workspace_features_prior": {"customers": None},
            "project_id": None,
        },
    )
    assert calls == []


def _teardown_deletes_release_tag_and_customer_property():
    from evals.seed import teardown

    plane = _TeardownPlane()
    ctx = {
        "workspace_slug": "ws",
        "project_id": "p1",
        "project_name": "EVAL x",
        "workspace_baseline": {
            "customers": set(),
            "release_tags": set(),
            "customer_properties": set(),
        },
        "workspace_objects": [
            {"kind": "release_tag", "id": "tag-tracked"},
            {"kind": "customer_property", "id": "prop-tracked"},
        ],
    }
    teardown(plane, ctx)
    kinds = {k for k, _ in plane.deleted}
    assert "release_tag" in kinds
    assert "customer_property" in kinds
    # Tracked ids deleted
    assert ("release_tag", "tag-tracked") in plane.deleted
    assert ("customer_property", "prop-tracked") in plane.deleted


@pytest.mark.parametrize(
    "case",
    case_params(
        _teardown_leaves_workspace_alone_when_prior_unknown,
        _teardown_deletes_release_tag_and_customer_property,
    ),
)
def test_teardown_behaviours(case):
    case()


def test_teardown_aggregates_failures_after_attempting_every_object():
    from evals.seed import TeardownError, teardown

    delete_calls: list[tuple[str, str]] = []

    def fail_delete(kind: str, object_id: str) -> None:
        delete_calls.append((kind, object_id))
        raise RuntimeError(f"cannot delete {kind} {object_id}")

    plane = SimpleNamespace(
        customers=SimpleNamespace(
            list=lambda **kw: _Page([]),
            delete=lambda **kw: fail_delete("customer", kw["customer_id"]),
            properties=SimpleNamespace(list=lambda **kw: _Page([]), delete=lambda **kw: None),
        ),
        releases=SimpleNamespace(
            delete=lambda **kw: fail_delete("release", kw["release_id"]),
            tags=SimpleNamespace(list=lambda **kw: _Page([]), delete=lambda **kw: None),
        ),
        projects=SimpleNamespace(delete=lambda **kw: fail_delete("project", kw["project_id"])),
        work_item_types=SimpleNamespace(list=lambda **kw: [], delete=lambda **kw: None),
    )
    context = {
        "workspace_slug": "ws",
        "project_id": "project-main",
        "project_name": "EVAL cleanup",
        "second_project_ids": ["project-second"],
        "workspace_objects": [
            {"kind": "customer", "id": "customer-1"},
            {"kind": "release", "id": "release-1"},
        ],
        "workspace_baseline": {
            "customers": set(),
            "release_tags": set(),
            "customer_properties": set(),
        },
    }

    with pytest.raises(TeardownError) as caught:
        teardown(plane, context)

    assert delete_calls == [
        ("customer", "customer-1"),
        ("release", "release-1"),
        ("project", "project-second"),
        ("project", "project-main"),
    ]
    assert len(caught.value.failures) == 4
    assert {failure.target for failure in caught.value.failures} == {
        "customer-1",
        "release-1",
        "project-second",
        "EVAL cleanup",
    }


def test_teardown_customer_baseline_behaviours(capsys):
    cases = (
        {
            "name": "pre-existing name match",
            "customer_id": "customer-existing",
            "baseline": {"customer-existing"},
            "tracked": False,
            "deleted": False,
            "warns": False,
        },
        {
            "name": "agent-created name match",
            "customer_id": "customer-agent",
            "baseline": set(),
            "tracked": False,
            "deleted": True,
            "warns": False,
        },
        {
            "name": "tracked id wins over baseline",
            "customer_id": "customer-tracked",
            "baseline": {"customer-tracked"},
            "tracked": True,
            "deleted": True,
            "warns": False,
        },
        {
            "name": "unavailable baseline fails closed",
            "customer_id": "customer-unknown",
            "baseline": None,
            "tracked": False,
            "deleted": False,
            "warns": True,
        },
    )

    for case in cases:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("EVAL_PLANE_WORKSPACE_SLUG", "test-ws")
            deleted: list[str] = []
            customer = SimpleNamespace(id=case["customer_id"], name="Acme Corp")
            plane = SimpleNamespace(
                customers=SimpleNamespace(
                    list=lambda customer=customer, **kw: _Page([customer]),
                    delete=lambda deleted=deleted, **kw: deleted.append(str(kw["customer_id"])),
                    properties=SimpleNamespace(list=lambda **kw: _Page([]), delete=lambda **kw: None),
                ),
                releases=SimpleNamespace(tags=SimpleNamespace(list=lambda **kw: _Page([]), delete=lambda **kw: None)),
                projects=SimpleNamespace(delete=lambda **kw: None),
            )
            workspace_objects = [{"kind": "customer", "id": case["customer_id"]}] if case["tracked"] else []
            context = {
                "workspace_slug": "test-ws",
                "project_id": None,
                "workspace_objects": workspace_objects,
                "workspace_baseline": {
                    "customers": case["baseline"],
                    "release_tags": set(),
                    "customer_properties": set(),
                },
            }
            if case["warns"]:
                with pytest.raises(seed_mod.TeardownError) as caught:
                    seed_mod.teardown(plane, context)
                assert "baseline unavailable" in str(caught.value)
            else:
                seed_mod.teardown(plane, context)
            output = capsys.readouterr().out
            assert (case["customer_id"] in deleted) is case["deleted"], case["name"]
            assert ("customers baseline unavailable" in output) is case["warns"], case["name"]
            if case["warns"]:
                assert case["customer_id"] in output


def test_preexisting_workspace_bug_is_reused_and_not_deleted():
    deleted_types: list[str] = []
    imported_types: list[str] = []
    bug = SimpleNamespace(id="bug-existing", name="Bug")
    plane = SimpleNamespace(
        workspaces=SimpleNamespace(
            get_features=lambda **kw: SimpleNamespace(model_dump=lambda: {"is_work_item_types_enabled": True})
        ),
        workspace_work_item_types=SimpleNamespace(
            list=lambda **kw: [bug],
            create=lambda **kw: pytest.fail("pre-existing Bug must be reused"),
            delete=lambda **kw: deleted_types.append(str(kw["type_id"])),
            properties=SimpleNamespace(list=lambda **kw: []),
        ),
        workspace_work_item_properties=SimpleNamespace(list=lambda **kw: [], delete=lambda **kw: None),
        work_item_types=SimpleNamespace(
            import_to_project=lambda **kw: imported_types.extend(str(value) for value in kw["work_item_type_ids"]),
        ),
        work_item_properties=SimpleNamespace(list=lambda **kw: [], delete=lambda **kw: None),
        customers=SimpleNamespace(
            list=lambda **kw: _Page([]),
            properties=SimpleNamespace(list=lambda **kw: _Page([])),
        ),
        releases=SimpleNamespace(tags=SimpleNamespace(list=lambda **kw: _Page([]))),
        projects=SimpleNamespace(delete=lambda **kw: None),
    )
    context = {
        "task_id": "S1",
        "workspace_slug": "ws",
        "project_id": "project-1",
        "workspace_objects": [],
        "workspace_baseline": {
            "customers": set(),
            "release_tags": set(),
            "customer_properties": set(),
            "work_item_types": {"bug-existing"},
            "work_item_properties": set(),
        },
    }

    seed_mod.seed_item_type(plane, "ws", context)
    seed_mod.teardown(plane, context)

    assert imported_types == ["bug-existing"]
    assert context["bug_type_created"] is False
    assert context["workspace_objects"] == []
    assert deleted_types == []


@pytest.mark.parametrize(
    ("baseline", "should_delete"),
    [
        pytest.param({"severity-existing"}, False, id="pre-existing-preserved"),
        pytest.param(set(), True, id="agent-created-deleted"),
    ],
)
def test_teardown_severity_property_uses_seed_baseline(baseline, should_delete):
    deleted: list[str] = []
    bug = SimpleNamespace(id="bug-existing", name="Bug")
    severity = SimpleNamespace(id="severity-existing", display_name="Severity")
    plane = SimpleNamespace(
        workspace_work_item_types=SimpleNamespace(
            list=lambda **kw: [bug],
            properties=SimpleNamespace(list=lambda **kw: [severity.id]),
        ),
        workspace_work_item_properties=SimpleNamespace(
            list=lambda **kw: [severity],
            delete=lambda **kw: deleted.append(str(kw["property_id"])),
        ),
        work_item_properties=SimpleNamespace(list=lambda **kw: [severity], delete=lambda **kw: None),
        customers=SimpleNamespace(
            list=lambda **kw: _Page([]),
            properties=SimpleNamespace(list=lambda **kw: _Page([])),
        ),
        releases=SimpleNamespace(tags=SimpleNamespace(list=lambda **kw: _Page([]))),
        projects=SimpleNamespace(delete=lambda **kw: None),
    )
    context = {
        "task_id": "S1",
        "workspace_slug": "ws",
        "project_id": "project-1",
        "bug_type": {"id": bug.id, "name": bug.name},
        "bug_type_workspace_level": True,
        "workspace_objects": [],
        "workspace_baseline": {
            "customers": set(),
            "release_tags": set(),
            "customer_properties": set(),
            "work_item_types": {bug.id},
            "work_item_properties": baseline,
        },
    }

    seed_mod.teardown(plane, context)

    assert (severity.id in deleted) is should_delete


@pytest.mark.parametrize(
    ("baseline", "should_delete"),
    [
        pytest.param({"incident-existing"}, False, id="pre-existing-preserved"),
        pytest.param(set(), True, id="agent-created-deleted"),
    ],
)
def test_teardown_workspace_incident_uses_seed_baseline(baseline, should_delete):
    deleted: list[str] = []
    incident = SimpleNamespace(id="incident-existing", name="Incident")
    plane = SimpleNamespace(
        workspaces=SimpleNamespace(
            get_features=lambda **kw: SimpleNamespace(model_dump=lambda: {"is_work_item_types_enabled": True})
        ),
        workspace_work_item_types=SimpleNamespace(
            list=lambda **kw: [incident],
            delete=lambda **kw: deleted.append(str(kw["type_id"])),
        ),
        customers=SimpleNamespace(
            list=lambda **kw: _Page([]),
            properties=SimpleNamespace(list=lambda **kw: _Page([])),
        ),
        releases=SimpleNamespace(tags=SimpleNamespace(list=lambda **kw: _Page([]))),
        projects=SimpleNamespace(delete=lambda **kw: None),
    )
    context = {
        "task_id": "S3",
        "workspace_slug": "ws",
        "project_id": "project-1",
        "workspace_objects": [],
        "workspace_baseline": {
            "customers": set(),
            "release_tags": set(),
            "customer_properties": set(),
            "work_item_types": baseline,
            "work_item_properties": None,
        },
    }

    seed_mod.teardown(plane, context)

    assert (incident.id in deleted) is should_delete


def test_preclean_behaviours():
    from evals.seed import check_workspace_fixture_collisions
    from evals.tasks.skip import TaskSkipped

    cases = (
        {
            "name": "release tag collision",
            "customers": [],
            "tags": [SimpleNamespace(id="tag-old", version=L3_TAG_VERSION)],
            "properties": [],
            "category": "release_tags",
            "fixture_name": L3_TAG_VERSION,
            "checked_categories": {"release_tags"},
            "workspace_types": [],
            "workspace_properties": [],
        },
        {
            "name": "customer property collision",
            "customers": [],
            "tags": [],
            "properties": [SimpleNamespace(id="prop-old", display_name=L4_PROP_DISPLAY, name="x")],
            "category": "customer_properties",
            "fixture_name": L4_PROP_DISPLAY,
            "checked_categories": {"customer_properties"},
            "workspace_types": [],
            "workspace_properties": [],
        },
        {
            "name": "customer collision",
            "customers": [SimpleNamespace(id="customer-old", name="Acme")],
            "tags": [],
            "properties": [],
            "category": "customers",
            "fixture_name": "Acme Corp",
            "checked_categories": {"customers"},
            "workspace_types": [],
            "workspace_properties": [],
        },
        {
            "name": "Bug Severity collision",
            "customers": [],
            "tags": [],
            "properties": [],
            "workspace_types": [SimpleNamespace(id="type-bug", name="Bug")],
            "workspace_properties": [SimpleNamespace(id="severity-old", display_name="Severity")],
            "category": "work_item_properties",
            "fixture_name": "Severity",
            "checked_categories": {"work_item_properties"},
        },
        {
            "name": "Incident collision",
            "customers": [],
            "tags": [],
            "properties": [],
            "workspace_types": [SimpleNamespace(id="incident-old", name="Incident")],
            "workspace_properties": [],
            "category": "work_item_types",
            "fixture_name": "Incident",
            "checked_categories": {"work_item_types"},
        },
        {
            "name": "clean workspace",
            "customers": [SimpleNamespace(id="customer-other", name="Other Corp")],
            "tags": [SimpleNamespace(id="tag-other", version="v2")],
            "properties": [SimpleNamespace(id="prop-other", display_name="Region", name="region")],
            "category": None,
            "fixture_name": None,
            "checked_categories": {"customers", "release_tags", "customer_properties"},
            "workspace_types": [],
            "workspace_properties": [],
        },
        {
            "name": "release tag irrelevant to checked category",
            "customers": [],
            "tags": [SimpleNamespace(id="tag-unrelated", version=L3_TAG_VERSION)],
            "properties": [],
            "category": None,
            "fixture_name": None,
            "checked_categories": {"customers"},
            "workspace_types": [SimpleNamespace(id="incident-unrelated", name="Incident")],
            "workspace_properties": [],
        },
    )

    for case in cases:
        with pytest.MonkeyPatch.context():
            deleted: list[tuple[str, str]] = []
            customers = case["customers"]
            tags = case["tags"]
            properties = case["properties"]
            workspace_types = case["workspace_types"]
            workspace_properties = case["workspace_properties"]
            plane = SimpleNamespace(
                releases=SimpleNamespace(
                    tags=SimpleNamespace(
                        list=lambda tags=tags, **kw: _Page(tags),
                        delete=lambda deleted=deleted, **kw: deleted.append(("tag", kw["tag_id"])),
                    )
                ),
                customers=SimpleNamespace(
                    list=lambda customers=customers, **kw: _Page(customers),
                    delete=lambda deleted=deleted, **kw: deleted.append(("customer", kw["customer_id"])),
                    properties=SimpleNamespace(
                        list=lambda properties=properties, **kw: _Page(properties),
                        delete=lambda deleted=deleted, **kw: deleted.append(("property", kw["property_id"])),
                    ),
                ),
                workspaces=SimpleNamespace(
                    get_features=lambda **kw: SimpleNamespace(model_dump=lambda: {"is_work_item_types_enabled": True})
                ),
                workspace_work_item_types=SimpleNamespace(
                    list=lambda workspace_types=workspace_types, **kw: workspace_types,
                    properties=SimpleNamespace(
                        list=lambda workspace_properties=workspace_properties, **kw: (
                            [row.id for row in workspace_properties] if kw.get("type_id") == "type-bug" else []
                        )
                    ),
                ),
                workspace_work_item_properties=SimpleNamespace(
                    list=lambda workspace_properties=workspace_properties, **kw: workspace_properties
                ),
            )

            if case["category"] is None:
                check_workspace_fixture_collisions(plane, "ws", case["checked_categories"])
            else:
                expected = f"env:fixture-collision:{case['category']}:{case['fixture_name']}"
                with pytest.raises(TaskSkipped) as caught:
                    check_workspace_fixture_collisions(plane, "ws", case["checked_categories"])
                assert caught.value.reason.startswith(expected), case["name"]
                assert case["fixture_name"] in caught.value.reason
                assert "python -m evals.cleanup --sentinels --yes" in caught.value.reason
            assert deleted == [], case["name"]


def test_collision_category_coverage_matches_task_prompts():
    from evals.seed import (
        CUSTOMER_NAME,
        EVALUATION_CUSTOMER_PROPERTY_NAME,
        EVALUATION_RELEASE_TAG_VERSION,
        INCIDENT_TYPE_NAME,
        SEVERITY_PROPERTY_NAME,
        collision_categories,
    )
    from evals.tasks.catalog import TASKS

    prompt_categories = (
        (CUSTOMER_NAME, "customers"),
        (EVALUATION_RELEASE_TAG_VERSION, "release_tags"),
        (EVALUATION_CUSTOMER_PROPERTY_NAME, "customer_properties"),
        (SEVERITY_PROPERTY_NAME, "work_item_properties"),
        (INCIDENT_TYPE_NAME, "work_item_types"),
    )
    for task in TASKS:
        task_id = str(task["id"])
        categories = collision_categories(set(task.get("needs") or set()), task_id)
        prompt = str(task.get("prompt") or "")
        for fixture_name, category in prompt_categories:
            if fixture_name in prompt:
                assert category in categories, f"task {task_id} prompt references {fixture_name!r}; missing {category}"


@pytest.mark.parametrize(
    ("context", "read_result", "expected_error", "match"),
    [
        pytest.param(
            {"project_id": "p1", "items": {}},
            [],
            RuntimeError,
            "fixture error: missing work_item_id",
            id="missing-work-item-is-fixture-error",
        ),
        pytest.param(
            {"project_id": None, "items": {R5_TITLE: "wi-r5"}},
            [],
            RuntimeError,
            "fixture error: missing project_id",
            id="missing-project-is-fixture-error",
        ),
        pytest.param(
            {"project_id": "p1", "items": {R5_TITLE: "wi-r5"}},
            ConnectionError("activity backend unavailable"),
            ConnectionError,
            "activity backend unavailable",
            id="read-failure-propagates-as-infrastructure",
        ),
        pytest.param(
            {"project_id": "p1", "items": {R5_TITLE: "wi-r5"}},
            [],
            TaskSkipped,
            "^env:no-activity-worker$",
            id="successful-empty-read-is-capability-skip",
        ),
        pytest.param(
            {"project_id": "p1", "items": {R5_TITLE: "wi-r5"}},
            [SimpleNamespace(id="a1")],
            None,
            None,
            id="successful-nonempty-read-proceeds",
        ),
        pytest.param(
            # Revision 8: a non-empty read is sufficient, and evidence is the activity count.
            # This case used to require the seeded comment phrase in the readback and expect a
            # fixture error without it — a contract Plane's activity API can never satisfy,
            # because it returns the creation row and never the comment text.
            {
                "task_id": "L2",
                "project_id": "p1",
                "items": {R5_TITLE: "wi-r5"},
                "l2_comment_phrases": ["hidden seeded comment"],
            },
            [SimpleNamespace(id="a1", comment="unrelated activity")],
            None,
            None,
            id="nonempty-read-without-comment-text-binds-count-evidence",
        ),
    ],
)
def test_l2_activity_gate_outcomes(context, read_result, expected_error, match):
    from evals.seed import _gate_activity_worker

    def list_activities(**kwargs):
        if isinstance(read_result, BaseException):
            raise read_result
        return SimpleNamespace(results=read_result)

    plane = SimpleNamespace(work_items=SimpleNamespace(activities=SimpleNamespace(list=list_activities)))
    if expected_error is None:
        _gate_activity_worker(plane, "ws", context)
        if context.get("task_id") == "L2":
            aggregates = context["evidence_aggregates"][TARGET_ENTITY_EVIDENCE]
            assert aggregates == ({"kind": "total_count", "value": 1},)
            assert context["evidence_targets"][TARGET_ENTITY_EVIDENCE] == ("wi-r5",)
    else:
        with pytest.raises(expected_error, match=match):
            _gate_activity_worker(plane, "ws", context)


def _create_project_retries_409_then_succeeds(monkeypatch):
    attempts: list[str] = []

    class FakeProjects:
        def create(self, *, workspace_slug, data):
            ident = data.identifier
            attempts.append(ident)
            if len(attempts) < 3:
                raise HttpError("Project identifier already taken", 409)
            return MagicMock(id="proj-ok", identifier=ident)

    plane = MagicMock()
    plane.projects = FakeProjects()

    # Force deterministic retries after first collision.
    suffixes = iter(["AAAAAAAA", "BBBBBBBB"])
    monkeypatch.setattr(seed_mod.secrets, "token_hex", lambda n: next(suffixes))

    project = create_project_with_identifier_retry(
        plane,
        "ws",
        name="EVAL abcd",
        identifier_prefix="EV",
        initial_suffix="DEADBEEF",
    )
    assert project.id == "proj-ok"
    assert attempts[0] == "EVDEADBEEF"
    assert len(attempts) == 3
    assert attempts[1] != attempts[0]
    assert attempts[2] != attempts[1]
    assert attempts[1] == "EVAAAAAAAA"
    assert attempts[2] == "EVBBBBBBBB"


def _create_project_raises_after_max_409s(monkeypatch):
    attempts: list[str] = []

    class Always409:
        def create(self, *, workspace_slug, data):
            attempts.append(data.identifier)
            raise HttpError("identifier already taken", 409)

    plane = MagicMock()
    plane.projects = Always409()
    suffixes = iter(
        [
            "11111111",
            "22222222",
            "33333333",
            "44444444",
            "55555555",
            "66666666",
            "77777777",
            "should-not-use",
        ]
    )
    monkeypatch.setattr(seed_mod.secrets, "token_hex", lambda n: next(suffixes))

    with pytest.raises(HttpError) as ei:
        create_project_with_identifier_retry(
            plane,
            "ws",
            name="EVAL x",
            identifier_prefix="EV",
            initial_suffix="00000000",
        )
    assert ei.value.status_code == 409
    assert len(attempts) == 8
    assert attempts[0] == "EV00000000"
    assert attempts[1] != attempts[0]
    assert attempts[1] == "EV11111111"
    assert attempts[-1] == "EV77777777"


def _create_project_non_collision_error_does_not_retry(_monkeypatch):
    class Fail500:
        def create(self, *, workspace_slug, data):
            raise HttpError("server error", 500)

    plane = MagicMock()
    plane.projects = Fail500()
    with pytest.raises(HttpError) as ei:
        create_project_with_identifier_retry(
            plane,
            "ws",
            name="EVAL x",
            identifier_prefix="EV",
            initial_suffix="00000000",
        )
    assert ei.value.status_code == 500


def _identifier_stays_within_plane_limit(_monkeypatch):
    plane = SimpleNamespace(
        projects=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(id="project", identifier=kwargs["data"].identifier)
        )
    )
    project = create_project_with_identifier_retry(
        plane,
        "ws",
        name="EVAL x",
        identifier_prefix="EV",
        initial_suffix="12345678",
    )
    assert project.identifier == "EV12345678"
    assert len(project.identifier) <= seed_mod.PLANE_PROJECT_IDENTIFIER_MAX_LENGTH

    with pytest.raises(ValueError, match="12-character limit"):
        create_project_with_identifier_retry(
            plane,
            "ws",
            name="EVAL x",
            identifier_prefix="TOO-LONG",
            initial_suffix="12345678",
        )


@pytest.mark.parametrize(
    "case",
    case_params(
        _create_project_retries_409_then_succeeds,
        _create_project_raises_after_max_409s,
        _create_project_non_collision_error_does_not_retry,
        _identifier_stays_within_plane_limit,
    ),
)
def test_create_behaviours(case, monkeypatch):
    case(monkeypatch)


def test_identifier_collision_requires_status_and_language():
    assert is_identifier_collision(HttpError("identifier already taken", 409)) is True
    assert is_identifier_collision(HttpError("project exists", 400)) is True
    # Validation-shaped: mentions identifier but not collision language → no retry
    assert is_identifier_collision(HttpError("identifier is required", 400)) is False
    assert is_identifier_collision(HttpError("identifier already taken", 500)) is False


def _cleanup_dry_run_never_calls_delete(monkeypatch, capsys, _yes):
    projects = [
        SimpleNamespace(id="p1", name="EVAL deadbeef", identifier="EVDEAD"),
        SimpleNamespace(id="p2", name="EVAL cafe", identifier="EVCAFE"),
        SimpleNamespace(id="p3", name="Production", identifier="PROD"),
    ]
    delete_calls: list[Any] = []

    class FakeProjects:
        def list(self, workspace_slug=None, params=None):
            return SimpleNamespace(results=projects, next_page_results=False, next_cursor="100:0:0")

        def delete(self, **kwargs):
            delete_calls.append(kwargs)

    plane = MagicMock()
    plane.projects = FakeProjects()
    monkeypatch.setattr("evals.seed.make_plane_client", lambda: (plane, "test-ws"))

    rc = cleanup_mod.main([])  # dry-run
    assert rc == 0
    assert delete_calls == []
    out = capsys.readouterr().out
    assert "EVAL deadbeef" in out
    assert "dry-run" in out
    assert "Production" not in out  # prefix filter


def _cleanup_yes_deletes(monkeypatch, capsys, _yes):
    projects = [SimpleNamespace(id="p1", name="EVAL x", identifier="EVX")]
    delete_calls: list[Any] = []

    class FakeProjects:
        def list(self, workspace_slug=None, params=None):
            return SimpleNamespace(results=projects, next_page_results=False, next_cursor="100:0:0")

        def delete(self, **kwargs):
            delete_calls.append(kwargs)

    plane = MagicMock()
    plane.projects = FakeProjects()
    monkeypatch.setattr("evals.seed.make_plane_client", lambda: (plane, "test-ws"))
    rc = cleanup_mod.main(["--yes"])
    assert rc == 0
    assert len(delete_calls) == 1
    assert delete_calls[0]["project_id"] == "p1"


def _cleanup_sentinel_mode(monkeypatch, capsys, yes):
    delete_calls: list[tuple[str, str]] = []
    plane = SimpleNamespace(
        customers=SimpleNamespace(
            list=lambda **kw: _Page(
                [
                    SimpleNamespace(id="customer-eval", name="Acme Corp"),
                    SimpleNamespace(id="customer-short", name="Acme"),
                    SimpleNamespace(id="customer-other", name="Other Corp"),
                ]
            ),
            delete=lambda **kw: delete_calls.append(("customer", kw["customer_id"])),
            properties=SimpleNamespace(
                list=lambda **kw: _Page(
                    [
                        SimpleNamespace(id="property-eval", display_name="Eval Industry", name="eval-industry"),
                        SimpleNamespace(id="property-other", display_name="Region", name="region"),
                    ]
                ),
                delete=lambda **kw: delete_calls.append(("customer_property", kw["property_id"])),
            ),
        ),
        releases=SimpleNamespace(
            tags=SimpleNamespace(
                list=lambda **kw: _Page(
                    [
                        SimpleNamespace(id="tag-eval", version="eval-rc1"),
                        SimpleNamespace(id="tag-other", version="v2"),
                    ]
                ),
                delete=lambda **kw: delete_calls.append(("release_tag", kw["tag_id"])),
            )
        ),
        workspace_work_item_types=SimpleNamespace(
            list=lambda **kw: [
                SimpleNamespace(id="type-bug", name="Bug"),
                SimpleNamespace(id="type-incident", name="Incident"),
                SimpleNamespace(id="type-epic", name="Epic"),
            ],
            properties=SimpleNamespace(list=lambda **kw: ["severity-eval"] if kw.get("type_id") == "type-bug" else []),
            delete=lambda **kw: delete_calls.append(("work_item_type", kw["type_id"])),
        ),
        workspace_work_item_properties=SimpleNamespace(
            list=lambda **kw: [
                SimpleNamespace(id="severity-eval", display_name="Severity"),
                SimpleNamespace(id="property-region", display_name="Region"),
            ],
            delete=lambda **kw: delete_calls.append(("work_item_property", kw["property_id"])),
        ),
    )
    monkeypatch.setattr("evals.seed.make_plane_client", lambda: (plane, "test-ws"))

    args = ["--sentinels", "--yes"] if yes else ["--sentinels"]
    assert cleanup_mod.main(args) == 0
    output = capsys.readouterr().out
    for fixture_name in ("Acme Corp", "eval-rc1", "Eval Industry", "Incident", "Severity"):
        assert fixture_name in output
    assert "Other Corp" not in output
    assert "Region" not in output
    if yes:
        assert set(delete_calls) == {
            ("customer", "customer-eval"),
            ("customer", "customer-short"),
            ("release_tag", "tag-eval"),
            ("customer_property", "property-eval"),
            ("work_item_type", "type-incident"),
            ("work_item_property", "severity-eval"),
        }
        assert "deleted sentinel" in output
        assert "would delete sentinel" not in output
    else:
        assert delete_calls == []
        assert output.count("would delete sentinel") == 6
        assert "dry-run" in output


@pytest.mark.parametrize(
    ("case", "yes"),
    [
        pytest.param(_cleanup_dry_run_never_calls_delete, None, id="project-dry-run"),
        pytest.param(_cleanup_yes_deletes, None, id="project-delete"),
        pytest.param(_cleanup_sentinel_mode, False, id="sentinel-dry-run"),
        pytest.param(_cleanup_sentinel_mode, True, id="sentinel-delete"),
    ],
)
def test_cleanup_behaviours(case, yes, monkeypatch, capsys):
    case(monkeypatch, capsys, yes)


def _list_projects_with_prefix_filters():
    projects = [
        SimpleNamespace(id="1", name="EVAL a"),
        SimpleNamespace(id="2", name="Other"),
        SimpleNamespace(id="3", name="EVAL b"),
        SimpleNamespace(id="4", name="EVALUATION"),  # must NOT match "EVAL "
    ]
    calls: list[Any] = []

    class FakeProjects:
        def list(self, workspace_slug=None, params=None):
            calls.append({"workspace_slug": workspace_slug, "params": params})
            assert params is not None
            assert params.per_page == 100
            # SDK always populates next_cursor even on last page.
            return SimpleNamespace(
                results=projects,
                next_page_results=False,
                next_cursor="100:0:0",
            )

    plane = MagicMock()
    plane.projects = FakeProjects()
    got = cleanup_mod.list_projects_with_prefix(plane, "ws", "EVAL ")
    assert [p.id for p in got] == ["1", "3"]
    assert len(calls) == 1  # one page only — no infinite loop on next_cursor
    assert calls[0]["params"].cursor is None


def _list_projects_two_page_pagination():
    page1 = [SimpleNamespace(id="1", name="EVAL one")]
    page2 = [SimpleNamespace(id="2", name="EVAL two")]
    seen_cursors: list[Any] = []

    class FakeProjects:
        def list(self, workspace_slug=None, params=None):
            seen_cursors.append(getattr(params, "cursor", None))
            if params.cursor is None:
                return SimpleNamespace(
                    results=page1,
                    next_page_results=True,
                    next_cursor="100:0:0",
                )
            assert params.cursor == "100:0:0"
            return SimpleNamespace(
                results=page2,
                next_page_results=False,
                next_cursor="200:0:0",
            )

    plane = MagicMock()
    plane.projects = FakeProjects()
    got = cleanup_mod.list_projects_with_prefix(plane, "ws", "EVAL ")
    assert [p.id for p in got] == ["1", "2"]
    assert seen_cursors == [None, "100:0:0"]


@pytest.mark.parametrize(
    "case",
    case_params(_list_projects_with_prefix_filters, _list_projects_two_page_pagination),
)
def test_list_projects_behaviours(case):
    case()
