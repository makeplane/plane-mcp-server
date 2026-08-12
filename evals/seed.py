"""Per-run fixture create/teardown via plane-sdk."""

from __future__ import annotations

import os
import secrets
from datetime import date, timedelta
from typing import Any

from plane import PlaneClient
from plane.errors.errors import HttpError
from plane.models.customers import CreateCustomer, CreateCustomerRequest
from plane.models.cycles import CreateCycle, UpdateCycle
from plane.models.intake import CreateIntakeWorkItem, WorkItemForIntakeRequest
from plane.models.labels import CreateLabel
from plane.models.modules import CreateModule
from plane.models.projects import CreateProject, ProjectFeature, UpdateProject
from plane.models.releases import CreateRelease, UpdateReleaseChangelog
from plane.models.work_item_types import CreateWorkItemType
from plane.models.work_items import CreateWorkItem, CreateWorkItemComment, UpdateWorkItem
from plane.models.workspaces import WorkspaceFeature

# Soft-deleted projects reserve identifiers; create may 409 — retry with a new suffix.
_PROJECT_CREATE_MAX_ATTEMPTS = 3

# Fixed fixture titles for the `items` group. Exactly 4 urgent; the rest medium/high/low.
# "Payment webhook drops retries" is the R1 target (urgent, non-default state).
ITEM_FIXTURES: list[tuple[str, str]] = [
    ("Payment webhook drops retries", "urgent"),
    ("Checkout times out on 3DS challenge", "urgent"),
    ("Session cookie not rotated after login", "urgent"),
    ("Inventory count goes negative under load", "urgent"),
    ("Search results ignore archived projects", "high"),
    ("CSV export truncates multi-byte chars", "high"),
    ("Webhook secret rotation docs missing", "medium"),
    ("Dark mode contrast fails WCAG AA", "medium"),
    ("Onboarding email template stale", "medium"),
    ("Sidebar collapse flickers on resize", "low"),
    ("Tooltip clipped inside modal dialog", "low"),
    ("Footer year still says 2024", "none"),
]

R1_TITLE = ITEM_FIXTURES[0][0]
# R5 discussion target + distinctive comment phrases (word-boundary matched at verify).
R5_TITLE = "Checkout times out on 3DS challenge"
R5_COMMENT_PHRASES = (
    "stripe callback race",
    "retry budget exhausted",
)
# W2 / W3 / W8 targets
W2_TITLE = "Sidebar collapse flickers on resize"
W3_TITLE = "Dark mode contrast fails WCAG AA"
W8_TITLE = R1_TITLE
# W7 relation pair + reference URL
W7_SOURCE_TITLE = "Search results ignore archived projects"
W7_TARGET_TITLE = "CSV export truncates multi-byte chars"
W7_URL = "https://example.com/eval/runbook-w7"
# R3: assignees + due this week (seeded count stored in ctx)
R3_DUE_TITLES = (
    "Webhook secret rotation docs missing",
    "Onboarding email template stale",
)
# W6 unfinished items in Sprint 12
W6_UNFINISHED_TITLES = (
    "Inventory count goes negative under load",
    "Tooltip clipped inside modal dialog",
)
# Module completed-item titles (created extra when seeding module)
MODULE_NAME = "Checkout revamp"
MODULE_COMPLETED_TITLES = (
    "Module done: cart totals",
    "Module done: tax lines",
    "Module done: shipping quote",
)
# Intake fixtures
INTAKE_BILLING_TITLE = "Billing: invoice PDF missing line items"
INTAKE_SPAM_TITLE = "SPAM: cheap crypto pumps guaranteed"
# Customer / release
CUSTOMER_NAME = "Acme Corp"
CUSTOMER_REQUEST_NAME = "SSO support"
RELEASE_NAME = "1.2.0"
RELEASE_CHANGELOG_TEXT = "Changelog entry one: OAuth login hardening. Changelog entry two: webhook retry backoff."
# R6 second project bug titles
R6_MAIN_BUG_TITLES = ("Main bug alpha", "Main bug beta")
R6_SECOND_BUG_TITLES = (
    "Second bug one",
    "Second bug two",
    "Second bug three",
    "Second bug four",
)

LABEL_NAMES = ("auth", "triage", "perf")
CYCLE_PAST = "Sprint 12"
CYCLE_CURRENT = "Sprint 13"
# WS3 long-tail fixtures that live at workspace scope (must pre-clean + teardown).
DEBIAS_RELEASE_TAG_VERSION = "eval-rc1"
DEBIAS_CUSTOMER_PROP_DISPLAY = "Eval Industry"


def make_plane_client() -> tuple[PlaneClient, str]:
    """Build a PlaneClient from EVAL_* env vars (mirrors stdio client construction)."""
    api_key = os.environ.get("EVAL_PLANE_API_KEY", "")
    workspace_slug = os.environ.get("EVAL_PLANE_WORKSPACE_SLUG", "")
    base_url = os.environ.get("EVAL_PLANE_BASE_URL", "https://api.plane.so")
    if not api_key or not workspace_slug:
        raise RuntimeError("EVAL_PLANE_API_KEY and EVAL_PLANE_WORKSPACE_SLUG are required for live runs")
    client = PlaneClient(base_url=base_url, api_key=api_key)
    return client, workspace_slug


