"""Version 1 simulation core public API."""

from __future__ import annotations

from typing import Any

from sim_core.batch import (
    build_result_distribution,
    hash_trades,
    run_simulation_ensemble,
    scenario_hash,
    verify_result_provenance,
)
from sim_core.diagnostics.coverage import CoverageReport, build_coverage_report
from sim_core.execution.ensemble import run_path_ensemble
from sim_core.exposure import (
    ExposureReport,
    InstrumentMargin,
    MarginPolicy,
    apply_margin_cap,
    build_exposure_report,
)
from sim_core.execution.replay import FixedContractPortfolio, run_fixed_contract_simulation
from sim_core.ingestion.csv_loader import (
    load_canonical_margin_csv,
    load_trade_csv,
    load_trade_csvs,
    normalize_canonical_margin_frame,
    normalize_trade_frame,
)
from sim_core.instruments import (
    DEFAULT_INSTRUMENT_REGISTRY,
    build_specs_from_registry,
    get_instrument_spec,
)
from sim_core.live_account import (
    AccountEvent,
    AccountState,
    CashFlow,
    CashFlowPolicy,
    FixedContractSizing,
    FixedDollarRiskSizing,
    LiveAccountConfig,
    LiveAccountPathResult,
    PercentEquitySizing,
    SizingDecision,
    StrategyAllocation,
    decide_contracts,
    run_live_account_path,
    summarize_live_account_paths,
)
from sim_core.metrics.reports import (
    monthly_equity_percentiles,
    ruin_probability,
    summarize_paths,
    trade_outcome_taxonomy,
)
from sim_core.prop_firm import (
    PayoutRecord,
    PropAccountResult,
    PropFirmRules,
    PropPhaseEvent,
    run_prop_account_path,
    run_prop_account_portfolio,
    summarize_prop_accounts,
)
from sim_core.models import (
    AccountConfig,
    BreakevenPolicy,
    EquityPoint,
    InstrumentSpec,
    ResampledPath,
    ResultDistribution,
    SampledBlock,
    Scenario,
    SimulationResult,
    StrategyCoverage,
    StrategyMetadata,
    Trade,
    ValidationIssue,
    VerificationReport,
)
from sim_core.resampling.policies import (
    HistoricalReplay,
    MovingBlockBootstrap,
    SameCalendarMonthBootstrap,
    StationaryBlockBootstrap,
)

__all__ = [
    "AccountConfig",
    "AccountEvent",
    "AccountState",
    "BreakevenPolicy",
    "build_coverage_report",
    "build_integration_report",
    "build_result_distribution",
    "build_specs_from_registry",
    "CashFlow",
    "CashFlowPolicy",
    "apply_margin_cap",
    "build_exposure_report",
    "CoverageReport",
    "decide_contracts",
    "EquityPoint",
    "ExposureReport",
    "InstrumentMargin",
    "MarginPolicy",
    "FixedContractSizing",
    "FixedContractPortfolio",
    "FixedDollarRiskSizing",
    "HistoricalReplay",
    "InstrumentSpec",
    "LiveAccountConfig",
    "LiveAccountPathResult",
    "MovingBlockBootstrap",
    "PercentEquitySizing",
    "PayoutRecord",
    "PropAccountResult",
    "PropFirmRules",
    "PropPhaseEvent",
    "run_prop_account_path",
    "run_prop_account_portfolio",
    "summarize_prop_accounts",
    "ResampledPath",
    "ResultDistribution",
    "SameCalendarMonthBootstrap",
    "SampledBlock",
    "Scenario",
    "SimulationResult",
    "SizingDecision",
    "StrategyCoverage",
    "StationaryBlockBootstrap",
    "StrategyAllocation",
    "StrategyMetadata",
    "Trade",
    "ValidationIssue",
    "VerificationReport",
    "DEFAULT_INSTRUMENT_REGISTRY",
    "get_instrument_spec",
    "hash_trades",
    "load_canonical_margin_csv",
    "load_trade_csv",
    "load_trade_csvs",
    "monthly_equity_percentiles",
    "normalize_canonical_margin_frame",
    "normalize_trade_frame",
    "run_live_account_path",
    "run_path_ensemble",
    "ruin_probability",
    "run_fixed_contract_simulation",
    "run_simulation_ensemble",
    "scenario_hash",
    "summarize_paths",
    "summarize_live_account_paths",
    "trade_outcome_taxonomy",
    "verify_result_provenance",
]


def __getattr__(name: str) -> Any:
    if name == "build_integration_report":
        from sim_core.integration.real_ledger import build_integration_report

        return build_integration_report
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
