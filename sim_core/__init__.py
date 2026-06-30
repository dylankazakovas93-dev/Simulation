"""Version 1 simulation core public API."""

from sim_core.execution.replay import FixedContractPortfolio, run_fixed_contract_simulation
from sim_core.ingestion.csv_loader import (
    load_canonical_margin_csv,
    load_trade_csv,
    load_trade_csvs,
    normalize_canonical_margin_frame,
)
from sim_core.instruments import DEFAULT_INSTRUMENT_REGISTRY, get_instrument_spec
from sim_core.metrics.reports import (
    monthly_equity_percentiles,
    ruin_probability,
    summarize_paths,
    trade_outcome_taxonomy,
)
from sim_core.models import (
    AccountConfig,
    EquityPoint,
    InstrumentSpec,
    ResampledPath,
    SampledBlock,
    SimulationResult,
    StrategyCoverage,
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
    "InstrumentSpec",
    "MovingBlockBootstrap",
    "ResampledPath",
    "SameCalendarMonthBootstrap",
    "SampledBlock",
    "SimulationResult",
    "StrategyCoverage",
    "StationaryBlockBootstrap",
    "StrategyMetadata",
    "Trade",
    "ValidationIssue",
    "DEFAULT_INSTRUMENT_REGISTRY",
    "get_instrument_spec",
    "load_canonical_margin_csv",
    "load_trade_csv",
    "load_trade_csvs",
    "monthly_equity_percentiles",
    "normalize_canonical_margin_frame",
    "ruin_probability",
    "run_fixed_contract_simulation",
    "summarize_paths",
    "trade_outcome_taxonomy",
]
