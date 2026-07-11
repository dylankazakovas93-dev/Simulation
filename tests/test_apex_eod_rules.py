from __future__ import annotations

import pandas as pd

from sim_core.lifecycle import LifecycleSettings, default_lifecycle_plans, simulate_lifecycle_path
from sim_core.models import Trade


PLAN = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]


def _trade(day: str, points: float, *, mae: float = 0.0, requested: int | None = None) -> Trade:
    entry = pd.Timestamp(f"{day}T14:00:00Z")
    metadata = {} if requested is None else {"requested_contracts": requested}
    return Trade(
        trade_id=day,
        source_row_id=day,
        strategy_id="apex",
        instrument="NQ",
        contract_symbol="MNQ",
        entry_time=entry,
        exit_time=entry + pd.Timedelta(hours=1),
        pnl_dollars=points * 2,
        pnl_points=points,
        mae_points=mae,
        mfe_points=max(points, 0),
        dollars_per_point=2,
        metadata=metadata,
    )


def _settings(**overrides) -> LifecycleSettings:
    values = dict(
        start_mode="funded",
        current_balance=50_000,
        current_floor=48_000,
        payout_request_mode="minimum_first_payout",
        funded_activation_date="2026-01-01",
        prior_completed_eod_balance=50_000,
    )
    values.update(overrides)
    return LifecycleSettings(**values)


def test_apex_floor_updates_after_completed_session_and_stops_at_ceiling():
    trades = [_trade("2026-01-02", 1_000), _trade("2026-01-05", 1_000)]

    result, _months, _events, ledger = simulate_lifecycle_path(
        trades, PLAN, contracts=1, settings=_settings(), return_trade_ledger=True
    )

    assert result.ending_floor == 50_100
    assert ledger[0]["floor_before"] == 48_000
    assert ledger[1]["floor_before"] == 50_000


def test_apex_tier_changes_only_between_sessions_and_rejects_oversize_pre_outcome():
    trades = [
        _trade("2026-01-02", 37.5, requested=20),
        _trade("2026-01-02", 1_000, requested=30),
        _trade("2026-01-05", 1, requested=30),
    ]

    _result, _months, _events, ledger = simulate_lifecycle_path(
        trades, PLAN, contracts=1, settings=_settings(), return_trade_ledger=True
    )

    assert ledger[0]["apex_tier"] == 1
    assert ledger[1]["strict_account_result"] == "REJECTED_TIER_LIMIT"
    assert ledger[2]["apex_tier"] == 2
    assert ledger[2]["executed"] is True


def test_apex_dll_pauses_session_then_resumes_next_session():
    trades = [
        _trade("2026-01-02", -10, mae=600),
        _trade("2026-01-02", 500),
        _trade("2026-01-05", 1),
    ]

    result, _months, events, ledger = simulate_lifecycle_path(
        trades, PLAN, contracts=1, settings=_settings(), return_trade_ledger=True
    )

    assert result.failed is False
    assert [event.event for event in events if event.event == "dll_pause"] == ["dll_pause"]
    assert ledger[1]["strict_account_result"] == "NOT_TAKEN_DAILY_PAUSE"
    assert ledger[2]["executed"] is True


def test_apex_missing_mae_marks_strict_result_unknown():
    trade = _trade("2026-01-02", -200)
    trade = Trade(**{**trade.__dict__, "mae_points": None})

    result, _months, _events, ledger = simulate_lifecycle_path(
        [trade], PLAN, contracts=1, settings=_settings(current_balance=49_500, current_floor=48_000), return_trade_ledger=True
    )

    assert result.terminal_status == "UNKNOWN_MISSING_MAE"
    assert ledger[0]["strict_account_result"] == "UNKNOWN_MISSING_MAE"


def test_apex_minimum_request_pays_500_and_exact_half_consistency_is_rejected():
    wins = [_trade(f"2026-01-{day:02d}", 250) for day in (2, 5, 6, 7, 8)]
    paid, _months, _events = simulate_lifecycle_path(
        wins, PLAN, contracts=1, settings=_settings(current_balance=50_100),
    )
    assert paid.total_payouts == 500
    equal_half = _settings(current_balance=52_600, current_winning_days=5, current_daily_profits=(1_300, 1_300))
    rejected, _months, _events = simulate_lifecycle_path([], PLAN, contracts=1, settings=equal_half)
    assert rejected.total_payouts == 0


def test_apex_inactivity_and_six_payout_completion_are_terminal_not_failures():
    inactive, _months, _events = simulate_lifecycle_path(
        [_trade("2026-02-02", 1)], PLAN, contracts=1, settings=_settings(),
    )
    assert inactive.terminal_status == "closed_for_inactivity"
    assert inactive.failed is False
    completed, _months, _events = simulate_lifecycle_path(
        [], PLAN, contracts=1,
        settings=_settings(current_balance=52_600, current_winning_days=5, current_daily_profits=(500,) * 5, payouts_already_taken=5),
    )
    assert completed.terminal_status == "completed_after_six_payouts"
    assert completed.failed is False
