"""Offline eval tests for seed."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from plane.errors.errors import HttpError

from evals import cleanup as cleanup_mod
from evals import seed as seed_mod
from evals.seed import (
    create_project_with_identifier_retry,
    is_identifier_collision,
    seed_plan,
)
from evals.tasks.debias import (
    L3_TAG_VERSION,
    L4_PROP_DISPLAY,
)


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
        self.workspace_work_item_types = SimpleNamespace(delete=lambda **kw: None)
        self.workspace_work_item_properties = SimpleNamespace(delete=lambda **kw: None)


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


def test_teardown_deletes_release_tag_and_customer_property():
    from evals.seed import teardown

    plane = _TeardownPlane()
    ctx = {
        "workspace_slug": "ws",
        "project_id": "p1",
        "project_name": "EVAL x",
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


def test_preclean_removes_stale_tag_and_property():
    from evals.seed import _preclean_ws3_workspace_artifacts

    deleted: list[tuple[str, str]] = []

    class Plane:
        releases = SimpleNamespace(
            tags=SimpleNamespace(
                list=lambda **kw: _Page([SimpleNamespace(id="t-old", version=L3_TAG_VERSION)]),
                delete=lambda **kw: deleted.append(("tag", kw["tag_id"])),
            )
        )
        customers = SimpleNamespace(
            properties=SimpleNamespace(
                list=lambda **kw: _Page([SimpleNamespace(id="p-old", display_name=L4_PROP_DISPLAY, name="x")]),
                delete=lambda **kw: deleted.append(("prop", kw["property_id"])),
            )
        )

    _preclean_ws3_workspace_artifacts(Plane(), "ws")
    assert ("tag", "t-old") in deleted
    assert ("prop", "p-old") in deleted


def test_preclean_delete_failure_raises_for_infra_seed():
    """Found artifact that cannot be deleted must raise (harness → infra_seed)."""
    from evals.seed import _preclean_ws3_workspace_artifacts

    class Plane:
        releases = SimpleNamespace(
            tags=SimpleNamespace(
                list=lambda **kw: _Page([SimpleNamespace(id="t-stuck", version=L3_TAG_VERSION)]),
                delete=lambda **kw: (_ for _ in ()).throw(RuntimeError("403 forbidden")),
            )
        )
        customers = SimpleNamespace(
            properties=SimpleNamespace(
                list=lambda **kw: _Page([]),
                delete=lambda **kw: None,
            )
        )

    with pytest.raises(RuntimeError, match="preclean|failed to delete|eval-rc1|release tag"):
        _preclean_ws3_workspace_artifacts(Plane(), "ws")


def test_preclean_empty_list_is_silent():
    from evals.seed import _preclean_ws3_workspace_artifacts

    class Plane:
        releases = SimpleNamespace(tags=SimpleNamespace(list=lambda **kw: _Page([]), delete=lambda **kw: None))
        customers = SimpleNamespace(properties=SimpleNamespace(list=lambda **kw: _Page([]), delete=lambda **kw: None))

    _preclean_ws3_workspace_artifacts(Plane(), "ws")  # no raise


def test_l2_activity_gate_raises_when_empty():
    """Empty activities list after comments → TaskSkipped env:no-activity-worker."""
    from types import SimpleNamespace

    from evals.seed import R5_TITLE, _gate_activity_worker
    from evals.tasks.skip import TaskSkipped

    class Plane:
        work_items = SimpleNamespace(activities=SimpleNamespace(list=lambda **kw: SimpleNamespace(results=[])))

    ctx = {"project_id": "p1", "items": {R5_TITLE: "wi-r5"}}
    with pytest.raises(TaskSkipped, match="env:no-activity-worker"):
        _gate_activity_worker(Plane(), "ws", ctx)


def test_l2_activity_gate_proceeds_when_nonempty():
    from types import SimpleNamespace

    from evals.seed import R5_TITLE, _gate_activity_worker

    class Plane:
        work_items = SimpleNamespace(
            activities=SimpleNamespace(list=lambda **kw: SimpleNamespace(results=[SimpleNamespace(id="a1")]))
        )

    ctx = {"project_id": "p1", "items": {R5_TITLE: "wi-r5"}}
    _gate_activity_worker(Plane(), "ws", ctx)  # no raise


def test_create_project_retries_409_then_succeeds(monkeypatch):
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
    suffixes = iter(["AAAA", "BBBB"])
    monkeypatch.setattr(seed_mod.secrets, "token_hex", lambda n: next(suffixes))

    project = create_project_with_identifier_retry(
        plane,
        "ws",
        name="EVAL abcd",
        identifier_prefix="EV",
        initial_suffix="DEAD",
    )
    assert project.id == "proj-ok"
    assert attempts[0] == "EVDEAD"
    assert len(attempts) == 3
    assert attempts[1] != attempts[0]
    assert attempts[2] != attempts[1]
    assert attempts[1] == "EVAAAA"
    assert attempts[2] == "EVBBBB"


def test_create_project_raises_after_max_409s(monkeypatch):
    attempts: list[str] = []

    class Always409:
        def create(self, *, workspace_slug, data):
            attempts.append(data.identifier)
            raise HttpError("identifier already taken", 409)

    plane = MagicMock()
    plane.projects = Always409()
    suffixes = iter(["1111", "2222", "3333", "should-not-use"])
    monkeypatch.setattr(seed_mod.secrets, "token_hex", lambda n: next(suffixes))

    with pytest.raises(HttpError) as ei:
        create_project_with_identifier_retry(
            plane,
            "ws",
            name="EVAL x",
            identifier_prefix="EV",
            initial_suffix="0000",
        )
    assert ei.value.status_code == 409
    assert len(attempts) == 3
    assert attempts[0] == "EV0000"
    assert attempts[1] != attempts[0]
    assert attempts[1] == "EV1111"
    assert attempts[2] == "EV2222"


def test_create_project_non_collision_error_does_not_retry():
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
            initial_suffix="0000",
        )
    assert ei.value.status_code == 500


def test_identifier_collision_requires_status_and_language():
    assert is_identifier_collision(HttpError("identifier already taken", 409)) is True
    assert is_identifier_collision(HttpError("project exists", 400)) is True
    # Validation-shaped: mentions identifier but not collision language → no retry
    assert is_identifier_collision(HttpError("identifier is required", 400)) is False
    assert is_identifier_collision(HttpError("identifier already taken", 500)) is False


def test_cleanup_dry_run_never_calls_delete(monkeypatch, capsys):
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


def test_cleanup_yes_deletes(monkeypatch, capsys):
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


def test_list_projects_with_prefix_filters():
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


def test_list_projects_two_page_pagination():
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
