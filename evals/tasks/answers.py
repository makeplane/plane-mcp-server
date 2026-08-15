"""Answer-contract matching for task verifiers."""

from __future__ import annotations

import re
from collections import Counter
from html import unescape
from typing import Any

from evals.evidence import TARGET_ENTITY_EVIDENCE


def word_boundary(value: str) -> re.Pattern[str]:
    """Compile a case-insensitive word-boundary match for an exact seeded value."""
    return re.compile(rf"\b{re.escape(value)}\b", re.IGNORECASE)


def reports_exact_int(text: str, n: int) -> bool:
    """True when ``text`` contains integer ``n`` as a whole word (not a substring of 10)."""
    return bool(word_boundary(str(int(n))).search(text or ""))


def whole_answer_int(text: str) -> int | None:
    """If the answer (or its last non-empty line) is exactly an integer, return it.

    Letters must not appear — only surrounding whitespace/punctuation is ignored —
    so prose like ``There are 3 comments…`` is not a whole-answer int. A **leading
    minus** attached to the number is preserved (``-3`` → -3, not 3).
    """

    def _as_int(s: str) -> int | None:
        # Collapse whitespace; then the whole string must be optional sign + digits
        # with only non-word punctuation wrappers (prefix must not eat the sign).
        compact = re.sub(r"\s+", "", s or "")
        m = re.fullmatch(r"[^\w+-]*([+-]?\d+)[^\w+-]*", compact, flags=re.UNICODE)
        if m:
            return int(m.group(1))
        return None

    blob = text or ""
    v = _as_int(blob)
    if v is not None:
        return v
    lines = [ln for ln in blob.splitlines() if ln.strip()]
    if lines:
        return _as_int(lines[-1])
    return None


def reports_contract_int(text: str, truth: int) -> bool:
    """True when final text reports ``truth`` via the explicit ``count: N`` contract.

    1. Scan lines matching ``^count:\\s*(-?\\d+)\\s*$`` (case-insensitive, surrounding
       whitespace allowed). Use the **last** match; require signed equality with
       ``truth``.
    2. Fallback: whole-answer / last-line bare integer (:func:`whole_answer_int`).
    3. No match at all → False (ignoring an explicit format instruction is a fail).
    """
    last: int | None = None
    for line in (text or "").splitlines():
        m = re.fullmatch(r"\s*count:\s*(-?\d+)\s*", line, flags=re.IGNORECASE)
        if m:
            last = int(m.group(1))
    if last is not None:
        return last == int(truth)
    whole = whole_answer_int(text)
    if whole is not None:
        return whole == int(truth)
    return False


def contract_values(text: str, field: str) -> list[str]:
    """Return non-empty values from exact ``field: value`` contract lines.

    The field name is case-insensitive, as with :func:`reports_contract_int`,
    while the value is preserved for exact comparison. Prose, bullets, inline
    mentions, and malformed/empty contract lines are ignored.
    """
    values: list[str] = []
    pattern = re.compile(rf"\s*{re.escape(field)}:\s*(.*?)\s*", flags=re.IGNORECASE)
    for line in (text or "").splitlines():
        match = pattern.fullmatch(line)
        if match and match.group(1):
            values.append(match.group(1))
    return values


def reports_contract_value(text: str, field: str, truth: str) -> bool:
    """True when exactly one ``field: value`` line equals ``truth`` exactly."""
    return contract_values(text, field) == [str(truth)]


def reports_contract_values(text: str, field: str, truths: list[str] | tuple[str, ...]) -> bool:
    """True when contract lines equal the expected value multiset.

    Ordering is deliberately ignored: the output contract defines one exact
    fact per line, not a presentation order. Missing, duplicate, or extra field
    lines fail.
    """
    return Counter(contract_values(text, field)) == Counter(str(value) for value in truths)


def get_final_text(run: dict[str, Any]) -> str:
    return run.get("final_text") or ""


def normalize_rich_text(value: Any) -> str:
    """Return exact comparable text from a rich-text API model, mapping, or string.

    Prefer authoritative stripped fields when the API exposes them, then normalize HTML
    entities, tags, and whitespace. Case and punctuation remain significant.
    """

    def field(name: str) -> Any:
        return value.get(name) if isinstance(value, dict) else getattr(value, name, None)

    candidates = (
        value if isinstance(value, str) else None,
        field("comment_stripped"),
        field("description_stripped"),
        field("comment_html"),
        field("description_html"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            without_tags = re.sub(r"<[^>]*>", " ", candidate)
            return " ".join(unescape(without_tags).split())
    return ""


def has_response_evidence(run: dict[str, Any], label: str = TARGET_ENTITY_EVIDENCE) -> bool:
    """Return whether a successful Plane response exposed the target's hidden fact.

    Tool identity is deliberately irrelevant. The transport records only non-sensitive
    sentinel labels after matching the in-memory response; no response body is required.
    """
    calls = run.get("calls")
    if not isinstance(calls, list):
        return False
    return any(
        isinstance(call, dict) and not bool(call.get("is_error")) and label in (call.get("observed_sentinels") or [])
        for call in calls
    )


def answer_with_provenance(
    answer_correct: bool,
    answer_note: str,
    run: dict[str, Any],
) -> tuple[bool, str]:
    """Combine answer correctness with route-agnostic response evidence.

    The two facts stay separate in the note. A successful unrelated call has no target
    label and therefore cannot satisfy provenance.
    """
    calls = run.get("calls")
    source = str(run.get("call_source") or "unknown")
    driver_notes = run.get("driver_notes")
    trace_incomplete = isinstance(driver_notes, list) and any(
        isinstance(note, str) and note.startswith("proxy_sidecar_incomplete") for note in driver_notes
    )
    available = bool(run.get("evidence_trace_available"))
    provenance = not trace_incomplete and has_response_evidence(run)
    if trace_incomplete:
        provenance_note = f"trace incomplete (source={source}; proxy sidecar was not authoritative)"
    elif provenance:
        provenance_note = f"observed target-entity response evidence (source={source})"
    elif not available:
        provenance_note = f"unavailable (source={source}; response-evidence matching was not active)"
    elif isinstance(calls, list) and calls:
        successful = sum(1 for call in calls if isinstance(call, dict) and not bool(call.get("is_error")))
        provenance_note = (
            f"missing (0 evidence-bearing of {successful} successful Plane calls; {len(calls)} total; source={source})"
        )
    else:
        provenance_note = f"missing (0 Plane calls observed; source={source})"
    note = f"answer_correct={str(bool(answer_correct)).lower()} ({answer_note}); provenance={provenance_note}"
    return bool(answer_correct) and provenance, note


__all__ = [
    "contract_values",
    "answer_with_provenance",
    "get_final_text",
    "has_response_evidence",
    "normalize_rich_text",
    "reports_contract_int",
    "reports_contract_value",
    "reports_contract_values",
    "reports_exact_int",
    "whole_answer_int",
    "word_boundary",
]
