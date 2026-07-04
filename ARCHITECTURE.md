# Architecture

This document reconciles Claude's target architecture with the current Codex
Version 1 implementation on `codex/v1-core`.

## Non-Negotiable Principles

- Engine/UI separation: `sim_core/` contains the headless simulation engine. `app/`
  is reserved for Streamlit after the core passes audit.
- Explicit typed domain: V1 uses frozen dataclasses and explicit validators.
- Determinism: stochastic policies use local `numpy.random.default_rng(seed)`;
  no module-level global RNG state is used.
- Assumptions are visible: modeling assumptions and deviations are recorded in
  `DECISIONS.md`, `KNOWN_LIMITATIONS.md`, and `HANDOFF.md`.
- V1 supports USD only.

## Current Package Layout

```text
sim_core/
  models.py        # typed domain objects and validation errors
  instruments.py   # explicit USD instrument/contract registry
  ingestion/       # CSV and canonical margin-ledger normalization
  resampling/      # historical, seasonal, moving, stationary bootstrap policies
  execution/       # fixed-contract realized-PnL replay
  metrics/         # drawdown, ruin, percentiles, outcome taxonomy
  exports.py       # CSV export helpers
app/               # reserved for later Streamlit UI
tests/             # pytest suite
sample_data/       # small fixtures, including canonical-schema fixture
configs/           # example V1 configuration
```

Claude's target layout used `core/`; this branch keeps the implemented
`sim_core/` package name from the initial vertical slice. The architecture is
otherwise aligned with the same engine/UI separation.

## Domain Models

- `InstrumentSpec`: explicit underlying, contract symbol, dollars per point,
  currency, and optional commission defaults.
- `StrategyMetadata`: strategy/instrument-specific defaults, never shared across
  instruments unless explicitly configured.
- `Trade`: normalized historical trade measured per one contract, with permanent
  `source_row_id` preserved through resampling.
- `StrategyCoverage`: declared verified coverage dates and partial-month flags
  used to distinguish flat months from missing data.
- `FixedContractPortfolio`: fixed contract counts per strategy or
  strategy/instrument pair.
- `AccountConfig`: initial equity and ruin threshold.
- `SampledBlock`: provenance record mapping target month to source month.
- `ResampledPath`: sampled trades plus block provenance.
- `EquityPoint`: one realized settlement event.
- `SimulationResult`: account, portfolio, trades, equity path, and block
  provenance.

## CSV Schemas

Generic V1 normalized schema requires:

- `strategy_id`
- `instrument`
- `entry_time`
- `exit_time`
- `pnl_dollars`, or both `pnl_points` and `dollars_per_point`

Optional generic fields:

- `trade_id`
- `source_row_id`
- `contract_symbol`
- `direction`
- `entry_price`
- `exit_price`
- `stop_points`
- `target_points`
- `mae_points`
- `mfe_points`
- `result_type`
- `session`
- `currency`
- `commission_round_turn`

Canonical margin-ledger schema supported by
`load_canonical_margin_csv`/`normalize_canonical_margin_frame`:

```text
strategy, inst, window, mult, year, sess_date, entry_utc, exit_utc, side,
pnl_pts, mae_pts, mfe_pts, exit, dpp, nq_inside
```

Mapping:

- `strategy` -> `strategy_id`
- `inst` -> underlying `instrument`
- explicit registry maps `NQ` -> `MNQ` at USD 2/point and `ES` -> `MES` at USD
  5/point
- `entry_utc` / `exit_utc` -> timestamps
- `pnl_pts` -> `pnl_points`
- `mae_pts` / `mfe_pts`
- `exit` -> `result_type`
- `dpp` -> `dollars_per_point`, validated against the registry
- `window`, `mult`, and `nq_inside` -> metadata only

`mult` is never used as position sizing in V1.

## Resampling

Implemented policies share:

```python
sample(trades, *, seed=None, path_index=0, coverage=None) -> ResampledPath
```

- `HistoricalReplay`
- `SameCalendarMonthBootstrap`
- `MovingBlockBootstrap`
- `StationaryBlockBootstrap`

Synchronized month/block selection is the default. Seasonal bootstrap only
selects historical blocks whose month-of-year matches the target slot. Moving
blocks sample complete contiguous historical blocks and truncate to the horizon;
they do not wrap the final month into the first month within a block. Stationary
bootstrap samples a new source start when the source boundary is reached.

Declared coverage can add verified flat zero-trade months to the sampling pool
and can exclude partial months.

## Event Processing

Version 1 books realized P&L at `exit_time`. Settlement ordering is:

1. `exit_time`
2. `entry_time`
3. `strategy_id`
4. permanent `source_row_id`

