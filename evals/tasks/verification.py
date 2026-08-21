"""Shared verifier failure semantics."""

from __future__ import annotations

from typing import NoReturn

from plane.errors.errors import HttpError


class VerifierReadError(RuntimeError):
    """An infrastructure failure while a verifier was reading authoritative state."""


def is_verifier_not_found(exc: BaseException) -> bool:
    """Return whether a verifier read got an authoritative HTTP 404 response."""
    return isinstance(exc, HttpError) and exc.status_code == 404


def raise_verifier_read_error(task_id: str, reading: str, exc: BaseException) -> NoReturn:
    """Raise a diagnosable infrastructure error for a required verifier API read."""
    raise VerifierReadError(f"{task_id} verifier read failed while {reading}: {type(exc).__name__}: {exc}") from exc


__all__ = ["VerifierReadError", "is_verifier_not_found", "raise_verifier_read_error"]
