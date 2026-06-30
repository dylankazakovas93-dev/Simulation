# Project Status

Last updated: 2026-06-30

Branch: `codex/v1-core`

## Repository Synchronization

- Remote: `https://github.com/dylankazakovas93-dev/Simulation.git`
- Local branch created for audit: `codex/v1-core`
- Fetched remote branches before reconciliation.
- Merged Claude governance branch
  `origin/claude/portfolio-sim-architecture-yq361s` into `codex/v1-core`.
- Reconciled documentation conflicts manually, preserving Claude's risk
  warnings and Codex's implementation details.
- No force-push performed.

## Current State

Version 1 remains a core-engine implementation only. No Version 2 work has been
started.

Implemented:

- CSV trade ledger loading and validation.
- Canonical margin-ledger schema mapper for
  `nq_es_margin_sim_master_2025_2026.csv` columns.
- Explicit instrument/contract registry for NQ->MNQ and ES->MES micro contract
  metadata.
- USD-only validation.
- Permanent `source_row_id` preserved through normalization and resampling.
- Deterministic realized-PnL ordering by `exit_time`, `entry_time`,
  `strategy_id`, `source_row_id`.
- Historical replay, same-calendar-month bootstrap, moving-block bootstrap, and
  stationary-block bootstrap.
- Declared strategy coverage for verified flat months and partial-month
  exclusion.
- Fixed-contract replay with commissions.
- Equity path, drawdown, ruin probability, monthly percentiles, outcome
  taxonomy, and CSV export helpers.
- Representative canonical-schema fixture using the requested filename.

## Tests

Passing:

```bash
python3 -m pytest
```

Result: 25 passed.

## Important Caveat

The real uploaded `nq_es_margin_sim_master_2025_2026.csv` file was not present
under `/Users/mariusvidziunas/Documents/Codex` during this session. A
representative fixture with the exact requested filename and columns was added
to lock the schema and registry behavior. Claude should rerun the canonical
integration test against the real ledger before accepting Version 1.

## Claude Review Requested

- Statistical validity of month-start timestamp shifting.
- Whether the `sim_core/` package name is acceptable versus Claude's target
  `core/` layout.
- Whether `StrategyCoverage` is sufficient for complete/partial/flat month
  handling in V1.
- Whether local seeded generators are enough for V1, or whether
  `SeedSequence.spawn()` should be implemented before acceptance.
- Whether the canonical fixture mapping should normalize `instrument` to
  underlying plus `contract_symbol`, or make contract symbol primary.

## Recommended Next Task

Claude should audit this branch against the T1-T20 matrix in `HANDOFF.md`. Do
not start reinvestment, margin, exposure, prop-firm rules, optimization, or
Streamlit until the audit gate is cleared.
