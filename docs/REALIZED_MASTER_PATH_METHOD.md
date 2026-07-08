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

Continuation rows are sampled from the RR-specific historical packet libraries:

- `data/forward_master_path/forward_1rr.csv`
- `data/forward_master_path/forward_1_5rr.csv`

The workflow samples complete historical packet rows. It does not fabricate synthetic P&L, exit reason, stop, target, MAE, MFE, or source packet IDs.

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

Rolling-PF disagreement diagnostics are not used to delete confirmed realized trades. The realized prefix remains executed regardless of any reconstructed gate state.
