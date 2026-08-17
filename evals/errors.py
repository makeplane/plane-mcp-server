"""Neutral evaluation control-flow exceptions."""

from __future__ import annotations


class TaskSkipped(Exception):
    """A task that cannot run in this environment without blaming the agent.

    ``reason`` is matched exactly by the skip taxonomy and must stay stable, so the
    refusal that caused the skip travels in ``detail`` instead. Without it, an
    intermittent gate is unexplainable after the fact: the status code that would say
    whether it was a plan limit, a feature toggle, or a transient failure is gone.
    """

    def __init__(self, reason: str, *, detail: str | None = None) -> None:
        self.reason = str(reason)
        self.detail = str(detail) if detail else None
        super().__init__(self.reason if not self.detail else f"{self.reason} ({self.detail})")


__all__ = ["TaskSkipped"]
