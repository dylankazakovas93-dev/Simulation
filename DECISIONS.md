# DECISIONS

Architecture Decision Records. Each ADR is a proposed default by the
architecture lead. Codex may implement as-is or challenge in `HANDOFF.md`.
Status: `PROPOSED` until confirmed by the user or accepted by implementation.

---

## ADR-001 — Engine and UI are separate packages
**Status:** PROPOSED
**Decision:** `core/` has no UI dependency; every simulation is runnable
headless and is the only thing tested in V1. Streamlit lives in `app/` and is
deferred until the core engine passes V1 acceptance.
**Why:** Testability, reproducibility, and the ability to run batch
optimizations later without a browser. UI-coupled engines cannot be audited.

## ADR-002 — Default generator is the synchronized seasonal bootstrap
**Status:** PROPOSED
**Decision:** The default stochastic resampler is the same-calendar-month block
bootstrap with **synchronized** source-block selection across strategies. IID
trade shuffling is **not** offered, not even as an option, because it is
routinely mistaken for a bootstrap while destroying autocorrelation and
clustering.
**Why:** Trade outcomes are serially dependent and seasonally structured;
strategies on correlated instruments share regimes. Independent or IID sampling
manufactures false diversification.
**Consequence:** Independent-per-strategy sampling exists only as an explicitly
labeled alternative scenario (`SamplingMode.INDEPENDENT`), surfaced in the
report as a correlation-destroying what-if.

## ADR-003 — Within a block, original trade order is preserved
**Status:** PROPOSED
**Decision:** A drawn `(year, month)` block contributes that strategy's trades
in their **exact original chronological order**. No intra-block shuffling.
**Why:** Preserves within-month clustering and streak structure.

## ADR-004 — A strategy flat in a drawn month contributes zero trades
**Status:** PROPOSED
**Decision:** If the synchronized source `(year, month)` contains no trades for
a given strategy, that strategy contributes nothing for that slot. It is **not**
resampled to a different month to "fill" the slot.
**Why:** "This strategy was flat while the other drew down" is a real, important
joint outcome. Backfilling it from another period silently breaks the
synchronization that ADR-002 exists to preserve. This is a model-risk trap
(KNOWN_LIMITATIONS) — flag the alternative only as a labeled scenario.

## ADR-005 — Breakeven tolerance is explicit and configurable
**Status:** PROPOSED
**Decision:** Breakeven classification uses a tolerance `eps`, configured per
`Scenario` and defaulting to a per-instrument value (proposed: `0.5 * tick_size
* point_value`, i.e. half a tick of dollar P&L per contract). Trades with
`|pnl_per_contract| <= eps` are breakevens, excluded from `true_win_rate`.
**Why:** Treating breakevens as losses understates true win rate and corrupts
every downstream stress and optimization. The tolerance must be a visible knob,
not a magic constant.

## ADR-006 — Equity is never silently capped or floored
**Status:** PROPOSED
**Decision:** The account ledger produces equity by replay with no clamping.
Ruin (equity ≤ 0 or below a configured threshold) is a recorded terminal
outcome of the path, not a value to floor at zero.
**Why:** Capping hides ruin and lets optimizers exploit a free put. Risk-of-ruin
must be measurable.

## ADR-007 — Deposits/withdrawals are contributions, not P&L
**Status:** PROPOSED
**Decision:** Cash flows hit a separate ledger lane. Three return measures are
reported distinctly: simple ending-equity, contribution-adjusted (time-weighted),
and money-weighted (IRR). No measure conflates a deposit with profit.
**Why:** Conflation inflates apparent performance and is a classic failure mode.

## ADR-008 — One master seed, spawned child streams
**Status:** PROPOSED
**Decision:** `numpy.random.SeedSequence(master).spawn(k)` yields one
independent `Generator` per stochastic role. No global RNG. Master seed is part
of the serialized `Scenario` and echoed in results.
**Why:** Reproducibility and the ability to add a new stochastic component later
without perturbing existing streams.

## ADR-009 — Percentiles are computed across paths, never by differencing medians
**Status:** PROPOSED
**Decision:** Monthly distribution metrics are computed across the path ensemble
per month-slot. "Median monthly change" is `median(per-path monthly change)`,
never `median(end) − median(start)`. Any differenced quantity is labeled as such.
**Why:** Differencing medians is not a median of differences and silently
misrepresents the distribution.

## ADR-010 — Partial (incomplete) months are flagged and excluded from the pool by default
**Status:** PROPOSED
**Decision:** The first/last calendar month of a strategy's data, if not a full
month of trading, is marked `partial` and excluded from the seasonal bootstrap
pool by default (configurable). Support counts per month-of-year are always
reported.
**Why:** Including a 3-day "month" as if it were a full month biases seasonal
distributions; thin or single-year support must be visible.

## ADR-011 — Contract mapping is declared per strategy; `dpp` is authoritative; no silent micro fallback
**Status:** ACCEPTED (user decision, 2026-06-30)
**Decision:** For the `nq_es_margin_sim_master_2025_2026` ledger the contracts
are **micros**: NQ rows = MNQ at $2/point, ES rows = MES at $5/point. The
mapping is not inferred from the underlying symbol. A strategy/instrument
configuration must explicitly declare `underlying`, `contract_symbol`,
`dollars_per_point`, and `currency`. The file's `dpp` field is **authoritative**
and is cross-checked against the declared mapping. A blank or missing `dpp`
must **fail validation**; it must never silently fall back to micro-contract
economics, and the engine must never silently infer `MNQ` from `NQ` (or `MES`
from `ES`).
**Why:** The micro values are correct for this ledger, but a built-in micro
default is a latent 10× mispricing for any full-size or differently-specced
ledger. Failing closed on a blank/contradictory `dpp` keeps the economics
explicit and auditable.
**Consequence (for Codex):** Replace the implicit
`DEFAULT_INSTRUMENT_REGISTRY` fallback with a required declared mapping;
`normalize_canonical_margin_frame` must raise (not default) on a blank `dpp`.
Covered by `tests/regression/test_h1_instrument_mapping.py`.
