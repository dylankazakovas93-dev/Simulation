from __future__ import annotations

import pandas as pd
import pytest

from sim_core.ingestion.csv_loader import normalize_trade_frame
from sim_core.live_account import (
    CashFlow,
    CashFlowPolicy,
    FixedContractSizing,
    FixedDollarRiskSizing,
    LiveAccountConfig,
    LiveAccountPathResult,
    PercentEquitySizing,
    StrategyAllocation,
    verify_live_account_result_provenance,
    run_live_account_path,
    summarize_live_account_paths,
)
from sim_core.resampling.policies import SameCalendarMonthBootstrap


def _trades(rows: list[dict]):
    if not rows:
        return []
    defaults = {
        "instrument": "ES",
        "contract_symbol": "MES",
        "dollars_per_point": 5,
        "stop_points": 100,
        "commission_round_turn": 0,
    }
    return normalize_trade_frame(
        pd.DataFrame([{**defaults, **row} for row in rows])
    )


def _allocation(strategy: str, policy):
    return {strategy: StrategyAllocation(strategy, policy)}


def _fixed_result(trades, cash_flows=None, contracts=1, starting_equity=10_000):
    return run_live_account_path(
        trades,
        config=LiveAccountConfig(starting_equity=starting_equity),
        allocations=_allocation("s", FixedContractSizing(contracts)),
        cash_flow_policy=CashFlowPolicy(cash_flows),
    )


def test_deposits_increase_equity_but_not_pnl():
    trades = _trades([])
    result = _fixed_result(
        trades,
        cash_flows=[CashFlow("2025-01-01T00:00:00Z", 5_000, "deposit")],
    )

    assert result.summary["ending_equity"] == 15_000
    assert result.summary["trading_pnl"] == 0
    assert result.summary["deposits"] == 5_000


def test_withdrawals_reduce_equity_but_not_pnl():
    result = _fixed_result(
        _trades([]),
        cash_flows=[CashFlow("2025-01-31T00:00:00Z", 2_000, "withdrawal")],
    )

    assert result.summary["ending_equity"] == 8_000
    assert result.summary["trading_pnl"] == 0
    assert result.summary["withdrawals"] == 2_000


def test_equal_timestamp_event_order_is_documented_and_applied():
    timestamp = "2025-01-02T10:00:00Z"
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-01T10:00:00Z",
                "exit_time": timestamp,
                "pnl_dollars": 100,
                "source_row_id": "exit",
            },
            {
                "strategy_id": "s",
                "entry_time": timestamp,
                "exit_time": "2025-01-03T10:00:00Z",
                "pnl_dollars": 0,
                "source_row_id": "entry",
            },
        ]
    )
    result = _fixed_result(
        trades,
        cash_flows=[
            CashFlow(timestamp, 1_000, "withdrawal"),
            CashFlow(timestamp, 2_000, "deposit"),
        ],
    )

    same_time = [event.event_type for event in result.events if event.timestamp == pd.Timestamp(timestamp)]
    assert same_time == ["deposit", "trade_exit", "withdrawal", "trade_entry"]


def test_start_of_month_deposits_affect_that_months_sizing():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-01T00:00:00Z",
                "exit_time": "2025-01-01T01:00:00Z",
                "pnl_dollars": 0,
                "source_row_id": "t1",
            }
        ]
    )
    result = run_live_account_path(
        trades,
        config=LiveAccountConfig(starting_equity=10_000),
        allocations=_allocation("s", FixedDollarRiskSizing(1_000, risk_proxy_dollars=500)),
        cash_flow_policy=CashFlowPolicy([CashFlow("2025-01-01T00:00:00Z", 5_000, "deposit")]),
    )

    assert result.sizing_decisions[0].contracts == 3


def test_end_of_month_deposits_do_not_affect_earlier_trades():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-01T00:00:00Z",
                "exit_time": "2025-01-01T01:00:00Z",
                "pnl_dollars": 0,
                "source_row_id": "t1",
            }
        ]
    )
    result = run_live_account_path(
        trades,
        config=LiveAccountConfig(starting_equity=10_000),
        allocations=_allocation("s", FixedDollarRiskSizing(1_000, risk_proxy_dollars=500)),
        cash_flow_policy=CashFlowPolicy([CashFlow("2025-01-31T23:59:00Z", 5_000, "deposit")]),
    )

    assert result.sizing_decisions[0].contracts == 2


