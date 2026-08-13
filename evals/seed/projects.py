"""Project creation and feature setup for evaluation fixtures."""

from __future__ import annotations

import secrets
from typing import Any

from plane import PlaneClient
from plane.errors.errors import HttpError
from plane.models.projects import CreateProject, ProjectFeature, UpdateProject
from plane.models.work_items import CreateWorkItem
from plane.models.workspaces import WorkspaceFeature

# Soft-deleted projects reserve identifiers; create may 409 — retry with a new suffix.
PROJECT_CREATE_ATTEMPT_LIMIT = 3

MAIN_PROJECT_BUG_TITLES = ("Main bug alpha", "Main bug beta")
SECOND_PROJECT_BUG_TITLES = (
    "Second bug one",
    "Second bug two",
    "Second bug three",
    "Second bug four",
)


def is_plan_gate(exc: BaseException) -> bool:
    """True only for genuine plan/subscription feature gates — not generic API failures."""
    if not isinstance(exc, HttpError):
        return False
    if exc.status_code in (402, 403):
        return True
    blob = f"{exc} {exc.response!s}".lower()
    keywords = ("plan", "subscription", "upgrade", "not available on your", "feature is not enabled")
    return any(keyword in blob for keyword in keywords)


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
    return any(keyword in blob for keyword in ("already", "exists", "taken"))


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
    triggers a new random 4-char hex suffix. At most three attempts are made,
    then the last collision error is raised again.
    """
    suffix = (initial_suffix or "")[:4].upper()
    if len(suffix) < 4:
        suffix = (suffix + secrets.token_hex(2).upper())[:4]
    last_exc: BaseException | None = None
    for attempt in range(PROJECT_CREATE_ATTEMPT_LIMIT):
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
            f"project create failed after {PROJECT_CREATE_ATTEMPT_LIMIT} identifier retries "
            f"(prefix={identifier_prefix!r}) with no captured exception"
        )
    raise last_exc


def enable_workspace_features(
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


def enable_project_features(
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

    update_values: dict[str, bool] = {}
    if "cycles" not in skip:
        update_values["cycle_view"] = True
    if "modules" not in skip:
        update_values["module_view"] = True
    if "intakes" not in skip:
        update_values["intake_view"] = True
    if "pages" not in skip:
        update_values["page_view"] = True
    if "worklogs" not in skip:
        update_values["is_time_tracking_enabled"] = True
    if update_values:
        plane.projects.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=UpdateProject(**update_values),
        )

    feature_values: dict[str, bool] = {}
    if "cycles" not in skip:
        feature_values["cycles"] = True
    if "modules" not in skip:
        feature_values["modules"] = True
    if "intakes" not in skip:
        feature_values["intakes"] = True
    if "pages" not in skip:
        feature_values["pages"] = True
    if feature_values:
        plane.projects.update_features(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=ProjectFeature(**feature_values),
        )


def seed_second_project(plane: PlaneClient, workspace_slug: str, context: dict[str, Any]) -> None:
    """Seed a second project with more open Bug items than the main project."""
    from .item_types import seed_item_type

    run_prefix = context["run8"]
    name = f"EVAL {run_prefix} B"
    project = create_project_with_identifier_retry(
        plane,
        workspace_slug,
        name=name,
        identifier_prefix="EB",
        initial_suffix=run_prefix[:4].upper(),
    )
    context["second_project_id"] = project.id
    context["second_project_name"] = name
    context["second_project_identifier"] = getattr(project, "identifier", None)
    # Track for teardown (project delete covers it; still record).
    context["second_project_ids"] = [project.id]
    enable_project_features(plane, workspace_slug, project.id)

    # Ensure Bug type exists on both projects.
    if not context.get("bug_type"):
        seed_item_type(plane, workspace_slug, context)
    bug = context.get("bug_type") or {}
    bug_id = bug.get("id") if isinstance(bug, dict) else bug
    if not bug_id:
        raise RuntimeError("seed second_project: bug_type required for R6 bug counts")

    # Import workspace-level type into second project when needed.
    if context.get("bug_type_workspace_level"):
        try:
            plane.work_item_types.import_to_project(
                workspace_slug=workspace_slug,
                project_id=project.id,
                work_item_type_ids=[bug_id],
            )
        except Exception as exc:
            if not is_plan_gate(exc):
                # May already be imported.
                if not (isinstance(exc, HttpError) and exc.status_code in (400, 409)):
                    raise

    main_id = context["project_id"]
    # Main project: fewer bugs
    main_bug_ids: list[str] = []
    for title in MAIN_PROJECT_BUG_TITLES:
        item = plane.work_items.create(
            workspace_slug=workspace_slug,
            project_id=main_id,
            data=CreateWorkItem(name=title, priority="high", type_id=str(bug_id)),  # type: ignore[arg-type]
        )
        main_bug_ids.append(item.id)
        context["items"][title] = item.id
        context["item_ids"].append(item.id)
    # Second project: more bugs
    second_bug_ids: list[str] = []
    for title in SECOND_PROJECT_BUG_TITLES:
        item = plane.work_items.create(
            workspace_slug=workspace_slug,
            project_id=project.id,
            data=CreateWorkItem(name=title, priority="high", type_id=str(bug_id)),  # type: ignore[arg-type]
        )
        second_bug_ids.append(item.id)
    context["r6_main_bug_count"] = len(main_bug_ids)
    context["r6_second_bug_count"] = len(second_bug_ids)
    context["r6_more_bugs_project"] = name  # second project has more
