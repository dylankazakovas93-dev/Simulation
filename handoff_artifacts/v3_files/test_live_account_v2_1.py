"""Review 007 V2.1 correction-pass regression tests.

Covers the four findings: (A) flow-neutral trading drawdown, (B) absorbing
barrier operational ruin, (C) period MWR vs annualized XIRR, (D) provenance.
Authored by the review lead while Codex was unavailable.
"""
from __future__ import annotations

import pandas as pd

from sim_core.live_account import (
    CashFlow,
    CashFlowPolicy,
    FixedContractSizing,
    LiveAccountConfig,
    StrategyAllocation,
    run_live_account_path,
)
from sim_core.models import Trade


def _trade(rid: str, entry: str, exit_: str, pnl: float, stop: float = 100.0) -> Trade:
    return Trade(
        trade_id=f"s-{rid}",
        source_row_id=rid,
        strategy_id="s",
        instrument="ES",
        contract_symbol="MES",
        entry_time=pd.Timestamp(entry),
        exit_time=pd.Timestamp(exit_),
        pnl_dollars=pnl,
        stop_points=stop,
        dollars_per_point=5.0,
    )


def _alloc():
    return {"s": StrategyAllocation("s", FixedContractSizing(1))}


def _run(trades, *, config=None, cash_flows=None):
    return run_live_account_path(
        trades,
        config=config or LiveAccountConfig(starting_equity=10_000),
        allocations=_alloc(),
        cash_flow_policy=CashFlowPolicy(cash_flows or []),
    )


# --- Finding A: flow-neutral trading drawdown ------------------------------------
def test_trading_drawdown_excludes_external_flows():
    result = _run(
        [_trade("loss250", "2025-02-03T09:30:00Z", "2025-02-03T10:00:00Z", -250)],
        cash_flows=[CashFlow("2025-02-04T00:00:00Z", 2_000, "withdrawal")],
    )
    s = result.summary
    assert s["max_drawdown"] == 2_250.0  # account-equity drawdown includes the withdrawal
    assert s["trading_max_drawdown"] == 250.0  # trading drawdown excludes it


def test_withdrawal_alone_does_not_trip_trading_drawdown_threshold():
    result = _run(
        [_trade("loss250", "2025-02-03T09:30:00Z", "2025-02-03T10:00:00Z", -250)],
        cash_flows=[CashFlow("2025-02-04T00:00:00Z", 2_000, "withdrawal")],
    )
    s = result.summary
    assert s["drawdown_thresholds_reached"]["0.2"] is True  # 22.5% account drawdown
    assert s["trading_drawdown_thresholds_reached"]["0.2"] is False  # 2.5% trading drawdown


# --- Finding B: absorbing barrier operational ruin -------------------------------
def test_operational_ruin_is_absorbing_barrier():
    result = _run(
        [
            _trade("breach", "2025-03-01T09:30:00Z", "2025-03-01T10:00:00Z", -2_000),
            _trade("recovery", "2025-03-02T09:30:00Z", "2025-03-02T10:00:00Z", 5_000),
        ],
        config=LiveAccountConfig(starting_equity=10_000, operational_ruin_threshold=9_000),
    )
    s = result.summary
    assert s["ending_equity"] == 13_000.0  # recovered above the threshold
    assert s["operational_ruin"] is True  # but the barrier was touched
    assert s["operational_ruin_first_timestamp"] == "2025-03-01T10:00:00+00:00"
    assert s["operational_ruin_trigger_event_id"] == "s-breach"
    assert s["operational_ruin_min_equity"] == 8_000.0


# --- Finding C: period MWR vs annualized XIRR ------------------------------------
def test_no_flow_period_mwr_equals_twr():
    result = _run([_trade("g", "2025-01-01T00:00:00Z", "2025-04-01T00:00:00Z", 1_000)])
    s = result.summary
    assert abs(s["period_money_weighted_return"] - s["time_weighted_return"]) < 1e-9
    assert abs(s["money_weighted_return"] - s["time_weighted_return"]) < 1e-9
    assert s["annualized_xirr"] > s["period_money_weighted_return"]  # annualized is separate/larger
    assert s["annualization_warning"] is None  # 90-day horizon


def test_annualized_xirr_warns_on_short_horizon():
    result = _run([_trade("g", "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z", 10)])
    s = result.summary
    assert abs(s["period_money_weighted_return"] - s["time_weighted_return"]) < 1e-9
    assert s["measurement_period_days"] == 1.0
    assert s["annualization_warning"] is not None
    assert "short" in s["annualization_warning"]


# --- Finding D: provenance -------------------------------------------------------
def test_live_account_result_carries_provenance_hashes():
    base = _run([_trade("t", "2025-01-01T00:00:00Z", "2025-01-01T01:00:00Z", 100)])
    assert len(base.summary["input_data_hash"]) == 64
    assert len(base.summary["config_hash"]) == 64

    changed_data = _run([_trade("t", "2025-01-01T00:00:00Z", "2025-01-01T01:00:00Z", 200)])
    assert changed_data.summary["input_data_hash"] != base.summary["input_data_hash"]

    changed_config = run_live_account_path(
        [_trade("t", "2025-01-01T00:00:00Z", "2025-01-01T01:00:00Z", 100)],
        config=LiveAccountConfig(starting_equity=20_000),
        allocations=_alloc(),
    )
    assert changed_config.summary["config_hash"] != base.summary["config_hash"]