def seed_plan(needs: set[str]) -> list[str]:
    """Human-readable seed plan for --dry-run (no network)."""
    lines = [
        "project: EVAL {run8} (identifier EV{XXXX})",
    ]
    if "items" in needs:
        lines.append(f"items: {len(ITEM_FIXTURES)} work items (exactly 4 urgent open)")
        lines.append(f"  - {R1_TITLE!r} (urgent, non-default started-group state)  # R1 target")
        lines.append(f"  - {len(R3_DUE_TITLES)} assigned-to-me with due this week  # R3")
        lines.append(f"  - comments on {R5_TITLE!r}  # R5 discussion")
    if "activity_feed" in needs:
        lines.append(
            f"activity_feed: gate that activities exist for {R5_TITLE!r} "
            "(TaskSkipped env:no-activity-worker if empty)  # L2"
        )
    if "labels" in needs:
        lines.append(f"labels: {', '.join(LABEL_NAMES)}")
    if "bug_type" in needs:
        lines.append(
            "bug_type: work item type 'Bug' (genuine plan-gate only → skip dependents; other seed errors raise)"
        )
    if "cycles" in needs:
        past_state = "ends tomorrow, still OPEN so it can be closed" if "cycles_open_past" in needs else "past-dated"
        lines.append(f"cycles: {CYCLE_PAST!r} ({past_state}) + {CYCLE_CURRENT!r} (current); unfinished on past")
    if "module" in needs:
        lines.append(f"module: {MODULE_NAME!r} with {len(MODULE_COMPLETED_TITLES)} completed items")
    if "intake" in needs:
        lines.append(f"intake: billing {INTAKE_BILLING_TITLE!r} + spam {INTAKE_SPAM_TITLE!r}")
    if "customer" in needs:
        lines.append(f"customer: {CUSTOMER_NAME!r} + request {CUSTOMER_REQUEST_NAME!r}")
    if "release" in needs:
        lines.append(f"release: {RELEASE_NAME!r} with changelog body (2 entries as plain text)")
    if "second_project" in needs:
        lines.append("second_project: EVAL {run8} B with more open Bug-typed items than main (R6)")
    if "leave_cycles_worklogs_off" in needs:
        lines.append(
            "feature_exclusions (S5): project cycles+worklogs OFF; workspace customers OFF "
            "(agent enables; teardown re-enables customers=True for later C1)"
        )
    else:
        lines.append(
            "workspace_features: customers=True "
            "(is_customer_enabled; NOT work_item_types — leaves S1/S3 type mode alone)"
        )
    return lines


def _is_plan_gate(exc: BaseException) -> bool:
    """True only for genuine plan/subscription feature gates — not generic API failures."""
    if not isinstance(exc, HttpError):
        return False
    if exc.status_code in (402, 403):
        return True
    blob = f"{exc} {exc.response!s}".lower()
    keywords = ("plan", "subscription", "upgrade", "not available on your", "feature is not enabled")
    return any(k in blob for k in keywords)


def is_identifier_collision(exc: BaseException) -> bool:
    """True when project create failed because the identifier is already taken.

    Requires HTTP 400/409 *and* collision language (already/exists/taken). A bare
    ``identifier`` mention (validation shape errors) must not trigger retry.
    """
    if not isinstance(exc, HttpError):
        return False
    if exc.status_code not in (400, 409):
        return False
    blob = f"{exc} {exc.response!s}".lower()
    return any(k in blob for k in ("already", "exists", "taken"))


def create_project_with_identifier_retry(
    plane: PlaneClient,
    workspace_slug: str,
    *,
    name: str,
    identifier_prefix: str,
    initial_suffix: str,
) -> Any:
    """Create a project, regenerating the identifier suffix on soft-delete collisions.

    Plane soft-deletes reserve identifiers; a 409 (or identifier-in-message error)
    triggers a new random 4-char hex suffix. Max ``_PROJECT_CREATE_MAX_ATTEMPTS``
    attempts, then re-raises the last collision error.
    """
    suffix = (initial_suffix or "")[:4].upper()
    if len(suffix) < 4:
        suffix = (suffix + secrets.token_hex(2).upper())[:4]
    last_exc: BaseException | None = None
    for attempt in range(_PROJECT_CREATE_MAX_ATTEMPTS):
        if attempt > 0:
            suffix = secrets.token_hex(2).upper()  # 4 hex chars
        identifier = f"{identifier_prefix}{suffix}"
        try:
            return plane.projects.create(
                workspace_slug=workspace_slug,
                data=CreateProject(name=name, identifier=identifier),
            )
        except Exception as exc:
            if is_identifier_collision(exc):
                last_exc = exc
                continue
            raise
    if last_exc is None:
        raise RuntimeError(
            f"project create failed after {_PROJECT_CREATE_MAX_ATTEMPTS} identifier retries "
            f"(prefix={identifier_prefix!r}) with no captured exception"
        )
    raise last_exc


def _enable_workspace_features(
    plane: PlaneClient,
    workspace_slug: str,
    *,
    exclude: set[str] | frozenset[str] | None = None,
) -> None:
    """Enable workspace-level feature toggles that task preconditions need.

    Gate (plane-ee): create-customer 403 when
    ``check_workspace_feature(slug, IS_CUSTOMER_ENABLED)`` is false — DB column
    ``WorkspaceFeature.is_customer_enabled``. Legacy/SDK flips it via
    ``workspaces.update_features`` / ``WorkspaceFeature(customers=True)``
    (API serializer maps ``customers`` → ``is_customer_enabled``).

    Deliberately does **not** set ``work_item_types``: that flips
    workspace-vs-project type ownership and would change S1/S3 seed mode.

    ``exclude`` may contain ``customers`` (S5 leaves it off for the agent to enable).
    """
    skip = set(exclude or ())
    data: dict[str, bool] = {}
    if "customers" not in skip:
        data["customers"] = True
    if not data:
        return
    plane.workspaces.update_features(
        workspace_slug=workspace_slug,
        data=WorkspaceFeature(**data),
    )


