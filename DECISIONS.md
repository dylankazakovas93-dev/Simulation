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

## 2026-06-30: D5 Instrument Registry

Status: accepted and implemented for the canonical NQ/ES fixture.

Version 1 uses explicit `InstrumentSpec` metadata. The default registry maps:

- `NQ` underlying -> `MNQ`, USD 2/point
- `ES` underlying -> `MES`, USD 5/point

The loader validates canonical `dpp` against the registry. It does not infer
micro versus full-size contracts from `NQ` or `ES` without registry metadata.

## 2026-06-30: D6 Currency

Status: accepted and implemented.

Version 1 supports USD only. Non-USD instruments/trades are rejected through
model validation or ingestion validation.

## 2026-06-30: Month Bootstrap Timestamp Shifting

Status: accepted with limitation.

Sampled trades are shifted from source month start to target month start using
timestamp offsets. This preserves intra-month spacing and intraday times, but
month-end trades can overflow into the next calendar month when shifted into
shorter months. This remains a model-risk limitation for Claude audit.
