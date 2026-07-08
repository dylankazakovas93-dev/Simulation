# Realized Master Path Method

The July-August forward workflow uses a fixed two-trade realized prefix plus a stochastic continuation sampled from historical forward packet libraries.

## Realized Prefix

The authoritative user-confirmed sequence is:

- `1rr`: July 7, 2026 `+150` points `TP`, then July 8, 2026 `-200` points `SL`, net `-50`.
- `1_5rr`: July 7, 2026 `0` points `BE`, then July 8, 2026 `-200` points `SL`, net `-200`.

The combined comparison file has four rows, but a selected portfolio path may use only one RR alternative. The four rows must never be summed as four live trades.

Exact entry/exit timestamps, direction, entry/exit prices, MAE/MFE, and source packet IDs were not source-verified for the realized rows. Those fields are null. The rows use:

- `source_type = USER_CONFIRMED`
- `evidence_status = USER_CONFIRMED_REALIZED_OUTCOME`
- `excursion_confidence = UNKNOWN_USER_CONFIRMED`
- `strict_barrier_status = UNKNOWN`

Final realized P&L is applied. Intratrade barrier accuracy is explicitly unavailable for those realized rows because MAE/MFE is unknown.

## Synthetic Continuation

Continuation rows are sampled independently with replacement from the RR-specific historical packet libraries:

- `data/forward_master_path/forward_1rr.csv`
- `data/forward_master_path/forward_1_5rr.csv`

The workflow samples complete historical packet rows. It does not fabricate synthetic P&L, exit reason, stop, target, MAE, MFE, direction, source ledger, source month, or source packet IDs.

Historical `FLAT` rows are excluded from the executable sampling pool because causal forward rolling-PF gating is not fully replayed here. The exported rows label gate state as `GATING_DISABLED_FLAT_ROWS_EXCLUDED`.

The default forward geometry policy is `FILTER_CURRENT_RANGE`. It filters continuation packets to current-sized effective stops and current-sized nonzero P&L before sampling. This keeps the clean two-month forward ledger from silently falling back to old low-volatility 10-40 point packets. Users can intentionally switch to `SOURCE_EXACT` in the app when they want unfiltered historical packet sizes.

Each synthetic trade is assigned to a unique business day after July 8, 2026 through August 31, 2026. Dates do not wrap; requesting more trades than available forecast trading days raises a validation error. Source entry/exit offsets are aligned from `source_session_date` to the assigned `session_date`, and the lifecycle trade day must equal that assigned session. Hard assertions reject duplicate lifecycle days, sequence/order drift, and overlapping synthetic positions.

The fixed prefix is never resampled, shuffled, duplicated, dropped, weighted, or outcome-adjusted. Synthetic sequence numbers start at `3`.

## Account-State Basis

`ACCOUNT_STATE_BEFORE_PREFIX` means the supplied account state is before July 7, so both realized rows are applied through the lifecycle engine before continuation.

`ACCOUNT_STATE_AFTER_PREFIX` means the supplied account state already includes July 7 and July 8, so the prefix is displayed but not applied again to account P&L.

## Exports

The default smoke export writes:

- `artifacts/forward_master_path/selected_realized_prefix.csv`
- `artifacts/forward_master_path/deterministic_master_path.csv`
- `artifacts/forward_master_path/forward_strategy_ledger.csv`
- `artifacts/forward_master_path/all_forward_strategy_ledgers.csv`
- `artifacts/forward_master_path/monte_carlo_strategy_path_manifest.csv`
- `artifacts/forward_master_path/path_level_point_results.csv`
- `artifacts/forward_master_path/lifecycle_account_results.csv`
- `artifacts/forward_master_path/lifecycle_monthly.csv`
- `artifacts/forward_master_path/lifecycle_events.csv`
- `artifacts/forward_master_path/per_trade_account_ledger.csv`
- `artifacts/forward_master_path/summary.csv`
- `artifacts/forward_master_path/validation_report.csv`
- `artifacts/forward_master_path/all_strategy_paths.csv`

## Limitations

PF, regime, and point-scale controls are recorded in scenario metadata in this implementation. The current sampler preserves historical packet outcomes and does not rewrite trade geometry.

Scenario controls are active:

- Expectancy scenarios alter packet sampling probabilities by outcome sign and are labeled `LOWER_EXPECTANCY`, `BASE_EXPECTANCY`, and `HIGHER_EXPECTANCY`. Numeric PF labels are not shown unless calibrated. Exports include `expected_weighted_source_pf`, the weighted expected PF of the reusable source pool under the selected weighting scheme.
- The artificial expectancy tilt is a continuous win/loss weighting control. It changes chronology/mixture by overweighting historical winners or losers; it does not change point scale.
- Regime scenarios alter packet sampling probabilities by source year/regime/outcome mix.
- Point-scale scenarios rescale P&L, stops, targets, MAE, and MFE together while preserving the sampled packet and exit identity; stop/target caps are enforced.

Rolling-PF disagreement diagnostics are not used to delete confirmed realized trades. Full causal rolling-PF forward gating is not enabled in this implementation; historical FLAT rows are excluded instead.

`forward_strategy_ledger.csv` is the clean two-month forward trade ledger. It contains strategy fields only: dates, sequence, points, stops, targets, MAE/MFE, source packet IDs, source timing, and scenario metadata. It intentionally excludes prop-firm, balance, floor, payout, fee, and account lifecycle columns.

The per-trade Prop Lab account trace is emitted by the authoritative lifecycle state machine itself. It is not a second account simulator. Payout rows, failure rows, monthly rows, events, and final result values reconcile to that same state pass.

Live current account state can be applied only to a single selected lifecycle plan. Multi-plan comparisons use fresh profile state so a 50K balance/floor cannot accidentally be applied to 100K or 150K accounts.
