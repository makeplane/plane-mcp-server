"""Focused seed/readback tests for per-row read-task truth."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from evals.evidence import TARGET_ENTITY_EVIDENCE
from evals.seed.cycles import seed_cycles
from evals.seed.states import seed_r7_state_oracle
from evals.seed.work_items import require_activities, seed_work_items


class _Page:
    def __init__(self, results: list[Any]):
        self.results = results
        self.next_page_results = False
        self.next_cursor = None


class _ReadSeedPlane:
    def __init__(self):
        self._states = [
            SimpleNamespace(id="state-started", name="In Progress", group="started", default=False),
            SimpleNamespace(id="state-done", name="Done", group="completed", default=False),
        ]
        self._items: dict[str, SimpleNamespace] = {}
        self._comments: dict[str, list[SimpleNamespace]] = {}
        self._attachments: dict[str, list[SimpleNamespace]] = {}
        self._cycles: dict[str, SimpleNamespace] = {}
        self._cycle_items: dict[str, list[str]] = {}
        self.states = SimpleNamespace(
            list=lambda **kwargs: _Page(list(self._states)),
            create=self._create_state,
            retrieve=self._retrieve_state,
        )
        self.users = SimpleNamespace(get_me=lambda: SimpleNamespace(id="user-me"))
        self.work_items = SimpleNamespace(
            create=self._create_item,
            update=self._update_item,
            retrieve=self._retrieve_item,
            list=lambda **kwargs: _Page(list(self._items.values())),
            comments=SimpleNamespace(create=self._create_comment, list=self._list_comments),
            activities=SimpleNamespace(list=self._list_activities),
            attachments=SimpleNamespace(upload_from_bytes=self._upload_attachment, list=self._list_attachments),
        )
        self.cycles = SimpleNamespace(
            create=self._create_cycle,
            update=self._update_cycle,
            retrieve=self._retrieve_cycle,
            add_work_items=self._add_cycle_items,
            list_work_items=self._list_cycle_items,
        )

    def _create_state(self, *, data, **kwargs):
        state = SimpleNamespace(
            id=f"state-{len(self._states) + 1}",
            name=data.name,
            group=data.group,
            default=False,
        )
        self._states.append(state)
        return state

    def _retrieve_state(self, *, state_id, **kwargs):
        return next(state for state in self._states if state.id == state_id)

    def _create_item(self, *, data, **kwargs):
        work_item_id = f"item-{len(self._items) + 1}"
        item = SimpleNamespace(
            id=work_item_id,
            sequence_id=len(self._items) + 1,
            name=data.name,
            priority=data.priority,
            state=data.state or "state-started",
            target_date=data.target_date,
            assignees=list(data.assignees or []),
        )
        self._items[work_item_id] = item
        return item

    def _update_item(self, *, work_item_id, data, **kwargs):
        item = self._items[work_item_id]
        for key, value in data.model_dump(exclude_none=True).items():
            setattr(item, key, value)
        return item

    def _retrieve_item(self, *, work_item_id, **kwargs):
        return self._items[work_item_id]

    def _create_comment(self, *, work_item_id, data, **kwargs):
        rows = self._comments.setdefault(work_item_id, [])
        comment = SimpleNamespace(
            id=f"comment-{len(rows) + 1}",
            comment_html=data.comment_html,
            comment_stripped=None,
        )
        rows.append(comment)
        return comment

    def _list_comments(self, *, work_item_id, **kwargs):
        return _Page(list(self._comments.get(work_item_id, [])))

    def _list_activities(self, *, work_item_id, **kwargs):
        rows = [
            SimpleNamespace(id=f"activity-{row.id}", comment=row.comment_html)
            for row in self._comments.get(work_item_id, [])
        ]
        return _Page(rows)

    def _upload_attachment(self, *, work_item_id, name, **kwargs):
        rows = self._attachments.setdefault(work_item_id, [])
        attachment = SimpleNamespace(id=f"attachment-{len(rows) + 1}", name=name)
        rows.append(attachment)
        return attachment

    def _list_attachments(self, *, work_item_id, **kwargs):
        return _Page(list(self._attachments.get(work_item_id, [])))

    def _create_cycle(self, *, data, **kwargs):
        cycle_id = f"cycle-{len(self._cycles) + 1}"
        cycle = SimpleNamespace(id=cycle_id, name=data.name, end_date=data.end_date)
        self._cycles[cycle_id] = cycle
        self._cycle_items[cycle_id] = []
        return cycle

    def _update_cycle(self, *, cycle_id, data, **kwargs):
        cycle = self._cycles[cycle_id]
        cycle.end_date = data.end_date
        return cycle

    def _retrieve_cycle(self, *, cycle_id, **kwargs):
        return self._cycles[cycle_id]

    def _add_cycle_items(self, *, cycle_id, issue_ids, **kwargs):
        self._cycle_items[cycle_id].extend(str(value) for value in issue_ids)

    def _list_cycle_items(self, *, cycle_id, **kwargs):
        return _Page([SimpleNamespace(work_item_id=value) for value in self._cycle_items[cycle_id]])


def _context(task_id: str, run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "run8": run_id[:8],
        "task_id": task_id,
        "project_id": "project-1",
        "project_identifier": "EVTEST",
        "items": {},
        "item_ids": [],
        "item_identifiers": {},
        "fixture_item_ids": {},
        "fixture_item_titles": {},
        "randomized_truth": {},
    }


@pytest.mark.parametrize(
    ("task_id", "oracle_key", "truth_key"),
    [
        ("R1", "r1_state_name", "R1.state"),
        ("R2", "r2_urgent_open_count", "R2.urgent_open_count"),
        ("R3", "r3_due_titles", "R3.due_templates"),
        ("R5", "r5_comment_phrases", "R5.comments"),
        ("I2", "i2_state_name", "I2.state"),
        ("L2", "l2_activity_count", "L2.activity_count"),
        ("L5", "l5_attachment_count", "L5.attachment_count"),
    ],
)
def test_work_item_read_truth_is_randomized_and_api_confirmed(task_id, oracle_key, truth_key):
    plane = _ReadSeedPlane()
    ctx = _context(task_id, f"{task_id.lower():0<8}0123456789abcdef")
    seed_work_items(plane, "ws", ctx)
    if task_id == "L2":
        require_activities(plane, "ws", ctx)

    assert ctx[oracle_key] not in (None, "", [])
    assert "confirmed" in ctx["randomized_truth"][truth_key]
    assert ctx["evidence_sentinels"][TARGET_ENTITY_EVIDENCE]
    assert ctx["evidence_targets"][TARGET_ENTITY_EVIDENCE]


def test_r2_randomized_counts_differ_between_rows_after_api_readback():
    contexts = []
    for run_id in ("00000000aaaaaaaa", "11111111bbbbbbbb"):
        plane = _ReadSeedPlane()
        ctx = _context("R2", run_id)
        seed_work_items(plane, "ws", ctx)
        contexts.append(ctx)

    counts = [ctx["r2_urgent_open_count"] for ctx in contexts]
    assert counts == [6, 3]
    for ctx in contexts:
        truth = ctx["randomized_truth"]["R2.urgent_open_count"]
        assert truth["confirmed"] == ctx["r2_urgent_open_count"]


def test_r4_cycle_inventory_is_randomized_and_api_confirmed():
    plane = _ReadSeedPlane()
    ctx = _context("R4", "44444444aaaaaaaa")
    seed_work_items(plane, "ws", ctx)
    seed_cycles(plane, "ws", ctx)

    truth = ctx["randomized_truth"]["R4.cycle_inventory"]
    assert ctx["r4_cycle_name"].startswith("Sprint ")
    assert ctx["r4_cycle_name"] != "Sprint 13"
    assert ctx["r4_active_titles"]
    assert ctx["r4_overdue_titles"]
    assert truth["confirmed"] == {
        "cycle": ctx["r4_cycle_name"],
        "active_titles": ctx["r4_active_titles"],
        "overdue_titles": ctx["r4_overdue_titles"],
    }
    assert ctx["evidence_sentinels"][TARGET_ENTITY_EVIDENCE]
    assert ctx["evidence_targets"][TARGET_ENTITY_EVIDENCE]


def test_r7_state_truth_is_randomized_api_confirmed_and_evidence_bearing():
    contexts: list[dict[str, Any]] = []
    for run_id in ("77777777aaaaaaaa", "88888888bbbbbbbb"):
        plane = _ReadSeedPlane()
        ctx = _context("R7", run_id)
        seed_r7_state_oracle(plane, "ws", ctx)
        contexts.append(ctx)

    assert contexts[0]["r7_state_pairs"] != contexts[1]["r7_state_pairs"]
    for ctx in contexts:
        truth = ctx["randomized_truth"]["R7.states"]
        assert truth["confirmed"] == ctx["r7_state_pairs"]
        assert ctx["evidence_sentinels"][TARGET_ENTITY_EVIDENCE]
        assert ctx["evidence_targets"][TARGET_ENTITY_EVIDENCE]


def test_l1_seed_oracle_is_the_api_confirmed_target_id():
    plane = _ReadSeedPlane()
    ctx = _context("L1", "11111111cccccccc")

    seed_work_items(plane, "ws", ctx)

    assert ctx["l1_expected_summary_ids"] == [ctx["fixture_item_ids"]["Payment webhook drops retries"]]
    assert ctx["evidence_sentinels"][TARGET_ENTITY_EVIDENCE] == tuple(ctx["l1_expected_summary_ids"])
    assert ctx["evidence_targets"][TARGET_ENTITY_EVIDENCE] == (ctx["project_id"],)
