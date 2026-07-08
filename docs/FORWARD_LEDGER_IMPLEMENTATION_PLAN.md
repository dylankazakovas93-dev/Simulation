# Forward Ledger Implementation Plan

## Scope

Primary repository: `dylankazakovas93-dev/Simulation`

Branch inspected: `recovery/prop-lab-local-2026-07-08`

Protected checkpoint: `4eb63052a0cac73512eb66cd5c352462e4b53cac`

Strategy evidence repository: `dylankazakovas93-dev/Volatility-hodlod`

Locked evidence commit requested: `269fabd6f94ae60bb7594ce9e00a1188695dfa7d`

This document began as the pre-implementation plan. The implementation is now present in `sim_core/forward_master_path.py`, `app/streamlit_app.py`, `data/forward_master_path/`, and `artifacts/forward_master_path/`.

The prior Stage 1 blocker was resolved by a later user instruction: user-confirmed realized outcomes are authoritative, and unrecovered optional execution fields must be stored as null rather than blocking the feature. This document is retained as an audit trail; `docs/REALIZED_MASTER_PATH_METHOD.md` is the current method note.

This plan covers adding a canonical two-event realized master prefix to the existing Prop Lab without replacing the app, deleting historical mode, changing frozen strategy rules, or altering historical trade outcomes.

Latest user clarification received after initial evidence audit:

- Realized sequence 1 is the July 7, 2026 signal.
  - `1RR / OG_OPERATIONAL_100R`: `+150` points, `TP`.
  - `1.5RR / OG_PRIMARY_150R`: `0` points, `BE`.
- Realized sequence 2 is the July 8, 2026 loss.
  - both configs: `-200` points, `SL`.
- Prefix nets remain:
  - `1RR`: `+150 - 200 = -50`.
  - `1.5RR`: `0 - 200 = -200`.
- The user gave rough timing guidance of about one hour around `08:00-09:00`, but rough timestamps are not enough to populate canonical executed-trade rows with source-backed MAE/MFE, stops, targets, and prices.

## Current Baseline

Before this plan was written:

- Repository path: `/Users/mariusvidziunas/Documents/Codex/2026-06-30/x/work/Simulation`
- Current branch: `recovery/prop-lab-local-2026-07-08`
- Current HEAD: `4eb63052a0cac73512eb66cd5c352462e4b53cac`
- Worktree status: clean
- Protected checkpoint reachability: `git cat-file -t 4eb63052a0cac73512eb66cd5c352462e4b53cac` returned `commit`; branch contains the commit.
- Baseline tests: `python3 -m pytest -q` passed with `162 passed, 1 skipped`.

## Exact Simulation Source Files

Canonical application:

- `app/streamlit_app.py`

Existing reusable simulation and account modules:

- `sim_core/models.py`
- `sim_core/ingestion/csv_loader.py`
- `sim_core/resampling/policies.py`
- `sim_core/execution/ensemble.py`
- `sim_core/lifecycle.py`
- `sim_core/prop_rules.py`
- `sim_core/exports.py`

Existing tests to preserve:

- `tests/test_streamlit_app_helpers.py`
- `tests/test_lifecycle.py`
- `tests/test_prop_rules.py`
- `tests/test_live_account.py`
- `tests/test_resampling.py`
- `tests/test_blocker_regressions.py`
- `tests/regression/*`

## Exact Evidence Source Files Inspected

From Volatility-hodlod at `269fabd6f94ae60bb7594ce9e00a1188695dfa7d`:

- `artifacts/forward_ledger/final/forward_1rr.csv`
- `artifacts/forward_ledger/final/forward_1_5rr.csv`
- `artifacts/forward_ledger/final/realized_anchor.csv`
- `artifacts/forward_ledger/final/schema.json`
- `artifacts/forward_ledger/final/forward_scenario_manifest.json`
- `artifacts/forward_ledger/final/point_scale_scenarios.json`
- `artifacts/forward_ledger/final/calendar_blocks.csv`
- `outputs/og_regime_killswitch/operational_100r_full_chronological_trades.csv`
- `outputs/og_regime_killswitch/primary_150r_full_chronological_trades.csv`
- `outputs/og_regime_killswitch/operational_100r_trigger_log_rolling_pf_w100_t1.1_symmetric.csv`
- `outputs/og_regime_killswitch/primary_150r_trigger_log_rolling_pf_w100_t1.1_symmetric.csv`

## Relevant Schemas