def _enable_project_features(
    plane: PlaneClient,
    workspace_slug: str,
    project_id: str,
    *,
    exclude: set[str] | frozenset[str] | None = None,
) -> None:
    """Enable per-project feature gates that fresh projects ship with disabled.

    Two SDK calls (harmless if already on):

    1. ``projects.update`` / ``UpdateProject`` — view columns API gates read:
       ``cycle_view``, ``module_view``, ``intake_view``, ``page_view``,
       ``is_time_tracking_enabled`` (worklog 404 when false).
    2. ``projects.update_features`` / ``ProjectFeature`` — capability flags
       that the server maps onto the same view columns for cycles/modules/… .

    ``exclude`` is a set of feature keys to leave disabled (for S5):
    ``cycles``, ``modules``, ``intakes``, ``pages``, ``worklogs``.
    Default: enable all (other catalog tasks need them).
    """
    skip = set(exclude or ())

    upd_kwargs: dict[str, bool] = {}
    if "cycles" not in skip:
        upd_kwargs["cycle_view"] = True
    if "modules" not in skip:
        upd_kwargs["module_view"] = True
    if "intakes" not in skip:
        upd_kwargs["intake_view"] = True
    if "pages" not in skip:
        upd_kwargs["page_view"] = True
    if "worklogs" not in skip:
        upd_kwargs["is_time_tracking_enabled"] = True
    if upd_kwargs:
        plane.projects.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=UpdateProject(**upd_kwargs),
        )

    feat_kwargs: dict[str, bool] = {}
    if "cycles" not in skip:
        feat_kwargs["cycles"] = True
    if "modules" not in skip:
        feat_kwargs["modules"] = True
    if "intakes" not in skip:
        feat_kwargs["intakes"] = True
    if "pages" not in skip:
        feat_kwargs["pages"] = True
    if feat_kwargs:
        plane.projects.update_features(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=ProjectFeature(**feat_kwargs),
        )


def _preclean_ws3_workspace_artifacts(plane: PlaneClient, workspace_slug: str) -> None:
    """Delete leftover WS3 long-tail artifacts so a dirty workspace cannot false-pass.

    Removes any existing release tag ``eval-rc1`` and customer property
    ``Eval Industry`` before the rep seeds.

    Empty / not-found lists are silent. Clients without the API surface (offline
    test stubs) are skipped silently. If a matching artifact is **found** and
    cannot be deleted — or list fails on a present API — raises so the harness
    records ``infra_seed`` rather than running against dirty state.
    """
    releases = getattr(plane, "releases", None)
    tags_api = getattr(releases, "tags", None) if releases is not None else None
    if tags_api is not None:
        try:
            page = tags_api.list(workspace_slug=workspace_slug)
        except Exception as exc:
            raise RuntimeError(f"WS3 preclean: list release tags failed: {exc}") from exc
        rows = page.results if hasattr(page, "results") else page
        for tag in rows or []:
            ver = (getattr(tag, "version", None) or "").strip()
            if ver != DEBIAS_RELEASE_TAG_VERSION:
                continue
            tid = getattr(tag, "id", None)
            if not tid:
                continue
            try:
                tags_api.delete(workspace_slug=workspace_slug, tag_id=tid)
            except Exception as exc:
                raise RuntimeError(
                    f"WS3 preclean: failed to delete stale release tag {DEBIAS_RELEASE_TAG_VERSION!r} id={tid}: {exc}"
                ) from exc

    customers = getattr(plane, "customers", None)
    props_api = getattr(customers, "properties", None) if customers is not None else None
    if props_api is not None:
        try:
            page = props_api.list(workspace_slug=workspace_slug)
        except Exception as exc:
            raise RuntimeError(f"WS3 preclean: list customer properties failed: {exc}") from exc
        rows = page.results if hasattr(page, "results") else page
        target = DEBIAS_CUSTOMER_PROP_DISPLAY.casefold()
        for prop in rows or []:
            display = (getattr(prop, "display_name", None) or getattr(prop, "name", None) or "").strip()
            if display.casefold() != target:
                continue
            pid = getattr(prop, "id", None)
            if not pid:
                continue
            try:
                props_api.delete(workspace_slug=workspace_slug, property_id=pid)
            except Exception as exc:
                raise RuntimeError(
                    f"WS3 preclean: failed to delete stale customer property "
                    f"{DEBIAS_CUSTOMER_PROP_DISPLAY!r} id={pid}: {exc}"
                ) from exc