def test_fixed_contract_sizing_remains_unchanged_by_equity():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-01T00:00:00Z",
                "exit_time": "2025-01-01T01:00:00Z",
                "pnl_dollars": 10_000,
                "source_row_id": "t1",
            },
            {
                "strategy_id": "s",
                "entry_time": "2025-01-02T00:00:00Z",
                "exit_time": "2025-01-02T01:00:00Z",
                "pnl_dollars": -5_000,
                "source_row_id": "t2",
            },
        ]
    )

    result = _fixed_result(
        trades,
        cash_flows=[CashFlow("2025-01-01T00:00:00Z", 5_000, "deposit")],
        contracts=3,
    )

    assert [decision.contracts for decision in result.sizing_decisions] == [3, 3]


def test_fixed_dollar_risk_responds_to_stop_size():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-01T00:00:00Z",
                "exit_time": "2025-01-01T01:00:00Z",
                "pnl_dollars": 0,
                "stop_points": 50,
                "source_row_id": "tight",
            },
            {
                "strategy_id": "s",
                "entry_time": "2025-01-02T00:00:00Z",
                "exit_time": "2025-01-02T01:00:00Z",
                "pnl_dollars": 0,
                "stop_points": 100,
                "source_row_id": "wide",
            },
        ]
    )

    result = run_live_account_path(
        trades,
        config=LiveAccountConfig(starting_equity=10_000),
        allocations=_allocation("s", FixedDollarRiskSizing(1_000)),
    )

    assert [decision.contracts for decision in result.sizing_decisions] == [4, 2]


def test_percentage_equity_sizing_increases_and_decreases_with_equity():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-01T00:00:00Z",
                "exit_time": "2025-01-01T01:00:00Z",
                "pnl_dollars": 10_000,
                "stop_points": None,
                "dollars_per_point": None,
                "source_row_id": "win",
            },
            {
                "strategy_id": "s",
                "entry_time": "2025-01-02T00:00:00Z",
                "exit_time": "2025-01-02T01:00:00Z",
                "pnl_dollars": -8_000,
                "stop_points": None,
                "dollars_per_point": None,
                "source_row_id": "loss",
            },
            {
                "strategy_id": "s",
                "entry_time": "2025-01-03T00:00:00Z",
                "exit_time": "2025-01-03T01:00:00Z",
                "pnl_dollars": 0,
                "stop_points": None,
                "dollars_per_point": None,
                "source_row_id": "after",
            },
        ]
    )

    result = run_live_account_path(
        trades,
        config=LiveAccountConfig(starting_equity=10_000),
        allocations=_allocation("s", PercentEquitySizing(0.10, risk_proxy_dollars=1_000)),
    )

    assert [decision.contracts for decision in result.sizing_decisions] == [1, 2, 0]


def test_nq_and_es_sizes_are_independent():
    trades = _trades(
        [
            {
                "strategy_id": "NQ_LOCKED",
                "instrument": "NQ",
                "contract_symbol": "MNQ",
                "entry_time": "2025-01-01T00:00:00Z",
                "exit_time": "2025-01-01T01:00:00Z",
                "pnl_dollars": 0,
                "stop_points": None,
                "dollars_per_point": None,
                "source_row_id": "nq",
            },
            {
                "strategy_id": "ES_EXPANDED",
                "entry_time": "2025-01-01T00:01:00Z",
                "exit_time": "2025-01-01T01:01:00Z",
                "pnl_dollars": 0,
                "stop_points": None,
                "dollars_per_point": None,
                "source_row_id": "es",
            },
        ]
    )

    result = run_live_account_path(
        trades,
        config=LiveAccountConfig(starting_equity=10_000),
        allocations={
            "NQ_LOCKED": StrategyAllocation(
                "NQ_LOCKED", FixedDollarRiskSizing(300, risk_proxy_dollars=100)
            ),
            "ES_EXPANDED": StrategyAllocation(
                "ES_EXPANDED", FixedDollarRiskSizing(225, risk_proxy_dollars=100)
            ),
        },
    )

    assert [(d.strategy_id, d.contracts) for d in result.sizing_decisions] == [
        ("NQ_LOCKED", 3),
        ("ES_EXPANDED", 2),
    ]


