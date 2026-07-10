from __future__ import annotations

import pandas as pd

from sim_core.ingestion.csv_loader import normalize_trade_frame
from sim_core.lifecycle import (
    LifecyclePathResult,
    LifecycleSettings,
    default_lifecycle_plans,
    run_lifecycle_grid,
    simulate_lifecycle_path,
    summarize_lifecycle_results,
    summarize_monthly_paths,
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
    assert [event.amount for event in fee_events] == [-50, -25, -100]
    assert result.total_fees == 175
    assert result.eval_passes == 1
    assert result.attempts == 2


def test_funded_failure_rebuy_charges_new_eval_not_eval_plus_reset():
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
        ]
    )
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD 50K - Eval to funded"]
    settings = LifecycleSettings(
        start_mode="funded",
        current_balance=50_500,
        current_floor=49_000,
        eval_fee=30,
        activation_fee=60,
        reset_fee=30,
        max_rebuy_capital=100,
        allow_rebuys=True,
    )

    result, _months, events = simulate_lifecycle_path(trades, plan, contracts=1, settings=settings)

    fee_events = [event for event in events if event.event in {"eval_fee", "activation_fee"}]
    assert [event.amount for event in fee_events] == [-30, -60]
    assert fee_events[0].note == "New evaluation after funded failure"
    assert result.total_fees == 90
    assert result.eval_passes == 1


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


def test_apex_50k_first_payout_requires_53600_for_1500_and_leaves_safety_net():
    trades = _trades(
        [
            {
                "entry_time": f"2026-01-{day:02d}T10:00:00Z",
                "exit_time": f"2026-01-{day:02d}T11:00:00Z",
                "pnl_points": 387.5,
                "source_row_id": f"qualifier-{day}",
            }
            for day in range(2, 6)
        ]
    )
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD 50K - Eval to funded"]
    settings = LifecycleSettings(
        start_mode="funded",
        current_balance=50_500,
        current_floor=49_000,
        current_winning_days=1,
        current_highest_winning_day=800,
        desired_payout=1_500,
        auto_payout=True,
    )

    result, _months, events = simulate_lifecycle_path(trades, plan, contracts=1, settings=settings)

    assert result.payouts_taken == 1
    assert result.total_payouts == 1_500
    assert result.ending_balance == 52_100
    assert [event.balance for event in events if event.event == "payout"] == [52_100]


def test_tpt_buffer_allows_withdrawal_after_threshold_without_preserving_full_buffer():
    trades = _trades(
        [
            {
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": 0,
                "mae_points": 0,
                "mfe_points": 0,
                "source_row_id": "status-check",
            }
        ]
    )
    plan = default_lifecycle_plans()["TakeProfitTrader - PRO 50K - Funded only"]
    settings = LifecycleSettings(
        start_mode="funded",
        current_balance=52_000,
        current_floor=48_000,
        desired_payout=0,
        auto_payout=True,
    )

    result, _months, events = simulate_lifecycle_path(trades, plan, contracts=1, settings=settings)

    assert result.payouts_taken == 1
    assert result.total_payouts == 1_600
    assert result.ending_balance == 50_000
    assert [event.event for event in events if event.event == "payout"] == ["payout"]


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


def test_month_change_does_not_double_count_last_day_for_payout_qualification():
    trades = _trades(
        [
            {
                "entry_time": "2026-01-31T10:00:00Z",
                "exit_time": "2026-01-31T11:00:00Z",
                "pnl_points": 125,
                "source_row_id": "jan-win",
            },
            {
                "entry_time": "2026-02-01T10:00:00Z",
                "exit_time": "2026-02-01T11:00:00Z",
                "pnl_points": 0,
                "source_row_id": "feb-check",
            },
        ]
    )
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD 50K - Eval to funded"]
    settings = LifecycleSettings(
        start_mode="funded",
        current_balance=53_500,
        current_floor=50_000,
        current_winning_days=3,
        current_highest_winning_day=500,
        desired_payout=500,
        required_cushion=2_100,
    )

    result, _months, events = simulate_lifecycle_path(trades, plan, contracts=1, settings=settings)

    assert result.payouts_taken == 0
    assert result.total_payouts == 0
    assert [event.event for event in events] == []


def test_current_balance_starts_future_max_drawdown_from_current_state():
    trades = _trades(
        [
            {
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": 0,
                "source_row_id": "flat",
            }
        ]
    )
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD 50K - Eval to funded"]
    settings = LifecycleSettings(
        start_mode="funded",
        current_balance=50_500,
        current_floor=49_000,
    )

    result, _months, _events = simulate_lifecycle_path(trades, plan, contracts=1, settings=settings)

    assert result.max_drawdown == 0