def seed(plane: PlaneClient, run_id: str, needs: set[str], ctx: dict[str, Any]) -> dict[str, Any]:
    """Create the eval project and declared fixture groups.

    Mutates the caller-provided `ctx` in place so project_id is visible to teardown
    even if a later fixture step raises (F5).
    """
    run8 = run_id[:8]
    project_name = f"EVAL {run8}"
    workspace_slug = os.environ["EVAL_PLANE_WORKSPACE_SLUG"]

    # Defensive: drop WS3 workspace artifacts that would make a no-op agent pass.
    _preclean_ws3_workspace_artifacts(plane, workspace_slug)

    # Reset known keys while preserving object identity for the caller.
    ctx.clear()
    ctx.update(
        {
            "run_id": run_id,
            "run8": run8,
            "workspace_slug": workspace_slug,
            "project_id": None,
            "project_name": project_name,
            "project_identifier": None,  # filled after create (may retry suffix)
            "labels": {},
            "items": {},
            "item_identifiers": {},  # title -> PROJ-N for ID-in-hand prompts
            "item_ids": [],
            "state_names": [],  # all project state display names (for R1 negative check)
            "r1_state_name": None,
            "bug_type": None,
            "bug_type_created": False,
            "bug_type_workspace_level": False,
            "bug_type_skip_reason": None,
            "cycles": {},
            "module": None,
            "module_completed_ids": [],
            "intake": {},
            "customer": None,
            "customer_request": None,
            "release": None,
            "second_project_id": None,
            "second_project_name": None,
            "r3_due_titles": list(R3_DUE_TITLES),
            "r3_due_count": len(R3_DUE_TITLES),
            "r5_title": R5_TITLE,
            "r5_comment_phrases": list(R5_COMMENT_PHRASES),
            "w6_unfinished_titles": list(W6_UNFINISHED_TITLES),
            "workspace_objects": [],  # [{kind, id}, ...] surviving project delete
        }
    )

    # EV + 4 hex chars; retry with a new suffix on soft-delete identifier collisions.
    project = create_project_with_identifier_retry(
        plane,
        workspace_slug,
        name=project_name,
        identifier_prefix="EV",
        initial_suffix=run8[:4].upper(),
    )
    ctx["project_id"] = project.id
    ctx["project_identifier"] = getattr(project, "identifier", None)

    # Feature enablement (workspace first, then project).
    #
    # Ordering for S5 vs C1 on a shared eval workspace:
    # - Each task-rep has its own seed/teardown; there is no multi-task seed batch.
    # - Default tasks: enable workspace customers=True so C1 create_customer works.
    # - S5 (needs leave_cycles_worklogs_off): leave project cycles+worklogs AND
    #   workspace customers OFF so the agent must flip all three; teardown then
    #   re-enables customers=True so a later C1 rep is not left 403ing.
    # - We do not try to "run workspace enable after S5 check" — seed is per-task.
    feature_exclude: set[str] = set()
    ws_feature_exclude: set[str] = set()
    if "leave_cycles_worklogs_off" in needs:
        feature_exclude = {"cycles", "worklogs"}
        ws_feature_exclude = {"customers"}
        ctx["s5_left_customers_off"] = True
    ctx["feature_exclude"] = sorted(feature_exclude)
    ctx["ws_feature_exclude"] = sorted(ws_feature_exclude)
    _enable_workspace_features(plane, workspace_slug, exclude=ws_feature_exclude)
    _enable_project_features(plane, workspace_slug, project.id, exclude=feature_exclude)

    # Labels before items so items can attach labels later if needed.
    if "labels" in needs:
        _seed_labels(plane, workspace_slug, ctx)
    if "items" in needs:
        _seed_items(plane, workspace_slug, ctx)
    # L2: comments must materialize as activities (activity worker must be running).
    if "activity_feed" in needs:
        if "items" not in needs and not ctx.get("item_ids"):
            _seed_items(plane, workspace_slug, ctx)
        _gate_activity_worker(plane, workspace_slug, ctx)
    if "bug_type" in needs:
        _seed_bug_type(plane, workspace_slug, ctx)
    if "cycles" in needs:
        # Cycles need items to attach unfinished work; seed items if not already.
        if "items" not in needs and not ctx["item_ids"]:
            _seed_items(plane, workspace_slug, ctx)
        _seed_cycles(plane, workspace_slug, ctx, leave_past_open="cycles_open_past" in needs)
    if "module" in needs:
        _seed_module(plane, workspace_slug, ctx)
    if "intake" in needs:
        _seed_intake(plane, workspace_slug, ctx)
    if "customer" in needs:
        _seed_customer(plane, workspace_slug, ctx)
    if "release" in needs:
        _seed_release(plane, workspace_slug, ctx)
    if "second_project" in needs:
        _seed_second_project(plane, workspace_slug, ctx)

    return ctx


def _seed_labels(plane: PlaneClient, workspace_slug: str, ctx: dict[str, Any]) -> None:
    for name in LABEL_NAMES:
        label = plane.labels.create(
            workspace_slug=workspace_slug,
            project_id=ctx["project_id"],
            data=CreateLabel(name=name),
        )
        ctx["labels"][name] = label.id


def _list_states(plane: PlaneClient, workspace_slug: str, project_id: str) -> list[Any]:
    page = plane.states.list(workspace_slug=workspace_slug, project_id=project_id)
    return list(page.results or [])


def _completed_state(states: list[Any]) -> Any | None:
    completed = [s for s in states if getattr(s, "group", None) == "completed"]
    if not completed:
        return None
    # Prefer a non-default completed state named Done if present.
    for s in completed:
        if (s.name or "").strip().casefold() == "done":
            return s
    return completed[0]


def _seed_items(plane: PlaneClient, workspace_slug: str, ctx: dict[str, Any]) -> None:
    project_id = ctx["project_id"]
    states = _list_states(plane, workspace_slug, project_id)
    ctx["state_names"] = sorted({(s.name or "").strip() for s in states if (s.name or "").strip()})

    # Prefer a non-default started-group state so R1 cannot be passed by guessing the default.
    started = [s for s in states if getattr(s, "group", None) == "started" and not getattr(s, "default", False)]
    if not started:
        started = [s for s in states if getattr(s, "group", None) == "started"]
    if not started:
        raise RuntimeError(
            "seed items: no started-group state available to place the R1 target; "
            f"states={[(s.name, s.group, s.default) for s in states]}"
        )
    r1_state = started[0]
    ctx["r1_state_name"] = r1_state.name
    ctx["r1_state_id"] = r1_state.id

    me = plane.users.get_me()
    me_id = str(me.id)
    ctx["me_id"] = me_id
    # Due dates must stay inside the current ISO week (Mon–Sun).
    # today+2d alone escapes the week on Sat/Sun — clamp to this week's Sunday.
    today = date.today()
    days_to_week_end = 6 - today.weekday()  # Mon=0 … Sun=6
    due_this_week = min(today + timedelta(days=2), today + timedelta(days=days_to_week_end)).isoformat()
    ctx["r3_due_date"] = due_this_week

    urgent_count = 0
    for title, priority in ITEM_FIXTURES:
        data_kwargs: dict[str, Any] = {"name": title, "priority": priority}
        if title == R1_TITLE:
            data_kwargs["state"] = str(r1_state.id)
        if title in R3_DUE_TITLES:
            data_kwargs["assignees"] = [me_id]
            data_kwargs["target_date"] = due_this_week
        item = plane.work_items.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=CreateWorkItem(**data_kwargs),  # type: ignore[arg-type]
        )
        # Some APIs ignore state on create; force via update if needed.
        if title == R1_TITLE:
            current = getattr(item, "state", None)
            current_id = current if isinstance(current, str) else getattr(current, "id", None)
            if str(current_id) != str(r1_state.id):
                item = plane.work_items.update(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    work_item_id=item.id,
                    data=UpdateWorkItem(state=str(r1_state.id)),
                )
        ctx["items"][title] = item.id
        ctx["item_ids"].append(item.id)
        seq = getattr(item, "sequence_id", None)
        if seq is not None and ctx.get("project_identifier"):
            ctx["item_identifiers"][title] = f"{ctx['project_identifier']}-{seq}"
        if priority == "urgent":
            urgent_count += 1
    assert urgent_count == 4, f"fixture invariant: expected 4 urgent items, got {urgent_count}"

    # R5: seed discussion comments on the known item.
    r5_id = ctx["items"].get(R5_TITLE)
    if r5_id:
        for phrase in R5_COMMENT_PHRASES:
            plane.work_items.comments.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=r5_id,
                data=CreateWorkItemComment(comment_html=f"<p>{phrase}</p>"),
            )


