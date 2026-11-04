"""Neutral evaluation control-flow exceptions."""

from __future__ import annotations


def describe_exception(exc: BaseException, *, limit: int = 4) -> str:
    """Render an exception for a result row, flattening any ExceptionGroup.

    An ExceptionGroup's own message names only how many sub-exceptions it holds, so recording
    ``f"{type(exc).__name__}: {exc}"`` on one throws the diagnosis away: an OpenAI 400 naming
    the exact unsupported parameter was persisted as "unhandled errors in a TaskGroup
    (1 sub-exception)", and finding it again meant reproducing the call by hand. anyio wraps
    everything the driver does in a task group, so this is the normal shape here, not an edge
    case. Nested groups are flattened; ``limit`` bounds a pathological fan-out.
    """
    leaves: list[str] = []

    def walk(node: BaseException) -> None:
        subs = getattr(node, "exceptions", None)
        if subs:
            for sub in subs:
                if len(leaves) >= limit:
                    return
                walk(sub)
            return
        leaves.append(f"{type(node).__name__}: {node}")

    walk(exc)
    if not leaves:
        return f"{type(exc).__name__}: {exc}"
    head = f"{type(exc).__name__}: {exc}" if getattr(exc, "exceptions", None) else ""
    body = " | ".join(leaves)
    return f"{head} -> {body}" if head else body


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
