from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pandas as pd

TradeResult = Literal["win", "loss", "breakeven"]


@dataclass(frozen=True)
class ValidationIssue:
    """A structured validation issue raised during ingestion."""

    row_number: int | None
    column: str | None
    message: str


class TradeValidationError(ValueError):
    """Raised when a trade ledger cannot be safely normalized."""

    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        details = "; ".join(
            f"row={issue.row_number} column={issue.column}: {issue.message}"
            for issue in issues[:10]
        )
        if len(issues) > 10:
            details += f"; plus {len(issues) - 10} more"
        super().__init__(details)


@dataclass(frozen=True)
class StrategyMetadata:
    """Metadata stays attached to a specific strategy and instrument pair."""

    strategy_id: str
    instrument: str
    dollars_per_point: float | None = None
    commission_round_turn: float = 0.0
    description: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ValueError("strategy_id is required")
        if not self.instrument:
            raise ValueError("instrument is required")
        if self.dollars_per_point is not None and self.dollars_per_point <= 0:
            raise ValueError("dollars_per_point must be positive")
        if self.commission_round_turn < 0:
            raise ValueError("commission_round_turn cannot be negative")


@dataclass(frozen=True)
class Trade:
    """A normalized historical trade, measured per one contract."""

    trade_id: str
    strategy_id: str
    instrument: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    pnl_dollars: float
    direction: str | None = None
    entry_price: float | None = None
    exit_price: float | None = None
    pnl_points: float | None = None
    stop_points: float | None = None
    target_points: float | None = None
    mae_points: float | None = None
    mfe_points: float | None = None
    result_type: TradeResult | None = None
    session: str | None = None
    dollars_per_point: float | None = None
    commission_round_turn: float = 0.0
    source_path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.trade_id:
            raise ValueError("trade_id is required")
        if not self.strategy_id:
            raise ValueError("strategy_id is required")
        if not self.instrument:
            raise ValueError("instrument is required")
        if self.exit_time < self.entry_time:
            raise ValueError("exit_time cannot precede entry_time")
        if self.commission_round_turn < 0:
            raise ValueError("commission_round_turn cannot be negative")
        if self.result_type is None:
            object.__setattr__(self, "result_type", classify_result(self.pnl_dollars))

    @property
    def source_month(self) -> pd.Period:
        return self.entry_time.to_period("M")

    def shifted_to_month(self, target_month: pd.Period) -> Trade:
        """Return a copy with entry/exit shifted by month-start offset."""

        source_start = self.source_month.to_timestamp()
        target_start = target_month.to_timestamp()
        entry_offset = self.entry_time - source_start
        exit_offset = self.exit_time - source_start
        return Trade(
            trade_id=f"{self.trade_id}@{target_month}",
            strategy_id=self.strategy_id,
            instrument=self.instrument,
            entry_time=target_start + entry_offset,
            exit_time=target_start + exit_offset,
            pnl_dollars=self.pnl_dollars,
            direction=self.direction,
            entry_price=self.entry_price,
            exit_price=self.exit_price,
            pnl_points=self.pnl_points,
            stop_points=self.stop_points,
            target_points=self.target_points,
            mae_points=self.mae_points,
            mfe_points=self.mfe_points,
            result_type=self.result_type,
            session=self.session,
            dollars_per_point=self.dollars_per_point,
            commission_round_turn=self.commission_round_turn,
            source_path=self.source_path,
            metadata={
                **self.metadata,
                "source_trade_id": self.trade_id,
                "source_month": str(self.source_month),
            },
        )


@dataclass(frozen=True)
class AccountConfig:
    """Version 1 account settings for fixed-contract replay."""

    initial_equity: float = 100_000.0
    ruin_threshold: float = 0.0

    def __post_init__(self) -> None:
        if self.initial_equity <= 0:
            raise ValueError("initial_equity must be positive")


@dataclass(frozen=True)
class FixedContractPortfolio:
    """Contracts are configured per strategy, with optional instrument overrides."""

    strategy_contracts: dict[str, int] = field(default_factory=dict)
    instrument_contracts: dict[tuple[str, str], int] = field(default_factory=dict)
    default_contracts: int = 1

    def contracts_for(self, trade: Trade) -> int:
        return self.instrument_contracts.get(
            (trade.strategy_id, trade.instrument),
            self.strategy_contracts.get(trade.strategy_id, self.default_contracts),
        )

    def __post_init__(self) -> None:
        values = list(self.strategy_contracts.values())
        values.extend(self.instrument_contracts.values())
        values.append(self.default_contracts)
        if any(value < 0 for value in values):
            raise ValueError("contract counts cannot be negative")


@dataclass(frozen=True)
class SampledBlock:
    path_index: int
    target_month: pd.Period
    source_month: pd.Period
    policy_name: str


@dataclass(frozen=True)
class ResampledPath:
    trades: list[Trade]
    sampled_blocks: list[SampledBlock]


@dataclass(frozen=True)
class EquityPoint:
    timestamp: pd.Timestamp
    equity: float
    trade_id: str
    strategy_id: str
    instrument: str
    contracts: int
    gross_pnl: float
    commission: float
    net_pnl: float


@dataclass(frozen=True)
class SimulationResult:
    account: AccountConfig
    portfolio: FixedContractPortfolio
    trades: list[Trade]
    equity_path: list[EquityPoint]
    sampled_blocks: list[SampledBlock] = field(default_factory=list)

    @property
    def terminal_equity(self) -> float:
        if not self.equity_path:
            return self.account.initial_equity
        return self.equity_path[-1].equity

    def to_equity_frame(self) -> pd.DataFrame:
        rows = [
            {
                "timestamp": point.timestamp,
                "equity": point.equity,
                "trade_id": point.trade_id,
                "strategy_id": point.strategy_id,
                "instrument": point.instrument,
                "contracts": point.contracts,
                "gross_pnl": point.gross_pnl,
                "commission": point.commission,
                "net_pnl": point.net_pnl,
            }
            for point in self.equity_path
        ]
        return pd.DataFrame(rows)


def classify_result(pnl_dollars: float, tolerance: float = 1e-9) -> TradeResult:
    if pnl_dollars > tolerance:
        return "win"
    if pnl_dollars < -tolerance:
        return "loss"
    return "breakeven"
