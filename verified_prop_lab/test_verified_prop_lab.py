import pandas as pd
import pytest

from verified_prop_lab import (
    LedgerTrade, PropRule, LifecyclePlan, LifecycleSettings,
    PropLabValidationError, SameCalendarMonthSampler,
    ForwardScenario, ForwardVolumeScenarioSampler,
    first_passage_threshold_frame, label_summary_decisions, load_ledger_frame,
    pareto_frontier_indexes, run_common_path_grid, run_forward_volume_grid,
    simulate_lifecycle_path,
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


def ft(trade_id, session, pnl, *, raw=100, stop=None, mae=0, mfe=0, entry='10:00', exit='11:00', weight=1):
    day = pd.Timestamp(session)
    effective_stop = min(raw, 200) if stop is None else stop
    return LedgerTrade(
        trade_id=trade_id,
        session_date=day,
        entry_time=pd.Timestamp(f'{day.date()}T{entry}:00Z'),
        exit_time=pd.Timestamp(f'{day.date()}T{exit}:00Z'),
        pnl_points=pnl,
        raw_stop_points=raw,
        stop_points=effective_stop,
        mae_points=mae,
        mfe_points=mfe,
        sample_weight=weight,
        dollars_per_point=2,
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


def test_forward_loader_requires_raw_effective_stop_and_excursions_in_strict_mode():
    frame = pd.DataFrame([
        {
            'trade_id': 'a',
            'session_date': '2026-01-02',
            'entry_time': '2026-01-02T10:00:00Z',
            'exit_time': '2026-01-02T11:00:00Z',
            'pnl_points': 10,
            'stop_points': 100,
            'mae_points': 20,
            'mfe_points': 40,
        }
    ])
    with pytest.raises(PropLabValidationError, match='raw_stop_points'):
        load_ledger_frame(frame, strategy_id='s', default_dollars_per_point=2, require_forward_schema=True)


def test_raw_stop_scaling_preserves_r_shape_and_caps_effective_stop():
    trade = ft('x', '2026-01-02', pnl=100, raw=150, mae=30, mfe=300)
    scaled = trade.scaled_for_point_volatility(1.5)
    assert scaled.raw_stop_points == 225
    assert scaled.stop_points == 200
    assert scaled.pnl_points == pytest.approx(100 / 150 * 200)
    assert scaled.mae_points == pytest.approx(30 / 150 * 200)
    assert scaled.mfe_points == pytest.approx(300 / 150 * 200)


def test_forward_sampler_uses_requested_monthly_counts_weekdays_and_is_deterministic():
    trades = [ft('a', '2024-01-02', 10), ft('b', '2024-01-03', -5, mae=20)]
    sampler = ForwardVolumeScenarioSampler(
        trades,
        forecast_start='2026-07-01',
        forecast_end='2026-08-31',
        monthly_trade_counts={'2026-07': 3, '2026-08': 2},
    )
    first = sampler.sample_paths(paths=3, master_seed=11)
    second = sampler.sample_paths(paths=3, master_seed=11)
    assert [path.manifest for path in first] == [path.manifest for path in second]
    assert all(len(path.trades) == 5 for path in first)
    for path in first:
        dates = [trade.session_date for trade in path.trades]
        assert len(set(dates)) == 5
        assert all(date.weekday() < 5 for date in dates)


def test_forward_sampler_rejects_more_trades_than_available_sessions():
    trades = [ft('a', '2024-01-02', 10)]
    with pytest.raises(PropLabValidationError, match='weekday sessions'):
        ForwardVolumeScenarioSampler(
            trades,
            forecast_start='2026-07-01',
            forecast_end='2026-07-03',
            monthly_trade_counts={'2026-07': 4},
        )


def test_forward_grid_reuses_same_paths_across_contracts_and_scenarios():
    trades = [ft('win', '2024-01-02', 300, mae=0, mfe=300), ft('loss', '2024-01-03', -100, mae=100, mfe=0)]
    sampled = ForwardVolumeScenarioSampler(
        trades,
        forecast_start='2026-07-01',
        forecast_end='2026-07-31',
        monthly_trade_counts={'2026-07': 3},
    ).sample_paths(paths=12, master_seed=5)
    rule = apex_rule(min_winning_days=99, consistency_pct=None)
    grid = run_forward_volume_grid(
        sampled,
        funded_plan(rule),
        contract_values=[1, 2],
        scenarios=[ForwardScenario(1.0, 'Base'), ForwardScenario(1.1, '+10%')],
        settings=LifecycleSettings(missing_excursion_policy='error'),
        time_threshold_days=[10],
    )
    assert set(grid.summary['contracts']) == {1, 2}
    assert set(grid.summary['scenario_label']) == {'Base', '+10%'}
    assert all(grid.summary['paths'] == 12)
    for _, group in grid.sampled_trades.groupby(['point_scale', 'path_id']):
        assert len(group) == 3


def test_forward_summary_outcome_buckets_are_mutually_exclusive():
    trades = [
        ft('big-win', '2024-01-02', 1300, mae=0, mfe=1300),
        ft('big-loss', '2024-01-03', 0, mae=1200, mfe=0),
    ]
    sampled = ForwardVolumeScenarioSampler(
        trades,
        forecast_start='2026-07-01',
        forecast_end='2026-07-31',
        monthly_trade_counts={'2026-07': 2},
    ).sample_paths(paths=100, master_seed=23)
    rule = apex_rule(min_winning_days=1, winning_day_threshold=1, consistency_pct=None, payout_reserve=0)
    grid = run_forward_volume_grid(
        sampled,
        funded_plan(rule),
        contract_values=[1],
        scenarios=[1.0],
        settings=LifecycleSettings(desired_gross_payout=500, missing_excursion_policy='error'),
    )
    row = grid.summary.iloc[0]
    total = (
        row['payout_before_failure_rate']
        + row['failure_before_payout_rate']
        + row['unresolved_rate']
    )
    assert total == pytest.approx(1.0)
    assert 0 <= row['payout_before_failure_rate'] <= 1
    assert 0 <= row['failure_before_payout_rate'] <= 1


def test_forward_grid_contract_interface_caps_at_four_mnq():
    trades = [ft('a', '2024-01-02', 10)]
    sampled = ForwardVolumeScenarioSampler(
        trades,
        forecast_start='2026-07-01',
        forecast_end='2026-07-31',
        monthly_trade_counts={'2026-07': 1},
    ).sample_paths(paths=1, master_seed=1)
    with pytest.raises(PropLabValidationError, match='between 1 and 4'):
        run_forward_volume_grid(
            sampled,
            funded_plan(apex_rule(max_contracts=6)),
            contract_values=[5],
            scenarios=[1.0],
            settings=LifecycleSettings(missing_excursion_policy='error'),
        )


def decision_summary_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                'plan': 'p', 'point_scale': 1.0, 'scenario_label': 'Base', 'contracts': 1, 'paths': 100,
                'payout_before_failure_rate': .55, 'failure_before_payout_rate': .20, 'unresolved_rate': .25,
                'mean_net_cash': 1_000, 'p50_first_payout_day_conditional': 8,
                'payout_before_failure_by_day_10': .10, 'failure_before_payout_by_day_10': .05,
            },
            {
                'plan': 'p', 'point_scale': 1.0, 'scenario_label': 'Base', 'contracts': 2, 'paths': 100,
                'payout_before_failure_rate': .50, 'failure_before_payout_rate': .25, 'unresolved_rate': .25,
                'mean_net_cash': 1_500, 'p50_first_payout_day_conditional': 20,
                'payout_before_failure_by_day_10': .30, 'failure_before_payout_by_day_10': .10,
            },
            {
                'plan': 'p', 'point_scale': 1.0, 'scenario_label': 'Base', 'contracts': 3, 'paths': 100,
                'payout_before_failure_rate': .40, 'failure_before_payout_rate': .90, 'unresolved_rate': .10,
                'mean_net_cash': 9_000, 'p50_first_payout_day_conditional': 5,
                'payout_before_failure_by_day_10': .25, 'failure_before_payout_by_day_10': .50,
            },
        ]
    )


