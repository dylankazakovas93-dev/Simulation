from __future__ import annotations

from dataclasses import asdict
import pandas as pd
import streamlit as st

from verified_prop_lab import (
    LifecyclePlan,
    LifecycleSettings,
    PropLabValidationError,
    PropRule,
    SameCalendarMonthSampler,
    load_ledger_frame,
    run_common_path_grid,
    simulate_lifecycle_path,
)


def apex_50k_provisional() -> PropRule:
    return PropRule(
        firm='Apex Trader Funding',
        account_name='EOD PA 50K (provisional rules)',
        starting_balance=50_000,
        max_loss=2_000,
        drawdown_mode='eod_trailing',
        max_contracts=6,
        daily_loss_limit=1_000,
        daily_loss_hard=False,
        min_winning_days=5,
        winning_day_threshold=250,
        consistency_pct=0.50,
        min_payout=500,
        payout_reserve=2_100,
        payout_profit_fraction=1.0,
        profit_split=1.0,
        payout_caps=(1_500,),
        max_payouts=6,
    )


def main() -> None:
    st.set_page_config(page_title='Verified Prop Lab', layout='wide')
    st.title('Verified Prop Lab')
    st.warning('Firm rules are provisional until rechecked against current official documents.')

    uploaded = st.file_uploader('Strategy ledger CSV', type=['csv'])
    if uploaded is None:
        return

    left, right = st.columns(2)
    contracts = left.number_input('MNQ contracts', min_value=1, max_value=6, value=1)
    dpp = left.number_input('Dollars per point per contract', min_value=0.01, value=2.0)
    commission = left.number_input('Round-turn commission per contract', min_value=0.0, value=0.0)
    slippage = left.number_input('Round-turn slippage, points', min_value=0.0, value=0.0)
    exact_mae = right.checkbox('Require MAE for exact barrier simulation', value=True)
    paths = right.number_input('Monte Carlo paths', min_value=10, max_value=5000, value=500)
    seed = right.number_input('Master seed', value=1729)
    start_month = right.text_input('Synthetic start month', value='2027-01')
    horizon = right.number_input('Horizon months', min_value=1, max_value=24, value=12)

    frame = pd.read_csv(uploaded)
    try:
        trades = load_ledger_frame(
            frame,
            strategy_id=uploaded.name.rsplit('.', 1)[0],
            default_dollars_per_point=float(dpp),
            default_commission_round_turn=float(commission),
            default_slippage_points_round_turn=float(slippage),
        )
        rule = apex_50k_provisional()
        plan = LifecyclePlan('Apex 50K funded', funded_rule=rule)
        settings = LifecycleSettings(
            start_stage='funded',
            missing_excursion_policy='error' if exact_mae else 'realized_only',
        )
        historical = simulate_lifecycle_path(trades, plan, contracts=int(contracts), settings=settings)
    except PropLabValidationError as exc:
        st.error(str(exc))
        if 'MAE is required' in str(exc):
            st.info('Regenerate the strategy ledger with MAE/MFE, or uncheck strict mode for an explicitly optimistic realized-only diagnostic.')
        return

    st.subheader('Historical trade-by-trade audit')
    st.json({
        'ending_balance': historical.ending_balance,
        'ending_floor': historical.ending_floor,
        'terminal_failed': historical.terminal_failed,
        'max_trading_drawdown': historical.max_trading_drawdown,
        'payouts_after_split': historical.cash_payouts_after_split,
        'external_fees': historical.external_fees,
    })
    audit = pd.DataFrame([asdict(row) for row in historical.trade_rows])
    periods = pd.DataFrame([asdict(row) for row in historical.drawdown_periods])
    st.dataframe(audit, use_container_width=True)
    st.subheader('Drawdown periods')
    st.dataframe(periods, use_container_width=True)
    st.download_button('Download trade audit CSV', audit.to_csv(index=False), 'trade_audit.csv')
    st.download_button('Download drawdown periods CSV', periods.to_csv(index=False), 'drawdown_periods.csv')

    st.subheader('Seasonal monthly-block Monte Carlo')
    try:
        sampled = SameCalendarMonthSampler(
            trades,
            horizon_months=int(horizon),
            start_month=start_month,
        ).sample_paths(paths=int(paths), master_seed=int(seed))
        _, summary = run_common_path_grid(
            sampled,
            [plan],
            [int(contracts)],
            {plan.key: settings},
        )
    except PropLabValidationError as exc:
        st.error(str(exc))
        return
    st.dataframe(summary, use_container_width=True)
    manifest = pd.DataFrame([
        {'path_id': path.path_id, 'target_month': target, 'source_month': source}
        for path in sampled
        for target, source in path.manifest
    ])
    st.dataframe(manifest, use_container_width=True)
    st.download_button('Download path manifest CSV', manifest.to_csv(index=False), 'path_manifest.csv')


if __name__ == '__main__':
    main()