def test_same_day_failure_then_rebuy_payout_uses_event_order_not_day_tie():
    result = LifecyclePathResult(
        plan_key="test-plan",
        firm="Test",
        account_name="Test Account",
        contracts=1,
        path_id=1,
        seed=1,
        failed=False,
        terminal_stage="funded",
        attempts=2,
        eval_passes=1,
        funded_failures=1,
        payouts_taken=1,
        first_payout_month=1,
        first_payout_day=0,
        first_payout_order=2,
        first_failure_month=1,
        first_failure_day=0,
        first_failure_order=1,
        total_payouts=500,
        total_fees=0,
        net_cash=500,
        roi_on_fees=None,
        ending_balance=50_000,
        ending_floor=48_000,
        max_drawdown=2_000,
        target_hit=True,
        cushion_ok_after_payout=True,
    )

    row = summarize_lifecycle_results([result]).iloc[0]

    assert row["current_account_paid_first_rate"] == 0
    assert row["current_account_blew_first_rate"] == 1
    assert row["payout_after_rebuy_rate"] == 1


def test_lifecycle_outcome_buckets_are_mutually_exclusive():
    rows = [
        LifecyclePathResult(
            plan_key="test-plan",
            firm="Test",
            account_name="Test Account",
            contracts=1,
            path_id=index,
            seed=index,
            failed=failed,
            terminal_stage="funded",
            attempts=1,
            eval_passes=0,
            funded_failures=1 if failure_order is not None else 0,
            payouts_taken=1 if payout_order is not None else 0,
            first_payout_month=1 if payout_order is not None else None,
            first_payout_day=0 if payout_order is not None else None,
            first_payout_order=payout_order,
            first_failure_month=1 if failure_order is not None else None,
            first_failure_day=0 if failure_order is not None else None,
            first_failure_order=failure_order,
            total_payouts=payouts,
            total_fees=fees,
            net_cash=payouts - fees,
            roi_on_fees=None,
            ending_balance=50_000,
            ending_floor=48_000,
            max_drawdown=1_000,
            target_hit=payouts > 0,
            cushion_ok_after_payout=payouts > 0,
        )
        for index, payout_order, failure_order, payouts, fees, failed in (
            (1, 1, None, 500, 50, False),
            (2, None, 1, 0, 100, True),
            (3, 2, 1, 500, 150, False),
            (4, None, None, 0, 0, False),
        )
    ]

    row = summarize_lifecycle_results(rows).iloc[0]
    bucket_total = (
        row["paid_before_first_blow_rate"]
        + row["blew_before_payout_rate"]
        + row["payout_after_rebuy_rate"]
        + row["no_resolution_rate"]
    )

    assert bucket_total == 1
    assert row["paid_before_first_blow_count"] == 1
    assert row["blew_before_payout_count"] == 1
    assert row["paid_after_rebuy_count"] == 1
    assert row["no_resolution_count"] == 1


def test_score_components_penalize_worse_survival_and_include_fees():
    safe = LifecyclePathResult(
        plan_key="test-plan",
        firm="Test",
        account_name="Test Account",
        contracts=1,
        path_id=1,
        seed=1,
        failed=False,
        terminal_stage="funded",
        attempts=1,
        eval_passes=0,
        funded_failures=0,
        payouts_taken=1,
        first_payout_month=2,
        first_payout_day=30,
        first_payout_order=1,
        first_failure_month=None,
        first_failure_day=None,
        first_failure_order=None,
        total_payouts=1_000,
        total_fees=100,
        net_cash=900,
        roi_on_fees=None,
        ending_balance=51_000,
        ending_floor=49_000,
        max_drawdown=500,
        target_hit=True,
        cushion_ok_after_payout=True,
    )
    risky = LifecyclePathResult(
        **{
            **safe.to_dict(),
            "contracts": 2,
            "path_id": 2,
            "failed": True,
            "payouts_taken": 0,
            "first_payout_month": None,
            "first_payout_day": None,
            "first_payout_order": None,
            "first_failure_month": 1,
            "first_failure_day": 5,
            "first_failure_order": 1,
            "total_payouts": 0,
            "total_fees": 200,
            "net_cash": -200,
            "target_hit": False,
            "cushion_ok_after_payout": False,
        }
    )

    summary = summarize_lifecycle_results([safe, risky]).sort_values("contracts")

    assert summary.iloc[0]["survival_score"] > summary.iloc[1]["survival_score"]
    assert summary.iloc[1]["avg_fees"] == 200
    assert summary.iloc[1]["speed_score"] == 0


