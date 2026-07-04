"""MEDIUM-R3-A — provenance self-verification.

verify_result_provenance detects changed input data, a tampered/mismatched
scenario, a different engine version, and drift in seed/paths/policy/mappings/
commissions.
"""
from __future__ import annotations

import pandas as pd

from sim_core.batch import hash_trades, run_simulation_ensemble, scenario_hash, verify_result_provenance
from sim_core.ingestion.csv_loader import normalize_trade_frame
from sim_core.models import Scenario
from sim_core.resampling.policies import SameCalendarMonthBootstrap

POLICY = SameCalendarMonthBootstrap(months=2, start_month="2026-01")


def _trades(bump: float = 0.0):
    rows = []
    for year in (2024, 2025):
        for month in (1, 2):
            rows.append(
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": f"{year}-{month:02d}-05T09:30:00Z",
                    "exit_time": f"{year}-{month:02d}-05T10:00:00Z",
                    "pnl_dollars": 10 + month + bump,
                }
            )
    return normalize_trade_frame(pd.DataFrame(rows))


def _scenario(trades, *, seed: int = 1234, engine_version: str | None = None) -> Scenario:
    kwargs = dict(
        scenario_id="prov",
        master_seed=seed,
        number_of_paths=4,
        horizon_months=2,
        starting_equity=100_000,
        resampling_method="same_calendar_month_bootstrap",
        resampling_params={"months": 2, "start_month": "2026-01"},
        contract_mappings={"s": {"underlying": "ES", "contract_symbol": "MES", "dpp": 5.0}},
        commission_assumptions={"s": 0.0},
        input_data_hash=hash_trades(trades),
    )
    if engine_version is not None:
        kwargs["engine_version"] = engine_version
    return Scenario(**kwargs)


def test_provenance_round_trip_ok():
    trades = _trades()
    scenario = _scenario(trades)
    _, distribution = run_simulation_ensemble(scenario, trades, POLICY)
    report = verify_result_provenance(distribution, scenario, trades)
    assert report.ok
    assert all(report.checks.values())


def test_provenance_detects_changed_input_data():
    trades = _trades()
    scenario = _scenario(trades)
    _, distribution = run_simulation_ensemble(scenario, trades, POLICY)
    report = verify_result_provenance(distribution, scenario, _trades(bump=1.0))
    assert not report.ok
    assert report.checks["input_data_hash"] is False
    assert "input_data_hash" in report.failures()


def test_provenance_detects_tampered_scenario():
    trades = _trades()
    scenario = _scenario(trades, seed=1234)
    _, distribution = run_simulation_ensemble(scenario, trades, POLICY)
    tampered = _scenario(trades, seed=9999)
    report = verify_result_provenance(distribution, tampered, trades)
    assert not report.ok
    assert report.checks["master_seed"] is False
    assert report.checks["scenario_hash"] is False
    assert scenario_hash(scenario) != scenario_hash(tampered)


def test_provenance_detects_engine_version_drift():
    trades = _trades()
    scenario = _scenario(trades, engine_version="9.9.9")
    _, distribution = run_simulation_ensemble(scenario, trades, POLICY)
    report = verify_result_provenance(distribution, scenario, trades)
    assert report.checks["engine_version"] is False
    assert not report.ok
