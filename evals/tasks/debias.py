"""ID-in-hand and long-tail de-biasing tasks with their verifiers."""

from __future__ import annotations

from typing import Any

from plane.models.query_params import RetrieveQueryParams

from evals.fixtures import (
    CUSTOMER_NAME,
    CYCLE_CURRENT,
    DEBIAS_CUSTOMER_PROP_DISPLAY,
    DEBIAS_RELEASE_TAG_VERSION,
    R1_TITLE,
    R5_TITLE,
    W2_TITLE,
    W3_TITLE,
    W8_TITLE,
)
from evals.state_oracle import worklog_summary_item_ids
from evals.tasks.answers import (
    answer_with_provenance,
    contract_values,
    get_final_text,
    reports_contract_int,
    reports_contract_value,
    reports_contract_values,
)
from evals.tasks.lookups import collect_paginated, ids
from evals.tasks.verification import raise_verifier_read_error

I1_TITLE = R1_TITLE


I2_TITLE = W2_TITLE


I3_TITLE = "Footer year still says 2024"


I4_TITLE = W3_TITLE


L1_TITLE = W8_TITLE


L2_TITLE = R5_TITLE


L5_TITLE = R1_TITLE


L3_TAG_VERSION = DEBIAS_RELEASE_TAG_VERSION


L4_PROP_DISPLAY = DEBIAS_CUSTOMER_PROP_DISPLAY


L4_PROP_VALUE = "Enterprise"


def _bind_item_uuid(title: str):
    def _bind(ctx: dict[str, Any]) -> dict[str, str]:
        wid = str((ctx.get("items") or {}).get(title) or "")
        return {"work_item_id": wid}

    return _bind


def _bind_item_identifier(title: str):
    def _bind(ctx: dict[str, Any]) -> dict[str, str]:
        ident = str((ctx.get("item_identifiers") or {}).get(title) or "")
        return {"work_item_identifier": ident}

    return _bind


def _bind_i3(ctx: dict[str, Any]) -> dict[str, str]:
    wid = str((ctx.get("items") or {}).get(I3_TITLE) or "")
    cycle_id = str(ctx.get("cycle_current_id") or (ctx.get("cycles") or {}).get(CYCLE_CURRENT) or "")
    return {"work_item_id": wid, "cycle_id": cycle_id}


def _bind_i4(ctx: dict[str, Any]) -> dict[str, str]:
    wid = str((ctx.get("items") or {}).get(I4_TITLE) or "")
    label_id = str((ctx.get("labels") or {}).get("perf") or "")
    return {"work_item_id": wid, "label_id": label_id}


