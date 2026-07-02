"""V3 — margin model and exposure measurement.

Two capabilities, both with EXPLICIT, declared assumptions (no silent defaults):

1. Margin: a per-contract initial/maintenance margin declared per contract symbol.
   A margin cap reduces the sized contract count so that
   `contracts * initial_margin <= max(0, equity - reserve)`. Reductions are
   reported (forced margin reductions), consistent with ADR-011's "declare, don't
   infer" rule for instrument metadata.

2. Exposure: measured from the trade entry/exit intervals and the simulated
   contract counts. Realized-P&L V1/V2 books at exit, so exposure here is based on
   the *scheduled* open interval [entry, exit] of each trade at its simulated size
   (see KNOWN_LIMITATIONS: no intratrade mark-to-market).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from sim_core.live_account import LiveAccountPathResult, SizingDecision
from sim_core.models import Trade


@dataclass(frozen=True)
class InstrumentMargin:
    contract_symbol: str
    initial_margin: float
    maintenance_margin: float
    currency: str = "USD"

    def __post_init__(self) -> None:
        if not self.contract_symbol:
            raise ValueError("contract_symbol is required")
        if self.initial_margin <= 0:
            raise ValueError("initial_margin must be positive")
        if self.maintenance_margin <= 0:
            raise ValueError("maintenance_margin must be positive")
        if self.maintenance_margin > self.initial_margin:
            raise ValueError("maintenance_margin cannot exceed initial_margin")
        if self.currency != "USD":
            raise ValueError("V3 supports USD margin only")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_symbol": self.contract_symbol,
            "initial_margin": self.initial_margin,
            "maintenance_margin": self.maintenance_margin,
            "currency": self.currency,
        }


@dataclass(frozen=True)
class MarginPolicy:
    """Explicit per-contract margin requirements plus an optional cash reserve."""

    margins: dict[str, InstrumentMargin]
    reserve: float = 0.0

    def __post_init__(self) -> None:
        if self.reserve < 0:
            raise ValueError("reserve cannot be negative")

    def require(self, contract_symbol: str | None) -> InstrumentMargin:
        if contract_symbol is None or contract_symbol not in self.margins:
            raise ValueError(
                f"no declared margin for contract {contract_symbol!r}; margin must be "
                "declared per contract symbol (no silent default)"
            )
        return self.margins[contract_symbol]

    def max_contracts(self, contract_symbol: str | None, equity: float) -> int:
        spec = self.require(contract_symbol)
        available = max(0.0, equity - self.reserve)
        return int(available // spec.initial_margin)

    def to_dict(self) -> dict[str, Any]:
        return {
            "margins": {sym: spec.to_dict() for sym, spec in sorted(self.margins.items())},
            "reserve": self.reserve,
        }


def apply_margin_cap(
    contracts: int, contract_symbol: str | None, equity: float, policy: MarginPolicy
) -> tuple[int, bool, float]:
    """Return (capped_contracts, was_reduced, initial_margin_used)."""

    cap = policy.max_contracts(contract_symbol, equity)
    capped = min(contracts, cap)
    spec = policy.require(contract_symbol)
    return capped, capped < contracts, capped * spec.initial_margin


# --------------------------------------------------------------------------- exposure


@dataclass(frozen=True)
class ExposureReport:
    time_in_market_fraction: float
    sessions_with_a_trade: int
    total_sessions: int
    peak_simultaneous_positions: int
    peak_simultaneous_contracts: int
    peak_initial_margin: float
    peak_open_stop_risk: float
    average_open_initial_margin: float
    peak_margin_utilization: float
    strategy_overlap_fraction: float
    instrument_overlap_fraction: float
    return_per_unit_peak_margin: float | None
    return_per_unit_peak_stop_risk: float | None
    per_instrument_time_in_market: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_in_market_fraction": self.time_in_market_fraction,
            "sessions_with_a_trade": self.sessions_with_a_trade,
            "total_sessions": self.total_sessions,
            "peak_simultaneous_positions": self.peak_simultaneous_positions,
            "peak_simultaneous_contracts": self.peak_simultaneous_contracts,
            "peak_initial_margin": self.peak_initial_margin,
            "peak_open_stop_risk": self.peak_open_stop_risk,
            "average_open_initial_margin": self.average_open_initial_margin,
            "peak_margin_utilization": self.peak_margin_utilization,
            "strategy_overlap_fraction": self.strategy_overlap_fraction,
            "instrument_overlap_fraction": self.instrument_overlap_fraction,
            "return_per_unit_peak_margin": self.return_per_unit_peak_margin,
            "return_per_unit_peak_stop_risk": self.return_per_unit_peak_stop_risk,
            "per_instrument_time_in_market": self.per_instrument_time_in_market,
        }


def _contracts_by_trade(sizing_decisions: list[SizingDecision]) -> dict[str, int]:
    # Last decision per trade wins (entry-time decision is authoritative).
    result: dict[str, int] = {}
    for decision in sizing_decisions:
        result[decision.trade_id] = decision.contracts
    return result


def build_exposure_report(
    result: LiveAccountPathResult, *, margin_policy: MarginPolicy | None = None
) -> ExposureReport:
    trades = result.trades
    contracts_by_trade = _contracts_by_trade(result.sizing_decisions)
    starting_equity = float(result.config.starting_equity)
    trading_pnl = float(result.summary["trading_pnl"])

    # Open intervals for trades that were actually sized (> 0 contracts).
    intervals: list[dict[str, Any]] = []
    for trade in trades:
        contracts = contracts_by_trade.get(trade.trade_id, 0)
        if contracts <= 0 or trade.exit_time <= trade.entry_time:
            continue
        margin = 0.0
        if margin_policy is not None:
            margin = contracts * margin_policy.require(trade.contract_symbol).initial_margin
        stop_risk = 0.0
        if trade.stop_points is not None and trade.dollars_per_point is not None:
            stop_risk = contracts * abs(float(trade.stop_points) * float(trade.dollars_per_point))
        intervals.append(
            {
                "entry": trade.entry_time,
                "exit": trade.exit_time,
                "contracts": contracts,
                "margin": margin,
                "stop_risk": stop_risk,
                "strategy_id": trade.strategy_id,
                "instrument": trade.instrument,
            }
        )

    if not intervals:
        return ExposureReport(
            0.0, 0, 0, 0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, None, None, {}
        )

    span_start = min(item["entry"] for item in intervals)
    span_end = max(item["exit"] for item in intervals)
    total_span = (span_end - span_start).total_seconds()

    # Sweep line over interval boundaries.
    points = sorted({item["entry"] for item in intervals} | {item["exit"] for item in intervals})
    in_market_seconds = 0.0
    strategy_overlap_seconds = 0.0
    instrument_overlap_seconds = 0.0
    margin_time_integral = 0.0
    peak_positions = 0
    peak_contracts = 0
    peak_margin = 0.0
    peak_stop_risk = 0.0
    per_instrument_seconds: dict[str, float] = {}

    for left, right in zip(points, points[1:]):
        duration = (right - left).total_seconds()
        if duration <= 0:
            continue
        mid = left + (right - left) / 2
        open_here = [item for item in intervals if item["entry"] <= mid < item["exit"]]
        if not open_here:
            continue
        open_positions = len(open_here)
        open_contracts = sum(item["contracts"] for item in open_here)
        open_margin = sum(item["margin"] for item in open_here)
        open_stop_risk = sum(item["stop_risk"] for item in open_here)
        distinct_strategies = {item["strategy_id"] for item in open_here}
        distinct_instruments = {item["instrument"] for item in open_here}

        in_market_seconds += duration
        margin_time_integral += open_margin * duration
        if len(distinct_strategies) >= 2:
            strategy_overlap_seconds += duration
        if len(distinct_instruments) >= 2:
            instrument_overlap_seconds += duration
        for instrument in distinct_instruments:
            per_instrument_seconds[instrument] = per_instrument_seconds.get(instrument, 0.0) + duration

        peak_positions = max(peak_positions, open_positions)
        peak_contracts = max(peak_contracts, open_contracts)
        peak_margin = max(peak_margin, open_margin)
        peak_stop_risk = max(peak_stop_risk, open_stop_risk)

    sessions = {item["entry"].date() for item in intervals} | {item["exit"].date() for item in intervals}
    all_days = pd.date_range(span_start.date(), span_end.date(), freq="D")
    total_sessions = len(all_days)

    def _frac(seconds: float) -> float:
        return seconds / total_span if total_span > 0 else 0.0

    return ExposureReport(
        time_in_market_fraction=_frac(in_market_seconds),
        sessions_with_a_trade=len({item["entry"].date() for item in intervals}),
        total_sessions=total_sessions,
        peak_simultaneous_positions=peak_positions,
        peak_simultaneous_contracts=peak_contracts,
        peak_initial_margin=peak_margin,
        peak_open_stop_risk=peak_stop_risk,
        average_open_initial_margin=(margin_time_integral / in_market_seconds) if in_market_seconds else 0.0,
        peak_margin_utilization=(peak_margin / starting_equity) if starting_equity else 0.0,
        strategy_overlap_fraction=_frac(strategy_overlap_seconds),
        instrument_overlap_fraction=_frac(instrument_overlap_seconds),
        return_per_unit_peak_margin=(trading_pnl / peak_margin) if peak_margin > 0 else None,
        return_per_unit_peak_stop_risk=(trading_pnl / peak_stop_risk) if peak_stop_risk > 0 else None,
        per_instrument_time_in_market={k: _frac(v) for k, v in sorted(per_instrument_seconds.items())},
    )
