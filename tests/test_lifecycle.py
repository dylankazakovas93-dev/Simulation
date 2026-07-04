from __future__ import annotations

import pandas as pd

from sim_core.ingestion.csv_loader import normalize_trade_frame
from sim_core.lifecycle import (
    LifecycleSettings,
    default_lifecycle_plans,
    run_lifecycle_grid,
    simulate_lifecycle_path,
)


def _trades(rows: list[dict]):
    defaults = {
        "strategy_id": "nq",
        "instrument": "NQ",
        "contract_symbol": "MNQ",
        "dollars_per_point": 2,
        "commission_round_turn": 0,
    }
    return normalize_trade_frame(pd.DataFrame([{**defaults, **row} for row in rows]))


def test_apex_50k_eval_can_pass_with_one_3000_day_then_pa_payout_after_qualification():
    trades = _trades(
        [
            {
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": 1500,
                "source_row_id": "eval-pass",
            },
            *[
                {
                    "entry_time": f"2026-01-{day:02d}T10:00:00Z",
                    "exit_time": f"2026-01-{day:02d}T11:00:00Z",
                    "pnl_points": 260,
                    "source_row_id": f"pa-win-{day}",
                }
                for day in range(3, 8)
            ],
        ]
    )
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD 50K - Eval to funded"]
    settings = LifecycleSettings(
        start_mode="new_eval",
        eval_fee=50,
        activation_fee=100,
        desired_payout=500,
        required_cushion=2_100,
        max_rebuy_capital=500,
    )

    result, _months, events = simulate_lifecycle_path(trades, plan, contracts=1, settings=settings)

    assert result.eval_passes == 1
    assert result.payouts_taken == 1
    assert result.total_payouts == 500
    assert result.total_fees == 150
    assert [event.event for event in events if event.event in {"eval_passed", "activation_fee", "payout"}] == [
        "eval_passed",
        "activation_fee",
        "payout",
    ]


def test_activation_fee_is_not_charged_for_failed_eval_rebuy_until_second_pass():
    trades = _trades(
        [
            {
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": -1200,
                "mae_points": 1200,
                "source_row_id": "eval-fail",
            },
            {
                "entry_time": "2026-01-03T10:00:00Z",
                "exit_time": "2026-01-03T11:00:00Z",
                "pnl_points": 1500,
                "source_row_id": "eval-pass",
            },
        ]
    )
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD 50K - Eval to funded"]
    settings = LifecycleSettings(
        start_mode="new_eval",
        eval_fee=50,
        activation_fee=100,
        reset_fee=25,
        max_rebuy_capital=500,
        allow_rebuys=True,
    )

    result, _months, events = simulate_lifecycle_path(trades, plan, contracts=1, settings=settings)

    fee_events = [event for event in events if event.event in {"eval_fee", "activation_fee"}]
    assert [event.event for event in fee_events] == ["eval_fee", "eval_fee", "activation_fee"]
    assert result.total_fees == 225
    assert result.eval_passes == 1
    assert result.attempts == 2


def test_payout_target_is_blocked_when_required_cushion_would_not_remain():
    trades = _trades(
        [
            {
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": 0,
                "source_row_id": "status-check",
            }
        ]
    )
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD 50K - Eval to funded"]
    settings = LifecycleSettings(
        start_mode="funded",
        current_balance=52_600,
        current_winning_days=5,
        current_highest_winning_day=500,
        desired_payout=500,
        required_cushion=2_500,
        auto_payout=True,
    )

    result, _months, _events = simulate_lifecycle_path(trades, plan, contracts=1, settings=settings)

    assert result.payouts_taken == 0
    assert result.total_payouts == 0
    assert result.cushion_ok_after_payout is False


def test_rebuy_payout_after_first_funded_failure_is_not_counted_as_current_account_paid_first():
    trades = _trades(
        [
            {
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": -800,
                "mae_points": 800,
                "source_row_id": "funded-fail",
            },
            {
                "entry_time": "2026-01-03T10:00:00Z",
                "exit_time": "2026-01-03T11:00:00Z",
                "pnl_points": 1500,
                "source_row_id": "eval-pass",
            },
            *[
                {
                    "entry_time": f"2026-01-{day:02d}T10:00:00Z",
                    "exit_time": f"2026-01-{day:02d}T11:00:00Z",
                    "pnl_points": 260,
                    "source_row_id": f"pa-win-{day}",
                }
                for day in range(4, 9)
            ],
        ]
    )
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD 50K - Eval to funded"]
    settings = LifecycleSettings(
        start_mode="funded",
        current_balance=50_500,
        current_floor=49_000,
        current_winning_days=1,
        current_highest_winning_day=800,
        desired_payout=500,
        max_rebuy_capital=500,
        allow_rebuys=True,
    )

    ranking, _monthly, _events = run_lifecycle_grid(
        trades,
        [plan],
        contract_values=[1],
        paths=1,
        horizon_months=1,
        seed=1,
        dollars_per_point=2,
        settings_by_plan={plan.key: settings},
    )

    row = ranking.iloc[0]
    assert row["current_account_paid_first_rate"] == 0
    assert row["current_account_blew_first_rate"] == 1
    assert row["payout_after_rebuy_rate"] == 1
    assert row["any_payout_rate"] == 1
