from __future__ import annotations

import pandas as pd

from sim_core.exports import export_simulation_result
from sim_core.execution.replay import run_fixed_contract_simulation
from sim_core.ingestion.csv_loader import normalize_trade_frame
from sim_core.metrics.reports import trade_outcome_taxonomy
from sim_core.models import AccountConfig, FixedContractPortfolio, StrategyCoverage
from sim_core.resampling.policies import (
    HistoricalReplay,
    SameCalendarMonthBootstrap,
    StationaryBlockBootstrap,
)


def test_stable_deterministic_tie_ordering_uses_source_row_id():
    trades = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "b",
                    "instrument": "ES",
                    "entry_time": "2025-01-02T09:31:00Z",
                    "exit_time": "2025-01-02T10:00:00Z",
                    "pnl_dollars": 1,
                    "source_row_id": "row-2",
                },
                {
                    "strategy_id": "a",
                    "instrument": "ES",
                    "entry_time": "2025-01-02T09:31:00Z",
                    "exit_time": "2025-01-02T10:00:00Z",
                    "pnl_dollars": 1,
                    "source_row_id": "row-1",
                },
            ]
        )
    )

    result = run_fixed_contract_simulation(trades, account=AccountConfig(initial_equity=100))

    assert [(point.strategy_id, point.source_row_id) for point in result.equity_path] == [
        ("a", "row-1"),
        ("b", "row-2"),
    ]


def test_historical_replay_preserves_full_ledger_order_and_equity():
    trades = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s1",
                    "instrument": "ES",
                    "entry_time": "2025-01-01T09:30:00Z",
                    "exit_time": "2025-01-01T10:00:00Z",
                    "pnl_dollars": 10,
                    "source_row_id": "1",
                },
                {
                    "strategy_id": "s2",
                    "instrument": "NQ",
                    "entry_time": "2025-01-01T09:40:00Z",
                    "exit_time": "2025-01-01T10:10:00Z",
                    "pnl_dollars": -5,
                    "source_row_id": "2",
                },
            ]
        )
    )

    replay = HistoricalReplay().sample(trades)
    result = run_fixed_contract_simulation(replay, account=AccountConfig(initial_equity=100))

    assert [trade.source_row_id for trade in replay.trades] == ["1", "2"]
    assert result.terminal_equity == 105


def test_seasonal_month_matching_over_many_seeds():
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
            ]
        )
    )

    for seed in range(20):
        path = SameCalendarMonthBootstrap(months=2, start_month="2026-01").sample(
            trades, seed=seed
        )
        assert all(
            block.source_month.month == block.target_month.month for block in path.sampled_blocks
        )


def test_flat_verified_zero_trade_month_remains_sampleable():
    trades = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2025-01-03T09:30:00Z",
                    "exit_time": "2025-01-03T10:00:00Z",
                    "pnl_dollars": 1,
                }
            ]
        )
    )
    coverage = [StrategyCoverage("s", "ES", "2025-01", "2025-02")]

    path = SameCalendarMonthBootstrap(months=2, start_month="2026-01").sample(
        trades, seed=1, coverage=coverage
    )

    assert [block.source_month for block in path.sampled_blocks] == [
        pd.Period("2025-01", "M"),
        pd.Period("2025-02", "M"),
    ]
    assert [pd.Period(trade.entry_time.strftime("%Y-%m"), "M") for trade in path.trades] == [
        pd.Period("2026-01", "M")
    ]


def test_partial_month_is_excluded_when_coverage_declares_it_partial():
    trades = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2025-02-03T09:30:00Z",
                    "exit_time": "2025-02-03T10:00:00Z",
                    "pnl_dollars": 1,
                }
            ]
        )
    )
    coverage = [StrategyCoverage("s", "ES", "2025-02", "2025-02", {"2025-02"})]

    try:
        SameCalendarMonthBootstrap(months=1, start_month="2026-02").sample(
            trades, seed=1, coverage=coverage
        )
    except ValueError as exc:
        assert "no source months" in str(exc)
    else:
        raise AssertionError("partial month should not be sampled")


def test_stationary_bootstrap_resamples_at_source_boundary_without_silent_wrap():
    trades = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2025-01-03T09:30:00Z",
                    "exit_time": "2025-01-03T10:00:00Z",
                    "pnl_dollars": 1,
                },
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2025-02-03T09:30:00Z",
                    "exit_time": "2025-02-03T10:00:00Z",
                    "pnl_dollars": 2,
                },
            ]
        )
    )

    path = StationaryBlockBootstrap(months=4, expected_block_length=99).sample(trades, seed=5)

    assert len(path.sampled_blocks) == 4
    assert any(
        later.source_month <= earlier.source_month
        for earlier, later in zip(path.sampled_blocks, path.sampled_blocks[1:])
    )


def test_explicit_outcome_taxonomy_excludes_breakevens_from_true_rate():
    trades = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2025-01-01T09:30:00Z",
                    "exit_time": "2025-01-01T10:00:00Z",
                    "pnl_dollars": 10,
                },
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2025-01-02T09:30:00Z",
                    "exit_time": "2025-01-02T10:00:00Z",
                    "pnl_dollars": -5,
                },
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2025-01-03T09:30:00Z",
                    "exit_time": "2025-01-03T10:00:00Z",
                    "pnl_dollars": 0,
                },
            ]
        )
    )

    taxonomy = trade_outcome_taxonomy(trades)

    assert taxonomy["true_win_rate_excluding_breakevens"] == 0.5
    assert taxonomy["breakeven_frequency_over_total"] == 1 / 3


def test_export_consistency_round_trips_equity_path_columns(tmp_path):
    trades = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2025-01-01T09:30:00Z",
                    "exit_time": "2025-01-01T10:00:00Z",
                    "pnl_dollars": 10,
                    "source_row_id": "row-1",
                }
            ]
        )
    )
    result = run_fixed_contract_simulation(
        trades,
        account=AccountConfig(initial_equity=100),
        portfolio=FixedContractPortfolio(default_contracts=2),
    )

    exported = export_simulation_result(result, tmp_path)
    frame = pd.read_csv(exported["equity_path"])

    assert frame.loc[0, "source_row_id"] == "row-1"
    assert frame.loc[0, "net_pnl"] == 20
    assert frame.loc[0, "equity"] == 120
