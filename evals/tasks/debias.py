"""ID-in-hand and long-tail de-biasing tasks with their verifiers."""

from __future__ import annotations

from typing import Any

from plane.models.query_params import RetrieveQueryParams

from evals.seed import (
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
from evals.tasks.common import (
    contract_values,
    get_final_text,
    ids,
    reports_contract_int,
    reports_contract_value,
    reports_contract_values,
    state_name,
)

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
    "tags": {"write", "tier1", "id_in_hand", "debias"},
    "prompt": ("In project {project}, update work item {work_item_id}: set its priority to high."),
    "prompt_bind": _bind_item_uuid(I1_TITLE),
    "optimal_calls": 1,
    "optimal_tools": {"update_work_item"},
    "alternate_tools": {
        "retrieve_work_item",
        "list_work_items",
        "search_work_items",
        "retrieve_work_item_by_identifier",
    },
    "needs": {"items"},
    "verify": verify_i1,
}


async def verify_i2(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """I2: final text reports the identifier-target state via ``state: NAME``."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    wid = (ctx.get("items") or {}).get(I2_TITLE)
    if not wid:
        return False, f"seed item {I2_TITLE!r} missing"
    detail = plane.work_items.retrieve(workspace_slug=workspace_slug, project_id=project_id, work_item_id=wid)
    name = state_name(plane, workspace_slug, project_id, detail.state)
    if not name:
        return False, "target state name unresolved"
    final_text = get_final_text(run)
    if reports_contract_value(final_text, "state", name):
        return True, f"final text reports state {name!r} via contract"
    return False, f"state values={contract_values(final_text, 'state')!r}; want [{name!r}]"


I2_TASK: dict[str, Any] = {
    "id": "I2",
    "author": "post-hoc-debias",
    "tags": {"read", "tier1", "id_in_hand", "debias"},
    "prompt": (
        "In project {project}, what is the current state of work item "
        "{work_item_identifier}? Return exactly one line: 'state: <exact state name>'."
    ),
    "prompt_bind": _bind_item_identifier(I2_TITLE),
    "optimal_calls": 1,
    "optimal_tools": {"retrieve_work_item_by_identifier"},
    "alternate_tools": {
        "retrieve_work_item",
        "list_work_items",
        "search_work_items",
        "list_states",
    },
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
    "tags": {"write", "tier1", "id_in_hand", "debias"},
    "prompt": ("In project {project}, add work item {work_item_id} to cycle {cycle_id}."),
    "prompt_bind": _bind_i3,
    "optimal_calls": 1,
    "optimal_tools": {"manage_cycle_work_items"},
    "alternate_tools": {
        "list_cycles",
        "list_cycle_work_items",
        "list_work_items",
        "retrieve_cycle",
    },
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
    "tags": {"write", "tier1", "id_in_hand", "debias"},
    "prompt": ("In project {project}, attach label {label_id} to work item {work_item_id}."),
    "prompt_bind": _bind_i4,
    "optimal_calls": 1,
    "optimal_tools": {"manage_work_item_label"},
    "alternate_tools": {
        "update_work_item",
        "list_labels",
        "retrieve_work_item",
        "list_work_items",
    },
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
    "tags": {"write", "tier1", "id_in_hand", "debias"},
    "prompt": ("In project {project}, set the priority of work item {work_item_id} to low."),
    "prompt_bind": _bind_item_uuid(I3_TITLE),  # footer item; not high-traffic elsewhere
    "optimal_calls": 1,
    "optimal_tools": {"update_work_item"},
    "alternate_tools": {
        "retrieve_work_item",
        "list_work_items",
        "search_work_items",
    },
    "needs": {"items"},
    "verify": verify_i5,
}


async def verify_l1(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """L1: 90-minute log exists and exact contract lines report the API summary."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    wid = (ctx.get("items") or {}).get(L1_TITLE)
    if not wid:
        return False, f"seed item {L1_TITLE!r} missing"
    # SDK: 90m log must be on THIS work item (list is already scoped to work_item_id).
    logs = plane.work_items.work_logs.list(workspace_slug=workspace_slug, project_id=project_id, work_item_id=wid)
    rows = logs if isinstance(logs, list) else (logs.results if hasattr(logs, "results") else logs)
    durations = [int(getattr(w, "duration", 0) or 0) for w in (rows or [])]
    if 90 not in durations:
        return False, f"no 90-minute work log on target item {wid}; durations={durations}"

    try:
        summary = plane.projects.get_worklog_summary(workspace_slug=workspace_slug, project_id=project_id)
        raw = summary if isinstance(summary, list) else (getattr(summary, "results", None) or summary or [])
        sum_rows = list(raw or [])
    except Exception as exc:
        return False, f"project worklog summary failed: {exc}"

    summary_ids: list[str] = []
    for row in sum_rows:
        dump = row.model_dump() if hasattr(row, "model_dump") else (row if isinstance(row, dict) else {})
        value = getattr(row, "issue_id", None) or getattr(row, "work_item_id", None)
        if value is None and isinstance(dump, dict):
            value = dump.get("issue_id") or dump.get("work_item_id")
        item_id = str(value or "").strip()
        if item_id and item_id not in summary_ids:
            summary_ids.append(item_id)
    if str(wid) not in summary_ids:
        return False, f"target item {wid} missing from project worklog summary ids={summary_ids!r}"

    final_text = get_final_text(run)
    if not reports_contract_value(final_text, "logged-minutes", "90"):
        return False, f"logged-minutes values={contract_values(final_text, 'logged-minutes')!r}; want ['90']"
    if not reports_contract_values(final_text, "summary-work-item-id", summary_ids):
        return False, (
            f"summary-work-item-id values={contract_values(final_text, 'summary-work-item-id')!r}; want {summary_ids!r}"
        )
    return True, f"90m log on {wid} + exact contract for {len(summary_ids)} summary row(s)"


L1_TASK: dict[str, Any] = {
    "id": "L1",
    "author": "post-hoc-debias",
    "tags": {"write", "read", "tier1", "long_tail", "debias"},
    "prompt": (
        f"In project {{project}}, log 1.5 hours (90 minutes) of work on the item titled "
        f"'{L1_TITLE}', then report the project's worklog summary. End with exactly "
        "one 'logged-minutes: 90' line and one "
        "'summary-work-item-id: <exact work item UUID>' line for every row returned "
        "by the project worklog summary. Include no other lines with those prefixes."
    ),
    "optimal_calls": 3,
    "optimal_tools": {"list_work_items", "create_work_log", "get_project_worklog_summary"},
    "alternate_tools": {
        "search_work_items",
        "list_work_logs",
        "retrieve_work_item",
        "list_projects",
    },
    "needs": {"items"},
    "verify": verify_l1,
}


async def verify_l2(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """L2: target has activities; final text reports the count via ``count: N`` contract."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    wid = (ctx.get("items") or {}).get(L2_TITLE)
    if not wid:
        return False, f"seed item {L2_TITLE!r} missing"
    try:
        page = plane.work_items.activities.list(workspace_slug=workspace_slug, project_id=project_id, work_item_id=wid)
    except Exception as exc:
        return False, f"activities.list failed: {exc}"
    rows = page.results if hasattr(page, "results") else page
    n = len(list(rows or []))
    if n < 1:
        return False, "no activities on target (seed comments should create some)"
    final_text = get_final_text(run)
    if not reports_contract_int(final_text, n):
        return False, f"final text missing contract count: {n} (need 'count: {n}' or bare integer)"
    return True, f"final text reports activity count {n} via contract"


L2_TASK: dict[str, Any] = {
    "id": "L2",
    "author": "post-hoc-debias",
    "tags": {"read", "tier1", "long_tail", "debias"},
    "prompt": (
        f"In project {{project}}, list the activity history for the work item titled "
        f"'{L2_TITLE}'. Summarize how many activities there are and mention any "
        "notable comment phrases you see. End your answer with a line of the form "
        "'count: N' where N is the number of activities."
    ),
    "optimal_calls": 2,
    "optimal_tools": {"list_work_items", "list_work_item_activities"},
    "alternate_tools": {
        "search_work_items",
        "retrieve_work_item",
        "list_work_item_comments",
        "retrieve_work_item_activity",
    },
    "needs": {"items", "activity_feed"},
    "verify": verify_l2,
}


async def verify_l3(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """L3: workspace has a release tag with version eval-rc1."""
    workspace_slug = ctx["workspace_slug"]
    try:
        page = plane.releases.tags.list(workspace_slug=workspace_slug)
    except Exception as exc:
        return False, f"list release tags failed: {exc}"
    rows = page.results if hasattr(page, "results") else page
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
    "tags": {"write", "tier1", "long_tail", "debias"},
    "prompt": (f"Create a release tag with version '{L3_TAG_VERSION}' (a version marker for the eval run)."),
    "optimal_calls": 1,
    "optimal_tools": {"create_release_tag"},
    "alternate_tools": {
        "list_release_tags",
        "retrieve_release_tag",
        "list_releases",
        "update_release_tag",
    },
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
        props = plane.customers.properties.list(workspace_slug=workspace_slug)
    except Exception as exc:
        return False, f"list customer properties failed: {exc}"
    prop_rows = props.results if hasattr(props, "results") else props
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
        return False, f"get property values failed: {exc}"
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
    "tags": {"write", "tier1", "long_tail", "debias"},
    "prompt": (
        f"For customer '{CUSTOMER_NAME}', ensure there is a text customer property "
        f"named '{L4_PROP_DISPLAY}' and set its value to '{L4_PROP_VALUE}'."
    ),
    "optimal_calls": 3,
    "optimal_tools": {
        "list_customers",
        "create_customer_property",
        "set_customer_property_values",
    },
    "alternate_tools": {
        "list_customer_properties",
        "get_customer_property_values",
        "retrieve_customer",
        "update_customer_property",
    },
    "needs": {"customer"},
    "verify": verify_l4,
}


async def verify_l5(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """L5: final text reports the attachment count via ``count: N`` contract."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    wid = (ctx.get("items") or {}).get(L5_TITLE)
    if not wid:
        return False, f"seed item {L5_TITLE!r} missing"
    try:
        page = plane.work_items.attachments.list(workspace_slug=workspace_slug, project_id=project_id, work_item_id=wid)
    except Exception as exc:
        return False, f"attachments.list failed: {exc}"
    rows = page.results if hasattr(page, "results") else page
    n = len(list(rows or []))
    final_text = get_final_text(run)
    if not reports_contract_int(final_text, n):
        return False, f"final text missing contract count: {n} (need 'count: {n}' or bare integer)"
    return True, f"final text reports attachment count {n} via contract"


L5_TASK: dict[str, Any] = {
    "id": "L5",
    "author": "post-hoc-debias",
    "tags": {"read", "tier1", "long_tail", "debias"},
    "prompt": (
        f"In project {{project}}, how many file attachments does the work item titled "
        f"'{L5_TITLE}' have? End your answer with a line of the form 'count: N' "
        "where N is the number of file attachments."
    ),
    "optimal_calls": 2,
    "optimal_tools": {"list_work_items", "list_work_item_attachments"},
    "alternate_tools": {
        "search_work_items",
        "retrieve_work_item",
        "get_work_item_attachment_download_url",
    },
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