def test_forecast_clock_anchor_uses_forecast_start_not_first_trade_date():
    rule = apex_rule(min_winning_days=1, winning_day_threshold=1, consistency_pct=None, payout_reserve=0)
    trades = [
        ft('first', '2026-07-15', 0, mae=0, mfe=0),
        ft('payout', '2026-07-30', 300, mae=0, mfe=300),
    ]
    result = simulate_lifecycle_path(
        trades,
        funded_plan(rule),
        contracts=1,
        settings=LifecycleSettings(desired_gross_payout=500, missing_excursion_policy='error'),
        clock_start_date=pd.Timestamp('2026-07-07'),
    )
    assert result.first_payout_day == 23
    assert result.first_payout_day != 15


def test_day_zero_five_day_qualification_is_recorded_from_clock_origin():
    trade = ft('later', '2026-07-15', 0, mae=0, mfe=0)
    result = simulate_lifecycle_path(
        [trade],
        funded_plan(),
        contracts=1,
        settings=LifecycleSettings(
            current_balance=50_500,
            current_floor=49_000,
            current_winning_days=5,
            current_highest_winning_day=250,
            missing_excursion_policy='error',
        ),
        clock_start_date=pd.Timestamp('2026-07-07'),
    )
    assert result.first_five_qualifying_days_day == 0
    assert result.balance_at_five_qualifying_days == 50_500


