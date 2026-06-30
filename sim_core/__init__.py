"""Version 1 simulation core public API."""

from sim_core.execution.replay import FixedContractPortfolio, run_fixed_contract_simulation
from sim_core.ingestion.csv_loader import load_trade_csv, load_trade_csvs
from sim_core.metrics.reports import monthly_equity_percentiles, ruin_probability, summarize_paths
from sim_core.models import (
    AccountConfig,
    EquityPoint,
    ResampledPath,
    SampledBlock,
    SimulationResult,
    StrategyMetadata,
    Trade,
    ValidationIssue,
)
from sim_core.resampling.policies import (
    HistoricalReplay,
    MovingBlockBootstrap,
    SameCalendarMonthBootstrap,
    StationaryBlockBootstrap,
)

__all__ = [
    "AccountConfig",
    "EquityPoint",
    "FixedContractPortfolio",
    "HistoricalReplay",
    "MovingBlockBootstrap",
    "ResampledPath",
    "SameCalendarMonthBootstrap",
    "SampledBlock",
    "SimulationResult",
    "StationaryBlockBootstrap",
    "StrategyMetadata",
    "Trade",
    "ValidationIssue",
    "load_trade_csv",
    "load_trade_csvs",
    "monthly_equity_percentiles",
    "ruin_probability",
    "run_fixed_contract_simulation",
    "summarize_paths",
]