def test_scale_down_occurs_after_losses():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-01T00:00:00Z",
                "exit_time": "2025-01-01T01:00:00Z",
                "pnl_dollars": -3_000,
                "source_row_id": "loss",
            },
            {
                "strategy_id": "s",
                "entry_time": "2025-01-02T00:00:00Z",
                "exit_time": "2025-01-02T01:00:00Z",
                "pnl_dollars": 0,
                "source_row_id": "after",
            },
        ]
    )

    result = run_live_account_path(
        trades,
        config=LiveAccountConfig(starting_equity=10_000),
        allocations=_allocation("s", PercentEquitySizing(0.10, risk_proxy_dollars=500)),
    )

    assert [decision.contracts for decision in result.sizing_decisions] == [2, 0]
    assert result.summary["forced_size_reductions"] == 1


def test_reinvestment_percentage_is_respected():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-01T00:00:00Z",
                "exit_time": "2025-01-01T01:00:00Z",
                "pnl_dollars": 10_000,
                "stop_points": None,
                "dollars_per_point": None,
                "source_row_id": "win",
            },
            {
                "strategy_id": "s",
                "entry_time": "2025-01-02T00:00:00Z",
                "exit_time": "2025-01-02T01:00:00Z",
                "pnl_dollars": 0,
                "stop_points": None,
                "dollars_per_point": None,
                "source_row_id": "after",
            },
        ]
    )

    no_reinvest = run_live_account_path(
        trades,
        config=LiveAccountConfig(starting_equity=10_000),
        allocations=_allocation(
            "s", FixedDollarRiskSizing(1_000, risk_proxy_dollars=1_000, reinvestment_rate=0)
        ),
    )
    full_reinvest = run_live_account_path(
        trades,
        config=LiveAccountConfig(starting_equity=10_000),
        allocations=_allocation(
            "s", FixedDollarRiskSizing(1_000, risk_proxy_dollars=1_000, reinvestment_rate=1)
        ),
    )

    assert no_reinvest.sizing_decisions[1].contracts == 1
    assert full_reinvest.sizing_decisions[1].contracts == 2


def test_contract_caps_are_respected():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-01T00:00:00Z",
                "exit_time": "2025-01-01T01:00:00Z",
                "pnl_dollars": 0,
                "stop_points": None,
                "dollars_per_point": None,
                "source_row_id": "t",
            }
        ]
    )

    result = run_live_account_path(
        trades,
        config=LiveAccountConfig(starting_equity=10_000),
        allocations=_allocation("s", FixedDollarRiskSizing(10_000, risk_proxy_dollars=100, contract_cap=2)),
    )

    assert result.sizing_decisions[0].contracts == 2


def test_cash_reserve_is_respected():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-01T00:00:00Z",
                "exit_time": "2025-01-01T01:00:00Z",
                "pnl_dollars": 0,
                "stop_points": None,
                "dollars_per_point": None,
                "source_row_id": "t",
            }
        ]
    )

    result = run_live_account_path(
        trades,
        config=LiveAccountConfig(starting_equity=10_000),
        allocations=_allocation(
            "s", FixedDollarRiskSizing(1_000, risk_proxy_dollars=250, minimum_reserve=5_000)
        ),
    )

    assert result.sizing_decisions[0].contracts == 2


def test_same_seed_reproduces_account_path():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-01T00:00:00Z",
                "exit_time": "2025-01-01T01:00:00Z",
                "pnl_dollars": 100,
                "source_row_id": "jan",
            },
            {
                "strategy_id": "s",
                "entry_time": "2025-02-01T00:00:00Z",
                "exit_time": "2025-02-01T01:00:00Z",
                "pnl_dollars": -50,
                "source_row_id": "feb",
            },
        ]
    )
    policy = SameCalendarMonthBootstrap(months=2, start_month="2026-01")
    first_path = policy.sample(trades, seed=7)
    second_path = policy.sample(trades, seed=7)

    kwargs = {
        "config": LiveAccountConfig(starting_equity=10_000),
        "allocations": _allocation("s", FixedContractSizing(1)),
    }
    assert run_live_account_path(first_path, **kwargs).to_json() == run_live_account_path(second_path, **kwargs).to_json()