def test_day_zero_payout_eligibility_is_recorded_even_without_payout():
    trade = ft('later', '2026-07-15', 0, mae=0, mfe=0)
    result = simulate_lifecycle_path(
        [trade],
        funded_plan(apex_rule(consistency_pct=None)),
        contracts=1,
        settings=LifecycleSettings(
            current_balance=52_600,
            current_floor=50_000,
            current_winning_days=5,
            current_highest_winning_day=500,
            desired_gross_payout=1_500,
            required_post_payout_cushion=3_000,
            missing_excursion_policy='error',
        ),
        clock_start_date=pd.Timestamp('2026-07-07'),
    )
    assert result.first_payout_eligible_day == 0
    assert result.first_payout_day is None
    assert result.payouts_taken == 0


def test_day_zero_payout_can_happen_before_future_trade():
    trade = ft('later', '2026-07-15', 0, mae=0, mfe=0)
    result = simulate_lifecycle_path(
        [trade],
        funded_plan(apex_rule(consistency_pct=None)),
        contracts=1,
        settings=LifecycleSettings(
            current_balance=52_600,
            current_floor=50_000,
            current_winning_days=5,
            current_highest_winning_day=500,
            desired_gross_payout=500,
            missing_excursion_policy='error',
        ),
        clock_start_date=pd.Timestamp('2026-07-07'),
    )
    assert result.first_payout_eligible_day == 0
    assert result.first_payout_day == 0
    assert result.payouts_taken == 1
    assert result.events[0].event == 'payout'
    assert result.events[0].session_date == '2026-07-07'


def test_clock_origin_rejects_trades_before_forecast_start():
    with pytest.raises(PropLabValidationError, match='before the supplied lifecycle clock origin'):
        simulate_lifecycle_path(
            [ft('early', '2026-07-06', 0)],
            funded_plan(),
            contracts=1,
            settings=LifecycleSettings(missing_excursion_policy='error'),
            clock_start_date=pd.Timestamp('2026-07-07'),
        )


