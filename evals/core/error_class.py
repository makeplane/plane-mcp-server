"""What kind of "no" a tool call received.

One errored-call count answers three unrelated questions at once, and the answers
pull in different directions:

  refused    the server turned the call away without acting on it -- a required
             field missing, an argument the action does not take, a value outside
             an enum. Entirely a property of the tool schema. The API was never
             asked anything.
  rejected   the call was well formed and the API refused its meaning: an
             undocumented precondition, a conflict. This is where tool-design
             defects live, and it is the number worth reading.
  not_found  a read that came back absent. Usually an answer rather than an
             obstacle -- "is there an estimate on this project?" has no cheaper
             form than asking -- so it is reported apart from friction.
  denied     credentials or plan. Says nothing about the tool surface.
  failed     the server or transport broke.

Classification runs where the payload still exists (the proxy), and stores only
the category, never the text.

Deliberately not coupled to this server: the categories are read off HTTP status
and the FastMCP/Pydantic validation shape, both of which any MCP server over a
REST API produces. Two patterns for this server's own refusal wording are
additive -- a foreign surface that does not match them still classifies by
status. That is what let one battery score both a 28-tool and a 177-tool server.
"""

from __future__ import annotations

import re

REFUSED = "refused"
REJECTED = "rejected"
NOT_FOUND = "not_found"
DENIED = "denied"
FAILED = "failed"
UNCLASSIFIED = "unclassified"

ERROR_CLASSES = (REFUSED, REJECTED, NOT_FOUND, DENIED, FAILED, UNCLASSIFIED)

#: Classes counted as friction attributable to the tool surface's design.
SURFACE_FRICTION_CLASSES = (REJECTED,)
#: Classes counted as the cost of navigating the schema rather than the API.
NAVIGATION_CLASSES = (REFUSED,)

_STATUS = re.compile(r"\b(?:HTTP|status(?:[ _]code)?[:= ]*)\s*(\d{3})\b", re.IGNORECASE)

# Shapes any FastMCP server emits when a call fails its own signature, before
# the tool body runs.
_VALIDATION = (
    "validation error for",
    "missing required argument",
    "input should be",
    "unexpected keyword argument",
)

# This server's own refusals, which are deliberate answers rather than failures
# of validation, so they carry no status and no pydantic shape.
_OWN_REFUSALS = (
    "requires an action. it takes:",
    "does not take:",
)

_BY_STATUS = {
    400: REJECTED,
    409: REJECTED,
    422: REJECTED,
    401: DENIED,
    402: DENIED,
    403: DENIED,
    404: NOT_FOUND,
}


def detect_refusal(payload: str | None) -> str | None:
    """Return ``REFUSED`` for a refusal that arrived flagged as a *successful* result.

    This server answers a malformed call with a plain result whose text begins
    "Error: ", so the protocol reports success and a caller counting failures sees
    none -- about 47 per 35-task battery. Classifying those anyway keeps the metric
    honest without asking the server to change what every agent receives.

    Deliberately narrow. Only wording this server owns counts, and the stray-argument
    form must carry both of its halves, so an ordinary tool result that happens to
    quote one phrase is not miscounted as a refusal.
    """
    text = (payload or "").lower()
    if "requires an action. it takes:" in text:
        return REFUSED
    if "does not take:" in text and "it takes:" in text:
        return REFUSED
    return None


def classify_error(payload: str | None) -> str:
    """Return the category of a failed call from its error payload.

    Status wins over wording: a 404 whose body happens to mention a missing
    argument is still an absent resource. Only when no status is present does the
    validation shape decide, because that is the case where the call never
    reached the API at all.
    """
    text = (payload or "").strip()
    if not text:
        return UNCLASSIFIED
    lowered = text.lower()

    match = _STATUS.search(text)
    if match:
        status = int(match.group(1))
        if status in _BY_STATUS:
            return _BY_STATUS[status]
        if 500 <= status <= 599:
            return FAILED
        if 400 <= status <= 499:
            return REJECTED

    if any(marker in lowered for marker in _OWN_REFUSALS):
        return REFUSED
    if any(marker in lowered for marker in _VALIDATION):
        return REFUSED
    return UNCLASSIFIED


__all__ = [
    "ERROR_CLASSES",
    "detect_refusal",
    "NAVIGATION_CLASSES",
    "SURFACE_FRICTION_CLASSES",
    "classify_error",
    "DENIED",
    "FAILED",
    "NOT_FOUND",
    "REFUSED",
    "REJECTED",
    "UNCLASSIFIED",
]