def _gate_activity_worker(plane: PlaneClient, workspace_slug: str, ctx: dict[str, Any]) -> None:
    """Skip L2 when comments never materialize as activities (no activity worker).

    Raises :class:`evals.tasks.TaskSkipped` with reason ``env:no-activity-worker``
    so the harness records a skip, not a task failure.
    """
    from evals.tasks import TaskSkipped

    project_id = ctx.get("project_id")
    wid = (ctx.get("items") or {}).get(R5_TITLE)
    if not project_id or not wid:
        raise TaskSkipped("env:no-activity-worker")
    try:
        page = plane.work_items.activities.list(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=wid,
        )
    except Exception as exc:
        raise TaskSkipped(f"env:no-activity-worker ({type(exc).__name__}: {exc})") from exc
    rows = page.results if hasattr(page, "results") else page
    if len(list(rows or [])) < 1:
        raise TaskSkipped("env:no-activity-worker")


def _seed_bug_type(plane: PlaneClient, workspace_slug: str, ctx: dict[str, Any]) -> None:
    """Create or resolve a 'Bug' work item type.

    Genuine plan-gate responses set bug_type=None + skip reason; all other failures raise.
    Workspace feature probe uses the real key `is_work_item_types_enabled` (F10).
    """
    project_id = ctx["project_id"]
    target = "Bug"
    try:
        features = plane.workspaces.get_features(workspace_slug=workspace_slug)
        dump = features.model_dump() if hasattr(features, "model_dump") else {}
        # Real API key (extra='allow' on WorkspaceFeature); never trust the fictional work_item_types key alone.
        workspace_owns = bool(dump.get("is_work_item_types_enabled"))

        if workspace_owns:
            existing = next(
                (
                    t
                    for t in plane.workspace_work_item_types.list(workspace_slug=workspace_slug)
                    if (t.name or "").strip() == target
                ),
                None,
            )
            created = False
            if existing is None:
                existing = plane.workspace_work_item_types.create(
                    workspace_slug=workspace_slug, data=CreateWorkItemType(name=target)
                )
                created = True
            plane.work_item_types.import_to_project(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_type_ids=[existing.id],
            )
            ctx["bug_type"] = {"id": existing.id, "name": target}
            ctx["bug_type_created"] = created
            ctx["bug_type_workspace_level"] = True
            if created:
                ctx["workspace_objects"].append({"kind": "work_item_type", "id": existing.id})
            return

        # Per-project types. Project features expose no work-item-type toggle — do not PATCH.
        existing = next(
            (
                t
                for t in plane.work_item_types.list(workspace_slug=workspace_slug, project_id=project_id)
                if (t.name or "").strip() == target
            ),
            None,
        )
        created = False
        if existing is None:
            existing = plane.work_item_types.create(
                workspace_slug=workspace_slug,
                project_id=project_id,
                data=CreateWorkItemType(name=target),
            )
            created = True
        ctx["bug_type"] = {"id": existing.id, "name": target}
        ctx["bug_type_created"] = created
        ctx["bug_type_workspace_level"] = False
    except Exception as exc:
        if _is_plan_gate(exc):
            ctx["bug_type"] = None
            ctx["bug_type_skip_reason"] = f"bug_type plan-gated: {exc}"
            return
        raise