### Existing Simulation Trade Schema

`sim_core.models.Trade` currently supports:

- `trade_id`
- `source_row_id`
- `strategy_id`
- `instrument`
- `contract_symbol`
- `entry_time`
- `exit_time`
- `pnl_dollars`
- `direction`
- `entry_price`
- `exit_price`
- `pnl_points`
- `stop_points`
- `target_points`
- `mae_points`
- `mfe_points`
- `result_type`
- `session`
- `dollars_per_point`
- `commission_round_turn`
- `source_path`
- `target_month`
- `metadata`

This is sufficient for carrying stop, target, MAE, MFE, entry/exit, dollars-per-point, and source metadata once the realized rows are available.

### Existing Volatility Final Forward Ledger Schema

The locked final ledgers include:

- `rr_config_id`
- `config`
- `config_label`
- `trade_packet_id`
- `source_year`
- `source_month`
- `source_session_date`
- `source_ledger_id`
- `source_block_id`
- `chronological_block_id`
- `entry_time`
- `exit_time`
- `holding_duration`
- `direction`
- `exit_reason`
- `effective_exit_reason`
- `pnl_points`
- `historical_unfiltered_pnl_points`
- `raw_stop_points`
- `effective_stop_points`
- `target_points`
- `mae_points`
- `mfe_points`
- `pnl_R`
- `historical_unfiltered_pnl_R`
- `effective_stop_R`
- `target_R`
- `mae_R`
- `mfe_R`
- `rolling_pf_window_trades`
- `rolling_pf_threshold`
- `rolling_pf_reentry`
- `rolling_pf_is_flat`
- `rolling_pf_switch_state`
- `rolling_pf_switch_mechanism`
- `volatility_proxy_anchor_points`
- `volatility_proxy_raw_stop_points`
- `strategy_scale_field`
- `raw_stop_percentile_rank`
- `volatility_regime_by_stop`
- `seasonality_month`
- `seasonality_bucket`
- `is_july_august_evidence`
- `forecast_start_date`
- `forecast_end_date`
- `mae_mfe_status`
- `mae_mfe_resolution`
- `intratrade_bar_count`
- `gap_through`

This schema is adequate for historical source packets and synthetic continuation rows.

### Required Realized Master Path Schema

Planned master-path files must include:

- `master_path_version`
- `master_path_id`
- `rr_config_id`
- `config`
- `config_label`
- `sequence_number`
- `event_group_id`
- `configuration_alternative_group_id`
- `status`
- `record_type`
- `source_session_date`
- `entry_time`
- `exit_time`
- `direction`
- `exit_reason`
- `effective_exit_reason`
- `pnl_points`
- `raw_stop_points`
- `effective_stop_points`
- `target_points`
- `mae_points`
- `mfe_points`
- `pnl_R`
- `effective_stop_R`
- `target_R`
- `mae_R`
- `mfe_R`
- `holding_duration`
- `source_trade_packet_id`
- `source_ledger_id`
- `source_commit`
- `evidence_status`
- `mutually_exclusive_config_alternative`

## Missing Required Evidence Fields

The locked evidence commit does not contain enough row-level evidence to safely create the four requested realized comparison records.

### Missing for the July 7, 2026 Event

`artifacts/forward_ledger/final/realized_anchor.csv` contains only the old single-anchor style:

- `anchor_id`
- `date`
- `status`
- `realized_pnl_points`
- `rr_config_id`
- `config`
- `comparison_group_id`
- `mutually_exclusive_config_alternative`
- `included_exactly_once`
- `included_in_forecast`
- `note`

It does not contain:

- source trade ID or level ID
- source packet ID
- entry timestamp
- exit timestamp
- direction
- entry price
- exit price
- raw stop
- effective stop
- target
- MAE
- MFE
- `pnl_R`
- holding duration
- gap-through state
- source ledger row

The locked final ledgers and chronological regime ledgers have zero rows containing `2026-07-07` and zero rows with `pnl_points == 150` or `pnl == 150`.

The user clarified that the rough July 7 timing was around `08:00-09:00`, but no source row has been found with the required exact replay fields.

### Missing for the July 8, 2026 `-200 SL` Event

The locked ledgers contain multiple `-200 SL` candidates, but none found so far are dated July 8, 2026. The requested realized July 8 loss cannot be identified from the locked evidence.

Candidate rows in `artifacts/forward_ledger/final/forward_1rr.csv`:

