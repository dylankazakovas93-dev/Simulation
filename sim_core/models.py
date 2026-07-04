from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any, Literal

import pandas as pd

ENGINE_VERSION = "0.1.0"
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
class InstrumentSpec:
    """Explicit contract reference metadata; never inferred from a symbol alone."""

    underlying: str
    contract_symbol: str
    dollars_per_point: float
    currency: str = "USD"
    commission_round_turn: float = 0.0

    def __post_init__(self) -> None:
        if not self.underlying:
            raise ValueError("underlying is required")
        if not self.contract_symbol:
            raise ValueError("contract_symbol is required")
        if self.dollars_per_point <= 0:
            raise ValueError("dollars_per_point must be positive")
        if self.currency != "USD":
            raise ValueError("Version 1 supports USD instruments only")
        if self.commission_round_turn < 0:
            raise ValueError("commission_round_turn cannot be negative")


@dataclass(frozen=True)
class StrategyMetadata:
    """Metadata stays attached to a specific strategy and instrument pair."""

    strategy_id: str
    instrument: str
    contract_symbol: str | None = None
    dollars_per_point: float | None = None
    currency: str = "USD"
    commission_round_turn: float = 0.0
    description: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ValueError("strategy_id is required")
        if not self.instrument:
            raise ValueError("instrument is required")
        if self.currency != "USD":
            raise ValueError("Version 1 supports USD instruments only")
        if self.dollars_per_point is not None and self.dollars_per_point <= 0:
            raise ValueError("dollars_per_point must be positive")
        if self.commission_round_turn < 0:
            raise ValueError("commission_round_turn cannot be negative")


@dataclass(frozen=True)
class Trade:
    """A normalized historical trade, measured per one contract."""

    trade_id: str
    source_row_id: str
    strategy_id: str
    instrument: str
    contract_symbol: str | None
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
    currency: str = "USD"
    commission_round_turn: float = 0.0
    source_path: Path | None = None
    target_month: pd.Period | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.trade_id:
            raise ValueError("trade_id is required")
        if not self.source_row_id:
            raise ValueError("source_row_id is required")
        if not self.strategy_id:
            raise ValueError("strategy_id is required")
        if not self.instrument:
            raise ValueError("instrument is required")
        if self.currency != "USD":
            raise ValueError("Version 1 supports USD trades only")
        if self.entry_time.tzinfo is None or self.entry_time.tz is None:
            object.__setattr__(self, "entry_time", self.entry_time.tz_localize("UTC"))
        if self.exit_time.tzinfo is None or self.exit_time.tz is None:
            object.__setattr__(self, "exit_time", self.exit_time.tz_localize("UTC"))
        object.__setattr__(self, "entry_time", self.entry_time.tz_convert("UTC"))
        object.__setattr__(self, "exit_time", self.exit_time.tz_convert("UTC"))
        if self.exit_time < self.entry_time:
            raise ValueError("exit_time cannot precede entry_time")
        if self.commission_round_turn < 0:
            raise ValueError("commission_round_turn cannot be negative")
        if self.result_type is None:
            object.__setattr__(self, "result_type", classify_result(self.pnl_dollars))

    @property
    def source_month(self) -> pd.Period:
        timestamp = self.entry_time.tz_convert("UTC")
        return pd.Period(f"{timestamp.year}-{timestamp.month:02d}", "M")

    def shifted_to_month(self, target_month: pd.Period) -> Trade:
        """Return a copy shifted inside the authoritative target month.

        The original offset from the source month start is preserved when it
        fits. If it would land outside the target month, the timestamp is
        clamped to the final valid instant of the target month. Exit duration is
        preserved when possible without crossing the target-month boundary.
        """

        target_month = pd.Period(target_month, "M")
        source_start = _month_start_utc(self.source_month)
        target_start = _month_start_utc(target_month)
        target_end = _month_end_utc(target_month)
        entry_offset = self.entry_time - source_start
        duration = self.exit_time - self.entry_time
        shifted_entry = min(target_start + entry_offset, target_end)
        shifted_exit = min(shifted_entry + duration, target_end)
        return Trade(
            trade_id=f"{self.trade_id}@{target_month}",
            source_row_id=self.source_row_id,
            strategy_id=self.strategy_id,
            instrument=self.instrument,
            contract_symbol=self.contract_symbol,
            entry_time=shifted_entry,
            exit_time=shifted_exit,
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
            currency=self.currency,
            commission_round_turn=self.commission_round_turn,
            source_path=self.source_path,
            target_month=target_month,
            metadata={
                **self.metadata,
                "source_trade_id": self.trade_id,
                "source_month": str(self.source_month),
                "target_month": str(target_month),
                "month_shift_policy": "preserve_offset_then_clamp_to_target_month_end",
            },
        )


