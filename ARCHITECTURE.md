# Architecture

## Version 1 Boundary

The first milestone is a deterministic simulation core:

CSV load -> validation -> synchronized resampling -> fixed-contract replay -> equity path -> risk report/export.

UI, prop-firm state machines, dynamic sizing, cash flows, optimizer objectives, and margin rules are deferred.

## Domain Models

- `StrategyMetadata`: strategy/instrument-specific metadata such as dollars per point and commission defaults.
- `Trade`: normalized historical trade measured per one contract.
- `FixedContractPortfolio`: fixed contract counts per strategy or per strategy/instrument pair.
- `AccountConfig`: initial equity and ruin threshold.
- `SampledBlock`: provenance record mapping a target month to the sampled source month.
- `ResampledPath`: trades plus block provenance.
- `EquityPoint`: one realized trade event in the replay equity curve.
- `SimulationResult`: replay result with equity path, trades, portfolio, account, and sampled block metadata.

The package uses typed dataclasses plus explicit validation as the configuration-validation equivalent for Version 1. This avoids introducing a larger config system before scenario schemas stabilize.

## CSV Schema

Required fields:

- `strategy_id`
- `instrument`
- `entry_time`
- `exit_time`
- `pnl_dollars`, or both `pnl_points` and `dollars_per_point`

Optional fields:

- `trade_id`
- `direction`
- `entry_price`
- `exit_price`
- `stop_points`
- `target_points`
- `mae_points`
- `mfe_points`
- `result_type`
- `session`
- `commission_round_turn`

Derived fields:

- `pnl_dollars` can be derived from `pnl_points * dollars_per_point`.
- `result_type` is derived as `win`, `loss`, or `breakeven` when absent.
- `trade_id` is generated from source path and CSV row when absent.

Validation behavior:

- Missing required columns raise `TradeValidationError`.
- Invalid timestamps raise `TradeValidationError`.
- `exit_time < entry_time` raises `TradeValidationError`.
- Duplicate trades with identical strategy, instrument, entry time, exit time, and PnL are rejected.
- Breakeven trades must have zero PnL if explicitly labeled.

## Resampling Interfaces

All policies expose:

```python
sample(trades, *, seed=None, path_index=0) -> ResampledPath
```

Implemented policies:

- `HistoricalReplay`
- `SameCalendarMonthBootstrap`
- `MovingBlockBootstrap`
- `StationaryBlockBootstrap`

For bootstraps, a sampled source month is applied to the entire portfolio, so all strategies draw from the same historical calendar block. This preserves observed cross-strategy dependence at month granularity for Version 1.

## Portfolio Event Flow

1. Normalize one or more ledgers into `Trade` objects ordered by entry time.
2. Resampling creates a `ResampledPath` and records source-month provenance.
3. Replay sorts realized events by `exit_time`, then `entry_time`, then `trade_id`.
4. Contracts are read from strategy/instrument overrides, strategy overrides, or the portfolio default.
5. Gross PnL is `trade.pnl_dollars * contracts`.
6. Commission is `trade.commission_round_turn * contracts`.
7. Net PnL is applied to equity at the trade exit timestamp.
8. Metrics consume the resulting realized equity path.

## Test Plan

Current tests cover:

- Chronological load ordering.
- Strategy/instrument metadata isolation.
- ES metadata not modifying NQ metadata.
- Mismatched strategy/instrument metadata not being applied to another instrument.
- Breakeven classification.
- Duplicate-trade rejection.
- Synchronized source-month selection across strategies.
- Identical seed reproducibility.
- Different seed variation.
- Fixed-contract replay math.
- MES and MNQ independent sizing overrides.
- Drawdown, ruin, and monthly percentile reporting.

Future tests should cover cash flows, forced size-down, margin restrictions, account failure, prop payouts, optimizer constraints, and stress adjustments as those modules are implemented.
