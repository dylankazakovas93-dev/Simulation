from __future__ import annotations

import pandas as pd
import pytest

from sim_core.ingestion.csv_loader import load_trade_csv, normalize_trade_frame
from sim_core.models import StrategyMetadata, TradeValidationError


def test_trade_ordering_is_chronological_after_load(tmp_path):
    path = tmp_path / "unordered.csv"
    path.write_text(
        "\n".join(
            [
                "strategy_id,instrument,entry_time,exit_time,pnl_dollars",
                "s1,ES,2024-02-02T09:30:00Z,2024-02-02T10:00:00Z,100",
                "s1,ES,2024-01-02T09:30:00Z,2024-01-02T10:00:00Z,-50",
            ]
        )
    )

    trades = load_trade_csv(path)

    assert [trade.entry_time for trade in trades] == sorted(trade.entry_time for trade in trades)
    assert [trade.pnl_dollars for trade in trades] == [-50.0, 100.0]


def test_metadata_stays_attached_to_specific_instrument():
    frame = pd.DataFrame(
        [
            {
                "strategy_id": "expanded",
                "instrument": "ES",
                "entry_time": "2024-01-02T09:30:00Z",
                "exit_time": "2024-01-02T10:00:00Z",
                "pnl_points": 2,
            },
        ]
    )

    trades = normalize_trade_frame(
        frame,
        metadata=StrategyMetadata("expanded", "ES", dollars_per_point=50),
    )

    assert trades[0].instrument == "ES"
    assert trades[0].dollars_per_point == 50
    assert trades[0].pnl_dollars == 100


def test_es_configuration_does_not_modify_nq_configuration():
    es = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "expanded",
                    "instrument": "ES",
                    "entry_time": "2024-01-02T09:30:00Z",
                    "exit_time": "2024-01-02T10:00:00Z",
                    "pnl_points": 2,
                }
            ]
        ),
        metadata=StrategyMetadata("expanded", "ES", dollars_per_point=50),
    )
    nq = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "expanded",
                    "instrument": "NQ",
                    "entry_time": "2024-01-02T09:30:00Z",
                    "exit_time": "2024-01-02T10:00:00Z",
                    "pnl_points": 2,
                }
            ]
        ),
        metadata=StrategyMetadata("expanded", "NQ", dollars_per_point=20),
    )

    assert es[0].pnl_dollars == 100
    assert nq[0].pnl_dollars == 40
    assert es[0].dollars_per_point != nq[0].dollars_per_point


def test_mismatched_metadata_is_not_applied_to_other_instrument():
    frame = pd.DataFrame(
        [
            {
                "strategy_id": "expanded",
                "instrument": "NQ",
                "entry_time": "2024-01-02T09:30:00Z",
                "exit_time": "2024-01-02T10:00:00Z",
                "pnl_points": 2,
            }
        ]
    )

    with pytest.raises(TradeValidationError):
        normalize_trade_frame(
            frame,
            metadata=StrategyMetadata("expanded", "ES", dollars_per_point=50),
        )


def test_breakeven_trades_are_not_classified_as_losses():
    trades = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s1",
                    "instrument": "ES",
                    "entry_time": "2024-01-02T09:30:00Z",
                    "exit_time": "2024-01-02T10:00:00Z",
                    "pnl_dollars": 0,
                }
            ]
        )
    )

    assert trades[0].result_type == "breakeven"


def test_duplicate_trades_raise_validation_error():
    frame = pd.DataFrame(
        [
            {
                "strategy_id": "s1",
                "instrument": "ES",
                "entry_time": "2024-01-02T09:30:00Z",
                "exit_time": "2024-01-02T10:00:00Z",
                "pnl_dollars": 0,
            },
            {
                "strategy_id": "s1",
                "instrument": "ES",
                "entry_time": "2024-01-02T09:30:00Z",
                "exit_time": "2024-01-02T10:00:00Z",
                "pnl_dollars": 0,
            },
        ]
    )

    with pytest.raises(TradeValidationError):
        normalize_trade_frame(frame)
