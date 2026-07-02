"""V6 — UI controller: the ONLY bridge between the Streamlit view and the engine.

Pure Python, no Streamlit import, fully unit-testable. Every function delegates all
computation to `sim_core` (no modelling logic lives here) and attaches the mandatory
model-risk disclosures so the view cannot render a number without its caveats. This
enforces the charter's "engine and UI must be separate" rule and its governing
principle that assumptions/limitations are explicit, not hidden behind pretty charts.
"""
from __future__ import annotations

from typing import Any, Sequence

import pandas as pd

from sim_core.diagnostics.coverage import build_coverage_report
from sim_core.batch import run_simulation_ensemble
from sim_core.exposure import MarginPolicy, build_exposure_report
from sim_core.live_account import (
    LiveAccountConfig,
    StrategyAllocation,
    run_live_account_path,
)
from sim_core.models import Scenario, Trade
from sim_core.optimize import Candidate, Constraint, Objective, optimize
from sim_core.prop_firm import (
    PropFirmRules,
    run_prop_account_path,
    summarize_prop_accounts,
)
from sim_core.resampling.policies import (
    HistoricalReplay,
    MovingBlockBootstrap,
    SameCalendarMonthBootstrap,
    StationaryBlockBootstrap,
)

from app.disclosures import for_section

_RESAMPLING_FACTORY = {
    "same_calendar_month_bootstrap": lambda p: SameCalendarMonthBootstrap(
        months=p["months"], start_month=p.get("start_month")
    ),
    "moving_block_bootstrap": lambda p: MovingBlockBootstrap(
        months=p["months"], block_length=p["block_length"], start_month=p.get("start_month")
    ),
    "stationary_block_bootstrap": lambda p: StationaryBlockBootstrap(
        months=p["months"],
        expected_block_length=p["expected_block_length"],
        start_month=p.get("start_month"),
    ),
    "historical_replay": lambda p: HistoricalReplay(),
}


def available_resampling_methods() -> list[str]:
    return list(_RESAMPLING_FACTORY.keys())


def build_policy(method: str, params: dict[str, Any]):
    if method not in _RESAMPLING_FACTORY:
        raise ValueError(f"unknown resampling method {method!r}")
    return _RESAMPLING_FACTORY[method](params)


def run_ensemble(
    trades: Sequence[Trade],
    *,
    method: str,
    resampling_params: dict[str, Any],
    number_of_paths: int,
    master_seed: int,
    starting_equity: float,
    ruin_threshold: float,
    fixed_contract_quantities: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Run the Monte Carlo ensemble and return a view-ready, disclosed result."""

    # Coverage is DECLARED metadata (ADR-016), never inferred from trades, so the
    # UI does not fabricate it — the ensemble runs with coverage=None (which raises
    # the documented coverage-absent warning) and we ALSO surface the standalone
    # coverage diagnostic's warnings (missing months / thin seasonal support).
    coverage = None
    try:
        coverage_warnings = build_coverage_report(list(trades)).warnings()
    except Exception:
        coverage_warnings = []

    selected = sorted({t.strategy_id for t in trades})
    scenario = Scenario(
        name="ui-scenario",
        master_seed=master_seed,
        number_of_paths=number_of_paths,
        starting_equity=starting_equity,
        selected_strategies=selected,
        fixed_contract_quantities=fixed_contract_quantities or {s: 1 for s in selected},
        resampling_method=method,
        resampling_params=resampling_params,
        ruin_threshold=ruin_threshold,
    )
    policy = build_policy(method, resampling_params)
    _results, dist = run_simulation_ensemble(scenario, list(trades), policy, coverage=coverage)

    return {
        "monthly_percentiles": dist.monthly_percentiles,
        "terminal_equity_distribution": dist.terminal_equity_distribution,
        "drawdown_metrics": dist.drawdown_metrics,
        "ruin_probability": dist.ruin_probability,
        "outcome_taxonomy": dist.outcome_taxonomy,
        "resampling_diagnostics": dist.resampling_diagnostics,
        "engine_warnings": list(dist.warnings),
        "coverage_warnings": coverage_warnings,
        "engine_known_limitations": list(dist.known_limitations),
        "data_hash": dist.data_hash,
        "disclosures_ensemble": for_section("ensemble"),
        "disclosures_drawdown": for_section("drawdown"),
    }


def run_live_account(
    trades: Sequence[Trade],
    *,
    starting_equity: float,
    allocations: dict[str, StrategyAllocation],
    cash_flow_policy=None,
    margin_policy: MarginPolicy | None = None,
) -> dict[str, Any]:
    result = run_live_account_path(
        list(trades),
        config=LiveAccountConfig(starting_equity=starting_equity),
        allocations=allocations,
        cash_flow_policy=cash_flow_policy,
        margin_policy=margin_policy,
    )
    payload = {
        "summary": result.summary,
        "terminal_equity": result.terminal_equity,
        "disclosures_live_account": for_section("live_account"),
    }
    if margin_policy is not None:
        exposure = build_exposure_report(result, margin_policy=margin_policy)
        payload["exposure"] = exposure.to_dict()
        payload["disclosures_margin_exposure"] = for_section("margin_exposure")
    return payload


def run_prop_single(trades: Sequence[Trade], rules: PropFirmRules) -> dict[str, Any]:
    result = run_prop_account_path(list(trades), rules)
    return {
        "summary": result.summary,
        "terminal_phase": result.terminal_phase,
        "payouts": [p.to_dict() for p in result.payouts],
        # The headline is realized cash; notional balance is explicitly demoted.
        "headline_net_trader_cash": result.net_trader_cash,
        "notional_terminal_balance_not_wealth": result.terminal_balance,
        "disclosures_prop_firm": for_section("prop_firm"),
    }


def run_prop_ensemble(
    trade_paths: Sequence[Sequence[Trade]], rules: PropFirmRules
) -> dict[str, Any]:
    """Run one prop account per resampled trade path and aggregate cash economics."""

    results = [run_prop_account_path(list(path), rules) for path in trade_paths]
    aggregate = summarize_prop_accounts(results)
    return {
        "aggregate": aggregate,
        "num_paths": len(results),
        "disclosures_prop_firm": for_section("prop_firm"),
    }


def run_optimizer(
    candidates: list[Candidate],
    objectives: list[Objective],
    constraints: list[Constraint] | None = None,
    *,
    allow_single_objective: bool = False,
) -> dict[str, Any]:
    result = optimize(
        candidates, objectives, constraints, allow_single_objective=allow_single_objective
    )
    payload = result.to_dict()
    payload["disclosures_optimizer"] = for_section("optimizer")
    return payload


def frontier_dataframe(optimizer_payload: dict[str, Any]) -> pd.DataFrame:
    """Flatten the Pareto frontier into a table for display."""

    rows = []
    for cand in optimizer_payload.get("pareto_frontier", []):
        row = {"id": cand["id"]}
        row.update({f"metric.{k}": v for k, v in cand["metrics"].items()})
        rows.append(row)
    return pd.DataFrame(rows)
