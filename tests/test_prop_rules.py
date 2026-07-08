from __future__ import annotations

import pandas as pd

from sim_core.ingestion.csv_loader import normalize_trade_frame
from sim_core.prop_rules import (
    default_prop_rule_profiles,
    resolve_overlapping_trades,
    simulate_prop_account,
)


def _trades(rows: list[dict]):
    defaults = {
        "instrument": "NQ",
        "contract_symbol": "MNQ",
        "dollars_per_point": 2,
        "commission_round_turn": 0,
    }
    return normalize_trade_frame(pd.DataFrame([{**defaults, **row} for row in rows]))


def test_overlap_resolver_keeps_priority_trade_even_when_lower_priority_entered_first():
    trades = _trades(
        [
            {
                "strategy_id": "slow",
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T12:00:00Z",
                "pnl_points": 10,
                "source_row_id": "slow-1",
            },
            {
                "strategy_id": "priority",
                "entry_time": "2026-01-02T11:00:00Z",
                "exit_time": "2026-01-02T11:30:00Z",
                "pnl_points": 20,
                "source_row_id": "priority-1",
            },
        ]
    )

    kept, decisions = resolve_overlapping_trades(trades, ["priority", "slow"])

    assert [trade.strategy_id for trade in kept] == ["priority"]
    dropped = [decision for decision in decisions if not decision.kept]
    assert dropped[0].strategy_id == "slow"
    assert dropped[0].conflicting_strategy_id == "priority"


def test_alpha_premium_50k_minimum_payout_needs_five_200_winning_days_and_1000_profit():
    trades = _trades(
        [
            {
                "strategy_id": "nq",
                "entry_time": f"2026-01-0{day}T10:00:00Z",
                "exit_time": f"2026-01-0{day}T11:00:00Z",
                "pnl_points": 100,
                "source_row_id": f"win-{day}",
            }
            for day in range(1, 6)
        ]
    )
    profile = default_prop_rule_profiles()["Alpha Futures - Premium 50K"]

    result = simulate_prop_account(trades, profile, contracts=1)

    assert result.eligible is True
    assert result.gross_cash_available == 500
    assert result.payout_after_split == 450
    assert result.first_eligible_day == 4


def test_alpha_eod_trailing_floor_moves_after_closed_day_high():
    trades = _trades(
        [
            {
                "strategy_id": "nq",
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": 250,
                "source_row_id": "win",
            },
            {
                "strategy_id": "nq",
                "entry_time": "2026-01-03T10:00:00Z",
                "exit_time": "2026-01-03T11:00:00Z",
                "pnl_points": -1050,
                "source_row_id": "loss",
            },
        ]
    )
    profile = default_prop_rule_profiles()["Alpha Futures - Premium 50K"]

    result = simulate_prop_account(trades, profile, contracts=1)

    assert result.failed is True
    assert result.ending_floor == 48_500
    assert result.ending_balance == 48_400


def test_tpt_intraday_trailing_uses_mfe_mae_when_present():
    trades = _trades(
        [
            {
                "strategy_id": "nq",
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": -250,
                "mfe_points": 600,
                "mae_points": 650,
                "source_row_id": "swing-loss",
            },
        ]
    )
    profile = default_prop_rule_profiles()["TakeProfitTrader - PRO 50K"]

    result = simulate_prop_account(trades, profile, contracts=1)

    assert result.failed is True
    assert result.failure_reason == "maximum loss limit breached by estimated adverse excursion"
    assert result.ending_floor == 49_200


def test_apex_50k_eod_pa_safety_net_and_minimum_payout_are_separate():
    trades = _trades(
        [
            {
                "strategy_id": "nq",
                "entry_time": f"2026-01-{day:02d}T10:00:00Z",
                "exit_time": f"2026-01-{day:02d}T11:00:00Z",
                "pnl_points": 260,
                "source_row_id": f"win-{day}",
            }
            for day in range(1, 6)
        ]
    )
    profile = default_prop_rule_profiles()["Apex Trader Funding - EOD PA 50K"]

    result = simulate_prop_account(trades, profile, contracts=1)

    assert profile.payout_reserve == 2_100
    assert profile.payout_profit_required == 2_600
    assert result.ending_balance == 52_600
    assert result.eligible is True
    assert result.gross_cash_available == 500
    assert result.payout_after_split == 500


def test_apex_eod_caps_are_micro_equivalent_for_mnq_sizing():
    profiles = default_prop_rule_profiles()

    assert profiles["Apex Trader Funding - EOD PA 25K"].max_micro_contracts == 20
    assert profiles["Apex Trader Funding - EOD PA 50K"].max_micro_contracts == 40
    assert profiles["Apex Trader Funding - EOD PA 100K"].max_micro_contracts == 60
    assert profiles["Apex Trader Funding - EOD PA 150K"].max_micro_contracts == 100


def test_apex_eod_50k_stores_confirmed_six_payout_cap_ladder():
    profile = default_prop_rule_profiles()["Apex Trader Funding - EOD PA 50K"]

    assert profile.payout_count_cap == 6
    assert profile.max_payout == 1_500
    assert profile.payout_cap_schedule == (1_500, 1_500, 2_000, 2_500, 2_500, 3_000)
