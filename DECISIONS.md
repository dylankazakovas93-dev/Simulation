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
