import pandas as pd

from verified_prop_lab import label_summary_decisions


def _row(
    scenario: str,
    contracts: int,
    *,
    payout: float,
    failure: float,
    cash: float,
    payout_d10: float,
    failure_d10: float,
    payout_day: float,
    source_pool_id: str | None = None,
) -> dict:
    row = {
        'plan': 'p',
        'point_scale': {'-20%': 0.8, 'Base': 1.0}[scenario],
        'scenario_label': scenario,
        'contracts': contracts,
        'paths': 100,
        'payout_before_failure_rate': payout,
        'failure_before_payout_rate': failure,
        'unresolved_rate': 1.0 - payout - failure,
        'mean_net_cash': cash,
        'p50_first_payout_day_conditional': payout_day,
        'payout_before_failure_by_day_10': payout_d10,
        'failure_before_payout_by_day_10': failure_d10,
    }
    if source_pool_id is not None:
        row['source_pool_id'] = source_pool_id
    return row


def _mixed_scenario_summary() -> pd.DataFrame:
    return pd.DataFrame(
        [
            _row('-20%', 1, payout=.30, failure=.25, cash=200, payout_d10=.10, failure_d10=.10, payout_day=18),
            _row('-20%', 2, payout=.35, failure=.30, cash=350, payout_d10=.20, failure_d10=.15, payout_day=14),
            _row('Base', 1, payout=.70, failure=.10, cash=1200, payout_d10=.50, failure_d10=.05, payout_day=8),
            _row('Base', 2, payout=.65, failure=.20, cash=1800, payout_d10=.55, failure_d10=.10, payout_day=7),
        ]
    )


def test_each_point_volatility_scenario_receives_its_own_decision_labels():
    labelled = label_summary_decisions(
        _mixed_scenario_summary(),
        max_failure_rate=.50,
        fastest_target_day=10,
    )

    for scenario, group in labelled.groupby('scenario_label'):
        labels = ', '.join(group['decision_label'])
        assert 'Survival' in labels, scenario
        assert 'Fastest D10' in labels, scenario
        assert 'Maximum EV' in labels, scenario
        assert 'Convex' in labels, scenario


def test_stronger_volatility_scenario_cannot_take_labels_from_another_scenario():
    labelled = label_summary_decisions(
        _mixed_scenario_summary(),
        max_failure_rate=.50,
        fastest_target_day=10,
    )
    low = labelled[labelled['scenario_label'] == '-20%']

    assert low['decision_label'].str.contains('Survival', regex=False).any()
    assert low['decision_label'].str.contains('Fastest D10', regex=False).any()
    assert low['decision_label'].str.contains('Maximum EV', regex=False).any()
    assert low['decision_label'].str.contains('Convex', regex=False).any()


def test_pareto_frontier_does_not_cross_point_volatility_scenarios():
    labelled = label_summary_decisions(
        _mixed_scenario_summary(),
        max_failure_rate=.50,
        fastest_target_day=10,
    )

    for scenario, group in labelled.groupby('scenario_label'):
        assert group['decision_label'].str.contains('Pareto frontier', regex=False).any(), scenario


def test_source_pool_id_is_also_a_decision_boundary_when_present():
    rows = []
    for pool, cash_offset in [('FULL_HISTORY', 0), ('HIGH_STOP_140', 5000)]:
        rows.extend(
            [
                _row('Base', 1, payout=.55, failure=.20, cash=500 + cash_offset, payout_d10=.20, failure_d10=.05, payout_day=12, source_pool_id=pool),
                _row('Base', 2, payout=.50, failure=.30, cash=800 + cash_offset, payout_d10=.30, failure_d10=.10, payout_day=10, source_pool_id=pool),
            ]
        )
    labelled = label_summary_decisions(
        pd.DataFrame(rows),
        max_failure_rate=.50,
        fastest_target_day=10,
    )

    for pool, group in labelled.groupby('source_pool_id'):
        labels = ', '.join(group['decision_label'])
        assert 'Survival' in labels, pool
        assert 'Fastest D10' in labels, pool
        assert 'Maximum EV' in labels, pool
        assert 'Convex' in labels, pool
        assert 'Pareto frontier' in labels, pool
