"""Task skip signal."""


class TaskSkipped(Exception):
    """Verifier signals that this task-rep should be recorded as skipped, not failed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


__all__ = ["TaskSkipped"]