@dataclass(frozen=True)
class StrategyCoverage:
    """Declared verified data coverage for distinguishing flat months from gaps."""

    strategy_id: str
    instrument: str
    start_month: pd.Period
    end_month: pd.Period
    partial_months: frozenset[pd.Period] = frozenset()

    def __init__(
        self,
        strategy_id: str,
        instrument: str,
        start_month: str | pd.Period,
        end_month: str | pd.Period,
        partial_months: set[str | pd.Period] | frozenset[str | pd.Period] | None = None,
    ) -> None:
        object.__setattr__(self, "strategy_id", strategy_id)
        object.__setattr__(self, "instrument", instrument)
        object.__setattr__(self, "start_month", pd.Period(start_month, "M"))
        object.__setattr__(self, "end_month", pd.Period(end_month, "M"))
        object.__setattr__(
            self,
            "partial_months",
            frozenset(pd.Period(month, "M") for month in (partial_months or set())),
        )
        if self.end_month < self.start_month:
            raise ValueError("end_month cannot precede start_month")

    def complete_months(self) -> list[pd.Period]:
        months = []
        current = self.start_month
        while current <= self.end_month:
            if current not in self.partial_months:
                months.append(current)
            current += 1
        return months


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
    diagnostics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EquityPoint:
    timestamp: pd.Timestamp
    equity: float
    trade_id: str
    source_row_id: str
    strategy_id: str
    instrument: str
    contract_symbol: str | None
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
                "source_row_id": point.source_row_id,
                "strategy_id": point.strategy_id,
                "instrument": point.instrument,
                "contract_symbol": point.contract_symbol,
                "contracts": point.contracts,
                "gross_pnl": point.gross_pnl,
                "commission": point.commission,
                "net_pnl": point.net_pnl,
            }
            for point in self.equity_path
        ]
        return pd.DataFrame(rows)


@dataclass(frozen=True, init=False)
class Scenario:
    scenario_id: str
    name: str
    master_seed: int
    number_of_paths: int
    horizon_months: int
    starting_equity: float
    selected_strategies: list[str]
    fixed_contract_quantities: dict[str, int]
    commission_assumptions: dict[str, float]
    resampling_method: str
    resampling_params: dict[str, Any]
    coverage_policy: dict[str, Any]
    ruin_threshold: float
    currency: str
    contract_mappings: dict[str, dict[str, Any]]
    breakeven_policy: dict[str, Any]
    input_data_hash: str
    engine_version: str = ENGINE_VERSION

    def __init__(
        self,
        scenario_id: str | None = None,
        name: str | None = None,
        master_seed: int = 0,
        number_of_paths: int = 1,
        horizon_months: int | None = None,
        starting_equity: float | None = None,
        selected_strategies: list[str] | None = None,
        fixed_contract_quantities: dict[str, int] | None = None,
        commission_assumptions: dict[str, float] | None = None,
        resampling_method: str | None = None,
        resampling_params: dict[str, Any] | None = None,
        coverage_policy: dict[str, Any] | None = None,
        ruin_threshold: float | None = None,
        currency: str = "USD",
        contract_mappings: dict[str, dict[str, Any]] | None = None,
        breakeven_policy: dict[str, Any] | None = None,
        input_data_hash: str | None = None,
        engine_version: str = ENGINE_VERSION,
        *,
        resampling_policy: str | None = None,
        policy_params: dict[str, Any] | None = None,
        account: AccountConfig | None = None,
        portfolio: FixedContractPortfolio | None = None,
        data_hash: str | None = None,
    ) -> None:
        resolved_policy = resampling_method or resampling_policy or "historical_replay"
        resolved_params = resampling_params if resampling_params is not None else (policy_params or {})
        resolved_account = account or AccountConfig()
        resolved_portfolio = portfolio or FixedContractPortfolio()
        object.__setattr__(self, "scenario_id", scenario_id or "scenario")
        object.__setattr__(self, "name", name or scenario_id or "scenario")
        object.__setattr__(self, "master_seed", master_seed)
        object.__setattr__(self, "number_of_paths", number_of_paths)
        object.__setattr__(
            self,
            "horizon_months",
            horizon_months or int(resolved_params.get("months", 1)),
        )
        object.__setattr__(self, "starting_equity", starting_equity or resolved_account.initial_equity)
        object.__setattr__(self, "selected_strategies", selected_strategies or [])
        object.__setattr__(
            self,
            "fixed_contract_quantities",
            fixed_contract_quantities or resolved_portfolio.strategy_contracts,
        )
        object.__setattr__(self, "commission_assumptions", commission_assumptions or {})
        object.__setattr__(self, "resampling_method", resolved_policy)
        object.__setattr__(self, "resampling_params", resolved_params)
        object.__setattr__(self, "coverage_policy", coverage_policy or {})
        object.__setattr__(
            self,
            "ruin_threshold",
            resolved_account.ruin_threshold if ruin_threshold is None else ruin_threshold,
        )
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "contract_mappings", contract_mappings or {})
        object.__setattr__(
            self,
            "breakeven_policy",
            breakeven_policy or DEFAULT_BREAKEVEN_POLICY.to_dict(),
        )
        object.__setattr__(self, "input_data_hash", input_data_hash or data_hash or "")
        object.__setattr__(self, "engine_version", engine_version)
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.currency != "USD":
            raise ValueError("Version 1 supports USD scenarios only")
        if self.number_of_paths <= 0:
            raise ValueError("number_of_paths must be positive")
        if self.horizon_months <= 0:
            raise ValueError("horizon_months must be positive")
        if self.starting_equity <= 0:
            raise ValueError("starting_equity must be positive")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["resampling_policy"] = self.resampling_method
        data["policy_params"] = self.resampling_params
        data["data_hash"] = self.input_data_hash
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> "Scenario":
        return cls(**json.loads(payload))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scenario":
        return cls(**data)


