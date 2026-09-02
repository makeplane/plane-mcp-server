"""Project creation and feature setup for evaluation fixtures."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from typing import Any

from plane import PlaneClient
from plane.errors.errors import HttpError
from plane.models.projects import CreateProject, ProjectFeature, UpdateProject
from plane.models.query_params import PaginatedQueryParams
from plane.models.work_items import CreateWorkItem
from plane.models.workspaces import WorkspaceFeature

from evals.core.evidence import set_target_count_evidence, set_target_evidence, set_target_grouped_count_evidence
from evals.core.fixtures import eval_project_name_variants

from .gates import is_plan_gate
from .identities import record_seeded_entity
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


def _is_collision(exc: BaseException) -> bool:
    """True for an HTTP 400/409 whose body reads as a uniqueness conflict."""
    if not isinstance(exc, HttpError):
        return False
    if exc.status_code not in (400, 409):
        return False
    blob = f"{exc} {exc.response!s}".lower()
    return any(keyword in blob for keyword in ("already", "exists", "taken"))


def is_name_collision(exc: BaseException) -> bool:
    """True when project create failed because the project *name* is already taken.

    Plane answers both conflicts with the same 409 and the same collision wording --
    ``name: The project name is already taken`` against
    ``identifier: ...`` -- so the named field is the only thing separating them. A body
    mentioning the identifier is treated as an identifier collision, because retrying a
    fresh suffix is cheap and was the behaviour before names could be retried at all.
    """
    if not _is_collision(exc):
        return False
    blob = f"{exc} {exc.response!s}".lower()
    return "name" in blob and "identifier" not in blob


def is_identifier_collision(exc: BaseException) -> bool:
    """True when project create failed because the identifier is already taken.

    Requires HTTP 400/409 *and* collision language (already/exists/taken). A bare
    ``identifier`` mention (validation shape errors) must not trigger retry.
    """
    return _is_collision(exc) and not is_name_collision(exc)


def find_project_by_identifier(plane: PlaneClient, workspace_slug: str, identifier: str) -> Any | None:
    """Return the project holding this exact identifier, or None.

    Identifiers are unique per workspace, so this settles the one question an ambiguous
    create leaves open: did the server create it before the client stopped waiting?
    """
    cursor = None
    while True:
        page = plane.projects.list(
            workspace_slug=workspace_slug,
            params=PaginatedQueryParams(per_page=100, cursor=cursor),
        )
        results = page.results if hasattr(page, "results") else page
        for proj in results or []:
            if (getattr(proj, "identifier", None) or "").strip().upper() == identifier.strip().upper():
                return proj
        # The SDK always populates next_cursor, so paging must stop on next_page_results.
        if not getattr(page, "next_page_results", False):
            return None
        cursor = page.next_cursor


def create_project_with_collision_retry(
    plane: PlaneClient,
    workspace_slug: str,
    *,
    name: str,
    identifier_prefix: str,
    initial_suffix: str,
    name_variants: Iterator[str] | None = None,
) -> Any:
    """Create a project, retrying past both kinds of uniqueness conflict.

    Plane soft-deletes reserve identifiers, so an identifier collision draws a new random
    8-char hex suffix. A *name* collision means a project of that name already exists --
    residue from a crashed run, since teardown deletes by recorded id -- and the suffix was
    never the problem, so it advances to the next name from ``name_variants`` instead.
    Without ``name_variants`` a name collision raises immediately rather than burning the
    budget regenerating an identifier that was already fine.

    Both kinds share the ``PROJECT_CREATE_ATTEMPT_LIMIT`` budget, so this survives up to
    seven collisions of either kind in one create; past that the last error is re-raised.
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
    for _attempt in range(PROJECT_CREATE_ATTEMPT_LIMIT):
        identifier = f"{identifier_prefix}{suffix}"
        try:
            return plane.projects.create(
                workspace_slug=workspace_slug,
                data=CreateProject(name=name, identifier=identifier),
            )
        except Exception as exc:
            if is_name_collision(exc):
                next_name = next(name_variants, None) if name_variants is not None else None
                if next_name is None:
                    raise
                name = next_name
                last_exc = exc
                continue
            if is_identifier_collision(exc):
                suffix = secrets.token_hex(4).upper()  # 8 hex chars / 32 bits
                last_exc = exc
                continue
            # An exception carrying no HTTP status means the client never learned the outcome:
            # the server may well have created the project before the read timed out. That is
            # how the orphans got there. The id was never returned, so the caller never put it
            # in the teardown context, so teardown was never asked to delete it -- and reported
            # cleanup_error 0 truthfully while a project sat in the workspace, skewing every
            # later workspace-wide task. Adopting the project both removes the orphan and turns
            # a lost row into a normal one.
            #
            # Limited to no-response errors on purpose. A 5xx is also ambiguous in principle,
            # but the measured failure is a client-side read timeout, and treating every HTTP
            # error as maybe-created would adopt projects after refusals that created nothing.
            if not isinstance(exc, HttpError):
                try:
                    adopted = find_project_by_identifier(plane, workspace_slug, identifier)
                except Exception:
                    adopted = None  # Lookup failed too; report the original failure.
                if adopted is not None:
                    return adopted
            raise
    if last_exc is None:
        raise RuntimeError(
            f"project create failed after {PROJECT_CREATE_ATTEMPT_LIMIT} attempts "
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


def _project_bug_type_id(plane: PlaneClient, workspace_slug: str, project_id: str) -> str:
    """Resolve or create a project-owned Bug type, so each project answers for its own."""
    from plane.models.work_item_types import CreateWorkItemType

    from .item_types import BUG_TYPE_NAME, is_work_item_type_named

    existing = next(
        (
            row
            for row in plane.work_item_types.list(workspace_slug=workspace_slug, project_id=project_id)
            if is_work_item_type_named(row, BUG_TYPE_NAME)
        ),
        None,
    )
    if existing is None:
        existing = plane.work_item_types.create(
            workspace_slug=workspace_slug,
            project_id=project_id,
            data=CreateWorkItemType(name=BUG_TYPE_NAME),
        )
    return str(existing.id)


def seed_second_project(plane: PlaneClient, workspace_slug: str, context: dict[str, Any]) -> None:
    """Seed two API-confirmed Bug counts, randomising R6's winner per row."""
    from .item_types import seed_item_type

    run_prefix = context["run8"]
    variants = eval_project_name_variants(run_prefix, second=True)
    name = next(variants)
    project = create_project_with_collision_retry(
        plane,
        workspace_slug,
        name=name,
        identifier_prefix="EB",
        initial_suffix=run_prefix.upper(),
        name_variants=variants,
    )
    context["second_project_id"] = project.id
    record_seeded_entity(context, "project", project.id)
    # The created name, not the requested one: a name collision advances to a variant.
    context["second_project_name"] = getattr(project, "name", None) or name
    context["second_project_identifier"] = getattr(project, "identifier", None)
    enable_project_features(plane, workspace_slug, project.id)

    # Ensure Bug type exists on both projects.
    if not context.get("bug_type"):
        seed_item_type(plane, workspace_slug, context)
    bug = context.get("bug_type") or {}
    bug_id = bug.get("id") if isinstance(bug, dict) else bug
    if not bug_id:
        raise RuntimeError("seed second_project: bug_type required for R6 bug counts")

    # Give the second project a Bug type of its own. Workspace-owned types are shared, so
    # importing is enough; project-owned types are not, and creating B's items with the main
    # project's type id left them invisible to an agent that resolves 'Bug' inside B. It
    # counted zero there and named the main project, always in that direction, while the
    # oracle — which reads those ids back directly — saw the seeded count and disagreed.
    second_bug_id = bug_id
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
    else:
        second_bug_id = _project_bug_type_id(plane, workspace_slug, str(project.id))

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
        record_seeded_entity(context, "work_item", item.id)
    second_bug_ids: list[str] = []
    for title in second_titles:
        item = plane.work_items.create(
            workspace_slug=workspace_slug,
            project_id=project.id,
            data=CreateWorkItem(name=title, priority="high", type_id=str(second_bug_id)),  # type: ignore[arg-type]
        )
        second_bug_ids.append(item.id)
        record_seeded_entity(context, "work_item", item.id)
    if task_id != "R6":
        context["r6_main_bug_count"] = len(main_bug_ids)
        context["r6_second_bug_count"] = len(second_bug_ids)
        context["r6_more_bugs_project"] = name
        return

    def confirmed_open_bug_count(project_id: str, work_item_ids: list[str], type_id: str) -> int:
        count = 0
        for work_item_id in work_item_ids:
            detail = plane.work_items.retrieve(
                workspace_slug=workspace_slug,
                project_id=project_id,
                work_item_id=work_item_id,
            )
            if str(getattr(detail, "type_id", None) or "") != str(type_id):
                continue
            if getattr(detail, "completed_at", None) or getattr(detail, "archived_at", None):
                continue
            count += 1
        return count

    confirmed_main = confirmed_open_bug_count(main_id, main_bug_ids, str(bug_id))
    confirmed_second = confirmed_open_bug_count(str(project.id), second_bug_ids, str(second_bug_id))
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
    set_target_evidence(context, [*main_titles, *second_titles])
    # Two honest call shapes reach this answer: one count grouped by project_id, or one
    # count per project. Only the grouped shape was provable, so every agent that took
    # the per-project route answered correctly and scored as unproven.
    set_target_grouped_count_evidence(
        context,
        {main_id: confirmed_main, str(project.id): confirmed_second},
    )
    set_target_count_evidence(context, confirmed_main, confirmed_second, target_ids=[main_id, project.id])
