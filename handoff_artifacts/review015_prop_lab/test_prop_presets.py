"""Tests for the firm presets and the comparison harness (config, not engine)."""
from __future__ import annotations

import pandas as pd

from app.prop_compare import compare_presets, comparison_dataframe
from app.prop_presets import ALL_PRESETS, presets_for_size, preset_by_key
from sim_core.models import Trade
from sim_core.prop_firm import PropFirmRules


def test_all_presets_are_valid_and_dated():
    assert len(ALL_PRESETS) == 8
    for p in ALL_PRESETS:
        assert isinstance(p.rules, PropFirmRules)      # constructs => validated
        assert p.sources and all(s.startswith("http") for s in p.sources)
        assert p.notes                                  # every preset states assumptions
        assert p.account_size in (50_000, 100_000)
        assert p.cost_to_funded >= 0
    assert len(presets_for_size(50_000)) == 4
    assert len(presets_for_size(100_000)) == 4
    assert preset_by_key("apex_eod_50k").firm == "Apex"


def test_presets_use_end_of_day_trailing_where_documented():
    # Apex/FundedNext/Alpha model EOD trailing; the field must be set (not defaulted silently).
    for key in ("apex_eod_50k", "fundednext_50k", "alpha_50k"):
        assert preset_by_key(key).rules.trailing_basis == "end_of_day"


def _synth_trades(n=120):
    trades = []
    day = pd.Timestamp("2025-01-02T14:35:00Z")
    i = 0
    while len(trades) < n:
        if day.weekday() < 5:
            i += 1
            pnl = 120.0 if i % 2 else -80.0
            trades.append(Trade(trade_id=f"t{i}", source_row_id=str(i), strategy_id="s",
                instrument="NQ", contract_symbol="MNQ", entry_time=day,
                exit_time=day + pd.Timedelta(minutes=20), pnl_dollars=pnl, dollars_per_point=2.0))
        day += pd.Timedelta(days=1)
    return trades


def test_compare_presets_produces_one_row_per_preset_with_components():
    trades = _synth_trades()
    rows = compare_presets(trades, ALL_PRESETS, funded_horizon_months=2,
                           all_horizons=(2,), num_starts=15, seed=1)
    assert len(rows) == len(ALL_PRESETS)
    df = comparison_dataframe(rows)
    for col in ("firm", "plan", "cost_to_funded", "eval_pass_rate", "funded_2mo_blow_rate"):
        assert col in df.columns
    for r in rows:
        b = r["funded_2mo_blow_rate"]
        assert b is None or 0.0 <= b <= 1.0
