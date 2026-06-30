from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from sim_core.batch import hash_trades, run_simulation_ensemble
from sim_core.exports import export_simulation_batch
from sim_core.ingestion.csv_loader import normalize_trade_frame
from sim_core.metrics.reports import monthly_equity_percentiles
from sim_core.models import AccountConfig, FixedContractPortfolio, Scenario, TradeValidationError
from sim_core.resampling.policies import SameCalendarMonthBootstrap
from sim_core.execution.replay import run_fixed_contract_simulation


def _scenario(data_hash: str = "abc123") -> Scenario:
    return Scenario(
        scenario_id="v1-regression",
        name="V1 regression",
        master_seed=1234,
        number_of_paths=4,
        horizon_months=2,
        starting_equity=100_000,
        selected_strategies=["s"],
        fixed_contract_quantities={"s": 1},
        commission_assumptions={"s": 0.0},
        resampling_method="same_calendar_month_bootstrap",
        resampling_params={"months": 2, "start_month": "2026-01"},
        coverage_policy={},
        ruin_threshold=0.0,
        currency="USD",
        contract_mappings={"s": {"underlying": "ES", "contract_symbol": "MES", "dpp": 5.0}},
        input_data_hash=data_hash,
    )


def test_naive_timestamps_rejected_unless_source_timezone_configured():
    frame = pd.DataFrame(
        [
            {
                "strategy_id": "s",
                "instrument": "ES",
                "entry_time": "2025-01-02 09:30",
                "exit_time": "2025-01-02 10:00",
                "pnl_dollars": 1,
            }
        ]
    )

    with pytest.raises(TradeValidationError):
        normalize_trade_frame(frame, source_timezone=None)

    trades = normalize_trade_frame(frame, source_timezone="America/Chicago")
    assert str(trades[0].entry_time.tz) == "UTC"
    assert trades[0].entry_time.hour == 15


def test_utc_timestamps_are_preserved_in_normalized_trades():
    trades = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2025-01-02T09:30:00Z",
                    "exit_time": "2025-01-02T10:00:00Z",
                    "pnl_dollars": 1,
                }
            ]
        )
    )

    assert trades[0].entry_time.isoformat() == "2025-01-02T09:30:00+00:00"


def test_january_31_shift_to_february_clamps_inside_target_month():
    trade = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2025-01-31T12:00:00Z",
                    "exit_time": "2025-01-31T13:00:00Z",
                    "pnl_dollars": 1,
                }
            ]
        )
    )[0]

    shifted = trade.shifted_to_month(pd.Period("2025-02", "M"))

    assert shifted.target_month == pd.Period("2025-02", "M")
    assert shifted.entry_time.month == 2
    assert shifted.exit_time.month == 2
    assert shifted.entry_time <= shifted.exit_time


def test_leap_day_shift_to_non_leap_february_clamps_inside_target_month():
    trade = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2024-02-29T16:00:00Z",
                    "exit_time": "2024-02-29T16:30:00Z",
                    "pnl_dollars": 1,
                }
            ]
        )
    )[0]

    shifted = trade.shifted_to_month(pd.Period("2025-02", "M"))

    assert shifted.entry_time.month == 2
    assert shifted.entry_time.day == 28
    assert shifted.exit_time.month == 2


def test_crossing_source_month_boundary_never_crosses_target_month_boundary():
    trade = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2025-01-31T23:00:00Z",
                    "exit_time": "2025-02-01T01:00:00Z",
                    "pnl_dollars": 1,
                }
            ]
        )
    )[0]

    shifted = trade.shifted_to_month(pd.Period("2025-02", "M"))

    assert shifted.entry_time.month == 2
    assert shifted.exit_time.month == 2
    assert shifted.exit_time >= shifted.entry_time


def test_ensemble_paths_are_independent_and_reproducible():
    trades = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2024-01-03T09:30:00Z",
                    "exit_time": "2024-01-03T10:00:00Z",
                    "pnl_dollars": 1,
                },
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2025-01-03T09:30:00Z",
                    "exit_time": "2025-01-03T10:00:00Z",
                    "pnl_dollars": 2,
                },
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2024-02-03T09:30:00Z",
                    "exit_time": "2024-02-03T10:00:00Z",
                    "pnl_dollars": 3,
                },
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2025-02-03T09:30:00Z",
                    "exit_time": "2025-02-03T10:00:00Z",
                    "pnl_dollars": 4,
                },
            ]
        )
    )
    scenario = _scenario(hash_trades(trades))
    policy = SameCalendarMonthBootstrap(months=2, start_month="2026-01")

    first, first_distribution = run_simulation_ensemble(scenario, trades, policy)
    second, second_distribution = run_simulation_ensemble(scenario, trades, policy)
    changed_seed, _ = run_simulation_ensemble(
        Scenario.from_json(scenario.to_json().replace('"master_seed": 1234', '"master_seed": 4321')),
        trades,
        policy,
    )

    assert first_distribution.to_json() == second_distribution.to_json()
    assert [r.sampled_blocks for r in first] == [r.sampled_blocks for r in second]
    assert len({tuple(block.source_month for block in r.sampled_blocks) for r in first}) > 1
    assert [r.sampled_blocks for r in first] != [r.sampled_blocks for r in changed_seed]


def test_scenario_and_result_distribution_json_round_trip():
    scenario = _scenario()
    trades = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2024-01-03T09:30:00Z",
                    "exit_time": "2024-01-03T10:00:00Z",
                    "pnl_dollars": 1,
                }
            ]
        )
    )
    _, distribution = run_simulation_ensemble(
        scenario,
        trades,
        SameCalendarMonthBootstrap(months=1, start_month="2026-01"),
    )

    assert Scenario.from_json(scenario.to_json()) == scenario
    assert distribution.from_json(distribution.to_json()) == distribution


def test_batch_export_includes_result_distribution_provenance(tmp_path: Path):
    trades = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2024-01-03T09:30:00Z",
                    "exit_time": "2024-01-03T10:00:00Z",
                    "pnl_dollars": 1,
                }
            ]
        )
    )
    scenario = _scenario(hash_trades(trades))
    results, distribution = run_simulation_ensemble(
        scenario,
        trades,
        SameCalendarMonthBootstrap(months=1, start_month="2026-01"),
    )

    exported = export_simulation_batch(
        results,
        tmp_path,
        scenario=scenario,
        distribution=distribution,
    )
    payload = json.loads(exported["result_distribution"].read_text(encoding="utf-8"))

    assert payload["scenario"]["scenario_id"] == "v1-regression"
    assert payload["data_hash"] == scenario.input_data_hash
    assert payload["known_limitations"]


def test_monthly_percentiles_carry_forward_all_paths():
    january = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2025-01-03T09:30:00Z",
                    "exit_time": "2025-01-03T10:00:00Z",
                    "pnl_dollars": 10,
                }
            ]
        )
    )
    march = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2025-03-03T09:30:00Z",
                    "exit_time": "2025-03-03T10:00:00Z",
                    "pnl_dollars": 30,
                }
            ]
        )
    )
    results = [
        run_fixed_contract_simulation(january, account=AccountConfig(initial_equity=100)),
        run_fixed_contract_simulation(march, account=AccountConfig(initial_equity=100)),
    ]

    frame = monthly_equity_percentiles(
        results,
        percentiles=(50,),
        months=[pd.Period("2025-01", "M"), pd.Period("2025-02", "M"), pd.Period("2025-03", "M")],
    )

    assert list(frame["month"]) == ["2025-01", "2025-02", "2025-03"]
    assert list(frame["p50"]) == [105.0, 105.0, 120.0]