async def verify_i1(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """I1: seeded R1 item priority is high (updated by UUID, not name)."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    wid = (ctx.get("items") or {}).get(I1_TITLE)
    if not wid:
        return False, f"seed item {I1_TITLE!r} missing"
    detail = plane.work_items.retrieve(workspace_slug=workspace_slug, project_id=project_id, work_item_id=wid)
    pr = (detail.priority or "").lower() if detail.priority else ""
    if pr == "high":
        return True, f"work_item {wid} priority=high"
    return False, f"work_item {wid} priority={pr!r} (want high)"


I1_TASK: dict[str, Any] = {
    "id": "I1",
    "author": "post-hoc-debias",
    "tags": {"write", "id_in_hand", "debias"},
    "prompt": ("In project {project}, update work item {work_item_id}: set its priority to high."),
    "prompt_bind": _bind_item_uuid(I1_TITLE),
    "needs": {"items"},
    "verify": verify_i1,
}


async def verify_i2(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """I2: final text reports the API-confirmed seed state with call provenance."""
    name = str(ctx.get("i2_state_name") or "")
    if not name:
        return answer_with_provenance(False, "API-confirmed target state missing from seed ctx", run)
    final_text = get_final_text(run)
    answer_correct = reports_contract_value(final_text, "state", name)
    answer_note = (
        f"final text reports state {name!r} via contract"
        if answer_correct
        else f"state values={contract_values(final_text, 'state')!r}; want [{name!r}]"
    )
    return answer_with_provenance(answer_correct, answer_note, run)


I2_TASK: dict[str, Any] = {
    "id": "I2",
    "author": "post-hoc-debias",
    "tags": {"read", "id_in_hand", "debias"},
    "prompt": (
        "In project {project}, what is the current state of work item "
        "{work_item_identifier}? Return exactly one line: 'state: <exact state name>'."
    ),
    "prompt_bind": _bind_item_identifier(I2_TITLE),
    "needs": {"items"},
    "verify": verify_i2,
}


async def verify_i3(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """I3: work item UUID is on the target cycle UUID."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    wid = str((ctx.get("items") or {}).get(I3_TITLE) or "")
    cycle_id = str(ctx.get("cycle_current_id") or (ctx.get("cycles") or {}).get(CYCLE_CURRENT) or "")
    if not wid or not cycle_id:
        return False, "seed work_item_id/cycle_id missing"
    page = plane.cycles.list_work_items(workspace_slug=workspace_slug, project_id=project_id, cycle_id=cycle_id)
    rows = page.results if hasattr(page, "results") else page
    ids = {str(getattr(r, "id", None) or r) for r in (rows or [])}
    # list may return issue wrappers with issue/id fields
    for r in rows or []:
        for attr in ("id", "issue", "work_item_id"):
            v = getattr(r, attr, None)
            if v is not None:
                ids.add(str(v if not hasattr(v, "id") else v.id))
    if wid in ids:
        return True, f"item {wid} on cycle {cycle_id}"
    return False, f"item {wid} not on cycle {cycle_id}; have {sorted(ids)[:12]}"


I3_TASK: dict[str, Any] = {
    "id": "I3",
    "author": "post-hoc-debias",
    "tags": {"write", "id_in_hand", "debias"},
    "prompt": ("In project {project}, add work item {work_item_id} to cycle {cycle_id}."),
    "prompt_bind": _bind_i3,
    "needs": {"items", "cycles"},
    "verify": verify_i3,
}


async def verify_i4(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """I4: work item has the seeded perf label id attached."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    wid = (ctx.get("items") or {}).get(I4_TITLE)
    label_id = (ctx.get("labels") or {}).get("perf")
    if not wid or not label_id:
        return False, "seed work_item_id/label_id missing"
    detail = plane.work_items.retrieve(
        workspace_slug=workspace_slug,
        project_id=project_id,
        work_item_id=wid,
        params=RetrieveQueryParams(expand="labels"),
    )
    label_ids = ids(detail.labels)
    if str(label_id) in label_ids:
        return True, f"label {label_id} on {wid}"
    return False, f"labels={sorted(label_ids)} missing perf={label_id}"


I4_TASK: dict[str, Any] = {
    "id": "I4",
    "author": "post-hoc-debias",
    "tags": {"write", "id_in_hand", "debias"},
    "prompt": ("In project {project}, attach label {label_id} to work item {work_item_id}."),
    "prompt_bind": _bind_i4,
    "needs": {"items", "labels"},
    "verify": verify_i4,
}


async def verify_i5(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """I5: target item priority is low (updated by UUID)."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    wid = (ctx.get("items") or {}).get(I3_TITLE)
    if not wid:
        return False, f"seed item {I3_TITLE!r} missing"
    detail = plane.work_items.retrieve(workspace_slug=workspace_slug, project_id=project_id, work_item_id=wid)
    pr = (detail.priority or "").lower() if detail.priority else ""
    if pr == "low":
        return True, f"work_item {wid} priority=low"
    return False, f"work_item {wid} priority={pr!r} (want low)"


I5_TASK: dict[str, Any] = {
    "id": "I5",
    "author": "post-hoc-debias",
    "tags": {"write", "id_in_hand", "debias"},
    "prompt": ("In project {project}, set the priority of work item {work_item_id} to low."),
    "prompt_bind": _bind_item_uuid(I3_TITLE),  # footer item; not high-traffic elsewhere
    "needs": {"items"},
    "verify": verify_i5,
}


async def verify_l1(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """L1: 90-minute log exists and reporting uses an immutable target-id oracle."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    wid = (ctx.get("items") or {}).get(L1_TITLE)
    if not wid:
        return answer_with_provenance(False, f"seed item {L1_TITLE!r} missing", run)
    expected_summary_ids = [str(value) for value in (ctx.get("l1_expected_summary_ids") or [])]
    if str(wid) not in expected_summary_ids:
        return answer_with_provenance(
            False,
            f"L1 fixture oracle mismatch: summary ids={expected_summary_ids!r} omit target={wid!r}",
            run,
        )
    if len(expected_summary_ids) < 2:
        return answer_with_provenance(
            False,
            f"L1 fixture error: the summary oracle {expected_summary_ids!r} holds only the agent's own "
            "row, so the answer does not require reading the summary",
            run,
        )
    # SDK: 90m log must be on THIS work item (list is already scoped to work_item_id).
    logs = plane.work_items.work_logs.list(workspace_slug=workspace_slug, project_id=project_id, work_item_id=wid)
    rows = logs if isinstance(logs, list) else (logs.results if hasattr(logs, "results") else logs)
    durations = [int(getattr(w, "duration", 0) or 0) for w in (rows or [])]
    if 90 not in durations:
        return answer_with_provenance(False, f"no 90-minute work log on target item {wid}; durations={durations}", run)

    try:
        summary = plane.projects.get_worklog_summary(workspace_slug=workspace_slug, project_id=project_id)
    except Exception as exc:
        raise_verifier_read_error("L1", "reading the project worklog summary", exc)

    summary_ids = worklog_summary_item_ids(summary)
    if str(wid) not in summary_ids:
        return answer_with_provenance(
            False,
            f"target item {wid} missing from project worklog summary ids={summary_ids!r}",
            run,
        )
    if sorted(summary_ids) != sorted(expected_summary_ids):
        return answer_with_provenance(
            False,
            f"worklog summary was mutated beyond the seeded oracle: live={summary_ids!r}; "
            f"expected={expected_summary_ids!r}",
            run,
        )

    final_text = get_final_text(run)
    if not reports_contract_value(final_text, "logged-minutes", "90"):
        return answer_with_provenance(
            False,
            f"logged-minutes values={contract_values(final_text, 'logged-minutes')!r}; want ['90']",
            run,
        )
    if not reports_contract_values(final_text, "summary-work-item-id", expected_summary_ids):
        return answer_with_provenance(
            False,
            f"summary-work-item-id values={contract_values(final_text, 'summary-work-item-id')!r}; "
            f"want {expected_summary_ids!r}",
            run,
        )
    return answer_with_provenance(
        True,
        f"90m log on {wid} + exact contract for {len(expected_summary_ids)} immutable summary row(s)",
        run,
    )


L1_TASK: dict[str, Any] = {
    "id": "L1",
    "author": "post-hoc-debias",
    "tags": {"write", "read", "long_tail", "debias"},
    "prompt": (
        f"In project {{project}}, log 1.5 hours (90 minutes) of work on the item titled "
        f"'{L1_TITLE}', then report the project's worklog summary. End with exactly one "
        "'logged-minutes: 90' line, and then, for each row the project worklog summary "
        "returns, one 'summary-work-item-id: <exact work item UUID>' line. The project "
        "may already have worklogs on other items. Include no other lines with those prefixes."
    ),
    "needs": {"items"},
    "verify": verify_l1,
}


async def verify_l2(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """L2: final text reports the API-confirmed seed activity count with provenance."""
    n = ctx.get("l2_activity_count")
    if not isinstance(n, int) or n < 1:
        return answer_with_provenance(False, "API-confirmed activity count missing from seed ctx", run)
    final_text = get_final_text(run)
    answer_correct = reports_contract_int(final_text, n)
    answer_note = (
        f"final text reports activity count {n} via contract"
        if answer_correct
        else f"final text missing contract count: {n} (need 'count: {n}' or bare integer)"
    )
    return answer_with_provenance(answer_correct, answer_note, run)


L2_TASK: dict[str, Any] = {
    "id": "L2",
    "author": "post-hoc-debias",
    "tags": {"read", "long_tail", "debias"},
    "prompt": (
        f"In project {{project}}, list the activity history for the work item titled "
        f"'{L2_TITLE}'. Summarize how many activities there are and mention any "
        "notable comment phrases you see. End your answer with a line of the form "
        "'count: N' where N is the number of activities."
    ),
    "needs": {"items", "activity_feed"},
    "verify": verify_l2,
}


async def verify_l3(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """L3: workspace has a release tag with version eval-rc1."""
    workspace_slug = ctx["workspace_slug"]
    try:
        rows = collect_paginated(
            lambda cursor: plane.releases.tags.list(
                workspace_slug=workspace_slug,
                params={"per_page": 100, **({"cursor": cursor} if cursor else {})},
            )
        )
    except Exception as exc:
        raise_verifier_read_error("L3", "listing workspace release tags", exc)
    versions = {(getattr(t, "version", None) or "").strip() for t in (rows or [])}
    if L3_TAG_VERSION in versions:
        # Track for teardown if id available.
        for t in rows or []:
            if (getattr(t, "version", None) or "").strip() == L3_TAG_VERSION:
                tid = getattr(t, "id", None)
                if tid:
                    objs = ctx.setdefault("workspace_objects", [])
                    if not any(o.get("kind") == "release_tag" and str(o.get("id")) == str(tid) for o in objs):
                        objs.append({"kind": "release_tag", "id": tid})
                break
        return True, f"release tag {L3_TAG_VERSION!r} present"
    return False, f"tag {L3_TAG_VERSION!r} missing; have {sorted(versions)}"


L3_TASK: dict[str, Any] = {
    "id": "L3",
    "author": "post-hoc-debias",
    "tags": {"write", "long_tail", "debias"},
    "prompt": (f"Create a release tag with version '{L3_TAG_VERSION}' (a version marker for the eval run)."),
    "needs": set(),  # workspace-level tag; no project fixture required
    "verify": verify_l3,
}


def _property_type_is_text(prop: Any) -> bool:
    raw = getattr(prop, "property_type", None)
    if raw is None:
        raw = getattr(prop, "type", None)
    if raw is None:
        return False
    if hasattr(raw, "value"):
        raw = raw.value
    if hasattr(raw, "name"):
        # Enum member: PropertyType.TEXT
        name = str(raw.name)
        if name.upper() == "TEXT":
            return True
    s = str(raw).upper()
    return s == "TEXT" or s.endswith(".TEXT") or s == "PROPERTYTYPE.TEXT"


async def verify_l4(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """L4: right customer has TEXT property 'Eval Industry' = Enterprise (exact name)."""
    workspace_slug = ctx["workspace_slug"]
    cust = ctx.get("customer") or {}
    customer_id = cust.get("id") if isinstance(cust, dict) else cust
    if not customer_id:
        return False, "customer missing from seed"
    try:
        prop_rows = collect_paginated(
            lambda cursor: plane.customers.properties.list(
                workspace_slug=workspace_slug,
                params={"per_page": 100, **({"cursor": cursor} if cursor else {})},
            )
        )
    except Exception as exc:
        raise_verifier_read_error("L4", "listing workspace customer properties", exc)
    target_prop: Any | None = None
    for p in prop_rows or []:
        # Exact display_name match only (case-insensitive full match) — not substring "Industry".
        display = (getattr(p, "display_name", None) or "").strip()
        if display.casefold() != L4_PROP_DISPLAY.casefold():
            continue
        if not _property_type_is_text(p):
            return False, (
                f"property {display!r} exists but property_type is not TEXT (got {getattr(p, 'property_type', None)!r})"
            )
        target_prop = p
        break
    if target_prop is None:
        return False, f"no TEXT customer property named exactly {L4_PROP_DISPLAY!r}"
    pid = str(target_prop.id)
    # Track for teardown.
    objs = ctx.setdefault("workspace_objects", [])
    if not any(o.get("kind") == "customer_property" and str(o.get("id")) == pid for o in objs):
        objs.append({"kind": "customer_property", "id": pid})
    try:
        values = plane.customers.property_values.list(workspace_slug=workspace_slug, customer_id=customer_id)
    except Exception as exc:
        raise_verifier_read_error("L4", f"reading property values for customer {customer_id}", exc)
    if not isinstance(values, dict):
        return False, f"unexpected property_values shape: {type(values)}"
    vals = values.get(pid) or values.get(str(pid)) or []
    flat = [str(v) for v in (vals if isinstance(vals, list) else [vals])]
    if any(L4_PROP_VALUE.casefold() == v.casefold() for v in flat):
        return True, f"customer {customer_id} property {pid} ({L4_PROP_DISPLAY})={L4_PROP_VALUE!r}"
    return False, f"customer {customer_id} property {pid} values {flat} lack {L4_PROP_VALUE!r}"


L4_TASK: dict[str, Any] = {
    "id": "L4",
    "author": "post-hoc-debias",
    "tags": {"write", "long_tail", "debias"},
    "prompt": (
        f"For customer '{CUSTOMER_NAME}', ensure there is a text customer property "
        f"named '{L4_PROP_DISPLAY}' and set its value to '{L4_PROP_VALUE}'."
    ),
    "needs": {"customer"},
    "verify": verify_l4,
}


async def verify_l5(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """L5: final text reports the API-confirmed seed attachment count with provenance."""
    n = ctx.get("l5_attachment_count")
    if not isinstance(n, int):
        return answer_with_provenance(False, "API-confirmed attachment count missing from seed ctx", run)
    final_text = get_final_text(run)
    answer_correct = reports_contract_int(final_text, n)
    answer_note = (
        f"final text reports attachment count {n} via contract"
        if answer_correct
        else f"final text missing contract count: {n} (need 'count: {n}' or bare integer)"
    )
    return answer_with_provenance(answer_correct, answer_note, run)


L5_TASK: dict[str, Any] = {
    "id": "L5",
    "author": "post-hoc-debias",
    "tags": {"read", "long_tail", "debias"},
    "prompt": (
        f"In project {{project}}, how many file attachments does the work item titled "
        f"'{L5_TITLE}' have? End your answer with a line of the form 'count: N' "
        "where N is the number of file attachments."
    ),
    "needs": {"items"},
    "verify": verify_l5,
}


DEBIAS_TASKS: list[dict[str, Any]] = [
    I1_TASK,
    I2_TASK,
    I3_TASK,
    I4_TASK,
    I5_TASK,
    L1_TASK,
    L2_TASK,
    L3_TASK,
    L4_TASK,
    L5_TASK,
]


__all__ = [
    "DEBIAS_TASKS",
    "I1_TITLE",
    "I2_TITLE",
    "I3_TITLE",
    "I4_TITLE",
    "L1_TITLE",
    "L2_TITLE",
    "L3_TAG_VERSION",
    "L4_PROP_DISPLAY",
    "L4_PROP_VALUE",
    "L5_TITLE",
    "verify_i1",
    "verify_i2",
    "verify_i3",
    "verify_i4",
    "verify_i5",
    "verify_l1",
    "verify_l2",
    "verify_l3",
    "verify_l4",
    "verify_l5",
]
