# Project Status

Last updated: 2026-06-30

Branch: `codex/v2-live-account`

Base: approved V1 head `8a81536e6335b5b4250b3ce9658fef3fe51af561`

Claude final V1 approval: Review 005 at `d196ed1`

## Current State

Version 2 first milestone is implemented as a narrow additive live-account
layer. No prop-firm rules, optimizer, Streamlit UI, or full margin/exposure
modeling were added.

V1 behavior is preserved: the existing path generator still produces ordered
per-contract `Trade` events, and live-account logic is isolated in
`sim_core/live_account.py`.

## V2 Milestone Implemented

- Starting equity through `LiveAccountConfig`.
- External deposits and withdrawals through `CashFlow` / `CashFlowPolicy`.
- Deterministic equal-timestamp priority:
  deposits, trade exits, withdrawals, trade entries / next sizing decisions.
- Fixed-contract sizing.
- Fixed-dollar risk sizing.
- Percentage-of-equity risk sizing.
- Independent `StrategyAllocation` per strategy.
- Reinvestment rate, immediate scale-down, contract cap, and minimum reserve.
- Monthly account reports.
- Time-weighted return and money-weighted/XIRR-style return.
- Trading P&L, deposits, withdrawals, net external contributions, ending equity,
  simple return on contributions, and trading return before cash flows reported
  separately.
- Drawdown depth, drawdown percent, drawdown duration, recovery duration,
  configured drawdown-threshold flags, forced size reductions, minimum contract
  size reached, and operational ruin distinct from zero-equity ruin.
- JSON serialization round-trip for V2 configuration/result models.

## Files Changed

- `sim_core/live_account.py`
- `sim_core/__init__.py`
- `tests/test_live_account.py`
- `ARCHITECTURE.md`
- `DECISIONS.md`
- `HANDOFF.md`
- `KNOWN_LIMITATIONS.md`
- `PROJECT_STATUS.md`

## Tests Added

`tests/test_live_account.py` adds 22 V2 tests covering:

- deposits and withdrawals as non-P&L events
- equal-timestamp cash-flow / trade-event ordering
- start-of-month versus end-of-month cash-flow timing
- fixed-contract sizing
- fixed-dollar risk sizing and stop-risk precedence
- percentage-equity size-up and size-down
- independent NQ and ES sizing
- scale-down after losses and forced reduction counts
- reinvestment percentage
- contract caps and cash reserve
- deterministic account paths from same sampled path seed
- TWR and MWR behavior
- drawdown context with cash flows
- operational ruin versus zero-equity ruin
- no equity cap unless configured
- path-level probability outputs
- JSON round-trip

## Test Results

Claude regression suite:

```bash
python3 -m pytest tests/regression -q
```

Result:

```text
22 passed
```

V1 + V2 full suite:

```bash
python3 -m pytest
```

Result:

```text
112 passed, 1 skipped, 73 warnings
```

V1 + V2 full suite with real ledger enabled:

```bash
SIM_REAL_LEDGER_PATH=/Users/mariusvidziunas/Downloads/nq_es_margin_sim_master_2025_2026.csv \
SIM_REAL_LEDGER_MAPPING=configs/nq_es_micro_contracts.yaml \
python3 -m pytest
```

Result:

```text
113 passed, 74 warnings
```

## Example Output

Shared example assumptions:

- Starting equity: USD 10,000
- Three MES trades
- Per-contract stop risk: 100 points x USD 5 = USD 500
- Trade P&L per contract: +5,000, -250, +750

```text
10000_start_no_deposits_fixed_contracts:
  ending_equity: 15500.0
  trading_pnl: 5500.0
  deposits: 0.0
  withdrawals: 0.0
  time_weighted_return: 0.55
  money_weighted_return: 14.061434
  max_drawdown: 250.0
  forced_size_reductions: 0

10000_start_5000_start_each_month_fixed_contracts:
  ending_equity: 30500.0
  trading_pnl: 5500.0
  deposits: 15000.0
  withdrawals: 0.0
  time_weighted_return: 0.353277
  money_weighted_return: 3.721965
  max_drawdown: 250.0
  forced_size_reductions: 0

fixed_dollar_risk_no_reinvestment:
  ending_equity: 21000.0
  trading_pnl: 11000.0
  deposits: 0.0
  withdrawals: 0.0
  time_weighted_return: 1.1
  money_weighted_return: 97.639707
  max_drawdown: 500.0
  forced_size_reductions: 0

fixed_dollar_risk_100pct_reinvestment:
  ending_equity: 21250.0
  trading_pnl: 11250.0
  deposits: 0.0
  withdrawals: 0.0
  time_weighted_return: 1.125
  money_weighted_return: 105.134944
  max_drawdown: 1000.0
  forced_size_reductions: 1
```

Money-weighted returns are annualized XIRR-style values, so short high-return
examples can produce large annualized numbers.

## Remaining Limitations

- No prop-firm rules.
- No optimizer.
- No Streamlit/UI.
- No full margin/exposure model.
- No shared portfolio-level constraints yet.
- No intratrade mark-to-market; drawdown is based on realized account events.
- Operational ruin is a configured account-equity threshold, not a broker or
  prop-firm liquidation model.

## Recommendation

Stop here for the first V2 milestone and send this branch to Claude for Review
006. The implementation is intentionally narrow and audit-ready.