def test_twr_is_unaffected_by_external_cash_flow_timing_without_trading_pnl():
    early = _fixed_result(
        _trades([]),
        cash_flows=[CashFlow("2025-01-01T00:00:00Z", 5_000, "deposit")],
    )
    late = _fixed_result(
        _trades([]),
        cash_flows=[CashFlow("2025-01-31T00:00:00Z", 5_000, "deposit")],
    )

    assert early.summary["time_weighted_return"] == 0
    assert late.summary["time_weighted_return"] == 0


def test_annualized_xirr_changes_with_cash_flow_timing():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-15T00:00:00Z",
                "exit_time": "2025-01-15T01:00:00Z",
                "pnl_dollars": 1_000,
                "source_row_id": "t",
            }
        ]
    )
    early = _fixed_result(
        trades,
        cash_flows=[CashFlow("2025-01-01T00:00:00Z", 5_000, "deposit")],
    )
    late = _fixed_result(
        trades,
        cash_flows=[CashFlow("2025-01-31T00:00:00Z", 5_000, "deposit")],
    )

    assert early.summary["annualized_xirr"] != late.summary["annualized_xirr"]


def test_drawdown_uses_account_equity_and_reports_cash_flow_context():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-02T00:00:00Z",
                "exit_time": "2025-01-02T01:00:00Z",
                "pnl_dollars": -3_000,
                "source_row_id": "loss",
            }
        ]
    )
    result = _fixed_result(
        trades,
        cash_flows=[CashFlow("2025-01-01T00:00:00Z", 5_000, "deposit")],
    )

    assert result.summary["account_peak_equity"] == 15_000
    assert result.summary["account_max_drawdown_dollars"] == 3_000
    assert result.summary["account_drawdown_duration"] >= 0
    assert result.summary["flow_neutral_peak_equity"] == 10_000
    assert result.summary["trading_max_drawdown_dollars"] == 3_000
    assert result.summary["trading_drawdown_duration"] >= 0
    assert result.summary["deposits"] == 5_000
    assert result.summary["trading_pnl"] == -3_000


def test_withdrawal_moves_account_drawdown_but_not_trading_drawdown():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-02T00:00:00Z",
                "exit_time": "2025-01-02T01:00:00Z",
                "pnl_dollars": -250,
                "source_row_id": "loss",
            }
        ]
    )
    result = _fixed_result(
        trades,
        cash_flows=[CashFlow("2025-01-03T00:00:00Z", 2_000, "withdrawal")],
    )

    assert result.summary["account_max_drawdown_dollars"] == 2_250
    assert result.summary["trading_max_drawdown_dollars"] == 250


def test_operational_ruin_differs_from_zero_equity_ruin():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-02T00:00:00Z",
                "exit_time": "2025-01-02T01:00:00Z",
                "pnl_dollars": -2_000,
                "source_row_id": "loss",
            }
        ]
    )
    result = run_live_account_path(
        trades,
        config=LiveAccountConfig(starting_equity=10_000, operational_ruin_threshold=9_000),
        allocations=_allocation("s", FixedContractSizing(1)),
    )

    assert result.summary["operational_ruin"] is True
    assert result.summary["operational_ruin_hit"] is True
    assert result.summary["zero_equity_ruin"] is False


def test_mid_path_operational_ruin_breach_remains_ruined_after_recovery():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-02T00:00:00Z",
                "exit_time": "2025-01-02T01:00:00Z",
                "pnl_dollars": -2_000,
                "source_row_id": "breach",
            },
            {
                "strategy_id": "s",
                "entry_time": "2025-01-03T00:00:00Z",
                "exit_time": "2025-01-03T01:00:00Z",
                "pnl_dollars": 5_000,
                "source_row_id": "recover",
            },
        ]
    )
    result = run_live_account_path(
        trades,
        config=LiveAccountConfig(starting_equity=10_000, operational_ruin_threshold=9_000),
        allocations=_allocation("s", FixedContractSizing(1)),
    )

    assert result.summary["ending_equity"] == 13_000
    assert result.summary["operational_ruin_hit"] is True
    assert result.summary["operational_ruin_trigger_event_id"] == trades[0].trade_id
    assert result.summary["operational_ruin_policy"] == "classify_and_continue"


