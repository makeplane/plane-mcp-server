"""What kind of wrong a failed task was.

One failed verifier answers several unrelated questions at once. In one measured
battery, ``W7`` put a link on the wrong work item, ``I1`` set ``urgent`` where
``high`` was asked, and ``S2`` spent 43 calls and produced nothing -- three
different defects, reported identically, separable only by reading notes by hand.

  unproven       the answer was right and the run could not evidence it. Not an
                 agent defect at all, and the largest single family in the recorded
                 corpus, so folding it into the others would misattribute most
                 failures to the model.
  wrong_value    a value was written or reported, and it differs from the one asked
                 for.
  missing_write  the thing was never created; the verifier found nothing.
  partial_write  a multi-part change half landed -- the shape that hides a wrong
                 target, since writing correctly to the wrong entity leaves the
                 right entity empty.
  abandoned      the run hit its iteration or token ceiling, so the note describes
                 an unfinished state rather than a defect.
  environment    a capability the environment does not have. Not a defect either.

Same contract as ``error_class``: a narrow pattern table over text the verifiers
own, and ``unclassified`` is a first-class member that is counted and printed. A
zero in some kind must mean "none of these", never "the classifier did not
recognise it".

Deliberately note-only. Whether a write went to the *wrong target* is not knowable
from a note that reports the right target as empty -- that needs call arguments,
which is a different measurement.
"""

from __future__ import annotations

import re

UNPROVEN = "unproven"
WRONG_VALUE = "wrong_value"
MISSING_WRITE = "missing_write"
PARTIAL_WRITE = "partial_write"
ABANDONED = "abandoned"
ENVIRONMENT = "environment"
UNCLASSIFIED = "unclassified"

FAILURE_KINDS = (
    UNPROVEN,
    WRONG_VALUE,
    MISSING_WRITE,
    PARTIAL_WRITE,
    ABANDONED,
    ENVIRONMENT,
    UNCLASSIFIED,
)

#: Kinds that are properties of the run or the environment, not of the agent.
NON_DEFECT_KINDS = (UNPROVEN, ENVIRONMENT, ABANDONED)

#: stop_reason values that mean the run was cut off rather than finished.
_CAPPED_STOP_REASONS = frozenset({"max_turns", "max_tokens", "max_iterations"})

#: The verifier states its verdict before its evidence, so these settle the note.
_ANSWER_CORRECT = "answer_correct=true"
_ANSWER_WRONG = "answer_correct=false"

#: Wording for something the verifier looked for and did not find.
_ABSENT = (
    "not found",
    "was not created",
    "missing",
    "have []",
)

#: Wording for something it did find. Only meaningful next to an absence, where the
#: pair means a change landed in part.
_PRESENT = (
    " present",
    "names ",
    " linked",
)

#: A stated expectation, which implies a value was compared rather than absent.
_EXPECTATION = ("(want ", "want ")

#: Absence phrasings that carry no "missing"/"not found" wording. Narrow on purpose --
#: a bare "not " would swallow "not closed: end_date=X (want Y)", which is a value
#: mismatch rather than an absence.
_ABSENT_PATTERN = re.compile(r"\bno \d|\bno comments\b|\bnot archived\b|\bnot created\b")


def classify_failure(
    note: str | None,
    *,
    stop_reason: str | None = None,
    hit_max_iterations: bool = False,
) -> str:
    """Return the kind of failure a verifier note describes.

    Structural signals win over the note: a run that hit its ceiling has an
    unfinished state to report regardless of what the note says about it.
    """
    text = (note or "").strip()
    lowered = text.lower()
    capped = hit_max_iterations or (stop_reason or "").strip().lower() in _CAPPED_STOP_REASONS

    if lowered.startswith("env:"):
        return ENVIRONMENT

    # A proven wrong answer outranks the cap. Running out of iterations explains why a
    # run stopped, not why what it wrote was wrong, and calling that combination
    # "abandoned" would file a demonstrated defect as a non-defect.
    #
    # The false marker is tested first because the true one is searched anywhere in the
    # note, and a wrong value quoted back by the verifier can itself contain the string.
    if _ANSWER_WRONG in lowered:
        return WRONG_VALUE
    if _ANSWER_CORRECT in lowered:
        return UNPROVEN

    if capped:
        return ABANDONED
    if not text:
        return UNCLASSIFIED

    absent = bool(_ABSENT_PATTERN.search(lowered)) or any(marker in lowered for marker in _ABSENT)
    present = any(marker in lowered for marker in _PRESENT)
    if absent and present:
        return PARTIAL_WRITE
    if absent:
        # Checked before the expectation markers on purpose. "missing X ... (want 5)"
        # is nothing written, not a wrong value.
        return MISSING_WRITE
    if any(marker in lowered for marker in _EXPECTATION):
        return WRONG_VALUE
    return UNCLASSIFIED


__all__ = [
    "ABANDONED",
    "ENVIRONMENT",
    "FAILURE_KINDS",
    "MISSING_WRITE",
    "NON_DEFECT_KINDS",
    "PARTIAL_WRITE",
    "UNCLASSIFIED",
    "UNPROVEN",
    "WRONG_VALUE",
    "classify_failure",
]
