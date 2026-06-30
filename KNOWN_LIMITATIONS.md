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
- **[GUARD]** The real 1,150-row ledger was integrated in approved V1.1; the
  integration report is committed under `reports/real_ledger_v1/`.
- **[GUARD/WARN]** Normalized timestamps are UTC-aware. Naive timestamps are
  localized through `source_timezone` with an explicit warning; callers can set
  `source_timezone=None` to fail closed.

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
- **[GUARD]** V2 milestone adds explicit deposits, withdrawals, contribution
  tracking, time-weighted returns, and money-weighted returns in
  `sim_core.live_account`.
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
- **[GUARD]** V2 milestone adds fixed contracts, fixed-dollar risk,
  percentage-equity risk, independent strategy allocations, reinvestment,
  contract caps, cash reserve, and forced size-down reporting.
- **[SCOPE]** No full margin/exposure analytics, prop-firm rules, optimizer, or
  shared portfolio-level constraints yet.

## Outputs

- **[GUARD]** Equity-path export includes source row IDs and P&L columns.
- **[GUARD]** Batch exports can include `ResultDistribution` JSON with scenario
  assumptions, data hash, diagnostics, and known limitations.
- **[WARN]** Single-path equity CSV exports include scenario metadata only when
  a `Scenario` is supplied by the caller.


## Review-004 status updates

- **[GUARD]** Contract mapping must be declared per strategy; no silent NQ->MNQ /
  ES->MES inference (ADR-011 enforced).
- **[GUARD]** Naive timestamps rejected unless `source_timezone` declared; DST
  gaps/overlaps fail unless an explicit `dst_resolution` is given (ADR-013).
- **[GUARD]** Breakeven is exact-zero by default; tolerance is explicit and
  recorded in the scenario (ADR-012).
- **[GUARD]** Moving/stationary blocks never bridge calendar gaps (ADR-015).
- **[GUARD]** Coverage report distinguishes missing vs verified-flat months; the
  coverage-absent warning fires for every bootstrap, not only seasonal.
- **[GUARD]** Result provenance is self-verifiable; the computed input-data hash
  is authoritative in exports (ADR-014).
- **[GUARD]** The real 1,150-row ledger was integrated and approved for V1.
  The clamp-to-month-end behavior for shifted month-end trades still clusters at
  the boundary (disclosed; acceptable for V1).

## Version 2 milestone limitations

- **[SCOPE]** Live-account sizing is recomputed at trade-entry events and cash
  flows; there is no intratrade mark-to-market or margin liquidation.
- **[SCOPE]** Shared portfolio constraints are reserved for a later V2 slice.
  Strategy sizing is intentionally independent in this milestone.
- **[SCOPE]** Operational ruin is a configured account-equity threshold; it does
  not yet model broker margin, exchange exposure, prop-firm trailing rules, or
  human operational constraints.
- **[SCOPE]** Money-weighted return uses a deterministic XIRR-style numeric
  solver and is intended for path reporting, not for optimization.
- **[SCOPE]** The engine reports drawdown duration and recovery duration from
  realized account events only.