| trade_packet_id | source_session_date | entry_time | exit_time | direction | exit_reason | pnl_points | raw_stop_points | effective_stop_points | target_points | mae_points | mfe_points | source_ledger_id |
|---|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---|
| `1rr|validation|1224_lower|2022-10-07 09:44:00-04:00` | 2022-10-07 | 2022-10-07 09:44:00-04:00 | 2022-10-07 14:31:00-04:00 | long | SL | -200.0 | 200.0 | 200.0 | 200.0 | 202.145996 | 8.854004 | validation |
| `1rr|build_years|2115_upper|2026-04-07 19:04:00-04:00` | 2026-04-08 | 2026-04-07 19:04:00-04:00 | 2026-04-08 01:51:00-04:00 | short | SL | -200.0 | 200.0 | 200.0 | 200.0 | 203.540318 | 70.709682 | build_years |

Candidate rows in `artifacts/forward_ledger/final/forward_1_5rr.csv`:

| trade_packet_id | source_session_date | entry_time | exit_time | direction | exit_reason | pnl_points | raw_stop_points | effective_stop_points | target_points | mae_points | mfe_points | source_ledger_id |
|---|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---|
| `1_5rr|validation|1224_lower|2022-10-07 09:44:00-04:00` | 2022-10-07 | 2022-10-07 09:44:00-04:00 | 2022-10-07 14:31:00-04:00 | long | SL | -200.0 | 200.0 | 200.0 | 300.0 | 202.145996 | 8.854004 | validation |
| `1_5rr|build_years|2115_upper|2026-04-07 19:04:00-04:00` | 2026-04-08 | 2026-04-07 19:04:00-04:00 | 2026-04-08 01:51:00-04:00 | short | SL | -200.0 | 200.0 | 200.0 | 300.0 | 203.540318 | 70.709682 | build_years |

The chronological regime ledgers contain four `-200 SL` rows per config because they include additional validation rows not present in the final forward pools:

- `1224_lower`, 2022-10-07, validation
- `1696_lower`, 2024-08-05, validation
- `1760_upper`, 2024-11-06, validation
- `2115_upper`, 2026-04-08, build_years

Without an explicit source row or unique matching key for the realized July 8 loss, selecting one of these would be a guess.

## Proposed Architecture Once Evidence Is Supplied

1. Add a master-path module, likely `sim_core/forward_master_path.py`, that:
   - loads `data/forward_master_path/realized_master_path.csv`;
   - validates exactly four comparison rows;
   - returns exactly two rows for a selected RR config;
   - rejects combining both RR alternatives as one portfolio;
   - rejects enabling legacy-anchor mode together with realized-master-prefix mode.

2. Add canonical data files:
   - `data/forward_master_path/realized_master_path.csv`
   - `data/forward_master_path/realized_master_path_1rr.csv`
   - `data/forward_master_path/realized_master_path_1_5rr.csv`
   - `data/forward_master_path/realized_master_path_manifest.json`
   - `data/forward_master_path/schema.json`

3. Convert selected realized rows into `sim_core.models.Trade` objects without changing point values, timestamps, stop/target, MAE/MFE, or exit reason.

4. Add a common-path generator:
- fixed realized prefix rows always sequence 1 and 2, with July 7 before July 8;
- synthetic continuation starts at sequence 3;
   - one underlying strategy sequence per path ID;
   - same generated strategy path reused across 1, 2, 3, and 4 MNQ and across selected firms/lifecycle plans;
   - continuation rows sampled only from genuine historical packets.

5. Extend `sim_core/lifecycle.py` or wrap it without changing existing historical mode:
   - `ACCOUNT_STATE_BEFORE_PREFIX` applies the two realized trades through the existing lifecycle engine before synthetic continuation;
   - `ACCOUNT_STATE_AFTER_PREFIX` displays the two realized trades but does not apply their P&L again;
   - selected basis stored in all exports.

6. Extend `app/streamlit_app.py` in place:
   - preserve current pages;
   - add RR config, master seed, MC seed, path count, PF scenario, regime, point-scale, July/August candidate counts, MNQ sizes 1-4, firms, and lifecycle plans;
   - add a Master Path section/page showing prefix, continuation, cumulative realized-prefix, forward-only, and combined totals;
   - keep the 1.5RR second event as BE with gross strategy P&L of zero before costs.

7. Add export helpers, likely in `sim_core/exports.py`, for:
   - selected realized prefix;
   - deterministic Master Path;
   - Monte Carlo strategy-path manifest;
   - Prop Lab summary;
   - path-level results;
   - lifecycle events.

