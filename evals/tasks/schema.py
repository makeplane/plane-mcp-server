"""Schema-task definitions and their verifiers."""

from __future__ import annotations

from typing import Any

from plane.errors.errors import HttpError
from plane.models.enums import PropertyType

from evals.core.errors import TaskSkipped
from evals.core.fixtures import INTAKE_BILLING_TITLE, INTAKE_SPAM_TITLE, W8_TITLE
from evals.tasks.lookups import as_id, find_item_by_name
from evals.tasks.verification import is_verifier_not_found, raise_verifier_read_error


async def verify_s1(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """S1: Bug type has an OPTION property 'Severity' with Critical/Major/Minor.

    Only type-scoped property listing is accepted (no project/workspace fallbacks that
    would pass an unattached Severity). Unexpected API errors propagate as harness errors.
    """
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    bug_type_id = (
        (ctx.get("bug_type") or {}).get("id") if isinstance(ctx.get("bug_type"), dict) else ctx.get("bug_type")
    )
    if not bug_type_id:
        raise TaskSkipped("bug_type not seeded")

    try:
        props = list(
            plane.work_item_properties.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                type_id=str(bug_type_id),
            )
            or []
        )
    except HttpError as exc:
        if is_verifier_not_found(exc):
            # A type-scoped 404 is authoritative absence, so it is evidence of a failed end state.
            return False, "Severity property not found on Bug type (type-scoped list empty/404)"
        raise_verifier_read_error("S1", "listing Bug type properties", exc)

    severity = None
    for p in props:
        display = (getattr(p, "display_name", None) or getattr(p, "name", None) or "").strip()
        if display.lower() == "severity":
            # Prefer an explicit type link when the API exposes it.
            issue_type = getattr(p, "issue_type", None)
            if issue_type is not None and str(issue_type) not in ("", str(bug_type_id)):
                continue
            severity = p
            break
    if severity is None:
        return False, "Severity property not found on Bug type"

    prop_type = getattr(severity, "property_type", None)
    prop_type_val = prop_type.value if isinstance(prop_type, PropertyType) else prop_type
    if str(prop_type_val or "").upper() != PropertyType.OPTION.value:
        return False, f"Severity property_type={prop_type_val!r} (want OPTION)"

    option_names = {
        (getattr(o, "name", None) or (o.get("name") if isinstance(o, dict) else "") or "").strip()
        for o in (getattr(severity, "options", None) or [])
    }
    if not option_names:
        try:
            opts = plane.work_item_properties.options.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                property_id=severity.id,
            )
            option_names = {(getattr(o, "name", None) or "").strip() for o in (opts or [])}
        except HttpError as exc:
            if not is_verifier_not_found(exc):
                raise_verifier_read_error("S1", "listing Severity options", exc)
            # A missing options collection definitively cannot contain the required choices.
            option_names = set()

    required = {"critical", "major", "minor"}
    have = {n.casefold() for n in option_names if n}
    missing = required - have
    if missing:
        return False, f"Severity options missing {sorted(missing)}; have {sorted(option_names)}"
    return True, "Severity OPTION with Critical/Major/Minor present on Bug type"


S1_TASK: dict[str, Any] = {
    "id": "S1",
    "tags": {"setup"},
    "prompt": (
        "In project {project}, add a Severity dropdown property (options: Critical, "
        "Major, Minor) to the Bug work item type."
    ),
    "needs": {"bug_type"},
    "verify": verify_s1,
}


