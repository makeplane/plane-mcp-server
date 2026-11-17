"""Cross-entity task definitions and their verifiers."""

from __future__ import annotations

from typing import Any

from evals.core.changelog import changelog_items, normalize_changelog_text
from evals.core.fixtures import (
    CUSTOMER_NAME,
    CUSTOMER_REQUEST_NAME,
    R1_TITLE,
)
from evals.tasks.answers import (
    answer_with_provenance,
    contract_values,
    get_final_text,
    reports_contract_value,
    reports_contract_values,
)
from evals.tasks.lookups import as_id, collect_paginated, find_item_by_name, ids
from evals.tasks.verification import is_verifier_not_found, raise_verifier_read_error


async def verify_c1(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """C1: customer 'Acme Corp' has request 'SSO support' linked to the R1 work item.

    Anchors: exact customer name, exact request name, and the R1_TITLE work item id
    resolved from the eval project at verify time (must be among linked ids).
    """
    workspace_slug = ctx["workspace_slug"]
    project_id = ctx["project_id"]
    notes: list[str] = []
    ok = True

    # Resolve the required link target first (seeded Payment webhook item).
    r1 = find_item_by_name(plane, workspace_slug, project_id, R1_TITLE)
    if r1 is None:
        return False, f"R1 item {R1_TITLE!r} not found in project"

    rows = collect_paginated(
        lambda cursor: plane.customers.list(
            workspace_slug=workspace_slug,
            params={"per_page": 100, **({"cursor": cursor} if cursor else {})},
        )
    )
    # Exact name only — do not match arbitrary acme* customers.
    acme = next((c for c in (rows or []) if (c.name or "").strip() == CUSTOMER_NAME), None)
    if acme is None:
        return False, f"customer {CUSTOMER_NAME!r} not found"

    # Track for teardown if agent-created
    if not ctx.get("customer"):
        ctx.setdefault("workspace_objects", []).append({"kind": "customer", "id": acme.id})

    rrows = collect_paginated(
        lambda cursor: plane.customers.requests.list(
            workspace_slug=workspace_slug,
            customer_id=acme.id,
            params={"per_page": 100, **({"cursor": cursor} if cursor else {})},
        )
    )
    sso = next(
        (r for r in (rrows or []) if (r.name or "").strip() == CUSTOMER_REQUEST_NAME),
        None,
    )
    if sso is None:
        ok = False
        notes.append(f"request {CUSTOMER_REQUEST_NAME!r} missing")
    else:
        notes.append("SSO request present")

    # Require the R1 work item among customer-linked work items.
    try:
        wi_rows = collect_paginated(
            lambda cursor: plane.customers.work_items.list(
                workspace_slug=workspace_slug,
                customer_id=acme.id,
                params={"per_page": 100, **({"cursor": cursor} if cursor else {})},
            )
        )
        linked_ids = ids(wi_rows)
        # Plain string ids also count.
        for row in wi_rows:
            if isinstance(row, str):
                linked_ids.add(row)
            elif isinstance(row, dict) and row.get("id"):
                linked_ids.add(str(row["id"]))
            else:
                # Customer work item wrappers may expose work_item / issue field.
                for attr in ("work_item", "work_item_id", "issue", "issue_id"):
                    ref = getattr(row, attr, None) if not isinstance(row, dict) else row.get(attr)
                    rid = as_id(ref)
                    if rid:
                        linked_ids.add(str(rid))
        if str(r1.id) not in linked_ids:
            ok = False
            notes.append(f"R1 item {r1.id} not linked; linked={sorted(linked_ids)}")
        else:
            notes.append(f"R1 item {r1.id} linked")
    except Exception as exc:
        raise_verifier_read_error("C1", f"listing work items linked to customer {acme.id}", exc)

    return ok, "; ".join(notes)


C1_TASK: dict[str, Any] = {
    "id": "C1",
    "tags": {"write"},
    "prompt": (
        f"Create customer '{CUSTOMER_NAME}' (if it does not already exist), add a "
        f"request named '{CUSTOMER_REQUEST_NAME}', and link that request to the work "
        f"item '{R1_TITLE}' in project {{project}}."
    ),
    # No pre-seeded customer — agent creates; items needed for link target.
    "needs": {"items"},
    "verify": verify_c1,
}


async def verify_c2(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """C2: report the immutable release baseline with target-response evidence."""
    final_text = get_final_text(run)
    notes: list[str] = []
    ok = True
    release = ctx.get("release") or {}
    expected_release = str(
        ctx.get("release_name")
        or (release.get("name") if isinstance(release, dict) else getattr(release, "name", None))
        or ""
    )
    if not expected_release:
        return answer_with_provenance(False, "fixture missing: seeded release name is unavailable", run)
    if not reports_contract_value(final_text, "release", expected_release):
        ok = False
        notes.append(f"release values={contract_values(final_text, 'release')!r}; want [{expected_release!r}]")
    else:
        notes.append(f"release={expected_release!r}")

    release_id = release.get("id") if isinstance(release, dict) else getattr(release, "id", release)
    if not release_id:
        return answer_with_provenance(False, "fixture missing: seeded release id is unavailable", run)
    baseline = ctx.get("release_changelog_text")
    if not isinstance(baseline, str) or not baseline.strip():
        return answer_with_provenance(False, "fixture missing: seeded changelog baseline is empty", run)
    try:
        live_release = plane.releases.retrieve(
            workspace_slug=ctx["workspace_slug"],
            release_id=release_id,
        )
        response = plane.releases.changelog.retrieve(
            workspace_slug=ctx["workspace_slug"],
            release_id=release_id,
        )
    except Exception as exc:
        if is_verifier_not_found(exc):
            return answer_with_provenance(
                False,
                f"seeded release/changelog no longer exists at verification ({exc})",
                run,
            )
        raise_verifier_read_error("C2", f"reading release {release_id} and its changelog", exc)
    live_release_name = str(getattr(live_release, "name", None) or "").strip()
    if live_release_name != expected_release:
        return answer_with_provenance(
            False,
            f"release name was mutated after seeding: live={live_release_name!r}; baseline={expected_release!r}",
            run,
        )
    live = normalize_changelog_text(response)
    if live != baseline:
        if not live:
            mutation_note = "changelog was mutated after seeding: live changelog is empty"
        else:
            mutation_note = f"changelog was mutated after seeding: live={live!r}; baseline={baseline!r}"
        return answer_with_provenance(False, mutation_note, run)
    shipped = changelog_items(baseline)
    if not shipped:
        return answer_with_provenance(
            False,
            f"fixture missing: seeded changelog baseline has no parseable entries: {baseline!r}",
            run,
        )
    if not reports_contract_values(final_text, "shipped", shipped):
        ok = False
        notes.append(f"shipped values={contract_values(final_text, 'shipped')!r}; want {shipped!r}")
    else:
        notes.append(f"{len(shipped)} exact shipped items")
    return answer_with_provenance(ok, "; ".join(notes), run)


def _bind_c2(ctx: dict[str, Any]) -> dict[str, str]:
    release = ctx.get("release") or {}
    name = ctx.get("release_name") or (release.get("name") if isinstance(release, dict) else None)
    return {"release_name": str(name or "")}


C2_TASK: dict[str, Any] = {
    "id": "C2",
    "tags": {"read"},
    "prompt": (
        "What shipped in release {release_name}? Summarize the changelog in any prose "
        "you like, then provide these exact contract lines: 'release: {release_name}' "
        "and one 'shipped: <verbatim changelog item text>' line per changelog item. "
        "For each 'shipped:' value, copy only the text after the changelog entry label, "
        "without its sentence-ending punctuation."
    ),
    "prompt_bind": _bind_c2,
    "needs": {"release"},
    "verify": verify_c2,
}


CROSS_TASKS: list[dict[str, Any]] = [C1_TASK, C2_TASK]


__all__ = ["CROSS_TASKS", "verify_c1", "verify_c2"]
