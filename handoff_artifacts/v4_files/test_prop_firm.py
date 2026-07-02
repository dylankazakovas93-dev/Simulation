"""V4 — prop-firm engine tests: state machine, breach rules, payout cash economics."""
from __future__ import annotations

import pandas as pd
import pytest

from sim_core.prop_firm import (
    PHASE_FAILED,
    PHASE_FUNDED,
    PHASE_RETIRED,
    PropFirmRules,
    run_prop_account_path,
    run_prop_account_portfolio,
    summarize_prop_accounts,
)
from sim_core.models import Trade


def _trade(rid, pnl, entry, exit_, strategy="s", symbol="MES", dpp=5.0):
    return Trade(
        trade_id=f"{strategy}-{rid}",
        source_row_id=str(rid),
        strategy_id=strategy,
        instrument="ES",
        contract_symbol=symbol,
        entry_time=pd.Timestamp(entry),
        exit_time=pd.Timestamp(exit_),
        pnl_dollars=pnl,
        dollars_per_point=dpp,
    )


def _day(n, pnl):
    """One trade on 2025-01-0n, entering 09:30 exiting 10:00 UTC."""
    d = f"2025-01-{n:02d}"
    return _trade(f"d{n}", pnl, f"{d}T09:30:00Z", f"{d}T10:00:00Z")


# --- evaluation ------------------------------------------------------------------
def test_evaluation_passes_and_activates_on_target_and_min_days():
    rules = PropFirmRules(
        account_size=50_000,
        profit_target=2_000,
        trailing_drawdown=2_500,
        min_trading_days=2,
        evaluation_fee=150,
        activation_fee=0,
    )
    # +1000 each on two distinct days -> hits 2000 target on day 2.
    result = run_prop_account_path([_day(1, 1_000), _day(2, 1_000)], rules)
    assert result.summary["reached_funded"] is True
    assert result.terminal_phase == PHASE_FUNDED
    # funded account starts fresh at account_size after activation
    assert result.terminal_balance == 50_000
    assert result.summary["eval_trading_days"] == 2


def test_evaluation_not_passed_before_min_trading_days_met():
    rules = PropFirmRules(
        account_size=50_000, profit_target=2_000, trailing_drawdown=2_500, min_trading_days=3
    )
    # Target reached in one day, but 3 distinct days required.
    result = run_prop_account_path([_day(1, 2_500)], rules)
    assert result.summary["reached_funded"] is False
    assert result.terminal_phase == "evaluation"


# --- breach: trailing drawdown ---------------------------------------------------
def test_trailing_drawdown_breach_without_reset_is_dead():
    rules = PropFirmRules(
        account_size=50_000, profit_target=5_000, trailing_drawdown=2_000, reset_fee=None
    )
    # Peak at +1000 (51000) -> floor 49000; then -1500 to 49500... not yet.
    # Drop to 48900 (< 49000) breaches.
    result = run_prop_account_path(
        [_day(1, 1_000), _day(2, -2_100)], rules
    )
    assert result.terminal_phase == PHASE_FAILED
    assert result.summary["survived"] is False
    assert result.summary["net_trader_cash"] == 0.0  # only the eval fee is a cost
    assert result.summary["resets_used"] == 0


def test_trailing_lock_at_start_floor_stops_rising():
    # Lock at account_size: once profit >= drawdown, floor stays at start.
    rules = PropFirmRules(
        account_size=50_000,
        profit_target=100_000,  # never passes; we only test the floor
        trailing_drawdown=2_000,
        trailing_lock_at=50_000,
        reset_fee=None,
    )
    # Climb to +5000 (peak 55000). Unlocked floor would be 53000; locked floor=50000.
    # Drop back to 50100 (> 50000) must NOT breach.
    result = run_prop_account_path(
        [_day(1, 5_000), _day(2, -4_900)], rules
    )
    assert result.terminal_phase == "evaluation"  # survived, still trading eval
    assert result.summary["survived"] is False  # not funded, but not dead
    assert result.terminal_phase != PHASE_FAILED


def test_evaluation_reset_costs_a_fee_and_restarts():
    rules = PropFirmRules(
        account_size=50_000,
        profit_target=5_000,
        trailing_drawdown=2_000,
        reset_fee=100,
    )
    result = run_prop_account_path([_day(1, -2_100)], rules)
    # Immediate breach, but reset allowed -> back to evaluation, fee charged.
    assert result.summary["resets_used"] == 1
    assert result.terminal_phase == "evaluation"
    # fees: eval (0 default) + reset 100
    assert result.summary["fees_paid"] == 100.0