def test_operational_ruin_barrier_handles_below_never_and_exact_touch():
    below = run_live_account_path(
        _trades(
            [
                {
                    "strategy_id": "s",
                    "entry_time": "2025-01-02T00:00:00Z",
                    "exit_time": "2025-01-02T01:00:00Z",
                    "pnl_dollars": -1_001,
                    "source_row_id": "below",
                }
            ]
        ),
        config=LiveAccountConfig(starting_equity=10_000, operational_ruin_threshold=9_000),
        allocations=_allocation("s", FixedContractSizing(1)),
    )
    never = run_live_account_path(
        _trades(
            [
                {
                    "strategy_id": "s",
                    "entry_time": "2025-01-02T00:00:00Z",
                    "exit_time": "2025-01-02T01:00:00Z",
                    "pnl_dollars": -999,
                    "source_row_id": "never",
                }
            ]
        ),
        config=LiveAccountConfig(starting_equity=10_000, operational_ruin_threshold=9_000),
        allocations=_allocation("s", FixedContractSizing(1)),
    )
    touch = run_live_account_path(
        _trades(
            [
                {
                    "strategy_id": "s",
                    "entry_time": "2025-01-02T00:00:00Z",
                    "exit_time": "2025-01-02T01:00:00Z",
                    "pnl_dollars": -1_000,
                    "source_row_id": "touch",
                }
            ]
        ),
        config=LiveAccountConfig(starting_equity=10_000, operational_ruin_threshold=9_000),
        allocations=_allocation("s", FixedContractSizing(1)),
    )

    assert below.summary["operational_ruin_hit"] is True
    assert never.summary["operational_ruin_hit"] is False
    assert touch.summary["operational_ruin_hit"] is True
    assert touch.summary["operational_ruin_comparison"] == "<="


def test_stop_trading_after_ruin_policy_halts_later_trade_events():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-02T00:00:00Z",
                "exit_time": "2025-01-02T01:00:00Z",
                "pnl_dollars": -2_000,
                "source_row_id": "breach",
            },
            {
                "strategy_id": "s",
                "entry_time": "2025-01-03T00:00:00Z",
                "exit_time": "2025-01-03T01:00:00Z",
                "pnl_dollars": 5_000,
                "source_row_id": "skipped",
            },
        ]
    )

    result = run_live_account_path(
        trades,
        config=LiveAccountConfig(
            starting_equity=10_000,
            operational_ruin_threshold=9_000,
            operational_ruin_policy="stop_trading_after_ruin",
        ),
        allocations=_allocation("s", FixedContractSizing(1)),
    )

    assert result.summary["ending_equity"] == 8_000
    assert result.summary["operational_ruin_hit"] is True
    assert result.summary["operational_ruin_policy"] == "stop_trading_after_ruin"


def test_operational_ruin_initial_threshold_touch_is_absorbing():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-02T00:00:00Z",
                "exit_time": "2025-01-02T01:00:00Z",
                "pnl_dollars": 5_000,
                "source_row_id": "skipped",
            }
        ]
    )

    result = run_live_account_path(
        trades,
        config=LiveAccountConfig(
            starting_equity=10_000,
            operational_ruin_threshold=10_000,
            operational_ruin_policy="stop_trading_after_ruin",
        ),
        allocations=_allocation("s", FixedContractSizing(1)),
    )

    assert result.summary["ending_equity"] == 10_000
    assert result.summary["operational_ruin_hit"] is True
    assert result.summary["operational_ruin_trigger_event_id"] == "initial_equity"
    assert result.summary["operational_ruin_first_timestamp"] is None


def test_no_equity_cap_is_applied_unless_configured():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-02T00:00:00Z",
                "exit_time": "2025-01-02T01:00:00Z",
                "pnl_dollars": 100_000,
                "source_row_id": "win",
            }
        ]
    )

    assert _fixed_result(trades).summary["ending_equity"] == 110_000


