# ARCHITECTURE

_Owner: Architecture/Model-Risk lead. Target design for Codex to implement._

This document defines the **intended** architecture. It is normative for V1 and
directional for later milestones. Where it constrains V1, deviations require a
`DECISIONS.md` entry and a note in `HANDOFF.md`.

## 1. Non-negotiable principles

1. **Engine/UI separation.** `core/` contains the entire simulation engine and
   has zero dependency on Streamlit, plotting, or any UI library. The UI
   (`app/`, later) depends only on the public `core` API. You must be able to
   run every simulation headless.
2. **Explicit typed domain.** No large untyped dicts threaded through the
   engine, no hidden global state, no module-level mutable singletons. Domain
   objects are typed (dataclasses; `frozen=True` for value objects).
3. **Determinism by construction.** Every stochastic component draws from an
   explicitly passed RNG derived from a single master seed. No bare
   `random.*`, no `np.random` global state. Same seed + same config + same
   input data ⇒ bit-identical paths.
4. **Serializable in, serializable out.** Every `Scenario`/config and every
   result aggregate round-trips to JSON. The exported report carries the
   assumptions (seed, resampling policy, stress settings, sizing, data hash)
   that produced it.
5. **Assumptions are visible.** Any modeling assumption that affects a number a
   user might act on must be (a) configurable or explicitly fixed in
   `DECISIONS.md`, and (b) surfaced in the result/report.

## 2. Package layout (target)

```
core/
  domain/        # typed entities & value objects (no logic beyond invariants)
    instrument.py      # Instrument: symbol, point_value, tick_size, currency
    trade.py           # Trade: timestamps, direction, qty, per-contract pnl…
    strategy.py        # Strategy: id, instrument, trade series, metadata
    cashflow.py        # CashFlow: scheduled deposit/withdrawal
    account.py         # Account: equity, contributions, ledger
    portfolio.py       # Portfolio: set of strategies + allocation
    policies.py        # SizingPolicy, ResamplingPolicy, AccountRulePolicy (ABCs)
    scenario.py        # Scenario: fully-specified, serializable run config
    results.py         # SimulationPath, ResultDistribution, metric records
  io/
    csv_loader.py      # parse + normalize raw CSV into domain objects
    schema.py          # column spec, dtypes, required/optional, validators
    validation.py      # ValidationReport (errors vs warnings), fail-closed
  resampling/
    replay.py          # exact chronological historical replay
    seasonal.py        # synchronized same-calendar-month block bootstrap
    blocks.py          # moving / stationary block primitives, wraparound
  sizing/
    fixed.py           # fixed-contract (V1), fixed-dollar, pct-equity (later)
  engine/
    merge.py           # chronological multi-strategy event merge
    simulator.py       # path simulation: events → equity ledger
    rng.py             # SeedSequence-based stream factory
  metrics/
    drawdown.py        # peak/trough, depth, duration, recovery
    percentiles.py     # cross-path monthly distributions
    returns.py         # simple / contribution-adjusted (TWR) / money-weighted
    exposure.py        # (later) time-in-market, margin, peak simultaneous risk
  stress/
    transforms.py      # orthogonal stress operators (see §7)
  prop/              # (later) event-driven account state machine
  optimize/          # (later) multi-objective + constraints + Pareto
app/                 # (later) Streamlit; imports core only
tests/
fixtures/
```

V1 touches: `domain/`, `io/`, `resampling/{replay,seasonal,blocks}`,
`sizing/fixed`, `engine/`, `metrics/{drawdown,percentiles,returns}`, plus tests
and fixtures. The rest are stubs/empty.

## 3. Domain model (V1 scope of each entity)

- **Instrument** — `symbol`, `point_value` (currency per 1.0 price move per
  contract), `tick_size`, `currency`. Immutable. The point value is the bridge
  that lets the simulator re-size: any trade's dollar P&L for `q` contracts is
  derived from a per-contract quantity, never copied from the historical row's
  contract count.
- **Trade** — `strategy_id`, `instrument`, `entry_ts`, `exit_ts` (tz-aware),
  `direction`, `qty_historical`, `pnl_per_contract_gross`,
  `commission_per_contract` (as recorded), optional `mae_per_contract`,
  `mfe_per_contract`, optional `entry_price`/`exit_price`. Realized P&L for the
  sim is `qty_sim * pnl_per_contract_*`. Breakeven is **not** a stored boolean;
  it is derived via the configured tolerance (see §6).
- **Strategy** — `id`, `Instrument`, ordered immutable sequence of `Trade`,
  plus derived metadata (date span, per-month trade counts, completeness flags).
