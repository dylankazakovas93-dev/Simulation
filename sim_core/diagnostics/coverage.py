"""Reusable per-strategy, per-calendar-month coverage diagnostics (MEDIUM-R3-C).

Distinguishes, for every strategy and month in the portfolio calendar:
  * complete       - the strategy traded that month
  * partial        - declared partial (excluded from sampling)
  * verified_flat  - declared-covered but zero trades (a real flat month)
  * missing        - no trades and not covered (cannot be assumed flat)

It also reports, per month-of-year, how many eligible source months a strategy
has (seasonal support), the trade count, coverage span, and whether each month
is eligible for seasonal / block resampling. The report feeds scenario
validation warnings and exported diagnostics.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from sim_core.models import StrategyCoverage, Trade

ELIGIBLE_STATUSES = {"complete", "verified_flat"}


@dataclass(frozen=True)
class CoverageMonth:
    strategy_id: str
    instrument: str
    month: str
    status: str
    trade_count: int
    source_month_support: int
    seasonal_eligible: bool
    block_eligible: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "instrument": self.instrument,
            "month": self.month,
            "status": self.status,
            "trade_count": self.trade_count,
            "source_month_support": self.source_month_support,
            "seasonal_eligible": self.seasonal_eligible,
            "block_eligible": self.block_eligible,
        }


@dataclass(frozen=True)
class CoverageReport:
    months: list[CoverageMonth]
    strategies: dict[str, dict[str, Any]]

    def status_for(self, strategy_id: str, month: str) -> str | None:
        for record in self.months:
            if record.strategy_id == strategy_id and record.month == month:
                return record.status
        return None

    def to_records(self) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self.months]

    def to_dict(self) -> dict[str, Any]:
        return {"strategies": self.strategies, "months": self.to_records()}

    def warnings(self) -> list[str]:
        notes: list[str] = []
        for strategy_id, meta in sorted(self.strategies.items()):
            if not meta["has_coverage"]:
                notes.append(
                    f"strategy '{strategy_id}': no coverage declared; missing months cannot "
                    "be distinguished from verified flat months"
                )
            missing = [m.month for m in self.months if m.strategy_id == strategy_id and m.status == "missing"]
            if missing:
                notes.append(
                    f"strategy '{strategy_id}': {len(missing)} missing month(s): {', '.join(missing)}"
                )
        # Thin seasonal support: an eligible month whose month-of-year has < 2 instances.
        for record in self.months:
            if record.seasonal_eligible and record.source_month_support < 2:
                notes.append(
                    f"strategy '{record.strategy_id}': calendar month "
                    f"{record.month[-2:]} has thin seasonal support "
                    f"({record.source_month_support} source month(s))"
                )
        return notes


def build_coverage_report(
    trades: Sequence[Trade],
    coverage: Sequence[StrategyCoverage] | None = None,
) -> CoverageReport:
    coverage_by_strategy = {item.strategy_id: item for item in (coverage or [])}

    trade_months: dict[str, Counter] = {}
    instrument_by_strategy: dict[str, str] = {}
    for trade in trades:
        trade_months.setdefault(trade.strategy_id, Counter())[trade.source_month] += 1
        instrument_by_strategy.setdefault(trade.strategy_id, trade.instrument)

    strategy_ids = sorted(set(trade_months) | set(coverage_by_strategy))

    # Portfolio calendar range across every strategy's trades and coverage spans.
    bounds: list[pd.Period] = []
    for counter in trade_months.values():
        bounds.extend(counter)
    for item in coverage_by_strategy.values():
        bounds.extend([item.start_month, item.end_month])
    if not bounds:
        return CoverageReport(months=[], strategies={})
    calendar_start, calendar_end = min(bounds), max(bounds)
    calendar: list[pd.Period] = []
    current = calendar_start
    while current <= calendar_end:
        calendar.append(current)
        current += 1

    months: list[CoverageMonth] = []
    strategies: dict[str, dict[str, Any]] = {}
    for strategy_id in strategy_ids:
        cov = coverage_by_strategy.get(strategy_id)
        counts = trade_months.get(strategy_id, Counter())
        instrument = instrument_by_strategy.get(strategy_id) or (cov.instrument if cov else "")

        statuses: dict[pd.Period, tuple[str, int]] = {}
        for month in calendar:
            trade_count = int(counts.get(month, 0))
            if cov is not None and month in cov.partial_months:
                status = "partial"
            elif trade_count > 0:
                status = "complete"
            elif cov is not None and cov.start_month <= month <= cov.end_month:
                status = "verified_flat"
            else:
                status = "missing"
            statuses[month] = (status, trade_count)

        support_by_moy: Counter = Counter()
        for month, (status, _count) in statuses.items():
            if status in ELIGIBLE_STATUSES:
                support_by_moy[month.month] += 1

        for month in calendar:
            status, trade_count = statuses[month]
            eligible = status in ELIGIBLE_STATUSES
            months.append(
                CoverageMonth(
                    strategy_id=strategy_id,
                    instrument=instrument,
                    month=str(month),
                    status=status,
                    trade_count=trade_count,
                    source_month_support=int(support_by_moy.get(month.month, 0)),
                    seasonal_eligible=eligible,
                    block_eligible=eligible,
                )
            )

        if cov is not None:
            start, end = str(cov.start_month), str(cov.end_month)
        elif counts:
            start, end = str(min(counts)), str(max(counts))
        else:
            start = end = None
        strategies[strategy_id] = {
            "instrument": instrument,
            "has_coverage": cov is not None,
            "coverage_start": start,
            "coverage_end": end,
        }

    return CoverageReport(months=months, strategies=strategies)