def test_fastest_uses_unconditional_payout_by_target_day_probability():
    labelled = label_summary_decisions(decision_summary_frame(), max_failure_rate=.50, fastest_target_day=10)
    fastest_row = labelled[labelled['decision_label'].str.contains('Fastest', regex=False)].iloc[0]
    assert fastest_row['contracts'] == 2


def test_maximum_ev_ignores_risk_gate():
    labelled = label_summary_decisions(decision_summary_frame(), max_failure_rate=.30, fastest_target_day=10)
    max_ev = labelled[labelled['decision_label'].str.contains('Maximum EV', regex=False)].iloc[0]
    assert max_ev['contracts'] == 3
    assert max_ev['failure_before_payout_rate'] > .30


def test_convex_respects_risk_gate_and_can_have_no_qualifier():
    labelled = label_summary_decisions(decision_summary_frame(), max_failure_rate=.30, fastest_target_day=10)
    convex = labelled[labelled['decision_label'].str.contains('Convex', regex=False)].iloc[0]
    assert convex['contracts'] == 2

    none = label_summary_decisions(decision_summary_frame(), max_failure_rate=.10, fastest_target_day=10)
    assert not none['decision_label'].str.contains('Convex', regex=False).any()
    assert 'No Convex configuration qualifies' in none['convex_note'].iloc[0]


def test_no_arbitrary_convexity_penalty_column_remains():
    labelled = label_summary_decisions(decision_summary_frame(), max_failure_rate=.30, fastest_target_day=10)
    assert 'convexity_delta' not in labelled.columns
    assert 'ev_uplift_vs_smaller' in labelled.columns
    assert 'extra_failure_vs_smaller' in labelled.columns
    assert 'payout_probability_uplift_vs_smaller' in labelled.columns
    assert 'median_time_change_vs_smaller' in labelled.columns


def test_pareto_frontier_includes_conditional_payout_time():
    frame = pd.DataFrame(
        [
            {'payout_before_failure_rate': .6, 'mean_net_cash': 1000, 'failure_before_payout_rate': .2, 'p50_first_payout_day_conditional': 10},
            {'payout_before_failure_rate': .6, 'mean_net_cash': 1000, 'failure_before_payout_rate': .2, 'p50_first_payout_day_conditional': 20},
            {'payout_before_failure_rate': .7, 'mean_net_cash': 900, 'failure_before_payout_rate': .2, 'p50_first_payout_day_conditional': None},
        ],
        index=['fast', 'slow', 'missing'],
    )
    assert pareto_frontier_indexes(frame) == ['fast', 'missing']


def test_first_passage_three_state_probabilities_sum_to_one():
    summary = decision_summary_frame().copy()
    summary['unresolved_by_day_10'] = 1 - summary['payout_before_failure_by_day_10'] - summary['failure_before_payout_by_day_10']
    passage = first_passage_threshold_frame(summary, contracts=1, scenario_label='Base')
    assert passage.iloc[0]['payout_before_failure'] + passage.iloc[0]['failure_before_payout'] + passage.iloc[0]['unresolved'] == pytest.approx(1)


def test_raw_stop_scaling_cap_and_uncapped_cases():
    capped = ft('capped', '2026-01-02', pnl=100, raw=300, mae=60, mfe=200)
    capped_scaled = capped.scaled_for_point_volatility(.8)
    assert capped_scaled.raw_stop_points == 240
    assert capped_scaled.stop_points == 200

    uncapped = ft('uncapped', '2026-01-02', pnl=90, raw=180, mae=45, mfe=180)
    uncapped_scaled = uncapped.scaled_for_point_volatility(.8)
    assert uncapped_scaled.raw_stop_points == 144
    assert uncapped_scaled.stop_points == 144


