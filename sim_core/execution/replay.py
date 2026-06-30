from __future__ import annotations

from collections.abc import Sequence

from sim_core.ingestion.csv_loader import sort_trades_chronologically
from sim_core.models import (
    AccountConfig,
    EquityPoint,
    FixedContractPortfolio,
    ResampledPath,
    SimulationResult,
    Trade,
)


def run_fixed_contract_simulation(
    trades: Sequence[Trade] | ResampledPath,
    *,
    account: AccountConfig | None = None,
    portfolio: FixedContractPortfolio | None = None,
) -> SimulationResult:
    """Replay realized trade PnL with fixed contract quantities."""

    account = account or AccountConfig()
    portfolio = portfolio or FixedContractPortfolio()
    if isinstance(trades, ResampledPath):
        trade_list = trades.trades
        sampled_blocks = trades.sampled_blocks
    else:
        trade_list = list(trades)
        sampled_blocks = []

    equity = account.initial_equity
    equity_path: list[EquityPoint] = []
    for trade in _sort_by_realization_time(trade_list):
        contracts = portfolio.contracts_for(trade)
        gross_pnl = trade.pnl_dollars * contracts
        commission = trade.commission_round_turn * contracts
        net_pnl = gross_pnl - commission
        equity += net_pnl
        equity_path.append(
            EquityPoint(
                timestamp=trade.exit_time,
                equity=equity,
                trade_id=trade.trade_id,
                strategy_id=trade.strategy_id,
                instrument=trade.instrument,
                contracts=contracts,
                gross_pnl=gross_pnl,
                commission=commission,
                net_pnl=net_pnl,
            )
        )

    return SimulationResult(
        account=account,
        portfolio=portfolio,
        trades=sort_trades_chronologically(trade_list),
        equity_path=equity_path,
        sampled_blocks=sampled_blocks,
    )


def _sort_by_realization_time(trades: Sequence[Trade]) -> list[Trade]:
    return sorted(trades, key=lambda trade: (trade.exit_time, trade.entry_time, trade.trade_id))
