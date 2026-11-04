"""What a run cost and how much it moved, in the view where two arms are compared.

Every number here was already recorded. Result tokens even reached ``--table``. None
of it reached the two-file A/B view, which is where a two-arm question is actually
asked -- so an arm making 32% fewer tool calls while burning 3.1x the input tokens
read as the efficient one until someone totalled the tokens by hand.

Two populations, deliberately:

  arm totals   every executed row, because cost was incurred whether or not the
               task passed.
  per task     the successful, trace-intact rows that call deltas already use, so a
               paired delta compares like with like.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from evals.core.pricing import PRICED, PRICES_AS_OF, UNMEASURED, UNPRICED, price_usage
from evals.core.results import TaskResult
from evals.core.token_accounting import has_token_counts, normalize_usage

from .load import ResultRow, is_infra_error_row, is_meta_row, read_result
from .schema_friction import successful_trace_rows
from .statistics import median, percentile


def format_usd(amount: float) -> str:
    """Render dollars without rounding a real figure down to nothing.

    A genuine $0.00032 printed as $0.000 is the same "reads as free" mistake the three
    cost outcomes exist to prevent, so small non-zero amounts keep enough digits to stay
    visible. Used for every figure, the drift diagnostic included.
    """
    magnitude = abs(amount)
    if magnitude < 1e-9:
        # Float noise from summing many rows, not a real fraction of a cent.
        return "$0.000"
    if magnitude < 0.001:
        return f"{'-' if amount < 0 else ''}${magnitude:.2e}"
    return f"${amount:,.3f}" if amount >= 0 else f"-${magnitude:,.3f}"


COST_LIMITATION = (
    "limitation: cost is computed from a static price table; a model absent from it reports "
    "unpriced, and a row whose driver recorded no usage at all reports unmeasured. Neither is $0"
)


@dataclass(frozen=True, slots=True)
class TaskEconomics:
    """One task's resource footprint across its eligible repetitions."""

    task_id: str
    repetitions: int
    med_total_input: float | None
    med_result_tokens: float | None
    med_wall_time_s: float | None
    med_call_latency_ms: float | None
    cost_usd: float | None
    """Mean billed cost per successful repetition -- not a total, unlike the arm figure."""


@dataclass(frozen=True, slots=True)
class EconomicsMeasurement:
    """Arm-level totals plus the per-task values a paired delta needs."""

    tasks: dict[str, TaskEconomics]
    total_input_tokens: int | None
    total_result_tokens: int
    total_wall_time_s: float
    cost_usd: float | None
    computed_cost_usd: float | None
    vendor_cost_usd: float | None
    cost_outcome: str
    priced_rows: int
    unpriced_rows: int
    unmeasured_rows: int
    missing_input_rows: int
    drift_computed_usd: float | None
    drift_vendor_usd: float | None
    drift_rows: int
    med_call_latency_ms: float | None
    p95_call_latency_ms: float | None
    prices_as_of: str = PRICES_AS_OF

    @property
    def cost_drift_usd(self) -> float | None:
        """How far the price table sits from what the vendor said it charged.

        Computed over the rows carrying *both* figures, so a row the vendor priced but the
        table could not (or the reverse) adds coverage noise to neither side. None when no
        row carries both, which is most runs.
        """
        if self.drift_computed_usd is None or self.drift_vendor_usd is None:
            return None
        return self.drift_computed_usd - self.drift_vendor_usd

    @property
    def cost_text(self) -> str:
        """Never render an unknown cost as a number, or a real one as zero."""
        if self.cost_usd is None:
            return UNMEASURED if self.cost_outcome == UNMEASURED else UNPRICED
        text = format_usd(self.cost_usd)
        if self.unpriced_rows or self.unmeasured_rows:
            text += f" (+{self.unpriced_rows} unpriced, {self.unmeasured_rows} unmeasured rows)"
        return text

    @property
    def input_text(self) -> str:
        """The input total, saying so when it does not cover every row."""
        if self.total_input_tokens is None:
            return UNMEASURED
        text = f"{self.total_input_tokens:,}"
        if self.missing_input_rows:
            text += f" (excludes {self.missing_input_rows} row(s) with unreadable usage)"
        return text


def _charged_rows(rows: list[ResultRow]) -> list[TaskResult]:
    """Every row whose model actually ran, including ones that later went wrong.

    A verifier crash, a contained timeout or a post-run skip happens after the tokens
    were spent, so excluding those rows understates an arm and hides the spend
    entirely -- it was not even counted as unmeasured. A row that carries usage is
    kept regardless of how it ended; one that never ran is not.
    """
    charged: list[TaskResult] = []
    for raw_row in rows:
        row = read_result(raw_row)
        if is_meta_row(row):
            continue
        if row.error or row.skipped or is_infra_error_row(row):
            # An infrastructure classification is applied *after* the agent run is folded
            # in, so a contained CLI timeout or trace failure can carry real usage. Keep
            # any row that shows the model ran; drop only ones that never started.
            if not has_token_counts(row.usage_total) and not row.calls:
                continue
        charged.append(row)
    return charged


def _row_input_tokens(row: TaskResult) -> int | None:
    accounting = normalize_usage(row.usage_total, model=row.model)
    return accounting.total_input if accounting else None


def _call_latencies(rows: list[TaskResult]) -> list[float]:
    return [float(call.duration_ms) for row in rows for call in row.calls if call.duration_ms is not None]