def test_monthly_summary_separates_terminal_paths_from_active_pnl():
    monthly = pd.DataFrame(
        [
            {
                "path_id": 1,
                "plan_key": "test-plan",
                "contracts": 2,
                "month_index": 3,
                "pnl": 0,
                "payouts": 0,
                "net_cash": 1500,
                "max_drawdown": 500,
                "status": "terminal",
            },
            {
                "path_id": 2,
                "plan_key": "test-plan",
                "contracts": 2,
                "month_index": 3,
                "pnl": 800,
                "payouts": 0,
                "net_cash": 0,
                "max_drawdown": 250,
                "status": "active",
            },
        ]
    )

    summary = summarize_monthly_paths(monthly).iloc[0]

    assert summary["active_paths"] == 1
    assert summary["active_path_rate"] == 0.5
    assert summary["terminal_path_rate"] == 0.5
    assert summary["p50_active_pnl"] == 800


def test_current_daily_history_changes_consistency_eligibility():
    trades = _trades(
        [
            {
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": 0,
                "mae_points": 0,
                "mfe_points": 0,
                "source_row_id": "status-check",
            }
        ]
    )
    plan = default_lifecycle_plans()["FundedNext Futures - Rapid 50K - Funded only"]
    blocked = LifecycleSettings(
        start_mode="funded",
        current_balance=53_000,
        current_daily_profits=(2_000,),
        auto_payout=True,
    )
    allowed = LifecycleSettings(
        start_mode="funded",
        current_balance=53_000,
        current_daily_profits=(1_000, 1_000, 1_000),
        auto_payout=True,
    )

    blocked_result, _months, blocked_events = simulate_lifecycle_path(trades, plan, contracts=1, settings=blocked)
    allowed_result, _months, allowed_events = simulate_lifecycle_path(trades, plan, contracts=1, settings=allowed)

    assert blocked_result.payouts_taken == 0
    assert [event.event for event in blocked_events] == []
    assert allowed_result.payouts_taken == 1
    assert [event.event for event in allowed_events] == ["payout"]


def test_prior_payout_count_changes_apex_payout_cap_selection():
    trades = _trades(
        [
            {
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": 0,
                "mae_points": 0,
                "mfe_points": 0,
                "source_row_id": "status-check",
            }
        ]
    )
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    base = LifecycleSettings(
        start_mode="funded",
        current_balance=56_000,
        current_winning_days=5,
        current_daily_profits=(500, 500, 500, 500, 500),
        auto_payout=True,
    )
    first_result, _months, _events = simulate_lifecycle_path(trades, plan, contracts=1, settings=base)
    third_result, _months, _events = simulate_lifecycle_path(
        trades,
        plan,
        contracts=1,
        settings=LifecycleSettings(**{**base.__dict__, "payouts_already_taken": 2}),
    )

    assert first_result.total_payouts == 1_500
    assert third_result.total_payouts == 2_000
    assert third_result.payouts_taken == 3


def test_prior_fees_are_included_in_net_cash():
    trades = _trades(
        [
            {
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": 0,
                "mae_points": 0,
                "mfe_points": 0,
                "source_row_id": "status-check",
            }
        ]
    )
    plan = default_lifecycle_plans()["TakeProfitTrader - PRO 50K - Funded only"]
    settings = LifecycleSettings(start_mode="funded", current_balance=52_000, prior_fees=100, auto_payout=True)

    result, _months, _events = simulate_lifecycle_path(trades, plan, contracts=1, settings=settings)

    assert result.total_payouts == 1_600
    assert result.total_fees == 100
    assert result.net_cash == 1_500


def test_authoritative_trade_ledger_reconciles_and_payout_row_matches_event():
    trades = _trades(
        [
            {
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": 0,
                "mae_points": 0,
                "mfe_points": 0,
                "source_row_id": "status-check",
            }
        ]
    )
    plan = default_lifecycle_plans()["TakeProfitTrader - PRO 50K - Funded only"]
    settings = LifecycleSettings(start_mode="funded", current_balance=52_000, auto_payout=True)

    result, _months, events, ledger = simulate_lifecycle_path(
        trades, plan, contracts=1, settings=settings, return_trade_ledger=True
    )
    payout_event = [event for event in events if event.event == "payout"][0]
    payout_row = [row for row in ledger if row["record_type"] == "PAYOUT"][0]

    assert ledger[-1]["balance_after"] == result.ending_balance
    assert ledger[-1]["floor_after"] == result.ending_floor
    assert ledger[-1]["total_payouts"] == result.total_payouts
    assert payout_row["trader_cash"] == payout_event.amount
    assert payout_row["balance_after"] == payout_event.balance
    assert payout_row["payout_event_order"] == payout_event.event_order


