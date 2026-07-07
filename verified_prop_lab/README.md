# Verified Prop Lab replacement core

This package is the independently tested replacement for the current `Simulation` repository lifecycle engine.

## What it corrects

- authoritative `session_date` rather than inferring the futures trading day from exit timestamps;
- `cap` is preserved as effective stop distance for legacy ledgers;
- DPP, commission, and slippage are explicit;
- exact floor touches (`<=`) fail;
- no payout can occur after account failure;
- evaluation pass and replacement accounts begin on the next session, not mid-session;
- trade-by-trade balance/floor/audit records;
- ordered peak-to-later-trough drawdown, not global max-minus-global-min;
- drawdown-period start, trough, recovery, depth, and duration;
- profit splits and repeated payout cycles;
- same-calendar-month block resampling for legacy diagnostics;
- rolling near-term forward volume simulation for active funded-account decisions;
- independent deterministic RNG streams;
- identical sampled paths reused across contract quantities and point-volatility scenarios;
- explicit variance, standard deviation, and first-passage outcome summaries.

## Rolling forward simulator

`ForwardVolumeScenarioSampler` builds 1-2 month forward paths from the uploaded trade ledger. The user supplies:

- forecast start and end dates;
- exact expected trade count per forecast calendar month;
- MNQ contract sizes, intentionally capped at 1-4 in the UI;
- point-volatility scenarios such as `-20%`, `Base`, and `+20%`;
- current funded account state.

The sampler draws whole historical trade shapes with replacement, assigns them to random valid weekday sessions inside the forecast window, and keeps at most one synthetic trade per session. The same sampled source rows, order, dates, and path IDs are reused across every MNQ size and point-volatility scenario so comparisons are paired rather than seed-noisy.

Point-volatility scenarios are not edge-degradation scenarios. They scale the raw stop geometry first:

```text
scaled_raw_stop = raw_stop_points * point_scale
scaled_stop = min(scaled_raw_stop, 200)
scaled_pnl = pnl_R * scaled_stop
scaled_mae = mae_R * scaled_stop
scaled_mfe = mfe_R * scaled_stop
```

This preserves the joint trade shape while testing whether the same strategy behaves differently if the near-term point environment is smaller or larger.

## Required forward ledger schema

Strict forward mode requires:

- `trade_id`
- `session_date`
- `entry_time`
- `exit_time`
- `raw_stop_points`
- `stop_points`
- `pnl_points`
- `mae_points`
- `mfe_points`

Optional columns:

- `strategy_id`
- `sample_weight`
- `result_type`
- `dollars_per_point`
- `commission_round_turn`
- `slippage_points_round_turn`

Accepted raw stop aliases include `raw_cap_points`, `uncapped_stop_points`, `uncapped_cap_points`, and `planned_raw_stop_points`. The loader validates that `stop_points == min(raw_stop_points, 200)`, MAE/MFE are non-negative, and sample weights are positive.

## Decision outputs

The Streamlit app surfaces transparent labels rather than one opaque best score:

- **Survival**: highest payout-before-failure rate;
- **Fastest**: lowest conditional median payout day;
- **Maximum EV**: highest average net cash across all paths;
- **Convex**: EV uplift versus the next smaller size after extra failure risk;
- **Pareto frontier**: not dominated on payout probability, EV, and failure risk.

Core output rates are mutually exclusive:

- `payout_before_failure_rate`
- `failure_before_payout_rate`
- `unresolved_rate`

Secondary metrics include five qualifying days before failure, payout eligibility before failure, any payout, net cash percentiles, payout cash, payout count, drawdown, ending cushion, and first-passage rates by day threshold.

## Critical data limitation

Legacy `Volatility-hodlod` ledgers that only include `session_date`, `cap`, and realized point PnL can still run legacy realized-only diagnostics, but they cannot support exact forward barrier simulation. Exact funded-account probabilities require per-trade raw stop, capped stop, MAE, and MFE from the one-minute engine.

## Verification

Run:

```bash
PYTHONPATH=verified_prop_lab python3 -m pytest -q verified_prop_lab/test_verified_prop_lab.py
python3 -m py_compile verified_prop_lab/verified_prop_lab.py
python3 -m py_compile verified_prop_lab/streamlit_verified_app.py
```
