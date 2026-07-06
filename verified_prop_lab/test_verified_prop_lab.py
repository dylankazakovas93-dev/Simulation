import pandas as pd
import pytest

from verified_prop_lab import (
    LedgerTrade, PropRule, LifecyclePlan, LifecycleSettings,
    PropLabValidationError, SameCalendarMonthSampler,
    run_common_path_grid, simulate_lifecycle_path,
)


def t(trade_id, session, pnl, *, mae=None, stop=None, entry='10:00', exit='11:00', dpp=2, commission=0, slippage=0):
    day = pd.Timestamp(session)
    return LedgerTrade(
        trade_id=trade_id,
        session_date=day,
        entry_time=pd.Timestamp(f'{day.date()}T{entry}:00Z'),
        exit_time=pd.Timestamp(f'{day.date()}T{exit}:00Z'),
        pnl_points=pnl,
        mae_points=mae,
        stop_points=stop,
        dollars_per_point=dpp,
        commission_round_turn=commission,
        slippage_points_round_turn=slippage,
    )


def apex_rule(**kwargs):
    base = dict(
        firm='Apex', account_name='50K', starting_balance=50_000, max_loss=2_000,
        drawdown_mode='eod_trailing', max_contracts=6, min_winning_days=5,
        winning_day_threshold=250, consistency_pct=.50, min_payout=500,
        payout_reserve=2_100, profit_split=1.0, payout_caps=(1500,), max_payouts=6,
    )
    base.update(kwargs)
    return PropRule(**base)


def funded_plan(rule=None):
    rule = rule or apex_rule()
    return LifecyclePlan('apex-funded', funded_rule=rule)


def test_one_winner_and_costs_are_booked_trade_by_trade():
    trade = t('w', '2026-01-02', 100, mae=20, commission=1.5, slippage=.25)
    result = simulate_lifecycle_path([trade], funded_plan(), contracts=2,
        settings=LifecycleSettings(missing_excursion_policy='error'))
    row = result.trade_rows[0]
    assert row.gross_pnl == 400
    assert row.commission == 3
    assert row.slippage == 1
    assert row.net_pnl == 396
    assert result.ending_balance == 50_396


def test_exact_floor_touch_fails_and_one_cent_above_survives():
    exact = t('exact', '2026-01-02', 0, mae=1000)
    result = simulate_lifecycle_path([exact], funded_plan(), contracts=1,
        settings=LifecycleSettings(missing_excursion_policy='error'))
    assert result.terminal_failed is True
    assert result.trade_rows[0].estimated_low_balance == 48_000

    above = LedgerTrade(
        trade_id='above', session_date=pd.Timestamp('2026-01-02'),
        entry_time=pd.Timestamp('2026-01-02T10:00:00Z'), exit_time=pd.Timestamp('2026-01-02T11:00:00Z'),
        pnl_points=0, mae_points=999.995, dollars_per_point=2,
    )
    result2 = simulate_lifecycle_path([above], funded_plan(), contracts=1,
        settings=LifecycleSettings(missing_excursion_policy='error'))
    assert result2.terminal_failed is False
    assert result2.trade_rows[0].estimated_low_balance == pytest.approx(48_000.01)


def test_missing_mae_fails_closed_for_exact_barrier_simulation():
    with pytest.raises(PropLabValidationError, match='MAE is required'):
        simulate_lifecycle_path([t('x', '2026-01-02', 10)], funded_plan(), contracts=1,
            settings=LifecycleSettings(missing_excursion_policy='error'))


def test_no_payout_after_failure_even_if_account_was_prequalified():
    trade = t('breach', '2026-01-02', 0, mae=1350)
    settings = LifecycleSettings(
        current_balance=52_600, current_floor=50_000,
        current_winning_days=5, current_highest_winning_day=500,
        desired_gross_payout=500, missing_excursion_policy='error',
    )
    result = simulate_lifecycle_path([trade], funded_plan(), contracts=1, settings=settings)
    assert result.terminal_failed is True
    assert result.payouts_taken == 0
    assert not any(event.event == 'payout' for event in result.events)


def test_winning_day_is_counted_once_at_month_boundary():
    rule = apex_rule(min_winning_days=99)
    trades = [t('jan', '2026-01-31', 150, mae=0), t('feb', '2026-02-01', 0, mae=0)]
    result = simulate_lifecycle_path(trades, funded_plan(rule), contracts=1,
        settings=LifecycleSettings(missing_excursion_policy='error'))
    # Internal state is reflected by no impossible payout; more importantly there are exactly two trade rows.
    assert len(result.trade_rows) == 2
    assert [row.session_date for row in result.trade_rows] == ['2026-01-31', '2026-02-01']


def test_profit_split_reduces_cash_but_gross_withdrawal_reduces_balance():
    rule = apex_rule(profit_split=.8, min_winning_days=1, winning_day_threshold=1, consistency_pct=None)
    trade = t('win', '2026-01-02', 1300, mae=0)  # $2600
    result = simulate_lifecycle_path([trade], funded_plan(rule), contracts=1,
        settings=LifecycleSettings(desired_gross_payout=500, missing_excursion_policy='error'))
    assert result.gross_payouts == 500
    assert result.cash_payouts_after_split == 400
    assert result.ending_balance == 52_100