def test_guaranteed_mae_failure_cannot_pay_afterward():
    trades = _trades(
        [
            {
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": 500,
                "mae_points": 2_000,
                "mfe_points": 500,
                "source_row_id": "mae-fail",
            }
        ]
    )
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    settings = LifecycleSettings(
        start_mode="funded",
        current_balance=53_600,
        current_floor=50_100,
        current_winning_days=5,
        current_daily_profits=(500, 500, 500, 500, 500),
        auto_payout=True,
    )

    result, _months, events, ledger = simulate_lifecycle_path(
        trades, plan, contracts=1, settings=settings, return_trade_ledger=True
    )

    assert result.failed is True
    assert result.total_payouts == 0
    assert "payout" not in [event.event for event in events]
    assert ledger[-1]["strict_account_result"] == "FAILED"


def test_apex_50100_fails_and_5010001_survives():
    trades = _trades(
        [
            {
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": 0,
                "mae_points": 0,
                "mfe_points": 0,
                "source_row_id": "boundary",
            }
        ]
    )
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]

    failed, _months, _events = simulate_lifecycle_path(
        trades,
        plan,
        contracts=1,
        settings=LifecycleSettings(start_mode="funded", current_balance=50_100.00, current_floor=50_100.00),
    )
    survived, _months, _events = simulate_lifecycle_path(
        trades,
        plan,
        contracts=1,
        settings=LifecycleSettings(start_mode="funded", current_balance=50_100.01, current_floor=50_100.00),
    )

    assert failed.failed is True
    assert survived.failed is False


def test_exact_cash_calculations_for_apex_alpha_fundednext_and_tpt():
    trades = _trades(
        [
            {
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": 0,
                "mae_points": 0,
                "mfe_points": 0,
                "source_row_id": "status-check",
            }
        ]
    )
    cases = [
        (
            "Apex Trader Funding - EOD PA 50K - Funded only",
            LifecycleSettings(
                start_mode="funded",
                current_balance=53_600,
                current_winning_days=5,
                current_daily_profits=(500, 500, 500, 500, 500),
            ),
            1_500,
            52_100,
        ),
        (
            "Alpha Futures - Advanced 50K - Funded only",
            LifecycleSettings(
                start_mode="funded",
                current_balance=54_000,
                current_winning_days=5,
                current_daily_profits=(500, 500, 500, 500, 500),
            ),
            1_800,
            52_000,
        ),
        (
            "FundedNext Futures - Rapid 50K - Funded only",
            LifecycleSettings(start_mode="funded", current_balance=53_000, current_daily_profits=(1_000, 1_000, 1_000)),
            1_200,
            51_500,
        ),
        (
            "TakeProfitTrader - PRO 50K - Funded only",
            LifecycleSettings(start_mode="funded", current_balance=52_000),
            1_600,
            50_000,
        ),
    ]

    for key, settings, expected_cash, expected_balance in cases:
        plan = default_lifecycle_plans()[key]
        result, _months, events = simulate_lifecycle_path(trades, plan, contracts=1, settings=settings)
        payout = [event for event in events if event.event == "payout"][0]
        assert result.total_payouts == expected_cash
        assert payout.amount == expected_cash
        assert result.ending_balance == expected_balance


def test_final_day_eod_floor_and_winning_day_state_are_finalized_in_trace():
    trades = _trades(
        [
            {
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": 125,
                "mae_points": 0,
                "mfe_points": 125,
                "source_row_id": "final-day-win",
            }
        ]
    )
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    settings = LifecycleSettings(start_mode="funded", current_balance=50_000, current_floor=48_000, auto_payout=False)

    result, _months, _events, ledger = simulate_lifecycle_path(
        trades, plan, contracts=1, settings=settings, return_trade_ledger=True
    )

    assert result.ending_balance == 50_250
    assert result.ending_floor == 48_250
    assert ledger[-1]["floor_after"] == 48_250
    assert ledger[-1]["winning_days_after"] == 1


