# Verified Prop Lab replacement core

This package is the independently tested replacement for the current `Simulation` repository lifecycle engine.

## What it corrects

- authoritative `session_date` rather than inferring the futures trading day from exit timestamps;
- `cap` is preserved as stop distance;
- DPP, commission, and slippage are explicit;
- exact floor touches (`<=`) fail;
- no payout can occur after account failure;
- evaluation pass and replacement accounts begin on the next session, not mid-session;
- trade-by-trade balance/floor/audit records;
- ordered peak-to-later-trough drawdown, not global max-minus-global-min;
- drawdown-period start, trough, recovery, depth, and duration;
- profit splits and repeated payout cycles;
- same-calendar-month block resampling;
- independent deterministic RNG streams;
- identical sampled paths reused across plans and contract quantities;
- explicit variance and standard deviation in ensemble summaries.

## Critical data limitation

The committed `Volatility-hodlod` strategy ledgers include `session_date`, `cap`, and realized point P&L, but not MAE/MFE. Therefore:

- realized P&L, commissions, balances, and realized-equity drawdowns can be exact;
- intratrade prop-account barrier touches cannot be exact from those ledgers alone;
- strict mode fails closed when MAE is missing;
- `realized_only` mode is available only as an explicitly optimistic lower-bound failure estimate.

Before probability conclusions, regenerate the strategy ledgers with per-trade MAE/MFE from the one-minute engine.

## Verification

Run:

```bash
python3 -m pytest -q
```

Expected result for this package:

```text
19 passed
```
