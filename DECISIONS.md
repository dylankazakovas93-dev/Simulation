# Decisions

## 2026-06-30: Keep UI Out of Version 1

Status: accepted.

The initial deliverable is a tested simulation core. Streamlit is reserved in
`app/` but not implemented until ingestion, resampling, replay, and metrics are
reviewable.

## 2026-06-30: Use `sim_core/` as the Engine Package

Status: accepted for this branch.

Claude's architecture draft used `core/`. The implementation had already
landed as `sim_core/`; the reconciled branch preserves that package name to
avoid churn while keeping the engine/UI separation intact.

## 2026-06-30: Use Typed Dataclasses for Initial Models

Status: accepted.

Version 1 uses typed frozen dataclasses and explicit validation errors. A later
scenario/config layer can move to pydantic once schemas stabilize.

## 2026-06-30: D1 Event Ordering

Status: accepted and implemented.

Realized P&L is applied at `exit_time`. Deterministic tie ordering is:

1. `exit_time`
2. `entry_time`
3. `strategy_id`
4. permanent `source_row_id`

Normalized ledgers remain sorted by entry time for input review.

## 2026-06-30: D2 Cash-Flow Timing

Status: accepted as future default; not implemented in V1.

Contributions occur at the start of the month before the first trading event.
Withdrawals occur after the final trading event of the configured period. Timing
must later be configurable. Deposits and withdrawals must be contributions, not
trading P&L.

## 2026-06-30: D3 Horizon Beyond Historical Span

Status: accepted and partly implemented.

Bootstrap policies sample historical blocks with replacement. Seasonal
bootstrap uses matching month-of-year blocks. Moving blocks sample complete
contiguous source blocks and truncate to the requested horizon without silently
wrapping final month to first month inside a block. Stationary bootstrap
resamples a new source start when the source boundary is reached.

## 2026-06-30: D4 Flat and Partial Months

Status: accepted and implemented as coverage metadata.

A complete verified month containing zero trades is valid and remains available
for sampling through `StrategyCoverage`. An absent month is not automatically a
zero-trade month. Declared partial months are excluded from bootstrap pools.

## 2026-06-30: D5 Explicit Per-Strategy Contract Declarations

Status: accepted and implemented for the canonical NQ/ES fixture.

Version 1 uses explicit `InstrumentSpec` metadata per strategy for canonical
ledger ingestion. The user confirmed the real ledger uses micro contracts:

- NQ strategies represent `MNQ`, USD 2/point.
- ES strategies represent `MES`, USD 5/point.

The canonical loader requires a declared contract specification for every
strategy and validates the file's authoritative `dpp` against that declaration.
Blank or missing `dpp` fails closed unless a future explicit fill policy is
configured. Declared full-size NQ/ES with micro `dpp` values fails validation.

## 2026-06-30: D6 Currency

Status: accepted and implemented.

Version 1 supports USD only. Non-USD instruments/trades are rejected through
model validation or ingestion validation.

## 2026-06-30: UTC Timestamp Policy

Status: accepted and implemented.

All normalized timestamps are timezone-aware UTC. Naive timestamps are localized
through an explicit `source_timezone` policy and emit a runtime warning; callers
can set `source_timezone=None` to fail closed on naive timestamps. Timezone
conversion is performed during ingestion and stored on normalized `Trade`
objects; timezone information is not silently dropped.

## 2026-06-30: Month Bootstrap Timestamp Shifting

Status: accepted and implemented.

Sampled trades are shifted from source month start to target month start using
timestamp offsets when possible. If a source offset would land outside the
target month, entry is clamped to the final valid instant of the target month.
Exit duration is preserved when possible without crossing the target-month
boundary. Every resampled trade carries explicit `target_month` metadata.

## 2026-06-30: Independent Path RNG Streams

Status: accepted and implemented.

Bootstrap policies derive each path's RNG from `numpy.random.SeedSequence` using
the scenario master seed and `path_index`. This makes same-seed ensembles
reproducible while allowing different path indices to sample independently. No
global RNG state is used.

## 2026-06-30: Scenario and ResultDistribution Provenance

Status: accepted and implemented for V1 core outputs.

Version 1 includes typed serializable `Scenario` and `ResultDistribution`
models. Batch exports write result-distribution JSON so CSV outputs are
accompanied by scenario assumptions, data hash, resampling diagnostics, warnings
and known limitations.


## Review-004 hardening decisions (2026-06-30)

### ADR-011 (ENFORCED) — Explicit per-strategy contract mapping
`normalize_canonical_margin_frame` / `load_canonical_margin_csv` now REQUIRE
`contract_specs_by_strategy`. Underlying symbols (NQ/ES) never silently imply a
contract (MNQ/MES). Unknown strategies, missing mappings, blank `dpp`, and `dpp`
that contradicts the declaration all fail validation. The default registry is
explicit convenience tooling only (`instruments.build_specs_from_registry`).

