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


def test_mwr_changes_with_cash_flow_timing():
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

    assert early.summary["money_weighted_return"] != late.summary["money_weighted_return"]


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

    assert result.summary["peak_equity"] == 15_000
    assert result.summary["max_drawdown"] == 3_000
    assert result.summary["deposits"] == 5_000
    assert result.summary["trading_pnl"] == -3_000


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
    assert result.summary["zero_equity_ruin"] is False


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
