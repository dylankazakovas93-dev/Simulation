from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import sys

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from verified_prop_lab import (  # noqa: E402
    ForwardScenario,
    ForwardVolumeScenarioSampler,
    LifecyclePlan,
    LifecycleSettings,
    PropLabValidationError,
    PropRule,
    forecast_months,
    load_ledger_frame,
    run_forward_volume_grid,
    simulate_lifecycle_path,
)


SCENARIO_OPTIONS = {
    '-20%': 0.80,
    '-15%': 0.85,
    '-10%': 0.90,
    'Base': 1.00,
    '+10%': 1.10,
    '+15%': 1.15,
    '+20%': 1.20,
}


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


def read_uploaded_ledger(uploaded) -> pd.DataFrame:
    name = uploaded.name.lower()
    if name.endswith(('.html', '.htm')):
        tables = pd.read_html(uploaded)
        if not tables:
            raise PropLabValidationError('HTML upload did not contain a readable table')
        return max(tables, key=len)
    return pd.read_csv(uploaded)


def money(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return '-'
    return f'${float(value):,.0f}'


def pct(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return '-'
    return f'{float(value) * 100:.1f}%'


def days(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return '-'
    return f'D{int(round(float(value)))}'


def enrich_summary(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary
    rows = summary.sort_values(['scenario_label', 'contracts']).copy()
    rows['ev_uplift_vs_smaller'] = 0.0
    rows['extra_blow_vs_smaller'] = 0.0
    rows['convexity_delta'] = rows['mean_net_cash']
    for (_, scenario), group in rows.groupby(['plan', 'scenario_label'], sort=False):
        ordered = group.sort_values('contracts')
        previous_index = None
        for index, row in ordered.iterrows():
            if previous_index is None:
                rows.loc[index, 'convexity_delta'] = row['mean_net_cash']
            else:
                prev = rows.loc[previous_index]
                ev_uplift = row['mean_net_cash'] - prev['mean_net_cash']
                extra_blow = row['failure_before_payout_rate'] - prev['failure_before_payout_rate']
                rows.loc[index, 'ev_uplift_vs_smaller'] = ev_uplift
                rows.loc[index, 'extra_blow_vs_smaller'] = extra_blow
                rows.loc[index, 'convexity_delta'] = ev_uplift - max(0.0, extra_blow) * 2_000
            previous_index = index
    return rows


def label_decisions(summary: pd.DataFrame, max_blow_rate: float) -> pd.DataFrame:
    rows = enrich_summary(summary)
    if rows.empty:
        return rows
    rows = rows.copy()
    rows['risk_gate'] = rows['failure_before_payout_rate'] <= max_blow_rate
    rows['decision_label'] = ''
    candidate = rows[rows['risk_gate']].copy()
    if candidate.empty:
        candidate = rows.copy()
    fastest = candidate.sort_values(
        ['p50_first_payout_day_conditional', 'payout_before_failure_rate'],
        ascending=[True, False],
        na_position='last',
    ).head(1).index
    survival = candidate.sort_values(
        ['payout_before_failure_rate', 'failure_before_payout_rate'],
        ascending=[False, True],
    ).head(1).index
    ev = candidate.sort_values('mean_net_cash', ascending=False).head(1).index
    convex = candidate.sort_values('convexity_delta', ascending=False).head(1).index
    for label, indexes in [('Fastest', fastest), ('Survival', survival), ('Maximum EV', ev), ('Convex', convex)]:
        for index in indexes:
            existing = rows.loc[index, 'decision_label']
            rows.loc[index, 'decision_label'] = f'{existing}, {label}'.strip(', ')
    pareto = []
    for index, row in candidate.iterrows():
        dominated = candidate[
            (candidate['payout_before_failure_rate'] >= row['payout_before_failure_rate'])
            & (candidate['mean_net_cash'] >= row['mean_net_cash'])
            & (candidate['failure_before_payout_rate'] <= row['failure_before_payout_rate'])
            & (
                (candidate['payout_before_failure_rate'] > row['payout_before_failure_rate'])
                | (candidate['mean_net_cash'] > row['mean_net_cash'])
                | (candidate['failure_before_payout_rate'] < row['failure_before_payout_rate'])
            )
        ]
        if dominated.empty:
            pareto.append(index)
    for index in pareto:
        existing = rows.loc[index, 'decision_label']
        rows.loc[index, 'decision_label'] = f'{existing}, Pareto frontier'.strip(', ')
    rows.loc[~rows['risk_gate'], 'decision_label'] = rows.loc[~rows['risk_gate'], 'decision_label'].replace('', 'Above risk gate')
    return rows


def summary_display(summary: pd.DataFrame) -> pd.DataFrame:
    display = summary[
        [
            'scenario_label',
            'contracts',
            'paths',
            'payout_before_failure_rate',
            'failure_before_payout_rate',
            'unresolved_rate',
            'p50_first_payout_day_conditional',
            'mean_net_cash',
            'p50_net_cash',
            'avg_payout_cash',
            'avg_payout_count',
            'p95_max_drawdown',
            'p50_ending_cushion',
            'convexity_delta',
            'decision_label',
        ]
    ].copy()
    display.columns = [
        'scenario',
        'MNQ',
        'paths',
        'paid before fail',
        'failed before payout',
        'unresolved',
        'median payout day',
        'avg net cash',
        'median net cash',
        'avg withdrawn',
        'avg payout count',
        'p95 drawdown',
        'median ending cushion',
        'convexity delta',
        'label',
    ]
    for column in ['paid before fail', 'failed before payout', 'unresolved']:
        display[column] = display[column].map(pct)
    for column in ['avg net cash', 'median net cash', 'avg withdrawn', 'p95 drawdown', 'median ending cushion', 'convexity delta']:
        display[column] = display[column].map(money)
    display['median payout day'] = display['median payout day'].map(days)
    display['avg payout count'] = display['avg payout count'].map(lambda value: f'{float(value):.2f}')
    return display


def render_first_answer(summary: pd.DataFrame) -> None:
    if summary.empty:
        return
    base = summary[summary['scenario_label'] == 'Base']
    focus = base if not base.empty else summary
    row = focus.sort_values(
        ['payout_before_failure_rate', 'failure_before_payout_rate', 'p50_first_payout_day_conditional'],
        ascending=[False, True, True],
        na_position='last',
    ).iloc[0]
    st.markdown(
        (
            f"### Base answer: {int(row['contracts'])} MNQ has "
            f"**{pct(row['payout_before_failure_rate'])}** payout-before-failure, "
            f"**{pct(row['failure_before_payout_rate'])}** failure-before-payout, "
            f"conditional median payout at **{days(row['p50_first_payout_day_conditional'])}**, "
            f"and average net cash **{money(row['mean_net_cash'])}** across "
            f"{int(row['paths'])} paired paths."
        )
    )


def main() -> None:
    st.set_page_config(page_title='Verified Prop Lab', layout='wide')
    st.title('Verified Prop Lab')
    st.caption('Rolling near-term forward simulator. Results are path-paired across MNQ size and point-volatility scenarios.')
    st.warning('Firm rules are provisional until rechecked against current official documents.')

    uploaded = st.file_uploader('Strategy ledger CSV or HTML table', type=['csv', 'html', 'htm'])
    if uploaded is None:
        st.info('Upload a ledger with raw/effective stop, PnL, MAE, and MFE columns to begin.')
        return

    frame = read_uploaded_ledger(uploaded)

    with st.expander('Ledger mapping and defaults', expanded=True):
        col1, col2, col3, col4 = st.columns(4)
        dpp = col1.number_input('Dollars per point per MNQ', min_value=0.01, value=2.0)
        commission = col2.number_input('Round-turn commission per MNQ', min_value=0.0, value=0.0)
        slippage = col3.number_input('Round-turn slippage, points', min_value=0.0, value=0.0)
        strict_schema = col4.checkbox('Require forward schema', value=True)

    try:
        trades = load_ledger_frame(
            frame,
            strategy_id=uploaded.name.rsplit('.', 1)[0],
            default_dollars_per_point=float(dpp),
            default_commission_round_turn=float(commission),
            default_slippage_points_round_turn=float(slippage),
            require_forward_schema=strict_schema,
        )
    except PropLabValidationError as exc:
        st.error(str(exc))
        return

    st.subheader('Source ledger audit')
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Trades loaded', len(trades))
    c2.metric('Strategies', len({trade.strategy_id for trade in trades}))
    c3.metric('Median raw stop', f'{pd.Series([trade.raw_stop_points for trade in trades]).median():.1f} pts')
    c4.metric('Median PnL R', f'{pd.Series([trade.pnl_r for trade in trades]).median():.2f}R')

    st.subheader('Rolling forecast controls')
    left, right = st.columns([1.15, 0.85])
    with left:
        dates = st.columns(2)
        forecast_start = dates[0].date_input('Forecast start', value=pd.Timestamp('2026-07-07').date())
        forecast_end = dates[1].date_input('Forecast end', value=pd.Timestamp('2026-08-31').date())
        months = forecast_months(pd.Timestamp(forecast_start), pd.Timestamp(forecast_end))
        default_volume = pd.DataFrame({'month': [str(month) for month in months], 'expected_trades': [0 for _ in months]})
        volume = st.data_editor(
            default_volume,
            hide_index=True,
            use_container_width=True,
            column_config={'expected_trades': st.column_config.NumberColumn(min_value=0, step=1)},
        )
        selected_labels = st.multiselect(
            'Point-volatility scenarios',
            list(SCENARIO_OPTIONS),
            default=list(SCENARIO_OPTIONS),
        )
        contract_values = st.multiselect('MNQ contract sizes', [1, 2, 3, 4], default=[1, 2, 3, 4])
    with right:
        paths = st.number_input('Bootstrapped paths', min_value=100, max_value=20_000, value=1_000, step=100)
        seed = st.number_input('Internal reproducibility seed', value=1729)
        thresholds_text = st.text_input('First-passage days', value='10,20,30,45,60')
        max_blow_rate = st.slider('Risk gate: max failure before payout', 0, 100, 50) / 100

    rule = apex_50k_provisional()
    plan = LifecyclePlan('Apex 50K funded', funded_rule=rule)

    st.subheader('Current funded account state')
    state_cols = st.columns(4)
    current_profit = state_cols[0].number_input('Current profit above start', value=500.0)
    current_cushion = state_cols[1].number_input('Drawdown cushion left', value=1_500.0)
    qualifying_days = state_cols[2].number_input('Qualifying days already', min_value=0, value=1)
    highest_day = state_cols[3].number_input('Highest winning day', value=800.0)
    payout_cols = st.columns(3)
    desired_payout = payout_cols[0].number_input('Desired gross payout, 0 = max allowed', value=1_500.0)
    required_cushion = payout_cols[1].number_input('Required cushion after payout', value=0.0)
    exact_mae = payout_cols[2].checkbox('Exact MAE barrier simulation', value=True)
    current_balance = rule.starting_balance + float(current_profit)
    current_floor = current_balance - float(current_cushion)
    st.caption(f'Effective account state: balance {money(current_balance)} | floor {money(current_floor)} | cushion {money(current_cushion)}')

    if not st.button('Run rolling forecast', type='primary', use_container_width=True):
        return

    try:
        monthly_counts = {str(row['month']): int(row['expected_trades']) for _, row in volume.iterrows()}
        if sum(monthly_counts.values()) <= 0:
            raise PropLabValidationError('enter at least one expected trade in the forecast volume table')
        thresholds = [int(part.strip()) for part in thresholds_text.split(',') if part.strip()]
        scenarios = [ForwardScenario(SCENARIO_OPTIONS[label], label) for label in selected_labels]
        settings = LifecycleSettings(
            start_stage='funded',
            current_balance=current_balance,
            current_floor=current_floor,
            current_winning_days=int(qualifying_days),
            current_highest_winning_day=float(highest_day),
            desired_gross_payout=float(desired_payout),
            required_post_payout_cushion=float(required_cushion),
            missing_excursion_policy='error' if exact_mae else 'realized_only',
        )
        sampled = ForwardVolumeScenarioSampler(
            trades,
            forecast_start=pd.Timestamp(forecast_start),
            forecast_end=pd.Timestamp(forecast_end),
            monthly_trade_counts=monthly_counts,
        ).sample_paths(paths=int(paths), master_seed=int(seed))
        historical = simulate_lifecycle_path(trades, plan, contracts=1, settings=settings)
        grid = run_forward_volume_grid(
            sampled,
            plan,
            contract_values=[int(value) for value in contract_values],
            scenarios=scenarios,
            settings=settings,
            time_threshold_days=thresholds,
        )
    except (PropLabValidationError, ValueError) as exc:
        st.error(str(exc))
        return

    summary = label_decisions(grid.summary, max_blow_rate=max_blow_rate)
    render_first_answer(summary)

    tabs = st.tabs(['Decision view', 'Heatmap', 'First-passage', 'Net cash', 'Path inspector', 'Validation audit'])
    with tabs[0]:
        st.dataframe(summary_display(summary), use_container_width=True, hide_index=True)
        st.caption('Labels are transparent: Survival maximizes payout-before-failure, Fastest minimizes conditional payout day, Maximum EV maximizes average net cash, Convex rewards EV uplift versus smaller size after extra failure risk.')

    with tabs[1]:
        metric = st.selectbox(
            'Heatmap value',
            ['payout_before_failure_rate', 'failure_before_payout_rate', 'mean_net_cash', 'avg_payout_cash', 'p50_first_payout_day_conditional'],
        )
        heat = summary.pivot_table(index='contracts', columns='scenario_label', values=metric, aggfunc='first')
        if 'rate' in metric:
            st.dataframe(heat.map(pct), use_container_width=True)
        elif 'day' in metric:
            st.dataframe(heat.map(days), use_container_width=True)
        else:
            st.dataframe(heat.map(money), use_container_width=True)

    with tabs[2]:
        threshold_cols = [column for column in summary.columns if column.startswith('payout_before_failure_by_day_')]
        passage_rows = []
        for _, row in summary.iterrows():
            for column in threshold_cols:
                passage_rows.append(
                    {
                        'scenario_contract': f"{row['scenario_label']} | {int(row['contracts'])} MNQ",
                        'day': int(column.rsplit('_', 1)[-1]),
                        'payout_before_failure': row[column],
                    }
                )
        passage = pd.DataFrame(passage_rows)
        if not passage.empty:
            chart = passage.pivot(index='day', columns='scenario_contract', values='payout_before_failure')
            st.line_chart(chart)

    with tabs[3]:
        net = summary[['scenario_label', 'contracts', 'mean_net_cash', 'p05_net_cash', 'p50_net_cash', 'p95_net_cash', 'avg_payout_cash', 'avg_payout_count']].copy()
        for column in ['mean_net_cash', 'p05_net_cash', 'p50_net_cash', 'p95_net_cash', 'avg_payout_cash']:
            net[column] = net[column].map(money)
        st.dataframe(net, use_container_width=True, hide_index=True)

    with tabs[4]:
        path_id = st.number_input('Path ID', min_value=0, max_value=max(0, int(paths) - 1), value=0)
        scenario_label = st.selectbox('Scenario', selected_labels)
        path_trades = grid.sampled_trades[
            (grid.sampled_trades['path_id'] == int(path_id))
            & (grid.sampled_trades['scenario_label'] == scenario_label)
        ]
        st.dataframe(path_trades, use_container_width=True, hide_index=True)
        path_events = grid.events[
            (grid.events['path_id'] == int(path_id))
            & (grid.events['scenario_label'] == scenario_label)
        ]
        st.dataframe(path_events, use_container_width=True, hide_index=True)

    with tabs[5]:
        audit = pd.DataFrame([asdict(row) for row in historical.trade_rows])
        source = pd.DataFrame(
            {
                'pnl_r': [trade.pnl_r for trade in trades],
                'mae_r': [trade.mae_r for trade in trades],
                'mfe_r': [trade.mfe_r for trade in trades],
                'raw_stop_points': [trade.raw_stop_points for trade in trades],
                'stop_points': [trade.stop_points for trade in trades],
                'sample_weight': [trade.sample_weight for trade in trades],
            }
        )
        st.write('Source shape summary')
        st.dataframe(source.describe().T, use_container_width=True)
        st.write('Historical one-contract audit on uploaded order')
        st.dataframe(audit, use_container_width=True, hide_index=True)
        st.download_button('Download forward summary CSV', summary.to_csv(index=False), 'forward_summary.csv')
        st.download_button('Download sampled trades CSV', grid.sampled_trades.to_csv(index=False), 'sampled_forward_trades.csv')


if __name__ == '__main__':
    main()
