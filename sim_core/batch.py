from __future__ import annotations

import hashlib
import warnings
from collections.abc import Sequence

import numpy as np
import pandas as pd

from sim_core.diagnostics.coverage import build_coverage_report
from sim_core.execution.replay import run_fixed_contract_simulation
from sim_core.metrics.reports import (
    max_drawdown,
    monthly_equity_percentiles,
    ruin_probability,
    trade_outcome_taxonomy,
)
from sim_core.models import (
    ENGINE_VERSION,
    AccountConfig,
    FixedContractPortfolio,
    ResultDistribution,
    Scenario,
    SimulationResult,
    StrategyCoverage,
    Trade,
    VerificationReport,
)
from sim_core.resampling.policies import ResamplingPolicy


KNOWN_V1_LIMITATIONS = [
    "V1 books realized P&L at trade exit only; no intratrade mark-to-market.",
    "V1 does not model deposits, withdrawals, margin, prop-firm rules, or optimization.",
    "Month shifting preserves source offset when possible and clamps to target month end.",
]


def hash_trades(trades: Sequence[Trade]) -> str:
    digest = hashlib.sha256()
    for trade in sorted(trades, key=lambda item: item.source_row_id):
        digest.update(
            "|".join(
                [
                    trade.source_row_id,
                    trade.strategy_id,
                    trade.instrument,
                    trade.contract_symbol or "",
                    trade.entry_time.isoformat(),
                    trade.exit_time.isoformat(),
                    f"{trade.pnl_dollars:.12f}",
                    f"{trade.commission_round_turn:.12f}",
                ]
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return digest.hexdigest()


def scenario_hash(scenario: Scenario) -> str:
    """Stable hash of the fully-specified scenario configuration."""

    return hashlib.sha256(scenario.to_json().encode("utf-8")).hexdigest()


def verify_result_provenance(
    result: ResultDistribution,
    scenario: Scenario,
    source_data: Sequence[Trade],
) -> VerificationReport:
    """Check that an exported result matches its declared scenario and input data.

    Detects changed input data (via recomputed `hash_trades`), a mismatched or
    tampered scenario (via `scenario_hash`), a different engine version, and any
    drift in master seed, path count, resampling policy, strategy mappings, or
    commission assumptions.
    """

    recomputed = hash_trades(source_data)
    embedded = result.scenario
    checks: dict[str, bool] = {
        "input_data_hash": recomputed == scenario.input_data_hash == result.data_hash,
        "scenario_hash": scenario_hash(scenario) == scenario_hash(embedded),
        "engine_version": embedded.engine_version == ENGINE_VERSION == scenario.engine_version,
        "resampling_policy": embedded.resampling_method == scenario.resampling_method,
        "master_seed": embedded.master_seed == scenario.master_seed,
        "number_of_paths": embedded.number_of_paths == scenario.number_of_paths,
        "strategy_mappings": embedded.contract_mappings == scenario.contract_mappings,
        "commission_assumptions": embedded.commission_assumptions == scenario.commission_assumptions,
    }
    details = {
        "recomputed_data_hash": recomputed,
        "scenario_declared_data_hash": scenario.input_data_hash,
        "result_data_hash": result.data_hash,
        "scenario_hash": scenario_hash(scenario),
        "embedded_scenario_hash": scenario_hash(embedded),
        "engine_version": ENGINE_VERSION,
    }
    return VerificationReport(ok=all(checks.values()), checks=checks, details=details)


def run_simulation_ensemble(
    scenario: Scenario,
    trades: Sequence[Trade],
    policy: ResamplingPolicy,
    *,
    coverage: Sequence[StrategyCoverage] | None = None,
) -> tuple[list[SimulationResult], ResultDistribution]:
    """Run a path ensemble and build the result distribution with provenance."""
    account = AccountConfig(
        initial_equity=scenario.starting_equity,
        ruin_threshold=scenario.ruin_threshold,
    )
    portfolio = FixedContractPortfolio(
        strategy_contracts=scenario.fixed_contract_quantities,
        default_contracts=1,
    )
    results: list[SimulationResult] = []
    for path_index in range(scenario.number_of_paths):
        sampled = policy.sample(
            trades,
            seed=scenario.master_seed,
            path_index=path_index,
            coverage=coverage,
        )
        results.append(
            run_fixed_contract_simulation(
                sampled,
                account=account,
                portfolio=portfolio,
            )
        )

    distribution = build_result_distribution(scenario, trades, results, coverage=coverage)
    return results, distribution


def build_result_distribution(
    scenario: Scenario,
    trades: Sequence[Trade],
    results: Sequence[SimulationResult],
    *,
    coverage: Sequence[StrategyCoverage] | None = None,
) -> ResultDistribution:
    months = _scenario_months(scenario)
    monthly = monthly_equity_percentiles(results, months=months).to_dict(orient="records")
    terminal = np.array([result.terminal_equity for result in results], dtype=float)
    drawdowns = [max_drawdown(result) for result in results]
    computed_data_hash = hash_trades(trades)
    if scenario.input_data_hash and scenario.input_data_hash != computed_data_hash:
        warnings.warn(
            "scenario.input_data_hash does not match the supplied trades; the "
            "computed hash is recorded as authoritative provenance",
            RuntimeWarning,
            stacklevel=2,
        )
    coverage_report = build_coverage_report(trades, coverage)
    diagnostics = {
        "path_count": len(results),
        "resampling_method": scenario.resampling_method,
        "computed_input_data_hash": computed_data_hash,
        "scenario_declared_data_hash": scenario.input_data_hash,
        "coverage": coverage_report.to_records(),
        "coverage_strategies": coverage_report.strategies,
        "sampled_blocks": [
            {
                "path_index": block.path_index,
                "target_month": str(block.target_month),
                "source_month": str(block.source_month),
                "policy_name": block.policy_name,
            }
            for result in results
            for block in result.sampled_blocks
        ],
    }
    return ResultDistribution(
        scenario=scenario,
        monthly_percentiles=monthly,
        terminal_equity_distribution={
            "min": float(terminal.min()) if len(terminal) else scenario.starting_equity,
            "p5": float(np.percentile(terminal, 5)) if len(terminal) else scenario.starting_equity,
            "median": float(np.percentile(terminal, 50)) if len(terminal) else scenario.starting_equity,
            "p95": float(np.percentile(terminal, 95)) if len(terminal) else scenario.starting_equity,
            "max": float(terminal.max()) if len(terminal) else scenario.starting_equity,
        },
        drawdown_metrics=drawdowns,
        ruin_probability=ruin_probability(results, scenario.ruin_threshold),
        outcome_taxonomy=trade_outcome_taxonomy(trades),
        resampling_diagnostics=diagnostics,
        warnings=coverage_report.warnings(),
        known_limitations=KNOWN_V1_LIMITATIONS,
        data_hash=computed_data_hash,
    )


def _scenario_months(scenario: Scenario) -> list[pd.Period]:
    start_month = scenario.resampling_params.get("start_month")
    if start_month is None:
        return []
    start = pd.Period(start_month, "M")
    return [start + offset for offset in range(scenario.horizon_months)]