@dataclass(frozen=True, init=False)
class ResultDistribution:
    scenario: Scenario
    monthly_percentiles: list[dict[str, Any]]
    terminal_equity_distribution: dict[str, float]
    drawdown_metrics: list[dict[str, float]]
    ruin_probability: float
    outcome_taxonomy: dict[str, float]
    resampling_diagnostics: dict[str, Any]
    warnings: list[str]
    known_limitations: list[str]
    data_hash: str

    def __init__(
        self,
        scenario: Scenario,
        monthly_percentiles: list[dict[str, Any]] | None = None,
        terminal_equity_distribution: dict[str, float] | None = None,
        drawdown_metrics: list[dict[str, float]] | None = None,
        ruin_probability: float = 0.0,
        outcome_taxonomy: dict[str, float] | None = None,
        resampling_diagnostics: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
        known_limitations: list[str] | None = None,
        data_hash: str | None = None,
        *,
        paths: list[Any] | None = None,
    ) -> None:
        object.__setattr__(self, "scenario", scenario)
        object.__setattr__(self, "monthly_percentiles", monthly_percentiles or [])
        object.__setattr__(self, "terminal_equity_distribution", terminal_equity_distribution or {})
        object.__setattr__(self, "drawdown_metrics", drawdown_metrics or [])
        object.__setattr__(self, "ruin_probability", ruin_probability)
        object.__setattr__(self, "outcome_taxonomy", outcome_taxonomy or {})
        object.__setattr__(self, "resampling_diagnostics", resampling_diagnostics or {})
        object.__setattr__(self, "warnings", warnings or [])
        object.__setattr__(self, "known_limitations", known_limitations or [])
        object.__setattr__(self, "data_hash", data_hash or scenario.input_data_hash)
        object.__setattr__(self, "paths", paths or [])

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> "ResultDistribution":
        data = json.loads(payload)
        data["scenario"] = Scenario(**data["scenario"])
        return cls(**data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResultDistribution":
        data = dict(data)
        data["scenario"] = Scenario.from_dict(data["scenario"])
        return cls(**data)


@dataclass(frozen=True)
class BreakevenPolicy:
    """Explicit breakeven-classification policy (ADR-012).

    The default is *exact zero*: a trade is breakeven only when its P&L is
    exactly 0. An optional tolerance may be declared either as explicit dollars
    or as a number of instrument ticks (resolved against the contract's dollar
    value per tick). The selected policy is recorded in `Scenario` metadata.
    """

    mode: str = "exact_zero"  # "exact_zero" | "dollars" | "ticks"
    tolerance_dollars: float = 0.0
    ticks: float = 0.0

    def __post_init__(self) -> None:
        if self.mode not in {"exact_zero", "dollars", "ticks"}:
            raise ValueError(f"unsupported breakeven mode: {self.mode!r}")
        if self.tolerance_dollars < 0 or self.ticks < 0:
            raise ValueError("breakeven tolerance cannot be negative")

    def resolved_tolerance(self, dollars_per_tick: float | None = None) -> float:
        if self.mode == "exact_zero":
            return 0.0
        if self.mode == "dollars":
            return self.tolerance_dollars
        if dollars_per_tick is None:
            raise ValueError("ticks mode requires an instrument dollars_per_tick value")
        return self.ticks * dollars_per_tick

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "tolerance_dollars": self.tolerance_dollars, "ticks": self.ticks}


DEFAULT_BREAKEVEN_POLICY = BreakevenPolicy()


@dataclass(frozen=True)
class VerificationReport:
    """Result of checking exported results against their declared provenance."""

    ok: bool
    checks: dict[str, bool]
    details: dict[str, Any]

    def __bool__(self) -> bool:
        return self.ok

    def failures(self) -> list[str]:
        return [name for name, passed in self.checks.items() if not passed]


def classify_result(pnl_dollars: float, tolerance: float = 0.0) -> TradeResult:
    """Classify a single trade. Default tolerance is exact zero (ADR-012)."""

    if pnl_dollars > tolerance:
        return "win"
    if pnl_dollars < -tolerance:
        return "loss"
    return "breakeven"


def _month_start_utc(month: pd.Period) -> pd.Timestamp:
    return pd.Timestamp(month.start_time).tz_localize("UTC")


def _month_end_utc(month: pd.Period) -> pd.Timestamp:
    return _month_start_utc(month + 1) - pd.Timedelta(nanoseconds=1)
