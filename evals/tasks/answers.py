"""Answer-contract matching for task verifiers."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any


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


__all__ = [
    "contract_values",
    "get_final_text",
    "reports_contract_int",
    "reports_contract_value",
    "reports_contract_values",
    "reports_exact_int",
    "whole_answer_int",
    "word_boundary",
]
