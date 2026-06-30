# Handoff

## Synchronization Summary

- Branch: `codex/v1-core`
- Remote: `https://github.com/dylankazakovas93-dev/Simulation.git`
- Claude governance branch merged:
  `origin/claude/portfolio-sim-architecture-yq361s`
- Final commit hash: recorded in final response after commit creation.
- No force-push used.

## Work Completed

Implemented and reconciled Version 1 only:

- `sim_core` typed domain models, including `InstrumentSpec`,
  `StrategyCoverage`, permanent `source_row_id`, and USD-only validation.
- Generic CSV ingestion and canonical margin-ledger schema normalization.
- Explicit instrument registry for NQ->MNQ and ES->MES micro contract mapping.
- Fixed-contract replay with deterministic settlement ordering.
- Historical, seasonal, moving-block, and stationary-block resampling.
- Coverage-aware sampling for verified flat zero-trade months and partial-month
  exclusion.
- Drawdown, ruin, cross-path monthly percentiles, explicit outcome taxonomy,
  path summaries, and CSV exports.
- Representative canonical fixture:
  `sample_data/nq_es_margin_sim_master_2025_2026.csv`.
- Reconciled `ARCHITECTURE.md`, `DECISIONS.md`, `PROJECT_STATUS.md`, and
  `KNOWN_LIMITATIONS.md` with Claude's governance docs.

## Tests Run

```bash
python3 -m pytest
```

Result: 25 passed.

## Acceptance Matrix

| Claude test requirement | Existing Codex test | Status | Missing work |
|---|---|---|---|
| T1 same seed + config + data gives identical distribution | `test_identical_seeds_reproduce_identical_outputs` | Partial | No full `ResultDistribution` JSON/hash object yet. |
| T2 different seed gives different valid paths and same invariants | `test_different_seeds_produce_different_valid_outputs` | Partial | Support-count invariants not yet reported. |
| T3 no global RNG module functions | Not explicit | Missing | Add grep/AST guard in CI. |
| T4 historical replay exact order and merged stream | `test_historical_replay_preserves_full_ledger_order_and_equity` | Partial | Needs golden real-ledger replay fixture. |
| T5 seasonal month matching | `test_seasonal_month_matching_over_many_seeds` | Covered | Property-style breadth can be expanded. |
| T6 synchronized source-month selection across strategies | `test_multiple_strategies_use_synchronized_source_months` | Covered | Independent stress mode intentionally not implemented. |
| T7 within-block order preserved | Covered by replay/resampling ordering tests | Partial | Add explicit intra-month multi-trade fixture. |
| T8 partial months excluded; support counts correct | `test_partial_month_is_excluded_when_coverage_declares_it_partial` | Partial | Support counts not emitted. |
| T9 flat verified month contributes zero trades | `test_flat_verified_zero_trade_month_remains_sampleable` | Covered | Real coverage metadata still needs scenario config. |
| T10 merged stream D1 ordering including ties | `test_stable_deterministic_tie_ordering_uses_source_row_id` | Covered | Future cash-flow event order not implemented. |
| T11 fixed-contract dollar P&L equals qty times per-contract P&L | `test_fixed_size_replay_matches_original_ledger_net_of_commissions` | Covered | Larger fixture advisable. |
| T12 deposits not P&L; withdrawals symmetric | Not implemented | Missing | Cash flows are outside V1 implementation pass per user instruction. |
| T13 equity can go <= 0 and is not floored | `ruin_probability` coverage only | Partial | Add explicit negative-equity fixture. |
| T14 return measures differ on deposit fixture | Not implemented | Missing | Requires cash-flow ledger and return metrics. |
| T15 five named rates with breakevens around eps | `test_explicit_outcome_taxonomy_excludes_breakevens_from_true_rate` | Partial | Add tolerance-boundary cases. |
| T16 drawdown depth/duration/recovery | `test_metrics_report_drawdown_ruin_and_monthly_percentiles` | Partial | Duration/recovery not implemented. |
| T17 cross-path monthly percentiles | `test_metrics_report_drawdown_ruin_and_monthly_percentiles` | Partial | Add skewed median-change non-equivalence fixture. |
| T18 validation ERROR rules reject bad CSVs | Several ingestion tests | Partial | Need table-driven coverage for all rules. |
| T19 validation WARNING rules fire without aborting | Not implemented | Missing | No warning report object yet. |
| T20 regression file convention | Not implemented | Missing | Add `tests/regression/` once first regression bug is logged. |

Additional requested checks:

| Requirement | Existing Codex test | Status | Missing work |
|---|---|---|---|
| Complete versus partial month handling | `test_partial_month_is_excluded_when_coverage_declares_it_partial` | Partial | Complete month coverage exists; support reports missing. |
| Seasonal month matching | `test_seasonal_month_matching_over_many_seeds` | Covered | None for V1. |
| Synchronized source-month selection | `test_multiple_strategies_use_synchronized_source_months` | Covered | None for default mode. |
| Stable deterministic tie ordering | `test_stable_deterministic_tie_ordering_uses_source_row_id` | Covered | None for trade settlements. |
| Cross-path percentile calculations | `test_metrics_report_drawdown_ruin_and_monthly_percentiles` | Partial | More robust skew fixture needed. |
| Explicit win-rate taxonomy | `test_explicit_outcome_taxonomy_excludes_breakevens_from_true_rate` | Partial | Boundary/tolerance tests needed. |
| Contract metadata isolation | `test_registry_explicitly_maps_underlying_to_micro_contract`, metadata isolation tests | Covered | Real ledger audit needed. |
| Full historical replay equality | `test_historical_replay_preserves_full_ledger_order_and_equity` | Partial | Needs real-ledger golden output. |
| Export consistency | `test_export_consistency_round_trips_equity_path_columns` | Covered | Scenario assumption export missing. |
| Flat verified zero-trade months | `test_flat_verified_zero_trade_month_remains_sampleable` | Covered | Scenario config support missing. |

## Remaining Blockers Before V1 Acceptance

- Run canonical integration against the real uploaded
  `nq_es_margin_sim_master_2025_2026.csv`.
- Implement or explicitly defer missing Claude acceptance items T3, T12, T14,
  T19, and T20.
- Add support-count reporting for bootstrap pools.
- Add scenario/result JSON serialization with data hashes if required before the
  V1 gate.
- Decide whether drawdown duration/recovery are required before acceptance.

## Specific Claude Audit Areas

- Canonical schema mapping and explicit NQ/MNQ, ES/MES registry behavior.
- Event ordering and `source_row_id` preservation through bootstrap resampling.
- Coverage model for flat versus missing versus partial months.
- Statistical implications of month-start timestamp shifting.
- Whether local `default_rng(seed)` determinism is acceptable for this V1
  branch or must move to `SeedSequence.spawn()` now.
