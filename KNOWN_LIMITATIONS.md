# Known Limitations

- No Streamlit UI yet.
- No deposits, withdrawals, reinvestment, fixed-dollar risk, percentage-equity risk, or forced size-down.
- No margin restrictions or exposure limits.
- No prop-firm account rules, payout rules, resets, or account failure state machine.
- No optimizer.
- No explicit stress engine for win rate, winner size, loss size, slippage, missed trades, or tail-loss injection.
- No open-position mark-to-market. Equity changes only when trades exit.
- Month bootstrap shifts timestamps by month-start offset; shifted month-end trades can land in the following target month for shorter months.
- Source-month selection currently uses the union of months across all strategies, so sparse strategies simply contribute no trades for months where they had none.
- Duplicate detection uses strategy, instrument, entry, exit, and PnL. It does not yet include prices, session, or custom identifiers unless `trade_id` is present.
- CSV export helpers exist, but no config-driven scenario runner is implemented yet.