# --- breach: daily loss ----------------------------------------------------------
def test_daily_loss_limit_breach():
    rules = PropFirmRules(
        account_size=50_000,
        profit_target=5_000,
        trailing_drawdown=10_000,  # loose, so daily-loss is the binding rule
        daily_loss_limit=1_000,
        reset_fee=None,
    )
    # Two trades same day summing to -1200 -> daily loss breach.
    trades = [
        _trade("a", -400, "2025-01-02T09:30:00Z", "2025-01-02T10:00:00Z"),
        _trade("b", -800, "2025-01-02T10:30:00Z", "2025-01-02T11:00:00Z"),
    ]
    result = run_prop_account_path(trades, rules)
    assert result.terminal_phase == PHASE_FAILED
    assert any("daily loss" in e.detail for e in result.events)


# --- payout cash economics -------------------------------------------------------
def test_funded_payout_split_and_net_cash():
    rules = PropFirmRules(
        account_size=50_000,
        profit_target=1_000,
        trailing_drawdown=2_000,
        min_trading_days=1,
        evaluation_fee=150,
        activation_fee=100,
        profit_split=0.9,
        first_payout_threshold=1_000,
        max_payouts=1,
    )
    # Day 1: pass eval (+1000). Day 2: funded +1500 -> eligible, withdraw down to start.
    result = run_prop_account_path([_day(1, 1_000), _day(2, 1_500)], rules)
    assert result.summary["reached_funded"] is True
    assert result.summary["payouts_count"] == 1
    # gross withdrawn = 1500 (down to account_size); trader keeps 90% = 1350.
    assert result.summary["gross_payouts"] == 1_500.0
    assert result.summary["trader_payouts"] == pytest.approx(1_350.0)
    # net cash = 1350 - (150 eval + 100 activation) = 1100
    assert result.summary["net_trader_cash"] == pytest.approx(1_100.0)
    assert result.terminal_phase == PHASE_RETIRED  # max_payouts=1 reached
    assert result.summary["time_to_first_payout_days"] is not None


def test_payout_cap_limits_withdrawal():
    rules = PropFirmRules(
        account_size=50_000,
        profit_target=1_000,
        trailing_drawdown=5_000,
        min_trading_days=1,
        profit_split=1.0,
        first_payout_threshold=0,
        payout_cap=800,
    )
    result = run_prop_account_path([_day(1, 1_000), _day(2, 2_000)], rules)
    # Funded profit 2000, but cap 800 per payout.
    assert result.payouts[0].gross_amount == 800.0
    assert result.summary["trader_payouts"] == pytest.approx(800.0)


# --- multi-account + aggregation -------------------------------------------------
def test_portfolio_sums_net_cash_across_copied_accounts():
    rules = PropFirmRules(
        account_size=50_000,
        profit_target=1_000,
        trailing_drawdown=2_000,
        min_trading_days=1,
        activation_fee=100,
        profit_split=0.9,
        first_payout_threshold=1_000,
        max_payouts=1,
    )
    trades = [_day(1, 1_000), _day(2, 1_500)]
    portfolio = run_prop_account_portfolio(trades, [rules, rules])
    assert portfolio["num_accounts"] == 2
    assert portfolio["num_with_payout"] == 2
    # each nets 1350 - 100 = 1250; combined 2500
    assert portfolio["combined_net_trader_cash"] == pytest.approx(2_500.0)


def test_summarize_prop_accounts_probabilities():
    passing = PropFirmRules(
        account_size=50_000,
        profit_target=1_000,
        trailing_drawdown=2_000,
        min_trading_days=1,
        first_payout_threshold=1_000,
        profit_split=1.0,
        max_payouts=1,
    )
    failing = PropFirmRules(
        account_size=50_000, profit_target=1_000, trailing_drawdown=500, reset_fee=None
    )
    r_pass = run_prop_account_path([_day(1, 1_000), _day(2, 1_500)], passing)
    r_fail = run_prop_account_path([_day(1, -600)], failing)
    agg = summarize_prop_accounts([r_pass, r_fail])
    assert agg["num_accounts"] == 2
    assert agg["prob_first_payout"] == 0.5
    assert agg["prob_failed"] == 0.5
    assert agg["prob_reached_funded"] == 0.5
    # expected net cash = (1500 + (-0)) / 2 for the passing; failing net = 0 (no fee set)
    assert agg["expected_net_trader_cash"] == pytest.approx((1_500.0 + 0.0) / 2)


def test_provenance_and_notional_disclosure_present():
    rules = PropFirmRules(account_size=50_000, profit_target=1_000, trailing_drawdown=2_000)
    result = run_prop_account_path([_day(1, 500)], rules)
    assert len(result.input_data_hash) == 64  # sha256 hex
    assert "config_hash" in result.summary
    assert "personal wealth" in result.summary["notional_balance_note"]
    assert "lower bound" in result.summary["realized_only_note"]
