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

## ADR-021 — Optimizer is a multi-objective Pareto selection layer; never single-objective by default
**Status:** ACCEPTED (Review 011, implemented by review lead — Codex offline)
**Decision:** V5 optimization is an explicit, engine-agnostic selection layer over
a caller-supplied `evaluate` function. Declared `Objective`s (max/min) and
`Constraint`s drive a Pareto non-dominated frontier as the decision output; a
min-max-normalized weighted-sum "scalarized ranking" is provided only as a
labeled secondary display aid (`decision_note`), never as the answer. The optimizer
refuses to collapse to a single objective by default: `optimize` raises unless ≥2
objectives are given; a single-objective run must set `allow_single_objective=True`
and is recorded as a warning. Rejected candidates report the exact binding
constraints; missing objective/constraint metrics raise (no silent zero-fill);
`expected_log_growth` returns `-inf` on any total-loss period rather than clipping.
**Why:** The charter forbids optimizing median terminal equity (or any lone metric)
alone, because a single objective will exploit model traps (capped equity,
realized-only drawdown, greedy prop payouts). A declared, constrained Pareto
frontier keeps trade-offs visible and auditable.
**Scope / limitation:** V5 evaluates a provided candidate set (grid/list); it is a
selection layer, not a continuous search. It inherits all upstream realized-only /
notional caveats of whatever metrics are fed in. Documented in KNOWN_LIMITATIONS.

## ADR-022 — Engine/UI separation; the UI must surface mandatory disclosures
**Status:** ACCEPTED (Review 012, implemented by review lead — Codex offline)
**Decision:** The V6 UI is strictly separate from the engine. `sim_core` keeps zero
UI dependency. `app/controller.py` is pure Python (no Streamlit import) and is the
only bridge to the engine — it delegates all computation to `sim_core` and returns
plain data. `app/streamlit_app.py` is a thin view (collect inputs → call controller
→ render → render disclosures) with no modelling logic. Streamlit is an optional
extra (`.[ui]`), never a core dependency. `app/disclosures.py` is the single source
of the model-risk caveats the charter requires shown WITH the numbers; every
controller result attaches its section's disclosures and the view renders them, so
a number cannot be shown without its caveats. The prop tab demotes the notional
balance under an explicit not-wealth key and leads with net trader cash; engine
warnings (coverage-absent, thin support, missing months) are surfaced, not swallowed.
**Why:** The charter mandates that the engine and UI be separate and that
assumptions/limitations be explicit and defensible rather than hidden behind
attractive projections. A pure, testable controller plus a disclosure registry make
both properties enforceable in tests.
**Scope / limitation:** Coverage is declared metadata (ADR-016) and is not inferred
from trades, so the UI runs with coverage=None + surfaced warnings; in-UI coverage
declaration and a guided optimizer candidate-builder are later add-ons. Documented in
KNOWN_LIMITATIONS.

## ADR-023 — Prop payout modes (standard/daily), funded-only runs, and stage analytics
**Status:** ACCEPTED (Review 014, implemented by review lead — Codex offline)
**Decision:** `PropFirmRules.payout_mode` is declared as `standard` (threshold +
`min_days_between_payouts`) or `daily` (at most one payout per calendar day — the
instant-funding model). `run_prop_account_path` accepts `initial_phase="funded"` to
start an account already funded (evaluation skipped) for "assume funded" analyses.
`summarize_evaluation_stage` reports pass/fail/incomplete rates and time-to-pass;
`funded_window_analysis` runs an already-funded account over windows that begin at
random real historical trade-start dates and reports, per horizon (default
2/4/6/8/12 months), blow rate, survival, payout probability, and realized net-cash
distribution. All keep the ADR-020 rules: realized net cash is the headline, notional
balance is not wealth, and breach checks are realized-only (a lower bound).
**Why:** Real prop firms differ most in payout cadence and in funded-stage survival,
which is what actually determines take-home cash. A declared payout mode plus a
random-start funded-window blow-rate analysis answer "how often do I blow up in N
months and what do I clear" without hardcoding any firm.
**Also (Review 013 fix, under ADR-020):** a funded withdrawal now lowers the current
day's opening baseline so a payout is never miscounted as a daily-loss breach
("withdrawals are not losses").
**Scope / limitation:** Funded windows overlap (reuse blocks of one history) and are
not independent samples; documented in KNOWN_LIMITATIONS.

## ADR-024 — Firm rules are declared config (verified per firm) and a browser Lab mirrors the engine
**Status:** ACCEPTED (Review 015, review lead)
**Decision:** Firm-specific presets live outside the engine (app/prop_presets.py) and each
carries a verified/assumed status with sources + retrieval date. A firm is only marked
"verified" when its rules come from that firm's own help center; Apex EOD is verified
(2026-07). The trade-order shuffler (σ) is the sanctioned way to defeat path-ordering luck:
single-path prop results are front-loaded and must be read through the shuffle distribution
(blow-up + P5), never at σ=0. The browser Lab (app/prop_lab.html) is a self-contained port
of the prop engine for the user to test their own ledgers; it is verified to reproduce the
Python results and may model firm rules the core PropFirmRules cannot yet express (ramped
caps, qualifying-day minimum daily profit, separate eval/payout min-days, max payouts),
which is documented as a follow-up to fold back into the tested core.
**Why:** The user makes real-money decisions from these numbers, so wrong firm rules
(Apex was wrong from secondary sources) are unacceptable; the verified/assumed flag and
help-center sourcing make trust auditable, and the shuffler prevents front-loaded single
paths from flattering the result.

## ADR-025 — v2 rebuild: monthly-first accounting, monotone floor, browser delivery
**Status:** ACCEPTED (Review 016, user mandate)
**Decision:** The Lab is rebuilt around per-calendar-month ledgers and an enforced
accounting identity (finalBal = start + tradingP&L − grossPayouts; netPersonal =
netPayouts − fees), rendered as an explicit waterfall with per-row "why this firm
differs". Failed paths are terminated, never zero-P&L months; negative months are
never clamped. The trailing failure floor is monotone (never decreases, including
after withdrawals). The user cushion is a TOTAL above the floor (max with the firm
buffer, never additive). Eval and funded are separate views. Compute happens only on
Run; display changes re-render cache. Delivery is the zero-install browser app whose
engine is verified by a 22-test acceptance suite (Scenarios A–E); the Python core
remains the reference for the pipeline and gains ledger parity next.
