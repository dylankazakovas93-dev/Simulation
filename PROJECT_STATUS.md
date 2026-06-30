# Project Status

Last updated: 2026-06-30

Branch: `codex/v1-core`

## Current State

Version 1 hardening for Claude Review 002 is implemented locally. No Version 2
work was started.

## Findings Resolved

- **BLOCKER-1 timezone policy:** ingestion now stores timezone-aware UTC
  timestamps, rejects naive timestamps unless `source_timezone` is explicitly
  configured, and preserves UTC through normalized trades and resampling.
- **BLOCKER-3 month-boundary-safe shifting:** `Trade.shifted_to_month` carries
  an explicit `target_month`, preserves source offset when possible, clamps to
  the final valid target-month instant when necessary, and never lets a shifted
  trade fall into the following month.
- **BLOCKER-2 independent path RNG:** resampling policies derive path-local RNGs
  from `SeedSequence(master_seed).spawn(path_index + 1)[path_index]`; path index
  now affects the stream.
- **BLOCKER-4 Scenario/ResultDistribution:** typed serializable models were
  added, with JSON round-trip tests and batch result/provenance exports.
- **HIGH contract registry:** canonical ingestion now requires explicit
  per-strategy `InstrumentSpec` declarations and validates authoritative `dpp`
  against them. Missing `dpp` fails closed.
- **HIGH monthly percentile denominator:** monthly percentiles now carry forward
  last equity so every path contributes one value for every requested month.
- **HIGH timestamp warnings:** code paths avoid pandas timezone-drop warnings in
  core month bucketing.

## Files Changed

- `HANDOFF.md`
- `DECISIONS.md`
- `PROJECT_STATUS.md`
- `sim_core/models.py`
- `sim_core/ingestion/csv_loader.py`
- `sim_core/resampling/policies.py`
- `sim_core/metrics/reports.py`
- `sim_core/exports.py`
- `sim_core/__init__.py`
- `sim_core/batch.py`
- `tests/test_blocker_regressions.py`
- `tests/test_canonical_fixture.py`
- existing tests and sample CSV timestamps updated to UTC-aware fixture values

## Tests Added

`tests/test_blocker_regressions.py` adds coverage for:

- naive timestamp rejection and explicit source-timezone localization
- UTC timestamp preservation
- January 31 -> February clamp
- leap-day -> non-leap February clamp
- source trades crossing a month boundary without target-month overflow
- reproducible independent path ensembles
- scenario and result-distribution JSON round trips
- provenance JSON export accompanying batch CSV outputs
- month-end percentile carry-forward denominators

`tests/test_canonical_fixture.py` now additionally proves:

- explicit micro contract mappings validate
- declared full-size contracts with micro `dpp` fail
- missing canonical `dpp` fails closed

## Tests

Passing:

```bash
python3 -m pytest
```

Result: 36 passed.

Last observed run: 36 passed.

## Canonical-Ledger Integration

The actual 1,150-row `nq_es_margin_sim_master_2025_2026.csv` was not available
locally. Searches under `/Users/mariusvidziunas/Documents/Codex` and
`/Users/mariusvidziunas/.codex/attachments` found only the representative
4-row fixture already checked into `sample_data/`.

Real-ledger acceptance remains blocked until the actual CSV is provided. The
current canonical fixture verifies schema mechanics and explicit contract
mapping, but it is not a substitute for the real-ledger integration check.

Representative fixture smoke output:

```text
fixture_rows 4
terminal_equity 100030.0
same_calendar_month_bootstrap 4 [('2026-01', '2025-01'), ('2026-02', '2025-02')]
moving_block_bootstrap 4 [('2026-01', '2025-02'), ('2026-02', '2025-01')]
stationary_block_bootstrap 4 [('2026-01', '2025-02'), ('2026-02', '2025-01')]
```

Required real-ledger checks still pending:

- all 1,150 rows load
- NQ strategies validate as explicitly declared MNQ at USD 2/point
- ES strategies validate as explicitly declared MES at USD 5/point
- historical replay reproduces aggregate ledger P&L after configured
  commissions
- seasonal, moving, and stationary bootstrap run on UTC-aware timestamps
- partial June 2026 is excluded or marked partial by coverage metadata
- all paths remain chronologically ordered
- strategy-specific fields remain isolated

## Remaining MEDIUM/LOW Findings

- No warning-report object yet; warning/provenance text is present in
  `ResultDistribution`, but ingestion warnings remain future work.
- Drawdown duration/recovery metrics remain unimplemented; V1 reports drawdown
  depth and percent.
- Scenario config file loading is still manual/programmatic; no CLI runner yet.
- GitHub push remains blocked from this environment by missing write
  credentials/plugin permission.

## New Commit

`443aad4` — `Address Claude V1 audit blockers`
