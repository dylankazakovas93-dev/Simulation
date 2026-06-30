from __future__ import annotations

import pandas as pd

from sim_core.execution.replay import run_fixed_contract_simulation
from sim_core.ingestion.csv_loader import normalize_trade_frame
from sim_core.metrics.reports import max_drawdown, monthly_equity_percentiles, ruin_probability
from sim_core.models import AccountConfig, FixedContractPortfolio


def _trades():
    return normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s1",
                    "instrument": "MES",
                    "entry_time": "2024-01-02T09:30:00Z",
                    "exit_time": "2024-01-02T10:00:00Z",
                    "pnl_dollars": 100,
                    "commission_round_turn": 2,
                },
                {
                    "strategy_id": "s2",
                    "instrument": "MNQ",
                    "entry_time": "2024-01-03T09:30:00Z",
                    "exit_time": "2024-01-03T10:00:00Z",
                    "pnl_dollars": -50,
                    "commission_round_turn": 1,
                },
                {
                    "strategy_id": "s1",
                    "instrument": "MES",
                    "entry_time": "2024-02-03T09:30:00Z",
                    "exit_time": "2024-02-03T10:00:00Z",
                    "pnl_dollars": 25,
                    "commission_round_turn": 2,
                },
            ]
        )
    )


def test_fixed_size_replay_matches_original_ledger_net_of_commissions():
    result = run_fixed_contract_simulation(
        _trades(),
        account=AccountConfig(initial_equity=10_000),
        portfolio=FixedContractPortfolio(strategy_contracts={"s1": 2, "s2": 1}),
    )

    assert [point.net_pnl for point in result.equity_path] == [196, -51, 46]
    assert result.terminal_equity == 10_191


def test_mes_sizing_is_not_hard_coded_as_equal_to_mnq_sizing():
    result = run_fixed_contract_simulation(
        _trades(),
        account=AccountConfig(initial_equity=10_000),
        portfolio=FixedContractPortfolio(
            instrument_contracts={("s1", "MES"): 3, ("s2", "MNQ"): 1}
        ),
    )

    assert [point.contracts for point in result.equity_path] == [3, 1, 3]


def test_metrics_report_drawdown_ruin_and_monthly_percentiles():
    result = run_fixed_contract_simulation(
        _trades(),
        account=AccountConfig(initial_equity=10_000, ruin_threshold=10_160),
        portfolio=FixedContractPortfolio(strategy_contracts={"s1": 2, "s2": 1}),
    )
    other = run_fixed_contract_simulation(
        _trades(),
        account=AccountConfig(initial_equity=10_000),
        portfolio=FixedContractPortfolio(default_contracts=1),
    )

    assert max_drawdown(result)["max_drawdown"] == 51
    assert ruin_probability([result]) == 1.0
    percentiles = monthly_equity_percentiles([result, other], percentiles=(50,))
    assert list(percentiles.columns) == ["month", "p50"]
    assert set(percentiles["month"]) == {"2024-01", "2024-02"}
