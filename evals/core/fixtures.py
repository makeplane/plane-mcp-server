"""Neutral fixture names shared by seeders, task prompts, and cleanup.

This module must not import either :mod:`evals.seed` or :mod:`evals.tasks`; both
packages re-export these names for backward compatibility.
"""

from __future__ import annotations

from collections.abc import Iterator

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


# Project names. Nothing here may look like an id.
#
# Seeded projects used to be called "EVAL 3c128f21". An agent is told only the project name
# and has to resolve it to a UUID, since project_id is required by 121 of the 183 actions —
# and a weaker model skipped the resolution and submitted a hex-looking substring as the id.
# Moving the hex into parentheses made it worse, not better: it became a cleaner token to
# extract, and non-UUID project_id attempts went from 4 to 17 across six repetitions.
#
# So the name carries no hex at all. Teardown deletes by recorded project_id, so per-run
# uniqueness in the *name* is not required for correctness; the word suffix exists only so a
# leftover project from a crashed run cannot make an agent's name lookup ambiguous, and so R6
# can tell its two projects apart. `python -m evals.cleanup --prefix "EVAL "` still matches.
EVAL_PROJECT_PREFIX = "EVAL "
PROJECT_TITLES = ("Delivery Planning", "Platform Migration")
# 64 words: one per run, derived from the seed so a name is reproducible from its run id.
PROJECT_SUFFIX_WORDS = (
    "Kestrel",
    "Osprey",
    "Falcon",
    "Harrier",
    "Merlin",
    "Kite",
    "Buzzard",
    "Goshawk",
    "Heron",
    "Egret",
    "Curlew",
    "Plover",
    "Godwit",
    "Dunlin",
    "Sanderling",
    "Turnstone",
    "Petrel",
    "Fulmar",
    "Gannet",
    "Guillemot",
    "Razorbill",
    "Puffin",
    "Skua",
    "Tern",
    "Swift",
    "Martin",
    "Swallow",
    "Wagtail",
    "Pipit",
    "Dipper",
    "Wren",
    "Dunnock",
    "Redstart",
    "Whinchat",
    "Wheatear",
    "Fieldfare",
    "Redwing",
    "Blackcap",
    "Chiffchaff",
    "Firecrest",
    "Treecreeper",
    "Nuthatch",
    "Jackdaw",
    "Chough",
    "Raven",
    "Rook",
    "Magpie",
    "Jay",
    "Linnet",
    "Twite",
    "Redpoll",
    "Siskin",
    "Crossbill",
    "Hawfinch",
    "Brambling",
    "Yellowhammer",
    "Corncrake",
    "Lapwing",
    "Woodcock",
    "Snipe",
    "Avocet",
    "Oystercatcher",
    "Shelduck",
    "Wigeon",
)


def _suffix_word_index(run_prefix: str) -> int:
    """Map a run prefix onto ``PROJECT_SUFFIX_WORDS``, tolerating non-hex prefixes."""
    try:
        return int(str(run_prefix)[:8], 16)
    except ValueError:
        return sum(ord(ch) for ch in str(run_prefix))


def eval_project_name(run_prefix: str, *, second: bool = False) -> str:
    """Build a seeded project's display name: readable, and never id-shaped.

    Deterministic in ``run_prefix`` so the same run always produces the same name, which
    keeps a resumed run and its teardown in agreement.
    """
    word = PROJECT_SUFFIX_WORDS[_suffix_word_index(run_prefix) % len(PROJECT_SUFFIX_WORDS)]
    title = PROJECT_TITLES[1 if second else 0]
    return f"{EVAL_PROJECT_PREFIX}{title} {word}"


def eval_project_name_variants(run_prefix: str, *, second: bool = False) -> Iterator[str]:
    """Yield the deterministic name, then every other word in order.

    The first name is exactly ``eval_project_name(run_prefix, second=second)``, so a
    resumed run and its teardown still agree on it. The rest exist only so a leftover
    project from a crashed run cannot fail a fresh one: Plane rejects a duplicate project
    name with a 409, and the word pool is small enough that residue makes that collision
    a matter of when. Walking forward from the deterministic index keeps the fallback
    order reproducible too.
    """
    words = PROJECT_SUFFIX_WORDS
    start = _suffix_word_index(run_prefix) % len(words)
    title = PROJECT_TITLES[1 if second else 0]
    for offset in range(len(words)):
        yield f"{EVAL_PROJECT_PREFIX}{title} {words[(start + offset) % len(words)]}"


__all__ = [
    "EVAL_PROJECT_PREFIX",
    "PROJECT_SUFFIX_WORDS",
    "PROJECT_TITLES",
    "eval_project_name",
    "eval_project_name_variants",
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
