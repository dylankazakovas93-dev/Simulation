"""V3 — margin cap and exposure measurement tests."""
from __future__ import annotations

import pandas as pd
import pytest

from sim_core.exposure import (
    InstrumentMargin,
    MarginPolicy,
    build_exposure_report,
)
from sim_core.live_account import (
    FixedContractSizing,
    LiveAccountConfig,
    StrategyAllocation,
    run_live_account_path,
)
from sim_core.models import Trade


def _trade(rid, strategy, instrument, symbol, entry, exit_, pnl, dpp, stop=100.0):
    return Trade(
        trade_id=f"{strategy}-{rid}",
        source_row_id=rid,
        strategy_id=strategy,
        instrument=instrument,
        contract_symbol=symbol,
        entry_time=pd.Timestamp(entry),
        exit_time=pd.Timestamp(exit_),
        pnl_dollars=pnl,
        stop_points=stop,
        dollars_per_point=dpp,
    )


# --- margin ---------------------------------------------------------------------
def test_margin_cap_reduces_contracts_and_flags_reduction():
    trades = [_trade("t", "s", "ES", "MES", "2025-01-02T09:30:00Z", "2025-01-02T10:00:00Z", 100, 5.0)]
    policy = MarginPolicy({"MES": InstrumentMargin("MES", 4_000, 3_500)})
    result = run_live_account_path(
        trades,
        config=LiveAccountConfig(starting_equity=10_000),
        allocations={"s": StrategyAllocation("s", FixedContractSizing(3))},  # wants 3
        margin_policy=policy,
    )
    decision = result.sizing_decisions[0]
    assert decision.contracts == 2  # 10000 // 4000
    assert decision.margin_forced_reduction is True
    assert decision.initial_margin_used == 8_000.0
    assert result.summary["margin_forced_reductions"] == 1


def test_margin_requires_declared_spec_for_every_traded_contract():
    trades = [_trade("t", "s", "ES", "MES", "2025-01-02T09:30:00Z", "2025-01-02T10:00:00Z", 100, 5.0)]
    with pytest.raises(ValueError, match="no declared margin"):
        run_live_account_path(
            trades,
            config=LiveAccountConfig(starting_equity=10_000),
            allocations={"s": StrategyAllocation("s", FixedContractSizing(1))},
            margin_policy=MarginPolicy({"MNQ": InstrumentMargin("MNQ", 2_000, 1_800)}),
        )


# --- exposure -------------------------------------------------------------------
def _two_overlapping_strategies():
    trades = [
        _trade("a", "s1", "ES", "MES", "2025-01-02T09:30:00Z", "2025-01-02T11:00:00Z", 100, 5.0),
        _trade("b", "s2", "NQ", "MNQ", "2025-01-02T10:00:00Z", "2025-01-02T10:30:00Z", 50, 2.0),
    ]
    return run_live_account_path(
        trades,
        config=LiveAccountConfig(starting_equity=100_000),
        allocations={
            "s1": StrategyAllocation("s1", FixedContractSizing(1)),
            "s2": StrategyAllocation("s2", FixedContractSizing(1)),
        },
        margin_policy=MarginPolicy(
            {"MES": InstrumentMargin("MES", 500, 400), "MNQ": InstrumentMargin("MNQ", 400, 300)}
        ),
    )


def test_exposure_time_in_market_and_peak_overlap():
    report = build_exposure_report(
        _two_overlapping_strategies(),
        margin_policy=MarginPolicy(
            {"MES": InstrumentMargin("MES", 500, 400), "MNQ": InstrumentMargin("MNQ", 400, 300)}
        ),
    )
    # A spans the whole window (09:30-11:00), so the account is always in market.
    assert report.time_in_market_fraction == 1.0
    assert report.peak_simultaneous_positions == 2
    assert report.peak_simultaneous_contracts == 2
    # Overlap is 10:00-10:30 = 1800s of the 5400s span.
    assert abs(report.strategy_overlap_fraction - 1 / 3) < 1e-9
    assert abs(report.instrument_overlap_fraction - 1 / 3) < 1e-9


def test_exposure_peak_margin_and_stop_risk_use_open_positions():
    report = build_exposure_report(
        _two_overlapping_strategies(),
        margin_policy=MarginPolicy(
            {"MES": InstrumentMargin("MES", 500, 400), "MNQ": InstrumentMargin("MNQ", 400, 300)}
        ),
    )
    # During overlap both are open: margin 500 + 400 = 900; stop risk 1*100*5 + 1*100*2 = 700.
    assert report.peak_initial_margin == 900.0
    assert report.peak_open_stop_risk == 700.0
    assert report.per_instrument_time_in_market["ES"] == 1.0  # ES open the whole span
