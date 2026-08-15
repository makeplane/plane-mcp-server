"""Neutral evaluation control-flow exceptions."""

from __future__ import annotations


class TaskSkipped(Exception):
    """A task that cannot run in this environment without blaming the agent."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason)
        super().__init__(self.reason)


__all__ = ["TaskSkipped"]
