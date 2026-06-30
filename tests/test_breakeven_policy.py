"""ADR-012 / MEDIUM-R3-D — explicit breakeven epsilon policy.

Exact zero by default; optional explicit dollar or tick tolerance; the selected
policy is recorded in Scenario metadata.
"""
from __future__ import annotations

import pandas as pd

from sim_core.metrics.reports import trade_outcome_taxonomy
from sim_core.models import BreakevenPolicy, Scenario, Trade


def _trade(pnl: float) -> Trade:
    return Trade(
        trade_id="t",
        source_row_id="r",
        strategy_id="s",
        instrument="ES",
        contract_symbol="MES",
        entry_time=pd.Timestamp("2025-01-02 09:30", tz="UTC"),
        exit_time=pd.Timestamp("2025-01-02 10:00", tz="UTC"),
        pnl_dollars=pnl,
    )


def test_default_is_exact_zero():
    tax = trade_outcome_taxonomy([_trade(0.0), _trade(0.01), _trade(-0.01)])
    assert tax["n_breakeven"] == 1.0
    assert tax["n_win"] == 1.0
    assert tax["n_loss"] == 1.0


def test_dollar_tolerance_boundary_below_at_above():
    policy = BreakevenPolicy(mode="dollars", tolerance_dollars=5.0)
    trades = [_trade(4.0), _trade(5.0), _trade(5.01), _trade(-5.0), _trade(-5.01)]
    tax = trade_outcome_taxonomy(trades, policy=policy)
    # below tolerance and exactly at tolerance -> breakeven; strictly above -> win/loss
    assert tax["n_breakeven"] == 3.0  # 4.0, 5.0, -5.0
    assert tax["n_win"] == 1.0  # 5.01
    assert tax["n_loss"] == 1.0  # -5.01


def test_tick_tolerance_resolves_against_dollars_per_tick():
    policy = BreakevenPolicy(mode="ticks", ticks=2.0)
    # MES tick value $1.25 -> tolerance = 2 * 1.25 = $2.50
    tax = trade_outcome_taxonomy(
        [_trade(2.5), _trade(2.51)], policy=policy, dollars_per_tick=1.25
    )
    assert tax["n_breakeven"] == 1.0  # 2.50 at tolerance
    assert tax["n_win"] == 1.0  # 2.51 above


def test_breakeven_policy_recorded_in_scenario_metadata_round_trips():
    policy = BreakevenPolicy(mode="dollars", tolerance_dollars=5.0)
    scenario = Scenario(
        master_seed=1,
        resampling_policy="historical_replay",
        policy_params={},
        breakeven_policy=policy.to_dict(),
    )
    assert scenario.breakeven_policy["mode"] == "dollars"
    assert scenario.breakeven_policy["tolerance_dollars"] == 5.0
    assert Scenario.from_dict(scenario.to_dict()).breakeven_policy == scenario.breakeven_policy