async def verify_s2(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """S2: Fibonacci estimate scale exists and target item estimate_point is 5."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    notes: list[str] = []
    ok = True

    # Resolve active estimate + points.
    try:
        est = plane.estimates.retrieve(workspace_slug=workspace_slug, project_id=project_id)
    except Exception as exc:
        if is_verifier_not_found(exc):
            return False, "project estimate not found; requested Fibonacci scale was not created"
        raise_verifier_read_error("S2", "retrieving the project estimate", exc)
    est_id = getattr(est, "id", None) or as_id(est)
    points = plane.estimates.list_points(workspace_slug=workspace_slug, project_id=project_id, estimate_id=est_id)
    point_rows = points if isinstance(points, list) else (points.results if hasattr(points, "results") else points)
    values = {(getattr(p, "value", None) or "").strip() for p in (point_rows or [])}
    fib_like = {"1", "2", "3", "5", "8"}
    if not fib_like.issubset(values):
        ok = False
        notes.append(f"estimate points missing fib subset; have {sorted(values)}")
    else:
        notes.append("fibonacci points present")

    five = next((p for p in (point_rows or []) if (getattr(p, "value", None) or "").strip() == "5"), None)
    item = find_item_by_name(plane, workspace_slug, project_id, W8_TITLE)
    if item is None:
        ok = False
        notes.append(f"target item {W8_TITLE!r} missing")
    else:
        detail = plane.work_items.retrieve(workspace_slug=workspace_slug, project_id=project_id, work_item_id=item.id)
        ep = getattr(detail, "estimate_point", None)
        ep_id = as_id(ep) if not isinstance(ep, (int, float)) else None
        # estimate_point may be expanded object or UUID.
        if five is not None and ep_id and str(ep_id) == str(five.id):
            notes.append("item estimate_point=5")
        elif ep is not None and str(getattr(ep, "value", ep)) in ("5", "5.0"):
            notes.append("item estimate value=5")
        else:
            ok = False
            notes.append(f"item estimate_point={ep!r} (want 5)")
    return ok, "; ".join(notes)


S2_TASK: dict[str, Any] = {
    "id": "S2",
    "tags": {"setup"},
    "prompt": (
        f"In project {{project}}, add a Fibonacci estimate scale (points 1,2,3,5,8) "
        f"and set the work item '{W8_TITLE}' to 5 points."
    ),
    "needs": {"items"},
    "verify": verify_s2,
}


async def verify_s3(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """S3: Incident type exists with a required TEXT property.

    Workspace-owned types: probe get_features.is_work_item_types_enabled at verify
    time (S3 needs is empty, so seed never sets bug_type_workspace_level).
    """
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    # Find Incident type (project list first).
    types = list(plane.work_item_types.list(workspace_slug=workspace_slug, project_id=project_id) or [])
    incident = next((t for t in types if (t.name or "").strip().casefold() == "incident"), None)
    if incident is None:
        # Probe workspace feature at verify time — do not rely on seed ctx flags.
        workspace_owns = False
        try:
            features = plane.workspaces.get_features(workspace_slug=workspace_slug)
            dump = features.model_dump() if hasattr(features, "model_dump") else {}
            workspace_owns = bool(dump.get("is_work_item_types_enabled"))
        except Exception as exc:
            raise_verifier_read_error("S3", "reading workspace work-item-type ownership", exc)
        if workspace_owns:
            try:
                wtypes = list(plane.workspace_work_item_types.list(workspace_slug=workspace_slug) or [])
                incident = next((t for t in wtypes if (t.name or "").strip().casefold() == "incident"), None)
            except Exception as exc:
                raise_verifier_read_error("S3", "listing workspace work-item types", exc)
    if incident is None:
        return False, "Incident work item type not found"

    try:
        props = list(
            plane.work_item_properties.list(
                workspace_slug=workspace_slug,
                project_id=project_id,
                type_id=str(incident.id),
            )
            or []
        )
    except HttpError as exc:
        if is_verifier_not_found(exc):
            # A type-scoped 404 is authoritative absence, not an unavailable read.
            return False, "no properties on Incident type"
        raise_verifier_read_error("S3", "listing Incident type properties", exc)

    required_text = None
    for p in props:
        prop_type = getattr(p, "property_type", None)
        prop_type_val = str(prop_type.value if isinstance(prop_type, PropertyType) else prop_type or "").upper()
        is_required = bool(getattr(p, "is_required", False))
        # TEXT only — no fallback to other required types (OPTION etc.).
        if is_required and prop_type_val in (PropertyType.TEXT.value, "TEXT", "STRING"):
            required_text = p
            break
    if required_text is None:
        return False, f"no required TEXT property on Incident; props={len(props)}"
    display = getattr(required_text, "display_name", None) or getattr(required_text, "name", None)
    return True, f"Incident type + required TEXT property {display!r}"


S3_TASK: dict[str, Any] = {
    "id": "S3",
    "tags": {"setup"},
    "prompt": (
        "In project {project}, create a work item type named 'Incident' and add a "
        "required text property (e.g. 'Impact summary') on it."
    ),
    "needs": set(),
    "verify": verify_s3,
}


async def verify_s4(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """S4: billing intake accepted (status=1), spam declined (status=-1)."""
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    intake = ctx.get("intake") or {}
    billing = intake.get("billing") or {}
    spam = intake.get("spam") or {}
    notes: list[str] = []
    ok = True

    def _status_of(issue_id: str | None, title: str) -> int | None:
        if not issue_id:
            return None
        try:
            row = plane.intake.retrieve(workspace_slug=workspace_slug, project_id=project_id, work_item_id=issue_id)
            return getattr(row, "status", None)
        except Exception:
            # Retrieve is optional because the independently authoritative list can resolve the same row.
            try:
                rows = plane.intake.list(workspace_slug=workspace_slug, project_id=project_id)
                results = rows.results if hasattr(rows, "results") else rows
                for r in results or []:
                    detail = getattr(r, "issue_detail", None)
                    name = getattr(detail, "name", None) if detail is not None else None
                    if name and name.strip() == title:
                        return getattr(r, "status", None)
            except Exception as exc:
                raise_verifier_read_error("S4", f"listing intake while resolving {title!r}", exc)
        return None

    b_status = _status_of(billing.get("issue_id"), INTAKE_BILLING_TITLE)
    s_status = _status_of(spam.get("issue_id"), INTAKE_SPAM_TITLE)
    # accept=1, decline=-1 per IntakeWorkItemStatusEnum
    if b_status != 1:
        ok = False
        notes.append(f"billing status={b_status!r} (want 1/accepted)")
    else:
        notes.append("billing accepted")
    if s_status != -1:
        ok = False
        notes.append(f"spam status={s_status!r} (want -1/declined)")
    else:
        notes.append("spam declined")
    return ok, "; ".join(notes)


S4_TASK: dict[str, Any] = {
    "id": "S4",
    "tags": {"setup"},
    "prompt": (
        f"In project {{project}}, triage intake: accept the billing request "
        f"'{INTAKE_BILLING_TITLE}' and reject/decline the spam item "
        f"'{INTAKE_SPAM_TITLE}'."
    ),
    "needs": {"intake"},
    "verify": verify_s4,
}


async def verify_s5(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """S5: project cycles + worklogs AND workspace customers enabled.

    Gates (plane-ee):
      - project.cycle_view — cycles create/list
      - project.is_time_tracking_enabled — worklogs
      - WorkspaceFeature.is_customer_enabled (API field ``customers``) — customer create 403
    """
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    notes: list[str] = []
    ok = True

    proj = plane.projects.retrieve(workspace_slug=workspace_slug, project_id=project_id)
    cycle_view = bool(getattr(proj, "cycle_view", None))
    time_tracking = bool(getattr(proj, "is_time_tracking_enabled", None))
    if not cycle_view:
        ok = False
        notes.append(f"cycle_view={getattr(proj, 'cycle_view', None)!r} (want True)")
    else:
        notes.append("cycle_view=True")
    if not time_tracking:
        ok = False
        notes.append(f"is_time_tracking_enabled={getattr(proj, 'is_time_tracking_enabled', None)!r} (want True)")
    else:
        notes.append("is_time_tracking_enabled=True")

    try:
        feat = plane.projects.get_features(workspace_slug=workspace_slug, project_id=project_id)
        dump = feat.model_dump() if hasattr(feat, "model_dump") else (feat if isinstance(feat, dict) else {})
        cycles_flag = dump.get("cycles") if isinstance(dump, dict) else getattr(feat, "cycles", None)
        if not cycles_flag:
            ok = False
            notes.append(f"features.cycles={cycles_flag!r} (want True)")
        else:
            notes.append("features.cycles=True")
    except Exception as exc:
        raise_verifier_read_error("S5", "reading project feature flags", exc)

    # Workspace customers toggle (is_customer_enabled behind API field ``customers``).
    try:
        ws_feat = plane.workspaces.get_features(workspace_slug=workspace_slug)
        ws_dump = (
            ws_feat.model_dump() if hasattr(ws_feat, "model_dump") else (ws_feat if isinstance(ws_feat, dict) else {})
        )
        customers_on = None
        if isinstance(ws_dump, dict):
            customers_on = ws_dump.get("customers")
            if customers_on is None:
                customers_on = ws_dump.get("is_customer_enabled")
        if customers_on is None:
            customers_on = getattr(ws_feat, "customers", None)
            if customers_on is None:
                customers_on = getattr(ws_feat, "is_customer_enabled", None)
        if not customers_on:
            ok = False
            notes.append(f"workspace.customers={customers_on!r} (want True)")
        else:
            notes.append("workspace.customers=True")
    except Exception as exc:
        raise_verifier_read_error("S5", "reading workspace customer feature flags", exc)

    return ok, "; ".join(notes)


S5_TASK: dict[str, Any] = {
    "id": "S5",
    "tags": {"setup"},
    "prompt": (
        "Enable cycles and time tracking (worklogs) for project {project}, "
        "and enable the customers feature for the workspace."
    ),
    # Minimal legacy path (2 calls):
    #   1. update_project(cycle_view=True, is_time_tracking_enabled=True)
    #   2. update_workspace_features(customers=True)
    # (features PATCH can set cycles→cycle_view but cannot set worklogs.)
    # Seed leaves project cycles+worklogs and workspace customers off.
    "needs": {"leave_cycles_worklogs_off"},
    "verify": verify_s5,
}


SCHEMA_TASKS: list[dict[str, Any]] = [S1_TASK, S2_TASK, S3_TASK, S4_TASK, S5_TASK]


__all__ = ["SCHEMA_TASKS", "verify_s1", "verify_s2", "verify_s3", "verify_s4", "verify_s5"]