- **CashFlow** — `date`, `amount` (sign = deposit/withdrawal), `kind`. Applied
  to the account ledger at a defined point in the event sequence (§5), tagged as
  contribution, never as P&L.
- **Account** — running `equity`, cumulative `contributions`, and an ordered
  `ledger` of typed entries (`TradePnL`, `Commission`, `Deposit`, `Withdrawal`).
  Equity is a pure function of the ledger; it is never clamped or floored
  silently (a negative equity / ruin event is a recorded outcome, not a bug to
  hide).
- **Portfolio** — set of `Strategy` with per-strategy `SizingPolicy` and capital
  allocation; produces the merged event stream.
- **Policy ABCs** — `SizingPolicy.contracts(context) -> int`,
  `ResamplingPolicy.generate(rng, strategies, horizon) -> blocks`,
  `AccountRulePolicy` (live margin / prop rules, later). Pluggable; the
  simulator depends on the ABC, not the concrete class.
- **Scenario** — the complete, serializable description of a run: data hash,
  master seed, resampling policy + params, sizing policies, cash-flow schedule,
  stress config, horizon, number of paths. Re-runnable from JSON alone (+ data).
- **Results** — `SimulationPath` (equity curve, ledger, per-event timestamps,
  per-month aggregates) and `ResultDistribution` (cross-path aggregates and the
  embedded `Scenario` that produced them).

## 4. Resampling architecture (the statistical core)

See `DECISIONS.md` ADR-002/003/004 for rationale. Summary of the V1 contract:

- **Historical replay** reproduces the exact original chronological trade order
  across all strategies; it is the determinism anchor and the regression oracle.
- **Synchronized same-calendar-month seasonal bootstrap** is the default
  stochastic generator. For each simulated month-slot with month-of-year `m`,
  one historical `(year, month)` block with that month-of-year is drawn from the
  shared pool, and the **same source block is applied to every strategy** so
  cross-strategy correlation, shared losing regimes, and clustering survive.
- **Chronological merge** then interleaves all strategies' trades from the
  drawn block(s) by timestamp into one event stream.
- **Independent sampling** (each strategy draws its own source block) exists
  only as an explicitly labeled alternative scenario, never the default.

Block primitives (`blocks.py`) own month boundaries, partial-month handling,
and year-boundary wraparound, and expose **support counts** (how many historical
instances back each month-of-year) so thin support is visible.

## 5. Equity accounting & event ordering

- Realized P&L is booked at **trade exit time**. The merged event stream is
  ordered by a single, documented key (proposed: `exit_ts`, ties broken by
  `entry_ts`, then `strategy_id`) — see HANDOFF open decision D1.
- Cash flows are applied at their scheduled calendar position within the ordered
  stream, **before** that day's first trade settlement (proposed) — see D2.
- Contributions are tracked separately from P&L so three return measures are
  distinguishable and never conflated: simple ending-equity return,
  contribution-adjusted time-weighted return, and money-weighted (IRR) return.
- Equity is computed by replaying the ledger; no capping, no flooring.

## 6. Win-rate & breakeven taxonomy

The bare label `win_rate` is **forbidden** anywhere in code, output, or report.
Given a P&L tolerance `eps` (per `DECISIONS.md` ADR-005):

```
n_total
n_win        = count(pnl >  eps)
n_loss       = count(pnl < -eps)
n_breakeven  = count(|pnl| <= eps)

win_rate_gross  = n_win / n_total
true_win_rate   = n_win / (n_win + n_loss)        # excludes breakevens
non_loss_rate   = (n_win + n_breakeven) / n_total
loss_rate       = n_loss / n_total
breakeven_freq  = n_breakeven / n_total
```

Every reported rate names its denominator. Stress operators that touch these
rates state which definition they move.

## 7. Stress operators are orthogonal

Each operator is a separate, independently toggled transform applied in a fixed,
documented order. The following must never be coupled silently:

true-win-rate · winner-size · loss-size · breakeven-rate · trade-frequency ·
slippage · commission · missed-trades · tail-loss-injection.

Any combination that compounds (e.g. lowering true win rate *and* shrinking
remaining winners) must be reported as a labeled compound stress. The engine
records exactly which operators were active and their parameters.

## 8. Randomness

`engine/rng.py` builds all streams from one `numpy.random.SeedSequence(master)`
via `.spawn()`, one independent child `Generator` per stochastic role (block
selection, stress sampling, …). No global RNG state anywhere. The master seed is
part of the `Scenario` and is echoed in every result.
