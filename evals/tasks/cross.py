"""Cross-entity task definitions and their verifiers."""

from __future__ import annotations

from typing import Any

from evals.seed import (
    CUSTOMER_NAME,
    CUSTOMER_REQUEST_NAME,
    R1_TITLE,
    RELEASE_CHANGELOG_TEXT,
    RELEASE_NAME,
)
from evals.tasks.common import as_id, find_item_by_name, get_final_text, ids, word_boundary


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

    customers = plane.customers.list(workspace_slug=workspace_slug)
    rows = customers.results if hasattr(customers, "results") else customers
    # Exact name only — do not match arbitrary acme* customers.
    acme = next((c for c in (rows or []) if (c.name or "").strip() == CUSTOMER_NAME), None)
    if acme is None:
        return False, f"customer {CUSTOMER_NAME!r} not found"

    # Track for teardown if agent-created
    if not ctx.get("customer"):
        ctx.setdefault("workspace_objects", []).append({"kind": "customer", "id": acme.id})

    reqs = plane.customers.requests.list(workspace_slug=workspace_slug, customer_id=acme.id)
    rrows = reqs.results if hasattr(reqs, "results") else reqs
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
        wi = plane.customers.work_items.list(workspace_slug=workspace_slug, customer_id=acme.id)
        wi_rows = list(wi.results if hasattr(wi, "results") else wi or [])
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
        ok = False
        notes.append(f"list customer work items failed: {exc}")

    return ok, "; ".join(notes)


C1_TASK: dict[str, Any] = {
    "id": "C1",
    "tags": {"write", "tier1"},
    "prompt": (
        f"Create customer '{CUSTOMER_NAME}' (if it does not already exist), add a "
        f"request named '{CUSTOMER_REQUEST_NAME}', and link that request to the work "
        f"item '{R1_TITLE}' in project {{project}}."
    ),
    "optimal_calls": 4,
    "optimal_tools": {
        "list_customers",
        "create_customer",
        "create_customer_request",
        "list_work_items",
    },
    "alternate_tools": {
        "retrieve_customer",
        "manage_customer_work_items",
        "list_customer_requests",
        "list_customer_work_items",
        "search_work_items",
        "list_projects",
    },
    "surface_tools": {
        "v2": {
            "optimal_calls": 4,
            "optimal_tools": {
                "list_customers",
                "create_customer",
                "log_customer_request",
                "link_customer_work_items",
            },
            "alternate_tools": {
                "get_customer",
                "find_work_items",
                "update_customer",
                "search_projects",
            },
        },
    },
    # No pre-seeded customer — agent creates; items needed for link target.
    "needs": {"items"},
    "verify": verify_c1,
}


async def verify_c2(plane: Any, ctx: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    """C2: final text mentions release 1.2.0 and at least one seeded changelog phrase."""
    final_text = get_final_text(run)
    notes: list[str] = []
    ok = True
    if not word_boundary(RELEASE_NAME).search(final_text):
        ok = False
        notes.append(f"missing release name {RELEASE_NAME!r}")
    else:
        notes.append(f"names {RELEASE_NAME}")
    changelog = ctx.get("release_changelog_text") or RELEASE_CHANGELOG_TEXT
    # Match distinctive fragments from the seeded changelog.
    fragments = ["OAuth login hardening", "webhook retry backoff"]
    hit = [f for f in fragments if word_boundary(f).search(final_text)]
    if not hit:
        # Also accept substring of full changelog without word-boundary if short.
        if changelog[:40].casefold() not in final_text.casefold():
            ok = False
            notes.append("missing changelog content")
        else:
            notes.append("changelog substring present")
    else:
        notes.append(f"changelog phrases {hit}")
    return ok, "; ".join(notes)


C2_TASK: dict[str, Any] = {
    "id": "C2",
    "tags": {"read", "tier1"},
    "prompt": (f"What shipped in release {RELEASE_NAME}? Summarize the changelog."),
    "optimal_calls": 2,
    "optimal_tools": {"list_releases", "get_release_changelog"},
    "alternate_tools": {
        "retrieve_release",
        "list_release_work_items",
        "update_release_changelog",
    },
    "surface_tools": {
        "v2": {
            "optimal_calls": 1,
            "optimal_tools": {"get_release"},
            "alternate_tools": {
                "list_releases",
                "assign_to_release",
                "get_workspace_context",
            },
        },
    },
    "needs": {"release"},
    "verify": verify_c2,
}


CROSS_TASKS: list[dict[str, Any]] = [C1_TASK, C2_TASK]


__all__ = ["CROSS_TASKS", "verify_c1", "verify_c2"]