def test_eval_pass_and_activation_fee_have_authoritative_trace_rows():
    trades = _trades(
        [
            {
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": 1500,
                "source_row_id": "eval-pass",
            }
        ]
    )
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD 50K - Eval to funded"]
    settings = LifecycleSettings(start_mode="new_eval", activation_fee=100)

    result, _months, events, ledger = simulate_lifecycle_path(
        trades, plan, contracts=1, settings=settings, return_trade_ledger=True
    )

    assert result.terminal_stage == "funded"
    assert result.ending_balance == 50_000
    assert result.total_fees == 100
    assert [event.event for event in events] == ["eval_passed", "activation_fee"]
    assert [row["record_type"] for row in ledger] == ["TRADE", "LIFECYCLE_EVENT", "FEE"]
    assert ledger[-1]["lifecycle_event"] == "activation_fee"
    assert ledger[-1]["balance_after"] == result.ending_balance
    assert ledger[-1]["floor_after"] == result.ending_floor
    assert ledger[-1]["total_fees"] == result.total_fees


def test_lifecycle_grid_reuses_shared_source_paths_across_sizes_and_plans():
    trades = _trades(
        [
            {
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": 10,
                "source_row_id": "jan",
            },
            {
                "entry_time": "2026-02-02T10:00:00Z",
                "exit_time": "2026-02-02T11:00:00Z",
                "pnl_points": -5,
                "source_row_id": "feb",
            },
        ]
    )
    plans = [
        default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"],
        default_lifecycle_plans()["TakeProfitTrader - PRO 50K - Funded only"],
    ]
    settings_by_plan = {
        plan.key: LifecycleSettings(
            start_mode="funded",
            current_balance=50_000,
            current_floor=0,
            auto_payout=False,
        )
        for plan in plans
    }

    _ranking_a, monthly_a, _events_a = run_lifecycle_grid(
        trades,
        plans,
        contract_values=[1, 2],
        paths=4,
        horizon_months=3,
        seed=99,
        dollars_per_point=2,
        settings_by_plan=settings_by_plan,
    )
    _ranking_b, monthly_b, _events_b = run_lifecycle_grid(
        trades,
        plans,
        contract_values=[1, 2],
        paths=4,
        horizon_months=3,
        seed=99,
        dollars_per_point=2,
        settings_by_plan=settings_by_plan,
    )

    for path_id, group in monthly_a.groupby("shared_strategy_path_id"):
        assert group["source_sequence_hash"].nunique() == 1
        assert group["source_sequence_hash"].iloc[0] == monthly_b[
            monthly_b["shared_strategy_path_id"] == path_id
        ]["source_sequence_hash"].iloc[0]
    assert set(monthly_a["shared_strategy_path_id"]) == {0, 1, 2, 3}
    assert monthly_a["account_result_id"].nunique() == 16


def test_shared_lifecycle_paths_scale_pnl_without_resampling():
    trades = _trades(
        [
            {
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": 10,
                "source_row_id": "jan",
            },
            {
                "entry_time": "2026-02-02T10:00:00Z",
                "exit_time": "2026-02-02T11:00:00Z",
                "pnl_points": 10,
                "source_row_id": "feb",
            },
        ]
    )
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    settings = LifecycleSettings(start_mode="funded", current_balance=50_000, current_floor=0, auto_payout=False)

    _ranking, monthly, _events = run_lifecycle_grid(
        trades,
        [plan],
        contract_values=[1, 3],
        paths=3,
        horizon_months=2,
        seed=7,
        dollars_per_point=2,
        settings_by_plan={plan.key: settings},
    )
    totals = monthly.groupby(["shared_strategy_path_id", "contracts"])["pnl"].sum().unstack()

    assert (totals[3] == totals[1] * 3).all()


def test_lifecycle_summary_separates_realized_cash_from_ending_profit():
    result = LifecyclePathResult(
        plan_key="test-plan",
        firm="Test",
        account_name="Test Account",
        contracts=1,
        path_id=1,
        seed=1,
        failed=False,
        terminal_stage="funded",
        attempts=1,
        eval_passes=0,
        funded_failures=0,
        payouts_taken=0,
        first_payout_month=None,
        first_payout_day=None,
        first_payout_order=None,
        first_failure_month=None,
        first_failure_day=None,
        first_failure_order=None,
        total_payouts=0,
        total_fees=0,
        net_cash=0,
        roi_on_fees=None,
        ending_balance=51_000,
        ending_floor=49_000,
        max_drawdown=0,
        target_hit=False,
        cushion_ok_after_payout=False,
        effective_starting_balance=50_000,
    )

    row = summarize_lifecycle_results([result]).iloc[0]

    assert row["avg_net_cash"] == 0
    assert row["p50_ending_balance"] == 51_000
    assert row["p50_ending_account_profit"] == 1_000
    assert row["p50_unresolved_ending_profit"] == 1_000