def _seed_cycles(plane: PlaneClient, workspace_slug: str, ctx: dict[str, Any], leave_past_open: bool = False) -> None:
    """Seed Sprint 12 (past) + Sprint 13 (active) with work items.

    Plane forbids adding issues to a cycle whose end_date is already past
    (``The Cycle has already been completed so no new issues can be added`` —
    plane-ee cycle/issue.py). Ordering for Sprint 12:

      1. create with an *active* window (start past, end future)
      2. add_work_items while still active
      3. update end_date to the past (backdate) so the cycle is completed

    ``leave_past_open`` skips step 3, leaving Sprint 12 ending tomorrow. Closing a
    cycle is only legal while it is still open — Plane rejects every edit to an
    ended cycle (``The Cycle has already been completed so it cannot be edited``)
    and rejects a transfer out of a still-running one (``The old cycle is not
    completed yet``), so a fixture that pre-closes Sprint 12 makes "close it"
    unachievable and leaves ``progress_snapshot`` (a transfer side effect) as the
    only observable close signal. W6 asks the agent to close, so it seeds open.

    Sprint 13 is created and populated while genuinely active (start ≤ today ≤ end).
    """
    project_id = ctx["project_id"]
    me_id = ctx.get("me_id") or str(plane.users.get_me().id)
    today = date.today()
    # Final past window for Sprint 12 after backdate (completedCycles / W6 transfer source).
    past_start = (today - timedelta(days=28)).isoformat()
    past_end_final = (today - timedelta(days=14)).isoformat()
    # Temporary active end so create + add succeed (end must be ≥ now). When the
    # cycle stays open this is its final window, so keep it short — Sprint 12 ends
    # tomorrow, which is what makes "close it and roll the rest over" natural.
    past_end_active = (today + timedelta(days=1 if leave_past_open else 7)).isoformat()
    # Sprint 13: genuinely active at seed time (start ≤ today ≤ end).
    cur_start = (today - timedelta(days=3)).isoformat()
    cur_end = (today + timedelta(days=10)).isoformat()

    # 1) Create Sprint 12 still active (items can be added).
    past = plane.cycles.create(
        workspace_slug=workspace_slug,
        project_id=project_id,
        data=CreateCycle(
            name=CYCLE_PAST,
            start_date=past_start,
            end_date=past_end_active,
            owned_by=me_id,
            project_id=str(project_id),
        ),
    )
    # Sprint 13: active window for R4 / W6 transfer target.
    current = plane.cycles.create(
        workspace_slug=workspace_slug,
        project_id=project_id,
        data=CreateCycle(
            name=CYCLE_CURRENT,
            start_date=cur_start,
            end_date=cur_end,
            owned_by=me_id,
            project_id=str(project_id),
        ),
    )
    ctx["cycles"] = {
        CYCLE_PAST: past.id,
        CYCLE_CURRENT: current.id,
    }
    ctx["cycle_past_id"] = past.id
    ctx["cycle_current_id"] = current.id

    # 2) Add unfinished items to Sprint 12 *before* backdating.
    unfinished_ids = [ctx["items"][t] for t in W6_UNFINISHED_TITLES if t in ctx["items"]]
    if unfinished_ids:
        plane.cycles.add_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=past.id,
            issue_ids=unfinished_ids,
        )
    # R4: items on the active cycle (window still open).
    active_ids: list[str] = []
    for title in (R1_TITLE, "Session cookie not rotated after login"):
        iid = ctx["items"].get(title)
        if iid:
            active_ids.append(iid)
    if active_ids:
        plane.cycles.add_work_items(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=current.id,
            issue_ids=active_ids,
        )
    overdue_id = ctx["items"].get("Session cookie not rotated after login")
    if overdue_id:
        plane.work_items.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            work_item_id=overdue_id,
            data=UpdateWorkItem(target_date=(today - timedelta(days=3)).isoformat()),
        )
        ctx["r4_overdue_title"] = "Session cookie not rotated after login"
        ctx["r4_overdue_id"] = overdue_id
    ctx["r4_active_item_ids"] = active_ids

    # 3) Backdate Sprint 12 so it is a completed cycle for R4 semantics — unless the
    # task needs to close it itself, in which case it must still be open.
    # UpdateCycle.end_date is writable; API allows past end_dates (no "can't backdate" gate
    # on the update path — only add_work_items checks end_date < now).
    if not leave_past_open:
        plane.cycles.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            cycle_id=past.id,
            data=UpdateCycle(end_date=past_end_final),
        )
    # Final seeded end_date for W6 close assertion (complete_cycle sets end_date=today).
    ctx["cycle_past_seed_end_date"] = past_end_active if leave_past_open else past_end_final
    ctx["cycle_past_open"] = leave_past_open
    ctx["cycle_past_end_date_before_backdate"] = past_end_active


def _seed_module(plane: PlaneClient, workspace_slug: str, ctx: dict[str, Any]) -> None:
    project_id = ctx["project_id"]
    states = _list_states(plane, workspace_slug, project_id)
    done = _completed_state(states)
    if done is None:
        raise RuntimeError("seed module: no completed-group state to place module items")

    mod = plane.modules.create(
        workspace_slug=workspace_slug,
        project_id=project_id,
        data=CreateModule(name=MODULE_NAME, status="in-progress"),
    )
    ctx["module"] = {"id": mod.id, "name": MODULE_NAME}
    completed_ids: list[str] = []
    for title in MODULE_COMPLETED_TITLES:
        item = plane.work_items.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=CreateWorkItem(name=title, priority="medium", state=str(done.id)),  # type: ignore[arg-type]
        )
        # Force completed state if create ignored it.
        current = getattr(item, "state", None)
        current_id = current if isinstance(current, str) else getattr(current, "id", None)
        if str(current_id) != str(done.id):
            item = plane.work_items.update(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=item.id,
                data=UpdateWorkItem(state=str(done.id)),
            )
        completed_ids.append(item.id)
        ctx["items"][title] = item.id
        ctx["item_ids"].append(item.id)
    plane.modules.add_work_items(
        workspace_slug=workspace_slug,
        project_id=project_id,
        module_id=mod.id,
        issue_ids=completed_ids,
    )
    ctx["module_completed_ids"] = completed_ids
    ctx["module_completed_state_id"] = done.id


def _seed_intake(plane: PlaneClient, workspace_slug: str, ctx: dict[str, Any]) -> None:
    project_id = ctx["project_id"]
    billing = plane.intake.create(
        workspace_slug=workspace_slug,
        project_id=project_id,
        data=CreateIntakeWorkItem(
            issue=WorkItemForIntakeRequest(name=INTAKE_BILLING_TITLE, priority="high"),
        ),
    )
    spam = plane.intake.create(
        workspace_slug=workspace_slug,
        project_id=project_id,
        data=CreateIntakeWorkItem(
            issue=WorkItemForIntakeRequest(name=INTAKE_SPAM_TITLE, priority="none"),
        ),
    )
    # IntakeWorkItem.issue is the work-item id used by triage tools.
    ctx["intake"] = {
        "billing": {
            "intake_id": billing.id,
            "issue_id": getattr(billing, "issue", None) or billing.id,
            "title": INTAKE_BILLING_TITLE,
        },
        "spam": {
            "intake_id": spam.id,
            "issue_id": getattr(spam, "issue", None) or spam.id,
            "title": INTAKE_SPAM_TITLE,
        },
    }


def _seed_customer(plane: PlaneClient, workspace_slug: str, ctx: dict[str, Any]) -> None:
    customer = plane.customers.create(
        workspace_slug=workspace_slug,
        data=CreateCustomer(name=CUSTOMER_NAME),
    )
    ctx["customer"] = {"id": customer.id, "name": CUSTOMER_NAME}
    ctx["workspace_objects"].append({"kind": "customer", "id": customer.id})
    req = plane.customers.requests.create(
        workspace_slug=workspace_slug,
        customer_id=customer.id,
        data=CreateCustomerRequest(name=CUSTOMER_REQUEST_NAME),
    )
    ctx["customer_request"] = {"id": req.id, "name": CUSTOMER_REQUEST_NAME, "customer_id": customer.id}