def test_probability_outputs_cover_size_reduction_drawdown_and_ruin():
    loss = _fixed_result(
        _trades(
            [
                {
                    "strategy_id": "s",
                    "entry_time": "2025-01-02T00:00:00Z",
                    "exit_time": "2025-01-02T01:00:00Z",
                    "pnl_dollars": -3_000,
                    "source_row_id": "loss",
                },
                {
                    "strategy_id": "s",
                    "entry_time": "2025-01-03T00:00:00Z",
                    "exit_time": "2025-01-03T01:00:00Z",
                    "pnl_dollars": 0,
                    "source_row_id": "after",
                },
            ]
        ),
        starting_equity=10_000,
    )
    ruin = run_live_account_path(
        _trades(
            [
                {
                    "strategy_id": "s",
                    "entry_time": "2025-01-02T00:00:00Z",
                    "exit_time": "2025-01-02T01:00:00Z",
                    "pnl_dollars": -2_000,
                    "source_row_id": "op",
                }
            ]
        ),
        config=LiveAccountConfig(starting_equity=10_000, operational_ruin_threshold=9_000),
        allocations=_allocation("s", FixedContractSizing(1)),
    )

    summary = summarize_live_account_paths([loss, ruin])

    assert summary["probability_drawdown_20pct"] == 1.0
    assert summary["probability_operational_ruin"] == 0.5


def test_return_fields_distinguish_period_twr_period_mwr_and_annualized_xirr():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-01T00:00:00Z",
                "exit_time": "2025-04-01T00:00:00Z",
                "pnl_dollars": 1_000,
                "source_row_id": "quarter",
            }
        ]
    )

    result = _fixed_result(trades)

    assert result.summary["period_money_weighted_return"] == pytest.approx(
        result.summary["period_twr"]
    )
    assert result.summary["annualized_xirr_status"] == "ok"
    assert result.summary["annualized_xirr"] != pytest.approx(result.summary["period_twr"])
    assert "money_weighted_return" in result.summary  # deprecated compatibility alias only


def test_cash_flow_timing_changes_xirr_but_not_twr_without_trading_difference():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-03-01T00:00:00Z",
                "exit_time": "2025-04-01T00:00:00Z",
                "pnl_dollars": 1_000,
                "source_row_id": "trade",
            }
        ]
    )
    early = _fixed_result(
        trades,
        cash_flows=[CashFlow("2025-01-01T00:00:00Z", 5_000, "deposit")],
    )
    late = _fixed_result(
        trades,
        cash_flows=[CashFlow("2025-02-28T00:00:00Z", 5_000, "deposit")],
    )

    assert early.summary["period_twr"] == pytest.approx(late.summary["period_twr"])
    assert early.summary["annualized_xirr"] != pytest.approx(late.summary["annualized_xirr"])


def test_short_horizon_annualized_xirr_warns():
    result = _fixed_result(
        _trades(
            [
                {
                    "strategy_id": "s",
                    "entry_time": "2025-01-01T00:00:00Z",
                    "exit_time": "2025-01-01T01:00:00Z",
                    "pnl_dollars": 1_000,
                    "source_row_id": "short",
                }
            ]
        )
    )

    assert result.summary["annualization_warning"]
    assert result.summary["annualized_twr"] is None


def test_non_unique_xirr_returns_unavailable_status():
    result = _fixed_result(
        _trades(
            [
                {
                    "strategy_id": "s",
                    "entry_time": "2025-02-01T00:00:00Z",
                    "exit_time": "2025-04-01T00:00:00Z",
                    "pnl_dollars": 100,
                    "source_row_id": "trade",
                }
            ]
        ),
        cash_flows=[
            CashFlow("2025-01-15T00:00:00Z", 15_000, "withdrawal"),
            CashFlow("2025-03-01T00:00:00Z", 1_000, "deposit"),
        ],
    )

    assert result.summary["annualized_xirr_status"] == "unavailable"
    assert result.summary["annualized_xirr_unavailable_reason"] == "non_unique_xirr_sign_pattern"


def test_fixed_dollar_risk_requires_declared_stop_or_proxy():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-01T00:00:00Z",
                "exit_time": "2025-01-01T01:00:00Z",
                "pnl_dollars": 0,
                "stop_points": None,
                "dollars_per_point": None,
                "source_row_id": "t",
            }
        ]
    )

    with pytest.raises(ValueError, match="no declared per-contract risk"):
        run_live_account_path(
            trades,
            config=LiveAccountConfig(starting_equity=10_000),
            allocations=_allocation("s", FixedDollarRiskSizing(1_000)),
        )