## Expected Artifacts Once Evidence Is Supplied

- `data/forward_master_path/realized_master_path.csv`
- `data/forward_master_path/realized_master_path_1rr.csv`
- `data/forward_master_path/realized_master_path_1_5rr.csv`
- `data/forward_master_path/realized_master_path_manifest.json`
- `data/forward_master_path/schema.json`
- `docs/REALIZED_MASTER_PATH_METHOD.md`
- `artifacts/prop_lab/selected_realized_prefix.csv`
- `artifacts/prop_lab/deterministic_master_path.csv`
- `artifacts/prop_lab/monte_carlo_strategy_path_manifest.csv`
- `artifacts/prop_lab/prop_lab_summary.csv`
- `artifacts/prop_lab/path_level_results.csv`
- `artifacts/prop_lab/lifecycle_events.csv`

Every exported path should include:

- `master_path_version`
- `rr_config_id`
- `path_id`
- `sequence_number`
- `status`
- realized/synthetic classification
- source packet ID
- realized-prefix net
- forward-only net
- combined net
- prefix application basis
- scenario IDs
- seed

## Tests To Add Once Evidence Is Supplied

Planned test file: `tests/test_forward_master_path.py`

Required invariants:

- realized master path has exactly four comparison rows;
- selecting 1RR returns exactly two rows;
- selecting 1.5RR returns exactly two rows;
- 1RR prefix points are `[150, -200]`;
- 1.5RR prefix points are `[0, -200]`;
- 1RR prefix net is `-50`;
- 1.5RR prefix net is `-200`;
- first 1RR event is `TP`;
- first 1.5RR event is `BE`;
- second event is `SL` for both configs;
- both configs share event group IDs `REALIZED_SIGNAL_001` and `REALIZED_SIGNAL_002`;
- RR alternatives cannot be combined as four portfolio trades;
- legacy anchor cannot be added on top of the new prefix;
- realized rows always precede synthetic rows;
- synthetic continuation begins at sequence 3;
- realized rows are never resampled or altered;
- continuation rows reference genuine historical source packets;
- fixed master seed reproduces the same visible Master Path;
- fixed Monte Carlo seed reproduces the same ensemble;
- different seeds produce different valid historical continuations;
- same strategy paths are reused across 1-4 MNQ;
- same strategy paths are reused across firms and lifecycle plans;
- `ACCOUNT_STATE_BEFORE_PREFIX` applies the prefix;
- `ACCOUNT_STATE_AFTER_PREFIX` does not apply it twice;
- MAE/MFE survive ingestion;
- 1.5RR BE remains gross zero before costs;
- winning-day logic uses the selected firm threshold;
- payout cannot occur after failure;
- historical mode remains unchanged;
- every existing firm and lifecycle plan remains present;
- Streamlit application compiles and boots.

Required commands:

- `python3 -m pytest -q`
- `python3 -m py_compile app/streamlit_app.py`
- deterministic smoke test with both RR configs, both prefix application bases, at least two firms, 1 MNQ and 4 MNQ, and 100 Monte Carlo paths.

## Blockers

Implementation is blocked at Stage 1 by missing evidence.

The requested four realized comparison rows require actual replay/source fields. The locked source commit does not provide a July 7, 2026 executed trade row with stop, target, MAE, MFE, timestamps, source packet ID, or exit identity. It also does not provide a July 8, 2026 `-200 SL` executed row with those fields.

Creating `realized_master_path.csv` now would require reconstructing the July 7 `+150 TP` / `0 BE` row and the July 8 `-200 SL` row from user-confirmed P&L plus rough timing alone. That would violate the instruction not to guess, estimate, reconstruct from P&L alone, or invent timestamps/stops/targets/MAE/MFE/exit reasons.

Minimum unblock requirement:

- Exact source/replay row for the July 7, 2026 signal in both RR configs, including entry/exit timestamp, direction, entry/exit price, raw/effective stop, target, MAE, MFE, exit reason, holding duration, gap-through state, and source packet ID.
- Exact source/replay row for the July 8, 2026 loss in both RR configs, including entry/exit timestamp, direction, entry/exit price, raw/effective stop, target, MAE, MFE, exit reason, holding duration, gap-through state, and source packet ID.

Until those fields are supplied or recovered from a verifiable source output, the correct behavior is to stop before creating false realized rows.