def _seed_release(plane: PlaneClient, workspace_slug: str, ctx: dict[str, Any]) -> None:
    rel = plane.releases.create(
        workspace_slug=workspace_slug,
        data=CreateRelease(name=RELEASE_NAME),
    )
    ctx["release"] = {"id": rel.id, "name": RELEASE_NAME}
    ctx["workspace_objects"].append({"kind": "release", "id": rel.id})
    # Single changelog body; DESIGN's "2 entries" are encoded as plain-text bullets.
    try:
        plane.releases.changelog.update(
            workspace_slug=workspace_slug,
            release_id=rel.id,
            data=UpdateReleaseChangelog(
                description_html=f"<p>{RELEASE_CHANGELOG_TEXT}</p>",
            ),
        )
    except Exception as exc:
        # Non-fatal for seed if changelog endpoint is flaky; C2 verifier still checks release name.
        print(f"seed warning: release changelog update failed: {exc}")
    ctx["release_changelog_text"] = RELEASE_CHANGELOG_TEXT


def _seed_second_project(plane: PlaneClient, workspace_slug: str, ctx: dict[str, Any]) -> None:
    """R6: second project with more open Bug items than the main eval project."""
    run8 = ctx["run8"]
    name = f"EVAL {run8} B"
    project = create_project_with_identifier_retry(
        plane,
        workspace_slug,
        name=name,
        identifier_prefix="EB",
        initial_suffix=run8[:4].upper(),
    )
    ctx["second_project_id"] = project.id
    ctx["second_project_name"] = name
    ctx["second_project_identifier"] = getattr(project, "identifier", None)
    # Track for teardown (project delete covers it; still record).
    ctx["second_project_ids"] = [project.id]
    _enable_project_features(plane, workspace_slug, project.id)

    # Ensure Bug type exists on both projects.
    if not ctx.get("bug_type"):
        _seed_bug_type(plane, workspace_slug, ctx)
    bug = ctx.get("bug_type") or {}
    bug_id = bug.get("id") if isinstance(bug, dict) else bug
    if not bug_id:
        raise RuntimeError("seed second_project: bug_type required for R6 bug counts")

    # Import workspace-level type into second project when needed.
    if ctx.get("bug_type_workspace_level"):
        try:
            plane.work_item_types.import_to_project(
                workspace_slug=workspace_slug,
                project_id=project.id,
                work_item_type_ids=[bug_id],
            )
        except Exception as exc:
            if not _is_plan_gate(exc):
                # May already be imported.
                if not (isinstance(exc, HttpError) and exc.status_code in (400, 409)):
                    raise

    main_id = ctx["project_id"]
    # Main project: fewer bugs
    main_bug_ids: list[str] = []
    for title in R6_MAIN_BUG_TITLES:
        item = plane.work_items.create(
            workspace_slug=workspace_slug,
            project_id=main_id,
            data=CreateWorkItem(name=title, priority="high", type_id=str(bug_id)),  # type: ignore[arg-type]
        )
        main_bug_ids.append(item.id)
        ctx["items"][title] = item.id
        ctx["item_ids"].append(item.id)
    # Second project: more bugs
    second_bug_ids: list[str] = []
    for title in R6_SECOND_BUG_TITLES:
        item = plane.work_items.create(
            workspace_slug=workspace_slug,
            project_id=project.id,
            data=CreateWorkItem(name=title, priority="high", type_id=str(bug_id)),  # type: ignore[arg-type]
        )
        second_bug_ids.append(item.id)
    ctx["r6_main_bug_count"] = len(main_bug_ids)
    ctx["r6_second_bug_count"] = len(second_bug_ids)
    ctx["r6_more_bugs_project"] = name  # second project has more


def _cleanup_severity_on_bug_type(plane: PlaneClient, ctx: dict[str, Any]) -> None:
    """Delete Severity properties attached to the seeded Bug type (avoids multi-rep pollution)."""
    bug = ctx.get("bug_type")
    if not bug:
        return
    bug_type_id = bug.get("id") if isinstance(bug, dict) else bug
    if not bug_type_id:
        return
    workspace_slug = ctx.get("workspace_slug") or ""
    project_id = ctx.get("project_id")

    props: list[Any] = []
    try:
        if project_id:
            props = list(
                plane.work_item_properties.list(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    type_id=str(bug_type_id),
                )
                or []
            )
    except HttpError as exc:
        if exc.status_code not in (404, 405):
            print(f"teardown warning: list Severity props failed: {exc}")
            return
    except Exception as exc:
        print(f"teardown warning: list Severity props failed: {exc}")
        return

    for p in props:
        display = (getattr(p, "display_name", None) or getattr(p, "name", None) or "").strip()
        if display.lower() != "severity":
            continue
        try:
            if project_id:
                plane.work_item_properties.delete(
                    workspace_slug=workspace_slug,
                    project_id=project_id,
                    type_id=str(bug_type_id),
                    work_item_property_id=p.id,
                )
            ctx.setdefault("workspace_objects", [])  # no-op anchor
        except Exception as exc:
            print(f"teardown warning: failed to delete Severity property {p.id}: {exc}")


def _cleanup_agent_incident_type(plane: PlaneClient, ctx: dict[str, Any]) -> None:
    """Best-effort cleanup of agent-created Incident type (S3 multi-rep pollution)."""
    workspace_slug = ctx.get("workspace_slug") or ""
    project_id = ctx.get("project_id")
    try:
        if ctx.get("bug_type_workspace_level"):
            for t in plane.workspace_work_item_types.list(workspace_slug=workspace_slug) or []:
                if (t.name or "").strip().casefold() == "incident":
                    plane.workspace_work_item_types.delete(workspace_slug=workspace_slug, type_id=t.id)
        elif project_id:
            for t in plane.work_item_types.list(workspace_slug=workspace_slug, project_id=project_id) or []:
                if (t.name or "").strip().casefold() == "incident":
                    plane.work_item_types.delete(
                        workspace_slug=workspace_slug, project_id=project_id, work_item_type_id=t.id
                    )
    except Exception as exc:
        print(f"teardown warning: Incident type cleanup failed: {exc}")


