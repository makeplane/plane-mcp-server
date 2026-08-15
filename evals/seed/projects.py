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

from evals.errors import TaskSkipped
from evals.evidence import set_target_evidence

from .randomize import random_truth_rng, random_truth_token, record_randomized_truth

# Plane's project identifier field is capped at 12 characters. Keep two characters
# for the eval prefix and use eight hex characters (32 bits), leaving two spare.
PLANE_PROJECT_IDENTIFIER_MAX_LENGTH = 12
PROJECT_IDENTIFIER_SUFFIX_LENGTH = 8
# Soft-deleted projects reserve identifiers; a long-lived workspace needs more than
# three chances even with the larger suffix space.
PROJECT_CREATE_ATTEMPT_LIMIT = 8

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
    ``TaskSkipped`` lives in a neutral module, so seed and task packages can import in
    either order without a cycle.
    """
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
    triggers a new random 8-char hex suffix. At most eight attempts are made,
    then the last collision error is raised again.
    """
    if len(identifier_prefix) + PROJECT_IDENTIFIER_SUFFIX_LENGTH > PLANE_PROJECT_IDENTIFIER_MAX_LENGTH:
        raise ValueError(
            f"identifier prefix {identifier_prefix!r} leaves fewer than "
            f"{PROJECT_IDENTIFIER_SUFFIX_LENGTH} suffix characters under Plane's "
            f"{PLANE_PROJECT_IDENTIFIER_MAX_LENGTH}-character limit"
        )
    suffix = (initial_suffix or "")[:PROJECT_IDENTIFIER_SUFFIX_LENGTH].upper()
    if len(suffix) < PROJECT_IDENTIFIER_SUFFIX_LENGTH:
        suffix = (suffix + secrets.token_hex(4).upper())[:PROJECT_IDENTIFIER_SUFFIX_LENGTH]
    last_exc: BaseException | None = None
    for attempt in range(PROJECT_CREATE_ATTEMPT_LIMIT):
        if attempt > 0:
            suffix = secrets.token_hex(4).upper()  # 8 hex chars / 32 bits
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
    except Exception as exc:
        raise RuntimeError(f"workspace feature snapshot failed before mutation: {exc}") from exc
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
    """Seed two API-confirmed Bug counts, randomising R6's winner per row."""
    from .item_types import seed_item_type

    run_prefix = context["run8"]
    name = f"EVAL {run_prefix} B"
    project = create_project_with_identifier_retry(
        plane,
        workspace_slug,
        name=name,
        identifier_prefix="EB",
        initial_suffix=run_prefix.upper(),
    )
    context["second_project_id"] = project.id
    context["second_project_name"] = name
    context["second_project_identifier"] = getattr(project, "identifier", None)
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
    task_id = str(context.get("task_id") or "")
    if task_id == "R6":
        rng = random_truth_rng(context, "R6:project-bugs")
        hidden_token = random_truth_token(context, "R6:project-bugs")
        main_count, second_count = rng.sample(range(1, 6), 2)
        main_titles = tuple(f"Main bug case {hidden_token}-{index + 1}" for index in range(main_count))
        second_titles = tuple(f"Second bug case {hidden_token}-{index + 1}" for index in range(second_count))
        record_randomized_truth(
            context,
            "R6.open_bug_counts",
            {"intended_main": main_count, "intended_second": second_count},
        )
    else:
        main_titles = MAIN_PROJECT_BUG_TITLES
        second_titles = SECOND_PROJECT_BUG_TITLES

    main_bug_ids: list[str] = []
    for title in main_titles:
        item = plane.work_items.create(
            workspace_slug=workspace_slug,
            project_id=main_id,
            data=CreateWorkItem(name=title, priority="high", type_id=str(bug_id)),  # type: ignore[arg-type]
        )
        main_bug_ids.append(item.id)
        context["items"][title] = item.id
        context["item_ids"].append(item.id)
    second_bug_ids: list[str] = []
    for title in second_titles:
        item = plane.work_items.create(
            workspace_slug=workspace_slug,
            project_id=project.id,
            data=CreateWorkItem(name=title, priority="high", type_id=str(bug_id)),  # type: ignore[arg-type]
        )
        second_bug_ids.append(item.id)
    if task_id != "R6":
        context["r6_main_bug_count"] = len(main_bug_ids)
        context["r6_second_bug_count"] = len(second_bug_ids)
        context["r6_more_bugs_project"] = name
        return

    def confirmed_open_bug_count(project_id: str, work_item_ids: list[str]) -> int:
        count = 0
        for work_item_id in work_item_ids:
            detail = plane.work_items.retrieve(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
            )
            if str(getattr(detail, "type_id", None) or "") != str(bug_id):
                continue
            if getattr(detail, "completed_at", None) or getattr(detail, "archived_at", None):
                continue
            count += 1
        return count

    confirmed_main = confirmed_open_bug_count(main_id, main_bug_ids)
    confirmed_second = confirmed_open_bug_count(str(project.id), second_bug_ids)
    if confirmed_main == confirmed_second:
        raise RuntimeError(f"seed R6: API-confirmed open Bug counts tie ({confirmed_main} each)")
    main_project = plane.projects.retrieve(workspace_slug=workspace_slug, project_id=main_id)
    second_project = plane.projects.retrieve(workspace_slug=workspace_slug, project_id=project.id)
    main_name = str(getattr(main_project, "name", None) or "")
    second_name = str(getattr(second_project, "name", None) or "")
    if not main_name or not second_name:
        raise RuntimeError("seed R6: API project readback returned a project without a name")
    context["r6_main_bug_count"] = confirmed_main
    context["r6_second_bug_count"] = confirmed_second
    context["r6_more_bugs_project"] = main_name if confirmed_main > confirmed_second else second_name
    context["randomized_truth"]["R6.open_bug_counts"]["confirmed"] = {
        "main": confirmed_main,
        "second": confirmed_second,
        "winner": context["r6_more_bugs_project"],
    }
    set_target_evidence(context, [*main_titles, *second_titles], target_ids=[main_id, project.id])