### ADR-012 — Breakeven epsilon policy
Default classification is **exact zero**. An optional tolerance may be declared
in explicit dollars or instrument ticks (`models.BreakevenPolicy`), resolved at
classification time and recorded in `Scenario.breakeven_policy`. No undocumented
floating-point constant.

### ADR-013 — Timezone policy
`normalize_trade_frame` default `source_timezone=None`: naive timestamps are
rejected unless a source timezone is declared; UTC-aware inputs are accepted and
normalized to UTC. DST-ambiguous / nonexistent local times fail clearly unless an
explicit `dst_resolution` policy is supplied.

### ADR-014 — Provenance self-verification
`batch.verify_result_provenance(result, scenario, source_data)` recomputes the
input-data hash and checks scenario hash, engine version, seed, path count,
policy, strategy mappings, and commission assumptions. `build_result_distribution`
records the *computed* input-data hash as authoritative and warns on a declared
mismatch.

### ADR-015 — Gap-aware block bootstraps
Moving/stationary blocks traverse only calendar-consecutive ("verified
consecutive") months. A missing/partial month breaks continuity; a block that
cannot fit in any run fails; restarts at a gap/boundary are recorded in
`ResampledPath.diagnostics`.

### ADR-016 — Coverage diagnostics
`diagnostics.build_coverage_report` produces per-strategy/per-month status
(complete / partial / verified_flat / missing), seasonal support counts, trade
counts, coverage span, and per-method eligibility. It feeds scenario-validation
warnings and exported diagnostics.

## Version 2 decisions (2026-06-30)

### ADR-017 — Additive live-account layer
V2 live-account accounting is additive and separate from V1 resampling. The V1
path generator produces ordered per-contract `Trade` events; `sim_core.live_account`
consumes those events and produces account events, sizing decisions, monthly
reports, and return/risk metrics. Resampling modules must not contain
live-account logic.

### ADR-018 — Cash flows are non-P&L events
Deposits and withdrawals are explicit `CashFlow` events. Deposits increase
equity and external contributions but never trading profit. Withdrawals reduce
equity and increase withdrawals but never trading loss. At equal timestamps,
processing priority is deposits, trade exits, withdrawals, then trade entries /
next sizing decisions.

### ADR-019 — Independent strategy sizing
Every strategy must have its own `StrategyAllocation` and `SizingPolicy`.
Portfolio-level shared constraints can be added later, but the first V2
milestone intentionally makes NQ and ES sizing independent and forbids coupling
MES quantity mechanically to MNQ quantity.

### ADR-020 — Stop-risk precedence for fixed-dollar and percent-equity sizing
Per-contract trade risk is derived in this order: explicit stop-loss dollars in
trade metadata, `stop_points * dollars_per_point`, configured strategy
`risk_proxy_dollars`, then validation failure. Average realized loss is not a
default proxy and must require an explicit future policy if added.

### ADR-021 — Reinvestment and symmetric size-down
Risk sizing uses external capital plus reinvested trading P&L. Positive trading
P&L is included according to `reinvestment_rate`; trading losses reduce sizing
basis immediately. Optional contract caps, minimum reserve, and scale up/down
buffers are applied after the raw contract count is computed. Size-down is
reported through forced size-reduction counts.

### ADR-022 — Return and risk metric separation
Reports separate trading P&L, deposits, withdrawals, ending equity, simple
return on contributions, period TWR, period money-weighted return, annualized
XIRR, and trading return before cash flows. Account-equity drawdown remains
available for statement history; ADR-023 establishes flow-neutral trading
drawdown as the default risk drawdown family. Operational ruin is configured
independently from zero-equity ruin.

## Review-007 hardening decisions (2026-07-02)

### ADR-023 — Separate account and flow-neutral trading drawdown
Account-equity drawdown remains available for account-statement and liquidity
reporting. Flow-neutral trading drawdown uses `starting_equity +
cumulative_trading_pnl`, excludes external deposits/withdrawals, and is the
default risk drawdown family for diagnostics and future margin/exposure logic.

### ADR-024 — Operational ruin is a barrier event
Operational ruin uses `equity <= operational_ruin_threshold`. Once touched, the
path remains classified as ruined even if later deposits or trading gains
recover the ending balance. V2.1 continues after ruin for diagnostics by
default through `operational_ruin_policy="classify_and_continue"`. The explicit
alternative is `operational_ruin_policy="stop_trading_after_ruin"`.

### ADR-025 — Period returns are not annualized XIRR
`period_twr` and `period_money_weighted_return` are period metrics. XIRR is
serialized separately as `annualized_xirr` with status, unavailable reason,
measurement dates, period length, and short-horizon warning. Non-unique sign
patterns return an unavailable status rather than a misleading number.

### ADR-026 — Live-account provenance hashes
Every `LiveAccountPathResult` records deterministic SHA-256 hashes for trade
inputs, live-account configuration, cash-flow schedule, strategy sizing
policies, contract specifications, ruin configuration, reinvestment
configuration, and result payload. Verification recomputes the hashes and
returns a `VerificationReport`.
