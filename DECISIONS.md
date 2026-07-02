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

## ADR-012 — Breakeven epsilon policy
**Status:** ACCEPTED (Review 004)
**Decision:** Default classification is exact zero. An optional tolerance may be
declared in explicit dollars or instrument ticks (`BreakevenPolicy`), resolved at
classification time and recorded in `Scenario.breakeven_policy`. No undocumented
floating-point constant; `classify_result` and `trade_outcome_taxonomy` agree.

## ADR-013 — Timezone ingestion policy
**Status:** ACCEPTED (Review 004)
**Decision:** `normalize_trade_frame` default `source_timezone=None`. UTC-aware
inputs are accepted and normalized to UTC; naive timestamps are rejected unless a
source timezone is explicitly declared; DST-ambiguous/nonexistent local times fail
clearly unless an explicit `dst_resolution` policy is supplied.

## ADR-014 — Provenance self-verification
**Status:** ACCEPTED (Review 004)
**Decision:** `verify_result_provenance(result, scenario, source_data)` recomputes
the input-data hash and checks scenario hash, engine version, master seed, path
count, resampling policy, strategy mappings, and commission assumptions.
`build_result_distribution` records the computed input-data hash as authoritative
and warns on a declared-vs-computed mismatch.

## ADR-015 — Gap-aware block bootstraps
**Status:** ACCEPTED (Review 004)
**Decision:** Moving and stationary block bootstraps traverse only calendar-
consecutive ("verified consecutive") months. A missing/partial month breaks
continuity; a block that cannot fit within any run fails; restarts at a gap or
dataset boundary are recorded in `ResampledPath.diagnostics`. Non-consecutive
source months are never treated as contiguous.

## ADR-016 — Coverage diagnostics
**Status:** ACCEPTED (Review 004)
**Decision:** `build_coverage_report` produces per-strategy/per-month status
(complete / partial / verified_flat / missing), seasonal support counts, trade
counts, coverage span, and per-method eligibility. It feeds scenario-validation
warnings and exported diagnostics. The coverage-absent warning is centralized
across every bootstrap policy, not only seasonal.

## ADR-017 — Declared per-contract margin; entry-time initial-margin cap; fail-closed
**Status:** ACCEPTED (Review 009, implemented by review lead — Codex offline)
**Decision:** Margin is declared per contract symbol via
`InstrumentMargin(contract_symbol, initial_margin, maintenance_margin)` collected
in a `MarginPolicy(margins, reserve)`. There is **no silent default**: a traded
contract with no declared margin **fails closed** (`ValueError`), consistent with
ADR-011's "declare, don't infer" rule. When a `margin_policy` is supplied,
`run_live_account_path` caps each sized position at entry so that
`contracts * initial_margin <= max(0, equity - reserve)`. Forced reductions are
recorded on the `SizingDecision` (`margin_forced_reduction`, `initial_margin_used`)
and counted in `summary["margin_forced_reductions"]`.
**Why:** Sizing that ignores capital-at-risk overstates deliverable size and
understates blow-up risk. Declaring margin per contract keeps the economics
explicit and auditable, and forced reductions are a first-class, counted event
rather than a silent clamp.
**Scope / limitation:** This is an **entry-time initial-margin cap only**. No
intraday maintenance-margin call or forced liquidation is modeled yet (V3.1
candidate). See KNOWN_LIMITATIONS.

## ADR-018 — Exposure measured over scheduled trade intervals (realized-only)
**Status:** ACCEPTED (Review 009, implemented by review lead — Codex offline)
**Decision:** `build_exposure_report(result, margin_policy=…)` measures exposure
by an interval sweep over each trade's **scheduled `[entry, exit]`** window at its
simulated contract count: time-in-market fraction, sessions with a trade, peak
simultaneous positions / contracts / initial-margin / open-stop-risk, average open
margin, peak margin utilization, strategy & instrument overlap fractions, return
per unit of peak margin / peak stop-risk, and per-instrument time-in-market. Open
stop-risk uses declared `stop_points × dollars_per_point`.
**Why:** Terminal equity alone hides how much capital and simultaneous risk a
plan actually consumes to earn its return. Peak simultaneous margin/stop-risk and
overlap fractions make "return per unit of risk actually held" measurable.
**Scope / limitation:** Consistent with V1/V2 realized-P&L booking, exposure uses
the scheduled open interval — there is **no intratrade mark-to-market / MAE path**.
The **marginal portfolio contribution** of adding a strategy (charter item) is not
yet computed; it requires an A/B scenario diff, deferred to a portfolio-comparison
pass. See KNOWN_LIMITATIONS.

## ADR-019 — Prop-firm engine is an explicit declared state machine (no firm baked in)
**Status:** ACCEPTED (Review 010, implemented by review lead — Codex offline)
**Decision:** V4 models a prop / funded account as an event-driven state machine
(`evaluation → funded → retired`, terminal `failed_dead`) consuming one
chronological trade stream. Every rule and cost is declared in `PropFirmRules`:
trailing max-drawdown (with optional `trailing_lock_at`), `end_of_trade`/
`end_of_day` basis, daily loss limit, profit target, minimum trading days,
consistency-% payout gate, evaluation/activation/reset fees, profit split, payout
buffer/threshold/cap, min days between payouts, max payouts, and a fixed
`contracts_per_trade` sizing on the copied stream. No specific firm's numbers are
hardcoded. An evaluation reset restarts the account at its declared reset cost and
keeps consuming the forward stream; a funded breach is terminal.
**Why:** Prop rules vary by firm and are the dominant driver of whether a strategy
ever pays out. Making them explicit and declared (consistent with ADR-011's
"declare, don't infer") keeps the mechanics auditable and prevents a hidden,
firm-specific assumption from flattering the result.

## ADR-020 — Prop output is realized net cash; notional balance is not wealth; breach is realized-only
**Status:** ACCEPTED (Review 010, implemented by review lead — Codex offline)
**Decision:** The headline prop-firm output is `net_trader_cash = Σ(payout ×
profit_split) − (evaluation + activation + reset fees)`. The notional account
balance is never reported as personal wealth; every result carries an explicit
`notional_balance_note`. Aggregates (`summarize_prop_accounts`) report P(reached
funded), P(first payout), P(survived), P(failed), expected/median/P5/P95 net cash,
and expected fees/payouts. Consistent with V1/V2 realized-P&L booking, drawdown and
daily-loss rules are evaluated on **end-of-trade balances only** — no intratrade
excursion — so reported breach probability is a **lower bound** and survival an
**upper bound**; this is stated on every prop result (`realized_only_note`).
**Why:** The charter's governing principle: only realized cash counts, and the
model must not flatter itself. Disclosing the realized-only breach bound prevents
presenting an optimistic survival number as if it were worst-case.
**Scope / limitation:** Greedy payout timing, funded-breach-is-terminal, and fixed
per-trade sizing are documented in KNOWN_LIMITATIONS as V4 scope choices.
