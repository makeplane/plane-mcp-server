"""How often an agent goes looking for an entity it has already resolved.

The sharpest single finding of the 2026-08-24 cross-harness pair was
``workitem.search`` 111 vs 26 against ``workitem.retrieve`` 4 vs 20. One arm carried
resolved ids across turns; the other went looking again each time. That is a
property of the **surface** as much as of the agent -- identifiers that stayed
sticky would close the gap without either agent changing -- and nothing measured it.

The rule is deliberately narrow, because the first version was not and counted
things no reasonable reader would call redundant:

  same entity   only the resource's *own* identifier counts. ``project_id`` on a
                work item call says where to look, not which item is known, and
                treating it as "in hand" flagged every list in a project.
  not paging    a lookup carrying a cursor or offset is continuing one traversal,
                not starting a second.
  args only     ids are read from request arguments. An id that arrived in a
                *result* is invisible here, so this undercounts rather than over.

What survives is still a heuristic: retrieving item A and then searching for an
unrelated item B of the same resource is counted, because nothing in the arguments
distinguishes that from re-hunting A. Read it as an upper bound on identifier
stickiness, not as a defect count.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field

from evals.core.results import CallRecord, TaskResult

from .load import ResultRow, is_meta_row, read_result

LOOKUP_REUSE_LIMITATION = (
    "limitation: counts a search/list on a resource whose own id was already an argument, "
    "excluding paginated continuations. It cannot tell re-hunting the same entity from looking "
    "up a different one of the same kind, and ids that arrived only in results are invisible"
)

#: Actions that go looking for something rather than addressing it directly.
_SEARCH_ACTIONS = frozenset({"search", "list", "list_archived"})

#: Arguments that mean "continue the previous traversal" rather than "look again".
_PAGINATION_ARGS = frozenset({"cursor", "offset", "page", "next_cursor", "page_token"})


def _args_of(call: CallRecord) -> dict | None:
    if not call.args_json:
        return None
    try:
        parsed = json.loads(call.args_json)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _own_ids(resource: str, args: dict) -> set[str]:
    """Identifiers of *this* resource, ignoring scope and cross-references.

    ``workitem_id`` on the ``workitem`` tool is the entity; ``project_id`` is the
    scope it lives in, and ``cycle_id`` is a different resource entirely.
    """
    own = {"id", f"{resource}_id"}
    return {value for name, value in args.items() if name in own and isinstance(value, str) and value}


@dataclass(frozen=True, slots=True)
class LookupReuseMeasurement:
    """Redundant lookups, and whether the question could be asked at all."""

    total: int = 0
    by_resource: dict[str, int] = field(default_factory=dict)
    rows_measured: int = 0
    rows_without_args: int = 0
    calls_without_args: int = 0

    @property
    def measurable(self) -> bool:
        return self.rows_measured > 0

    def statement(self) -> str:
        if not self.measurable:
            return f"redundant lookups: not measured — {self.rows_without_args} row(s) carry no recorded call arguments"
        detail = ", ".join(f"{resource}={count}" for resource, count in sorted(self.by_resource.items()))
        line = f"redundant lookups: {self.total}"
        if detail:
            line += f" [{detail}]"
        extras = []
        if self.rows_without_args:
            extras.append(f"{self.rows_without_args} row(s) not measured for want of arguments")
        if self.calls_without_args:
            extras.append(f"{self.calls_without_args} call(s) skipped inside measured rows")
        if extras:
            line += "; " + "; ".join(extras)
        return f"{line}\n  {LOOKUP_REUSE_LIMITATION}"


def measure_lookup_reuse(rows: list[ResultRow]) -> LookupReuseMeasurement:
    """Count searches issued after the same resource's own id was already an argument.

    Scoped to one row. Each repetition is a fresh conversation, so an id learned in
    one tells the agent in another nothing.
    """
    total = 0
    by_resource: dict[str, int] = defaultdict(int)
    measured = 0
    rows_without_args = 0
    calls_without_args = 0
    for raw_row in rows:
        row: TaskResult = read_result(raw_row)
        if is_meta_row(row):
            continue
        if not any(call.args_json for call in row.calls):
            rows_without_args += 1
            continue
        measured += 1
        known: dict[str, set[str]] = defaultdict(set)
        for call in row.calls:
            args = _args_of(call)
            if args is None:
                calls_without_args += 1
                continue
            resource = call.tool
            action = (call.action or "").lower()
            paging = any(name in args for name in _PAGINATION_ARGS)
            if action in _SEARCH_ACTIONS and known[resource] and not paging:
                total += 1
                by_resource[resource] += 1
            # Learned after the check, so the call that first resolves an id is never
            # charged for the lookup that produced it.
            known[resource].update(_own_ids(resource, args))
    return LookupReuseMeasurement(
        total=total,
        by_resource=dict(by_resource),
        rows_measured=measured,
        rows_without_args=rows_without_args,
        calls_without_args=calls_without_args,
    )


__all__ = ["LOOKUP_REUSE_LIMITATION", "LookupReuseMeasurement", "measure_lookup_reuse"]