def teardown(plane: PlaneClient, ctx: dict[str, Any]) -> None:
    """Delete the project and any workspace-scoped objects we created."""
    if not ctx:
        return
    workspace_slug = ctx.get("workspace_slug") or os.environ.get("EVAL_PLANE_WORKSPACE_SLUG", "")
    project_id = ctx.get("project_id")

    # S5 left customers off (or agent enabled them): re-enable for subsequent task-reps
    # on the shared eval workspace. We always set customers=True — we do not restore a
    # prior false (S5's job is to leave the workspace usable for C1).
    if ctx.get("s5_left_customers_off"):
        try:
            plane.workspaces.update_features(
                workspace_slug=workspace_slug,
                data=WorkspaceFeature(customers=True),
            )
        except Exception as exc:
            print(f"teardown warning: re-enable workspace customers failed: {exc}")

    # Drop agent-created Severity on Bug before project/type teardown (F8 multi-rep pollution).
    try:
        _cleanup_severity_on_bug_type(plane, ctx)
    except Exception as exc:
        print(f"teardown warning: Severity cleanup failed: {exc}")
    try:
        _cleanup_agent_incident_type(plane, ctx)
    except Exception as exc:
        print(f"teardown warning: Incident cleanup failed: {exc}")

    # Best-effort: agent-created Acme Corp customers (C1) that never hit workspace_objects.
    try:
        page = plane.customers.list(workspace_slug=workspace_slug)
        rows = page.results if hasattr(page, "results") else page
        for c in rows or []:
            if (c.name or "").strip().casefold() in (CUSTOMER_NAME.casefold(), "acme"):
                # Only delete if we seeded or created during this run (tracked or name match + run).
                tracked = {o.get("id") for o in (ctx.get("workspace_objects") or []) if o.get("kind") == "customer"}
                if str(c.id) in tracked or ctx.get("customer") is None:
                    # Avoid deleting long-lived Acme if we pre-seeded and tracked it — still delete tracked.
                    if str(c.id) in tracked or not ctx.get("customer"):
                        ctx.setdefault("workspace_objects", []).append({"kind": "customer", "id": c.id})
    except Exception as exc:
        print(f"teardown warning: customer scan failed: {exc}")

    # Workspace-scoped cleanup first (survive project deletion).
    seen_ws: set[str] = set()
    for obj in ctx.get("workspace_objects") or []:
        kind = obj.get("kind")
        oid = obj.get("id")
        if not oid:
            continue
        key = f"{kind}:{oid}"
        if key in seen_ws:
            continue
        seen_ws.add(key)
        try:
            if kind == "work_item_type":
                plane.workspace_work_item_types.delete(workspace_slug=workspace_slug, type_id=oid)
            elif kind == "work_item_property":
                plane.workspace_work_item_properties.delete(workspace_slug=workspace_slug, property_id=oid)
            elif kind == "customer":
                plane.customers.delete(workspace_slug=workspace_slug, customer_id=oid)
            elif kind == "release":
                plane.releases.delete(workspace_slug=workspace_slug, release_id=oid)
            elif kind == "release_tag":
                plane.releases.tags.delete(workspace_slug=workspace_slug, tag_id=oid)
            elif kind == "customer_property":
                plane.customers.properties.delete(workspace_slug=workspace_slug, property_id=oid)
        except Exception as exc:
            print(f"teardown warning: failed to delete workspace {kind} {oid}: {exc}")

    # Sweep by well-known WS3 names in case tracking missed an agent-created row.
    try:
        page = plane.releases.tags.list(workspace_slug=workspace_slug)
        rows = page.results if hasattr(page, "results") else page
        for tag in rows or []:
            if (getattr(tag, "version", None) or "").strip() == DEBIAS_RELEASE_TAG_VERSION:
                tid = getattr(tag, "id", None)
                if tid and f"release_tag:{tid}" not in seen_ws:
                    try:
                        plane.releases.tags.delete(workspace_slug=workspace_slug, tag_id=tid)
                    except Exception as exc:
                        print(f"teardown warning: sweep release tag {tid}: {exc}")
    except Exception as exc:
        print(f"teardown warning: sweep release tags failed: {exc}")
    try:
        page = plane.customers.properties.list(workspace_slug=workspace_slug)
        rows = page.results if hasattr(page, "results") else page
        target = DEBIAS_CUSTOMER_PROP_DISPLAY.casefold()
        for prop in rows or []:
            display = (getattr(prop, "display_name", None) or getattr(prop, "name", None) or "").strip()
            if display.casefold() == target:
                pid = getattr(prop, "id", None)
                if pid and f"customer_property:{pid}" not in seen_ws:
                    try:
                        plane.customers.properties.delete(workspace_slug=workspace_slug, property_id=pid)
                    except Exception as exc:
                        print(f"teardown warning: sweep customer property {pid}: {exc}")
    except Exception as exc:
        print(f"teardown warning: sweep customer properties failed: {exc}")

    # Second project before main (no dependency either way, but be thorough).
    for pid in ctx.get("second_project_ids") or []:
        if not pid or pid == project_id:
            continue
        try:
            plane.projects.delete(workspace_slug=workspace_slug, project_id=pid)
        except Exception as exc:
            print(f"teardown warning: failed to delete second project {pid}: {exc}")

    if project_id:
        try:
            plane.projects.delete(workspace_slug=workspace_slug, project_id=project_id)
        except Exception as exc:
            name = ctx.get("project_name", project_id)
            print(f"teardown warning: failed to delete project {name!r}: {exc}")
            print(f"orphaned project: {name}")