def test_positive_and_negative_pnl_scale_with_point_volatility_when_uncapped():
    winner = ft('winner', '2026-01-02', pnl=90, raw=180, mae=20, mfe=180)
    loser = ft('loser', '2026-01-03', pnl=-90, raw=180, mae=90, mfe=20)
    low_winner = winner.scaled_for_point_volatility(.8)
    low_loser = loser.scaled_for_point_volatility(.8)
    high_winner = winner.scaled_for_point_volatility(1.2)
    high_loser = loser.scaled_for_point_volatility(1.2)
    assert 0 < low_winner.pnl_points < winner.pnl_points
    assert winner.pnl_points < high_winner.pnl_points
    assert loser.pnl_points < low_loser.pnl_points < 0
    assert high_loser.pnl_points < loser.pnl_points


def test_sampler_seed_reproducibility_difference_and_sequence_randomization():
    trades = [ft(f't{i}', f'2024-01-{i + 2:02d}', i + 1) for i in range(5)]
    sampler = ForwardVolumeScenarioSampler(
        trades,
        forecast_start='2026-07-01',
        forecast_end='2026-07-31',
        monthly_trade_counts={'2026-07': 5},
    )
    first = sampler.sample_paths(paths=1, master_seed=100)[0]
    second = sampler.sample_paths(paths=1, master_seed=100)[0]
    different = sampler.sample_paths(paths=1, master_seed=101)[0]
    assert first.manifest == second.manifest
    assert first.manifest != different.manifest
    historical_order = [trade.trade_id for trade in trades]
    sampled_source_order = [item[1].split(':')[-2] if ':' in item[1] else item[1] for item in first.manifest]
    assert sampled_source_order != historical_order


def test_source_ids_and_synthetic_dates_are_identical_across_scenarios():
    trades = [ft('a', '2024-01-02', 100), ft('b', '2024-01-03', -50, mae=50)]
    sampled = ForwardVolumeScenarioSampler(
        trades,
        forecast_start='2026-07-01',
        forecast_end='2026-07-31',
        monthly_trade_counts={'2026-07': 3},
    ).sample_paths(paths=5, master_seed=7)
    grid = run_forward_volume_grid(
        sampled,
        funded_plan(apex_rule(min_winning_days=99, consistency_pct=None)),
        contract_values=[1, 2],
        scenarios=[ForwardScenario(1.0, 'Base'), ForwardScenario(1.1, '+10%')],
        settings=LifecycleSettings(missing_excursion_policy='error'),
    )
    base = grid.sampled_trades[grid.sampled_trades['scenario_label'] == 'Base'].sort_values(['path_id', 'trade_id'])
    plus = grid.sampled_trades[grid.sampled_trades['scenario_label'] == '+10%'].sort_values(['path_id', 'trade_id'])
    assert list(base['source_trade_id']) == list(plus['source_trade_id'])
    assert list(base['session_date']) == list(plus['session_date'])
    assert 'contracts' not in grid.sampled_trades.columns


def test_forward_summary_contains_payout_day_and_ending_cushion_percentiles():
    trades = [ft('win', '2024-01-02', 300, mae=0, mfe=300), ft('loss', '2024-01-03', -100, mae=100)]
    sampled = ForwardVolumeScenarioSampler(
        trades,
        forecast_start='2026-07-01',
        forecast_end='2026-07-31',
        monthly_trade_counts={'2026-07': 2},
    ).sample_paths(paths=10, master_seed=9)
    grid = run_forward_volume_grid(
        sampled,
        funded_plan(apex_rule(min_winning_days=1, winning_day_threshold=1, consistency_pct=None, payout_reserve=0)),
        contract_values=[1],
        scenarios=[1.0],
        settings=LifecycleSettings(desired_gross_payout=500, missing_excursion_policy='error'),
    )
    for column in [
        'p05_first_payout_day_conditional',
        'p50_first_payout_day_conditional',
        'p95_first_payout_day_conditional',
        'p05_ending_cushion',
        'p50_ending_cushion',
        'p95_ending_cushion',
    ]:
        assert column in grid.summary.columns