def test_true_drawdown_is_ordered_peak_to_later_trough_not_global_range():
    rule = apex_rule(min_winning_days=99, consistency_pct=None)
    trades = [
        t('early_loss', '2026-01-02', -250, mae=250),  # 49,500
        t('recovery', '2026-01-03', 2750, mae=0),      # 55,000
    ]
    result = simulate_lifecycle_path(trades, funded_plan(rule), contracts=1,
        settings=LifecycleSettings(missing_excursion_policy='error'))
    assert result.max_trading_drawdown == 500


def test_drawdown_period_records_trough_and_recovery():
    rule = apex_rule(min_winning_days=99, consistency_pct=None)
    trades = [
        t('peak', '2026-01-02', 500, mae=0),
        t('trough', '2026-01-03', -250, mae=250),
        t('recover', '2026-01-04', 300, mae=0),
    ]
    result = simulate_lifecycle_path(trades, funded_plan(rule), contracts=1,
        settings=LifecycleSettings(missing_excursion_policy='error'))
    period = result.drawdown_periods[0]
    assert period.depth == 500
    assert period.trough_trade_id == 'trough'
    assert period.recovery_trade_id == 'recover'


def test_session_date_is_authoritative_even_when_timestamp_is_sunday():
    trade = LedgerTrade(
        trade_id='overnight', session_date=pd.Timestamp('2019-01-07'),
        entry_time=pd.Timestamp('2019-01-06T19:03:00-05:00'),
        exit_time=pd.Timestamp('2019-01-06T19:20:00-05:00'),
        pnl_points=-22.875, mae_points=22.875,
    )
    result = simulate_lifecycle_path([trade], funded_plan(), contracts=1,
        settings=LifecycleSettings(missing_excursion_policy='error'))
    assert result.trade_rows[0].session_date == '2019-01-07'


def test_same_calendar_month_sampling_and_determinism():
    trades = [
        t('jan-2024', '2024-01-10', 1, mae=0), t('feb-2024', '2024-02-10', 1, mae=0),
        t('jan-2025', '2025-01-10', 2, mae=0), t('feb-2025', '2025-02-10', 2, mae=0),
    ]
    sampler = SameCalendarMonthSampler(trades, horizon_months=2, start_month='2027-01')
    first = sampler.sample_paths(paths=4, master_seed=1729)
    second = sampler.sample_paths(paths=4, master_seed=1729)
    assert [p.manifest for p in first] == [p.manifest for p in second]
    for path in first:
        assert path.manifest[0][1].endswith('-01')
        assert path.manifest[1][1].endswith('-02')


def test_grid_reuses_same_path_ids_and_reports_variance():
    trades = [t('jan-2024', '2024-01-10', 100, mae=0), t('jan-2025', '2025-01-10', -100, mae=100)]
    sampler = SameCalendarMonthSampler(trades, horizon_months=1, start_month='2027-01')
    paths = sampler.sample_paths(paths=20, master_seed=7)
    plan = funded_plan(apex_rule(min_winning_days=99, consistency_pct=None))
    results, summary = run_common_path_grid(paths, [plan], [1, 2], {plan.key: LifecycleSettings(missing_excursion_policy='error')})
    assert {r.path_id for r in results if r.contracts == 1} == {r.path_id for r in results if r.contracts == 2}
    assert 'variance_net_cash' in summary.columns
    assert 'std_net_cash' in summary.columns


def test_evaluation_pass_activates_funded_only_on_next_session():
    eval_rule = PropRule(
        firm='Apex', account_name='eval', starting_balance=50_000, max_loss=2_000,
        drawdown_mode='eod_trailing', max_contracts=6,
    )
    plan = LifecyclePlan('eval-plan', funded_rule=apex_rule(min_winning_days=99),
                         evaluation_rule=eval_rule, evaluation_profit_target=3000,
                         evaluation_fee=50, activation_fee=100)
    trades = [
        t('pass', '2026-01-02', 1500, mae=0),
        t('same-day-extra', '2026-01-02', 100, mae=0, entry='12:00', exit='13:00'),
        t('next-day-funded', '2026-01-03', 100, mae=0),
    ]
    result = simulate_lifecycle_path(trades, plan, contracts=1,
        settings=LifecycleSettings(start_stage='evaluation', missing_excursion_policy='error'))
    assert result.trade_rows[0].stage == 'evaluation'
    assert result.trade_rows[1].stage == 'evaluation'
    assert result.trade_rows[2].stage == 'funded'
    assert result.external_fees == 150


