# V2 Metrics

Version 2 live-account metrics are serialized on `LiveAccountPathResult.summary`.

## Drawdown Families

Account-equity drawdown is computed from actual account equity after deposits,
withdrawals, and realized trade P&L. It is intended for account-statement and
liquidity reconciliation.

Canonical fields:

- `account_peak_equity`
- `account_current_drawdown_dollars`
- `account_max_drawdown_dollars`
- `account_max_drawdown_percent`
- `account_drawdown_duration`

Flow-neutral trading drawdown is computed from:

```text
starting_equity + cumulative_trading_pnl
```

External deposits and withdrawals do not move this curve. This is the default
risk drawdown family for diagnostics and future margin/exposure logic.

Canonical fields:

- `flow_neutral_peak_equity`
- `current_trading_drawdown_dollars`
- `trading_current_drawdown_dollars`
- `trading_max_drawdown_dollars`
- `trading_max_drawdown_percent`
- `trading_drawdown_duration`
- `trading_recovery_duration`
- `trading_drawdown_thresholds_reached`

The legacy aliases `max_drawdown`, `max_drawdown_pct`, and
`drawdown_thresholds_reached` now point to flow-neutral trading drawdown.
Second-based duration aliases are serialized for compatibility, but the
canonical Review 007 names above are the audit surface.

## Barrier Ruin

Operational ruin is path-barrier based. Once account equity is less than or
equal to `LiveAccountConfig.operational_ruin_threshold`, the path remains marked
as ruined even if it later recovers.

Serialized fields include:

- `operational_ruin_hit`
- `operational_ruin_first_timestamp`
- `operational_ruin_trigger_event_id`
- `operational_ruin_min_equity`
- `operational_ruin_event_index`
- `operational_ruin_comparison` (`<=`)
- `operational_ruin_policy`

`operational_ruin_policy` is either `classify_and_continue` (default) or
`stop_trading_after_ruin`. `zero_equity_ruin` remains separate.

## Returns

`period_twr` is cumulative time-weighted return over the simulated period.
External cash flows reset subperiod capital and do not count as return.

`period_money_weighted_return` is the period money-weighted return. With no
external flows it equals `period_twr`.

`annualized_xirr` is an annualized XIRR-style return. It is reported separately
with:

- `annualized_xirr_status`
- `annualized_xirr_unavailable_reason`
- `measurement_start`
- `measurement_end`
- `measurement_period_days`
- `annualization_applied`
- `annualization_warning`

Short measurement periods can produce extreme annualized rates; the warning is
serialized when the period is shorter than the configured threshold.

## Provenance

Every live-account result includes deterministic hashes under `provenance`:

- `trade_input_hash`
- `live_account_config_hash`
- `cash_flow_schedule_hash`
- `sizing_policy_hash`
- `contract_specification_hash`
- `ruin_configuration_hash`
- `reinvestment_configuration_hash`
- `result_hash`
- `engine_version`
- `scenario_id`
- `master_seed`
- `path_index`

`verify_live_account_result_provenance` recomputes these fields and returns a
`VerificationReport`.
