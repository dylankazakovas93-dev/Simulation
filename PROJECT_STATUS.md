# Project Status

Last updated: 2026-06-30

## Current State

Version 1 vertical slice is implemented in `sim_core`:

- CSV trade ledger loading and validation.
- Explicit strategy/instrument metadata model.
- Chronological trade normalization.
- Historical replay, same-calendar-month bootstrap, moving-block bootstrap, and stationary-block bootstrap.
- Synchronized source-month selection across strategies.
- Fixed-contract portfolio replay with per-trade round-turn commissions.
- Equity path, drawdown, ruin probability, monthly percentile, summary, and CSV export helpers.
- Initial pytest suite covering core Version 1 behaviors.

No Streamlit UI has been built yet. That is intentional until the simulation core has review coverage.

## Files Changed

- `README.md`
- `pyproject.toml`
- `sim_core/**`
- `tests/**`
- `configs/v1_example.json`
- `sample_data/es_strategy.csv`
- `sample_data/nq_strategy.csv`
- `ARCHITECTURE.md`
- `DECISIONS.md`
- `HANDOFF.md`
- `KNOWN_LIMITATIONS.md`
- `PROJECT_STATUS.md`

## Tests

Passing:

- `python3 -m pytest`
- Result: 12 passed in 0.70s

## Claude Review Requested

Please review:

- Whether realized equity should be ordered by `exit_time` for Version 1, while normalized ledgers remain ordered by `entry_time`.
- Whether month shifting by month-start offset is acceptable for the first bootstrap implementation.
- Whether source-month union sampling is sufficient for sparse multi-strategy ledgers or should require a complete panel.
- Whether duplicate detection should include prices/session once those fields become important.

## Recommended Next Task

After review, add a scenario runner that reads `configs/v1_example.json`, runs multiple sampled paths, and writes `reports/` CSV outputs. Keep it CLI-oriented before starting Streamlit.
