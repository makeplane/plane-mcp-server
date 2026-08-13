"""Human-readable plans for evaluation fixture creation."""

from __future__ import annotations

from .customers import CUSTOMER_NAME, CUSTOMER_REQUEST_NAME
from .cycles import CYCLE_CURRENT, CYCLE_PAST
from .intake import INTAKE_BILLING_TITLE, INTAKE_SPAM_TITLE
from .labels import LABEL_NAMES
from .modules import MODULE_COMPLETED_TITLES, MODULE_NAME
from .releases import RELEASE_NAME
from .work_items import (
    CHECKOUT_TIMEOUT_TITLE,
    DUE_THIS_WEEK_TITLES,
    PAYMENT_WEBHOOK_TITLE,
    WORK_ITEM_FIXTURES,
)


def seed_plan(needs: set[str]) -> list[str]:
    """Human-readable seed plan for --dry-run (no network)."""
    lines = [
        "project: EVAL {run8} (identifier EV{XXXX})",
    ]
    if "items" in needs:
        lines.append(f"items: {len(WORK_ITEM_FIXTURES)} work items (exactly 4 urgent open)")
        lines.append(f"  - {PAYMENT_WEBHOOK_TITLE!r} (urgent, non-default started-group state)  # R1 target")
        lines.append(f"  - {len(DUE_THIS_WEEK_TITLES)} assigned-to-me with due this week  # R3")
        lines.append(f"  - comments on {CHECKOUT_TIMEOUT_TITLE!r}  # R5 discussion")
    if "activity_feed" in needs:
        lines.append(
            f"activity_feed: gate that activities exist for {CHECKOUT_TIMEOUT_TITLE!r} "
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
