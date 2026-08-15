"""Neutral fixture names shared by seeders, task prompts, and cleanup.

This module must not import either :mod:`evals.seed` or :mod:`evals.tasks`; both
packages re-export these names for backward compatibility.
"""

from __future__ import annotations

CUSTOMER_NAME = "Acme Corp"
CUSTOMER_REQUEST_NAME = "SSO support"
EVALUATION_CUSTOMER_PROPERTY_NAME = "Eval Industry"
_EVALUATION_CUSTOMER_NAMES = {CUSTOMER_NAME.casefold(), "acme"}

RELEASE_NAME = "1.2.0"
RELEASE_CHANGELOG_TEXT = "Changelog entry one: OAuth login hardening. Changelog entry two: webhook retry backoff."
EVALUATION_RELEASE_TAG_VERSION = "eval-rc1"

INTAKE_BILLING_TITLE = "Billing: invoice PDF missing line items"
INTAKE_SPAM_TITLE = "SPAM: cheap crypto pumps guaranteed"

CYCLE_PAST = "Sprint 12"
CYCLE_CURRENT = "Sprint 13"

MODULE_NAME = "Checkout revamp"
MODULE_COMPLETED_TITLES = (
    "Module done: cart totals",
    "Module done: tax lines",
    "Module done: shipping quote",
)

# Fixed fixture titles for the ``items`` group. Exactly four are urgent.
WORK_ITEM_FIXTURES: list[tuple[str, str]] = [
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

PAYMENT_WEBHOOK_TITLE = WORK_ITEM_FIXTURES[0][0]
CHECKOUT_TIMEOUT_TITLE = "Checkout times out on 3DS challenge"
CHECKOUT_COMMENT_PHRASES = (
    "stripe callback race",
    "retry budget exhausted",
)
SIDEBAR_TITLE = "Sidebar collapse flickers on resize"
DARK_MODE_TITLE = "Dark mode contrast fails WCAG AA"
BLOCKING_SOURCE_TITLE = "Search results ignore archived projects"
BLOCKING_TARGET_TITLE = "CSV export truncates multi-byte chars"
BLOCKING_REFERENCE_ADDRESS = "https://example.com/eval/runbook-w7"
DUE_THIS_WEEK_TITLES = (
    "Webhook secret rotation docs missing",
    "Onboarding email template stale",
)
UNFINISHED_CYCLE_TITLES = (
    "Inventory count goes negative under load",
    "Tooltip clipped inside modal dialog",
)

# Historical public aliases used by task catalog modules and downstream scripts.
DEBIAS_CUSTOMER_PROP_DISPLAY = EVALUATION_CUSTOMER_PROPERTY_NAME
DEBIAS_RELEASE_TAG_VERSION = EVALUATION_RELEASE_TAG_VERSION
ITEM_FIXTURES = WORK_ITEM_FIXTURES
R1_TITLE = PAYMENT_WEBHOOK_TITLE
R3_DUE_TITLES = DUE_THIS_WEEK_TITLES
R5_COMMENT_PHRASES = CHECKOUT_COMMENT_PHRASES
R5_TITLE = CHECKOUT_TIMEOUT_TITLE
W2_TITLE = SIDEBAR_TITLE
W3_TITLE = DARK_MODE_TITLE
W6_UNFINISHED_TITLES = UNFINISHED_CYCLE_TITLES
W7_SOURCE_TITLE = BLOCKING_SOURCE_TITLE
W7_TARGET_TITLE = BLOCKING_TARGET_TITLE
W7_URL = BLOCKING_REFERENCE_ADDRESS
W8_TITLE = PAYMENT_WEBHOOK_TITLE


def is_evaluation_customer_name(name: str | None) -> bool:
    """Return whether a customer name matches an eval fixture alias."""
    return (name or "").strip().casefold() in _EVALUATION_CUSTOMER_NAMES


__all__ = [
    "BLOCKING_REFERENCE_ADDRESS",
    "BLOCKING_SOURCE_TITLE",
    "BLOCKING_TARGET_TITLE",
    "CHECKOUT_COMMENT_PHRASES",
    "CHECKOUT_TIMEOUT_TITLE",
    "CUSTOMER_NAME",
    "CUSTOMER_REQUEST_NAME",
    "CYCLE_CURRENT",
    "CYCLE_PAST",
    "DARK_MODE_TITLE",
    "DEBIAS_CUSTOMER_PROP_DISPLAY",
    "DEBIAS_RELEASE_TAG_VERSION",
    "DUE_THIS_WEEK_TITLES",
    "EVALUATION_CUSTOMER_PROPERTY_NAME",
    "EVALUATION_RELEASE_TAG_VERSION",
    "INTAKE_BILLING_TITLE",
    "INTAKE_SPAM_TITLE",
    "ITEM_FIXTURES",
    "MODULE_COMPLETED_TITLES",
    "MODULE_NAME",
    "PAYMENT_WEBHOOK_TITLE",
    "R1_TITLE",
    "R3_DUE_TITLES",
    "R5_COMMENT_PHRASES",
    "R5_TITLE",
    "RELEASE_CHANGELOG_TEXT",
    "RELEASE_NAME",
    "SIDEBAR_TITLE",
    "UNFINISHED_CYCLE_TITLES",
    "W2_TITLE",
    "W3_TITLE",
    "W6_UNFINISHED_TITLES",
    "W7_SOURCE_TITLE",
    "W7_TARGET_TITLE",
    "W7_URL",
    "W8_TITLE",
    "WORK_ITEM_FIXTURES",
    "is_evaluation_customer_name",
]