def measure_economics(rows: list[ResultRow]) -> EconomicsMeasurement:
    """Total an arm's cost and volume, and break it down per task."""
    executed = _charged_rows(rows)

    total_input = 0
    saw_input = False
    missing_input = 0
    billed_total = 0.0
    saw_billed = False
    computed_total = 0.0
    saw_computed = False
    vendor_total = 0.0
    saw_vendor = False
    drift_computed = 0.0
    drift_vendor = 0.0
    drift_rows = 0
    priced = unpriced = unmeasured = 0
    for row in executed:
        tokens = _row_input_tokens(row)
        if tokens is not None:
            total_input += tokens
            saw_input = True
        else:
            missing_input += 1
        cost = price_usage(row.usage_total, model=row.model)
        if cost.outcome == PRICED:
            priced += 1
        elif cost.outcome == UNPRICED:
            unpriced += 1
        else:
            unmeasured += 1
        # Billed is what to report; computed is the table's own opinion, kept apart so
        # the drift check compares the table against the vendor rather than the vendor
        # against itself. Billed accrues on any row that has a figure, so an
        # authoritative vendor cost survives a model the table cannot price.
        if cost.billed_usd is not None:
            billed_total += cost.billed_usd
            saw_billed = True
        if cost.usd is not None:
            computed_total += cost.usd
            saw_computed = True
        if cost.vendor_usd is not None:
            vendor_total += cost.vendor_usd
            saw_vendor = True
        # Drift is only meaningful over rows that carry *both* figures. Summing each side
        # independently would fold coverage differences into what is meant to be a
        # price-table comparison.
        if cost.usd is not None and cost.vendor_usd is not None:
            drift_computed += cost.usd
            drift_vendor += cost.vendor_usd
            drift_rows += 1

    if not saw_billed:
        # Nothing to report: say which kind of nothing it is.
        outcome = UNMEASURED if unpriced == 0 else UNPRICED
    else:
        outcome = PRICED  # possibly partial; the counts travel alongside

    by_task: dict[str, list[TaskResult]] = defaultdict(list)
    for row in successful_trace_rows(rows):
        by_task[row.task_id].append(row)

    tasks: dict[str, TaskEconomics] = {}
    for task_id in sorted(by_task):
        task_rows = by_task[task_id]
        inputs = [float(value) for value in (_row_input_tokens(row) for row in task_rows) if value is not None]
        costs = [price_usage(row.usage_total, model=row.model).billed_usd for row in task_rows]
        known_costs = [value for value in costs if value is not None]
        tasks[task_id] = TaskEconomics(
            task_id=task_id,
            repetitions=len(task_rows),
            med_total_input=median(inputs),
            med_result_tokens=median([float(row.total_result_tokens) for row in task_rows]),
            med_wall_time_s=median([float(row.wall_time_s) for row in task_rows]),
            med_call_latency_ms=median(_call_latencies(task_rows)),
            cost_usd=(sum(known_costs) / len(known_costs) if known_costs else None),
        )

    latencies = _call_latencies(executed)
    return EconomicsMeasurement(
        tasks=tasks,
        total_input_tokens=total_input if saw_input else None,
        total_result_tokens=sum(row.total_result_tokens for row in executed),
        total_wall_time_s=sum(float(row.wall_time_s) for row in executed),
        cost_usd=billed_total if saw_billed else None,
        computed_cost_usd=computed_total if saw_computed else None,
        vendor_cost_usd=vendor_total if saw_vendor else None,
        missing_input_rows=missing_input,
        drift_computed_usd=drift_computed if drift_rows else None,
        drift_vendor_usd=drift_vendor if drift_rows else None,
        drift_rows=drift_rows,
        cost_outcome=outcome,
        priced_rows=priced,
        unpriced_rows=unpriced,
        unmeasured_rows=unmeasured,
        med_call_latency_ms=median(latencies),
        p95_call_latency_ms=percentile(latencies, 0.95),
    )


def economics_statement(measurement: EconomicsMeasurement) -> str:
    """One block naming cost, volume and latency, with unknowns named as unknowns."""
    input_text = measurement.input_text
    latency = measurement.med_call_latency_ms
    p95 = measurement.p95_call_latency_ms
    latency_text = f"{latency:,.0f}ms median / {p95:,.0f}ms p95" if latency is not None and p95 is not None else "n/a"
    lines = [
        f"economics: cost={measurement.cost_text} (prices as of {measurement.prices_as_of}); "
        f"input tokens={input_text}; result tokens={measurement.total_result_tokens:,}",
        f"  wall time={measurement.total_wall_time_s:,.0f}s; call latency {latency_text}",
    ]
    drift = measurement.cost_drift_usd
    if drift is not None:
        # The only standing check that the price table has not gone stale, so it compares
        # the table's own figure against the vendor's -- not the reported cost, which
        # already prefers the vendor and would always agree with itself.
        lines.append(
            f"  price-table check over {measurement.drift_rows} row(s) carrying both: "
            f"vendor {format_usd(measurement.drift_vendor_usd)}, "
            f"table {format_usd(measurement.drift_computed_usd)} "
            f"(differs by {format_usd(drift)})"
        )
    lines.append(f"  {COST_LIMITATION}")
    return "\n".join(lines)


__all__ = [
    "COST_LIMITATION",
    "format_usd",
    "EconomicsMeasurement",
    "TaskEconomics",
    "economics_statement",
    "measure_economics",
]
