"""Project creation and feature setup for evaluation fixtures."""

from __future__ import annotations

import contextlib
import secrets
from collections.abc import Iterator
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


# Wording a refusal uses when the workspace's plan is what stands in the way. A feature
# switched off for a project says "not enabled for this project" instead, which is a
# configuration state the harness can change and so is not a gate.
PLAN_GATE_PROSE = ("upgrade your plan", "payment required", "subscription", "not available on your")


def is_plan_gate(exc: BaseException) -> bool:
    """True only for genuine plan gates — not generic API failures.

    402 is unambiguous. 403 and 400 are not: Plane uses 403 for ordinary permission denial
    and for the initiative/teamspace plan gates in the same shape, so a bare 403 counted as
    a gate turned real permission bugs into environment skips. Those two now need the
    refusal to name a plan limit.
    """
    if not isinstance(exc, HttpError):
        return False
    if exc.status_code == 402:
        return True
    if exc.status_code not in (400, 403):
        return False
    blob = f"{exc} {exc.response!s}".lower()
    return any(phrase in blob for phrase in PLAN_GATE_PROSE)


@contextlib.contextmanager
def plan_gate_skips(feature: str) -> Iterator[None]:
    """Turn a plan refusal raised inside the block into a task skip.

    An uncaught seed exception becomes infra_seed and kills the task-rep; a capability the
    plan excludes is an environment fact, recorded like L2's missing activity worker.
    TaskSkipped is imported inside because evals.tasks imports this package at module load.
    """
    from evals.tasks.skip import TaskSkipped

    try:
        yield
    except Exception as exc:
        if is_plan_gate(exc):
            raise TaskSkipped(f"env:plan-gated:{feature}") from exc
        raise


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


def workspace_feature_state(plane: PlaneClient, workspace_slug: str) -> dict[str, bool | None]:
    """Read the workspace feature toggles this module writes, so teardown can put them back.

    The API exposes ``customers``; older payloads spell it ``is_customer_enabled``. Returns
    ``None`` for a value the API did not report rather than guessing a default.
    """
    try:
        features = plane.workspaces.get_features(workspace_slug=workspace_slug)
    except Exception:
        return {"customers": None}
    dump = features.model_dump() if hasattr(features, "model_dump") else {}
    value = dump.get("customers")
    if value is None:
        value = dump.get("is_customer_enabled")
    if value is None:
        value = getattr(features, "customers", None)
    return {"customers": None if value is None else bool(value)}


def enable_workspace_features(
    plane: PlaneClient,
    workspace_slug: str,
    *,
    exclude: set[str] | frozenset[str] | None = None,
) -> dict[str, bool | None]:
    """Set workspace-level feature toggles, returning the prior values for teardown.

    Excluded features are written ``False``, not skipped: the workspace outlives every run,
    so omitting the write silently satisfied S5's customers precondition after run one.
    Never sets ``work_item_types`` — that flips type ownership and changes S1/S3 seed mode.
    """
    skip = set(exclude or ())
    prior = workspace_feature_state(plane, workspace_slug)
    plane.workspaces.update_features(
        workspace_slug=workspace_slug,
        data=WorkspaceFeature(customers="customers" not in skip),
    )
    return prior


def enable_project_features(
    plane: PlaneClient,
    workspace_slug: str,
    project_id: str,
    *,
    exclude: set[str] | frozenset[str] | None = None,
) -> None:
    """Set per-project feature gates; ``exclude`` names the ones to leave off.

    Excluded features are written ``False``, not omitted: ``page_view`` defaults to True,
    so omission would silently leave it on.
    """
    skip = set(exclude or ())

    update_values: dict[str, bool] = {
        "cycle_view": "cycles" not in skip,
        "module_view": "modules" not in skip,
        "intake_view": "intakes" not in skip,
        "page_view": "pages" not in skip,
        "is_time_tracking_enabled": "worklogs" not in skip,
    }
    if update_values:
        plane.projects.update(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=UpdateProject(**update_values),
        )

    feature_values: dict[str, bool] = {
        "cycles": "cycles" not in skip,
        "modules": "modules" not in skip,
        "intakes": "intakes" not in skip,
        "pages": "pages" not in skip,
    }
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