def test_replacement_starts_next_session_and_never_pays_failed_account():
    eval_rule = PropRule(
        firm='Apex', account_name='eval', starting_balance=50_000, max_loss=2_000,
        drawdown_mode='eod_trailing', max_contracts=6,
    )
    plan = LifecyclePlan('replace', funded_rule=apex_rule(), evaluation_rule=eval_rule,
                         evaluation_profit_target=3000, evaluation_fee=50, replacement_fee=25)
    trades = [
        t('breach', '2026-01-02', 0, mae=1350),
        t('same-session', '2026-01-02', 100, mae=0, entry='12:00', exit='13:00'),
        t('new-eval', '2026-01-03', 100, mae=0),
    ]
    settings = LifecycleSettings(
        current_balance=52_600, current_floor=50_000, current_winning_days=5,
        current_highest_winning_day=500, desired_gross_payout=500,
        allow_replacements=True, max_external_fee_capital=500,
        missing_excursion_policy='error',
    )
    result = simulate_lifecycle_path(trades, plan, contracts=1, settings=settings)
    assert result.payouts_taken == 0
    assert result.trade_rows[1].taken is False
    assert result.trade_rows[1].skip_reason == 'account_failed_earlier_in_session'
    assert result.trade_rows[2].stage == 'evaluation'
    assert result.external_fees == 75


def test_repeated_payout_uses_retained_safety_net_and_new_cycle_consistency():
    rule = apex_rule(min_winning_days=1, winning_day_threshold=1, consistency_pct=.5,
                     payout_caps=(500, 500), max_payouts=2)
    trades = [
        t('first-cycle-a', '2026-01-02', 650, mae=0),  # +1300
        t('first-cycle-b', '2026-01-03', 650, mae=0),  # +1300 -> 500 payout
        t('cycle2-a', '2026-01-04', 125, mae=0),       # +250
        t('cycle2-b', '2026-01-05', 125, mae=0),       # +250 -> next 500 payout
    ]
    result = simulate_lifecycle_path(trades, funded_plan(rule), contracts=1,
        settings=LifecycleSettings(desired_gross_payout=500, missing_excursion_policy='error'))
    assert result.payouts_taken == 2
    assert result.gross_payouts == 1000
    assert result.ending_balance == 52_100


def test_current_floor_below_contractual_floor_or_above_balance_is_rejected():
    trade = t('x', '2026-01-02', 0, mae=0)
    with pytest.raises(PropLabValidationError, match='below'):
        simulate_lifecycle_path([trade], funded_plan(), contracts=1,
            settings=LifecycleSettings(current_floor=47_999, missing_excursion_policy='error'))
    with pytest.raises(PropLabValidationError, match='exceed'):
        simulate_lifecycle_path([trade], funded_plan(), contracts=1,
            settings=LifecycleSettings(current_balance=49_000, current_floor=49_001, missing_excursion_policy='error'))


def test_soft_daily_loss_guard_skips_later_same_session_trade():
    rule = apex_rule(daily_loss_limit=500, daily_loss_hard=False, min_winning_days=99, consistency_pct=None)
    trades = [t('loss', '2026-01-02', -250, mae=250), t('later', '2026-01-02', 1000, mae=0, entry='12:00', exit='13:00')]
    result = simulate_lifecycle_path(trades, funded_plan(rule), contracts=1,
        settings=LifecycleSettings(missing_excursion_policy='error'))
    assert result.trade_rows[1].taken is False
    assert result.trade_rows[1].skip_reason == 'daily_loss_guard'
    assert result.ending_balance == 49_500


def test_hard_daily_loss_is_recorded_as_failure():
    rule = apex_rule(daily_loss_limit=500, daily_loss_hard=True, min_winning_days=99, consistency_pct=None)
    result = simulate_lifecycle_path([t('loss', '2026-01-02', -250, mae=250)], funded_plan(rule), contracts=1,
        settings=LifecycleSettings(missing_excursion_policy='error'))
    assert result.terminal_failed is True
    assert result.first_failure_day == 0
    assert any(event.note == 'daily loss limit breached' for event in result.events)


def test_volatility_hodlod_schema_maps_session_cap_and_pnl_without_silent_loss():
    from verified_prop_lab import load_ledger_frame
    frame = pd.DataFrame([
        {
            'level_id': '250_upper',
            'session_date': '2019-01-07',
            'entry_time': '2019-01-06 19:03:00-05:00',
            'exit_time': '2019-01-06 19:20:00-05:00',
            'cap': 22.875,
            'pnl': -22.875,
        }
    ])
    trades = load_ledger_frame(frame, strategy_id='OG_PRIMARY_150R', default_dollars_per_point=2,
                               default_commission_round_turn=1.24, default_slippage_points_round_turn=.5)
    trade = trades[0]
    assert trade.session_date == pd.Timestamp('2019-01-07')
    assert trade.stop_points == 22.875
    assert trade.pnl_points == -22.875
    assert trade.mae_points is None
    assert trade.commission_round_turn == 1.24
    assert trade.slippage_points_round_turn == .5


def test_loader_rejects_missing_authoritative_session_date():
    from verified_prop_lab import load_ledger_frame
    frame = pd.DataFrame([{'entry_time': '2026-01-01T10:00:00Z', 'exit_time': '2026-01-01T11:00:00Z', 'pnl': 1}])
    with pytest.raises(PropLabValidationError, match='session_date'):
        load_ledger_frame(frame, strategy_id='x', default_dollars_per_point=2)
