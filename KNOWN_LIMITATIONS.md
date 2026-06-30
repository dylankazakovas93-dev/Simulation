# Known Limitations

Legend: **[GUARD]** enforced in code · **[WARN]** should be surfaced in reports ·
**[SCOPE]** out of Version 1 scope.

## Statistical

- **[GUARD]** IID trade shuffling is not implemented.
- **[GUARD]** Synchronized month/block sampling is the default. Independent
  per-strategy sampling is not implemented in this V1 branch.
- **[GUARD]** Seasonal bootstrap matches month-of-year.
- **[GUARD]** Moving blocks do not silently wrap the final source month into the
  first source month inside a block.
- **[GUARD]** Stationary bootstrap resamples a source start at the source
  boundary instead of silently wrapping.
- **[GUARD]** Declared partial months are excluded from sampling pools.
- **[GUARD]** Resampled trades carry `target_month` and are clamped inside that
  month when source offsets would overflow.
- **[GUARD]** Ensemble paths use path-indexed `SeedSequence` RNG streams.
- **[WARN]** Thin seasonal support counts are not yet emitted in exported
  reports.
- **[SCOPE]** No out-of-sample degradation/haircut controls in V1.

## Data Completeness

- **[GUARD]** `StrategyCoverage` can distinguish verified flat zero-trade months
  from missing data.
- **[WARN]** The real uploaded
  `nq_es_margin_sim_master_2025_2026.csv` was not present in the local
  workspace. The branch includes a representative fixture with the requested
  filename and schema, but Claude should rerun against the real ledger.
- **[GUARD]** Normalized timestamps are UTC-aware. Naive timestamps are rejected
  unless `source_timezone` is explicitly configured.

## Classification and Stress

- **[GUARD]** Breakevens are classified separately from losses.
- **[GUARD]** Outcome taxonomy reports named rates with explicit denominators.
- **[SCOPE]** Stress operators for true-win-rate, winner size, loss size,
  slippage, commission, missed trades, and tail-loss injection are not
  implemented.

## Accounting

- **[GUARD]** Equity is not capped or floored; ruin can be measured.
- **[GUARD]** Realized P&L is ordered by `exit_time`, `entry_time`,
  `strategy_id`, `source_row_id`.
- **[SCOPE]** No deposits, withdrawals, contributions ledger, time-weighted
  returns, or money-weighted returns in V1.
- **[SCOPE]** No open-position mark-to-market. Drawdown is realized-only and can
  understate intratrade risk.

## Sizing and Instruments

- **[GUARD]** Canonical ingestion requires explicit per-strategy contract
  metadata. NQ strategies must be declared as MNQ at USD 2/point and ES
  strategies as MES at USD 5/point for the confirmed real-ledger contract
  mapping.
- **[GUARD]** The file's `dpp` is authoritative and is cross-checked against the
  declared strategy contract. Missing `dpp` fails closed.
- **[GUARD]** `mult` from the canonical file is preserved as metadata and is not
  used as position sizing.
- **[SCOPE]** No reinvestment, percentage-equity sizing, fixed-dollar risk,
  margin restrictions, forced size-down, exposure analytics, prop-firm rules, or
  optimizer.

## Outputs

- **[GUARD]** Equity-path export includes source row IDs and P&L columns.
- **[GUARD]** Batch exports can include `ResultDistribution` JSON with scenario
  assumptions, data hash, diagnostics, and known limitations.
- **[WARN]** Single-path equity CSV exports include scenario metadata only when
  a `Scenario` is supplied by the caller.