Future event-driven cash-flow ordering is locked in `DECISIONS.md`: deposits
before exits and exits before entries when timestamps are identical. Cash flows
remain outside this implementation pass.

## Metrics

Implemented:

- equity path export
- terminal equity
- max drawdown depth and percent
- ruin probability
- cross-path monthly equity percentiles
- explicit outcome taxonomy:
  `rate_wins_over_total`, `true_win_rate_excluding_breakevens`,
  `non_loss_rate_over_total`, `loss_rate_over_total`, and
  `breakeven_frequency_over_total`

The engine does not expose a bare ambiguous `win_rate` metric.


## Review-004 hardening (modules)

- `sim_core/diagnostics/coverage.py` — reusable per-strategy/per-month coverage
  report (complete/partial/verified_flat/missing, support counts, eligibility).
- `sim_core/integration/real_ledger.py` — CLI harness:
  `python -m sim_core.integration.real_ledger --csv ... --mapping
  configs/nq_es_micro_contracts.yaml --output ...`. Discovers strategy IDs, fails
  closed on any unmapped strategy, and writes a provenance-stamped integration
  report (row count, mappings, date range, tz validation, coverage, replay P&L by
  strategy, breakeven taxonomy, seasonal/moving/stationary smoke tests,
  chronological-order check, data hash, scenario hash, warnings).
- `models.BreakevenPolicy`, `models.VerificationReport`, `batch.hash_trades`,
  `batch.verify_result_provenance`, `batch.scenario_hash` are part of the public
  API (`sim_core.__all__`).
- Block bootstraps now carry `ResampledPath.diagnostics` (consecutive runs,
  restart counts).

## Version 2 Live-Account Layer

Version 2 starts on `codex/v2-live-account` from approved V1 head
`8a81536e6335b5b4250b3ce9658fef3fe51af561`. The V1 path generator remains the
source of ordered `Trade` events. Live-account accounting is additive and lives
outside `sim_core/resampling/`.

New module:

```text
sim_core/live_account.py
```

Typed V2 concepts:

- `LiveAccountConfig`: starting equity, currency, operational ruin threshold,
  and drawdown thresholds.
- `CashFlow` and `CashFlowPolicy`: explicit non-P&L deposits and withdrawals.
- `FixedContractSizing`, `FixedDollarRiskSizing`, and `PercentEquitySizing`:
  strategy-level sizing policies.
- `StrategyAllocation`: one independent sizing policy per strategy.
- `AccountState`, `AccountEvent`, and `SizingDecision`: deterministic event and
  sizing audit records.
- `LiveAccountPathResult`: account events, sizing decisions, monthly reports,
  summary risk/return metrics, and JSON round-trip support.

The event engine processes:

1. Cash-flow events.
2. Trade-entry sizing decisions.
3. Trade-exit realized P&L events.

At equal timestamps, priority is:

1. Deposits.
2. Trade exits and realized P&L.
3. Withdrawals.
4. Trade entries / next sizing decisions.

Deposits never count as trading profit. Withdrawals never count as trading
losses. Monthly and path reports expose trading P&L, deposits, withdrawals, net
external contributions, ending equity, simple return on contributions,
period time-weighted return, period money-weighted return, annualized XIRR, and
trading return before cash flows.

Review 007 hardening names account-equity drawdown and flow-neutral trading
drawdown separately. Account-equity drawdown includes deposits and withdrawals
for cash-statement reconciliation. Flow-neutral trading drawdown uses
`starting_equity + cumulative_trading_pnl`, excludes external cash flows, and is
the default risk drawdown family exposed by the legacy drawdown aliases.

Operational ruin is path-barrier based. Once equity is `<=
operational_ruin_threshold`, `operational_ruin_hit` remains true even if the
path later recovers. The result records the first timestamp, event index, trade
or cash-flow trigger event ID, and minimum equity observed.

Return serialization separates `period_twr`, `period_money_weighted_return`,
and `annualized_xirr`. Short annualized XIRR horizons carry an explicit warning,
and non-unique/unavailable XIRR cases return a typed unavailable status.

Every `LiveAccountPathResult` includes deterministic provenance hashes for trade
inputs, live-account config, cash-flow schedule, sizing policies, contract
specifications, ruin config, reinvestment config, and the result payload.
`verify_live_account_result_provenance` recomputes those hashes and returns a
`VerificationReport`.

Fixed-dollar and percentage-equity sizing derive per-contract risk in this
order:

1. Explicit stop-loss dollar risk in trade metadata.
2. `stop_points * dollars_per_point`.
3. Configured strategy risk proxy.
4. Validation error.

Average realized loss is intentionally not used as a silent stop-risk proxy.
Sizing is independent per strategy; MES quantity is never mechanically tied to
MNQ quantity.
