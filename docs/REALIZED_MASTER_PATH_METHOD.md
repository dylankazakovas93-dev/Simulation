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

Each synthetic trade is assigned to a unique business day after July 8, 2026 through August 31, 2026. Dates do not wrap; requesting more trades than available forecast trading days raises a validation error. Source entry time-of-day and holding duration are shifted onto the assigned forecast date and labeled `SYNTHETIC_SHIFTED_SOURCE_TIME_OF_DAY`.

The fixed prefix is never resampled, shuffled, duplicated, dropped, weighted, or outcome-adjusted. Synthetic sequence numbers start at `3`.

## Account-State Basis

`ACCOUNT_STATE_BEFORE_PREFIX` means the supplied account state is before July 7, so both realized rows are applied through the lifecycle engine before continuation.

`ACCOUNT_STATE_AFTER_PREFIX` means the supplied account state already includes July 7 and July 8, so the prefix is displayed but not applied again to account P&L.

## Exports

The default smoke export writes:

- `artifacts/forward_master_path/selected_realized_prefix.csv`
- `artifacts/forward_master_path/deterministic_master_path.csv`
- `artifacts/forward_master_path/monte_carlo_strategy_path_manifest.csv`
- `artifacts/forward_master_path/path_level_point_results.csv`
- `artifacts/forward_master_path/lifecycle_account_results.csv`
- `artifacts/forward_master_path/lifecycle_events.csv`
- `artifacts/forward_master_path/summary.csv`
- `artifacts/forward_master_path/validation_report.csv`
- `artifacts/forward_master_path/all_strategy_paths.csv`

## Limitations

PF, regime, and point-scale controls are recorded in scenario metadata in this implementation. The current sampler preserves historical packet outcomes and does not rewrite trade geometry.

Scenario controls are active:

- PF scenarios alter packet sampling probabilities by outcome sign.
- Regime scenarios alter packet sampling probabilities by source year/regime/outcome mix.
- Point-scale scenarios rescale P&L, stops, targets, MAE, and MFE together while preserving the sampled packet and exit identity; stop/target caps are enforced.

Rolling-PF disagreement diagnostics are not used to delete confirmed realized trades. Full causal rolling-PF forward gating is not enabled in this implementation; historical FLAT rows are excluded instead.

The per-trade Path Inspector ledger is a forward-workflow ledger for funded-stage account state. The historical lifecycle bootstrap remains unchanged and remains the authoritative legacy mode for uploaded historical ledgers.