def test_json_serialization_round_trips_for_v2_models():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-01T00:00:00Z",
                "exit_time": "2025-01-01T01:00:00Z",
                "pnl_dollars": 100,
                "source_row_id": "t",
            }
        ]
    )
    result = run_live_account_path(
        trades,
        config=LiveAccountConfig(starting_equity=10_000),
        allocations=_allocation(
            "s", FixedDollarRiskSizing(1_000, risk_proxy_dollars=500, reinvestment_rate=0.5)
        ),
        cash_flow_policy=CashFlowPolicy([CashFlow("2025-01-01T00:00:00Z", 1_000, "deposit")]),
    )

    restored = LiveAccountPathResult.from_json(result.to_json())

    assert restored.config == result.config
    assert restored.allocations["s"].sizing_policy == result.allocations["s"].sizing_policy
    assert restored.summary == result.summary
    assert restored.provenance == result.provenance


def test_live_account_provenance_verifies_and_detects_mismatches():
    trades = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-01T00:00:00Z",
                "exit_time": "2025-01-01T01:00:00Z",
                "pnl_dollars": 100,
                "source_row_id": "t",
            }
        ]
    )
    config = LiveAccountConfig(starting_equity=10_000, scenario_id="prov", master_seed=7, path_index=2)
    cash_flows = CashFlowPolicy([CashFlow("2025-01-01T00:00:00Z", 1_000, "deposit")])
    allocations = _allocation(
        "s", FixedDollarRiskSizing(1_000, risk_proxy_dollars=500, reinvestment_rate=0.5)
    )
    result = run_live_account_path(
        trades,
        config=config,
        allocations=allocations,
        cash_flow_policy=cash_flows,
    )

    assert verify_live_account_result_provenance(
        result, trades, config, cash_flows, allocations
    ).ok

    changed_trade = _trades(
        [
            {
                "strategy_id": "s",
                "entry_time": "2025-01-01T00:00:00Z",
                "exit_time": "2025-01-01T01:00:00Z",
                "pnl_dollars": 101,
                "source_row_id": "t",
            }
        ]
    )
    changed_deposit = CashFlowPolicy([CashFlow("2025-01-01T00:00:00Z", 2_000, "deposit")])
    changed_sizing = _allocation(
        "s", FixedDollarRiskSizing(1_000, risk_proxy_dollars=500, reinvestment_rate=1.0)
    )
    changed_config = LiveAccountConfig(starting_equity=10_000, operational_ruin_threshold=1)
    changed_contract = _trades(
        [
            {
                "strategy_id": "s",
                "instrument": "NQ",
                "contract_symbol": "MNQ",
                "entry_time": "2025-01-01T00:00:00Z",
                "exit_time": "2025-01-01T01:00:00Z",
                "pnl_dollars": 100,
                "dollars_per_point": 2,
                "source_row_id": "t",
            }
        ]
    )
    mutated_payload = result.to_dict()
    mutated_payload["summary"]["ending_equity"] += 1
    mutated_result = LiveAccountPathResult.from_dict(mutated_payload)

    assert not verify_live_account_result_provenance(
        result, changed_trade, config, cash_flows, allocations
    ).checks["trade_input_hash"]
    assert not verify_live_account_result_provenance(
        result, trades, config, changed_deposit, allocations
    ).checks["cash_flow_schedule_hash"]
    assert not verify_live_account_result_provenance(
        result, trades, config, cash_flows, changed_sizing
    ).checks["sizing_policy_hash"]
    assert not verify_live_account_result_provenance(
        result, trades, config, cash_flows, changed_sizing
    ).checks["reinvestment_configuration_hash"]
    assert not verify_live_account_result_provenance(
        result, trades, changed_config, cash_flows, allocations
    ).checks["live_account_config_hash"]
    assert not verify_live_account_result_provenance(
        result, trades, changed_config, cash_flows, allocations
    ).checks["ruin_configuration_hash"]
    assert not verify_live_account_result_provenance(
        result, changed_contract, config, cash_flows, allocations
    ).checks["contract_specification_hash"]
    assert not verify_live_account_result_provenance(
        mutated_result, trades, config, cash_flows, allocations
    ).checks["result_hash"]
