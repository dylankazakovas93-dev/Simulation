from __future__ import annotations

import pandas as pd

from sim_core.ingestion.csv_loader import normalize_trade_frame
from sim_core.resampling.policies import MovingBlockBootstrap, SameCalendarMonthBootstrap


def _multi_strategy_trades():
    rows = []
    for month in [1, 2, 3, 4, 5, 6]:
        for strategy, instrument, pnl in [("es_open", "ES", 100), ("nq_open", "NQ", -50)]:
            rows.append(
                {
                    "strategy_id": strategy,
                    "instrument": instrument,
                    "entry_time": f"2024-{month:02d}-05 09:30",
                    "exit_time": f"2024-{month:02d}-05 10:00",
                    "pnl_dollars": pnl + month,
                }
            )
    return normalize_trade_frame(pd.DataFrame(rows))


def test_multiple_strategies_use_synchronized_source_months():
    path = SameCalendarMonthBootstrap(months=3, start_month="2025-01").sample(
        _multi_strategy_trades(), seed=7
    )

    assert [block.target_month for block in path.sampled_blocks] == [
        pd.Period("2025-01", "M"),
        pd.Period("2025-02", "M"),
        pd.Period("2025-03", "M"),
    ]
    for block in path.sampled_blocks:
        block_trades = [
            trade
            for trade in path.trades
            if trade.entry_time.to_period("M") == block.target_month
        ]
        assert {trade.strategy_id for trade in block_trades} == {"es_open", "nq_open"}
        assert {trade.metadata["source_month"] for trade in block_trades} == {
            str(block.source_month)
        }


def test_identical_seeds_reproduce_identical_outputs():
    policy = MovingBlockBootstrap(months=5, block_length=2, start_month="2025-01")
    first = policy.sample(_multi_strategy_trades(), seed=123)
    second = policy.sample(_multi_strategy_trades(), seed=123)

    assert first.sampled_blocks == second.sampled_blocks
    assert [(trade.trade_id, trade.entry_time) for trade in first.trades] == [
        (trade.trade_id, trade.entry_time) for trade in second.trades
    ]


def test_different_seeds_produce_different_valid_outputs():
    policy = MovingBlockBootstrap(months=5, block_length=2, start_month="2025-01")
    first = policy.sample(_multi_strategy_trades(), seed=123)
    second = policy.sample(_multi_strategy_trades(), seed=456)

    assert first.sampled_blocks != second.sampled_blocks
    assert len(first.trades) == len(second.trades) == 10
