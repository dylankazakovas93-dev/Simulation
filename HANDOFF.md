# HANDOFF

Review log between the Architecture/Model-Risk lead and Codex. Newest review on
top. Findings are classified `BLOCKER` / `HIGH` / `MEDIUM` / `LOW` / `OPTIONAL`.

---

## Review 002 — 2026-06-30 — Audit of `codex/v1-core` @ fe408db (INTERIM)

### Executive verdict: **CONDITIONAL APPROVAL**
The architecture and direction are sound and the foundation is genuinely good
(typed domain, no IID, synchronized default, correct outcome taxonomy,
deterministic tie-ordering with `source_row_id`, equity not floored, no global
RNG state, `mult`/`dpp` handled as metadata + registry cross-check). **But the
V1 milestone is NOT yet acceptable.** Several confirmed correctness defects below
must be cleared first. This is an *interim* verdict: it covers the code actually
provided (resampling, replay, metrics, exports, all 25 tests). It does **not**
finalize the ingestion/model-layer findings, because `models.py`,
`ingestion/csv_loader.py`, `instruments.py`, and the sample CSVs were **not
supplied** (see "Pending files"). The verdict cannot move to APPROVE until those
are reviewed and the BLOCKER/HIGH items are fixed.

### Scope reviewed
`sim_core/resampling/policies.py`, `sim_core/execution/replay.py`,
`sim_core/metrics/reports.py`, `sim_core/exports.py`, package `__init__`s, all
five test files, and Codex's reconciled docs. **Not yet reviewed (Batch 1 not
received):** `sim_core/models.py`, `sim_core/ingestion/csv_loader.py`,
`sim_core/instruments.py`, `sample_data/*.csv`, `configs/v1_example.json`.

### Findings

**BLOCKER-A — Path ensemble collapses: `path_index` is never fed into the RNG.**
`policies.py` → every `sample()` does `rng = np.random.default_rng(seed)` and
uses `path_index` only as a label in `SampledBlock(path_index, …)`. The random
draws depend on `seed` alone. Therefore `sample(trades, seed=123, path_index=0)`
and `sample(trades, seed=123, path_index=1)` produce **identical** sampled
months and trades. Every consumer of a path *ensemble* —
`monthly_equity_percentiles`, `ruin_probability`, `summarize_paths` — takes a
`Sequence[SimulationResult]`, but nothing in the reviewed code builds that
sequence with independent per-path randomness, and the API actively invites
building it by varying `path_index` (which is inert). Result: the cross-path
distribution — the entire point of percentiles and ruin probability — is
**degenerate** (all paths equal) unless the caller manually varies `seed`. There
is no ensemble/batch driver in the reviewed code that does so.
*Required:* derive independent per-path streams from one master seed
(`SeedSequence(master).spawn(n_paths)` → one `Generator` per path; ADR-008), or
at minimum fold `path_index` into the seed and provide a tested batch runner.
*Regression test:* build an N-path ensemble at a fixed master seed and assert
the sampled months are **not** all identical across paths, and that the same
master seed reproduces the whole ensemble bit-for-bit.

**BLOCKER-B — Calendar-month timestamp shifting leaks trades across month
boundaries.** `policies.py` → `_materialize_months` calls
`trade.shifted_to_month(target_month)`; `DECISIONS.md` ("Month Bootstrap
Timestamp Shifting") admits "month-end trades can overflow into the next calendar
month when shifted into shorter months." `metrics/reports.py`
(`monthly_equity_percentiles`) then buckets by the **shifted** timestamp's
`.dt.to_period("M")`. Consequence: a trade assigned to a 28/30-day target month
but sourced from a 31-day month overflows into the *following* month and is
mis-attributed in monthly percentiles and any month-keyed metric. For moving-
and stationary-block bootstraps (which map arbitrary source month-of-year →
sequential targets) this is the common case; for the seasonal default it is
limited to Feb 29 across leap/non-leap years, but still real.
*Required:* carry the block's `target_month` as the authoritative month label on
each sampled trade and key all month bucketing off that label (not off the
shifted wall-clock), and define an overflow rule that cannot cross the target
month boundary (e.g. day-clamp or proportional intra-month mapping).
*Regression test:* seasonal Feb-29 source → non-leap Feb target keeps the trade
in February; moving-block 31-day source → 30-day target keeps every trade inside
the target month.

**BLOCKER-C — No `Scenario` / `ResultDistribution` / serialization / data
hash.** ARCHITECTURE principle 4–5 and BLOCKER-5 require runs to be reproducible
from a serialized config and require exports to carry their assumptions. The
implementation has no `Scenario` object, no `ResultDistribution`, no JSON
round-trip, and no input-data hash. `exports.py` writes equity/summary/percentile
CSVs with **no** embedded seed, policy, parameters, data hash, or limitations.
A result therefore cannot be tied to what produced it.
*Required:* a serializable `Scenario` (master seed, policy + params, account,
portfolio, coverage, data hash) and a `ResultDistribution` that embeds it; every
export carries the `Scenario` + KNOWN_LIMITATIONS caveats.
*Regression test:* `Scenario` JSON round-trips; re-running from the JSON + data
hash reproduces an identical `ResultDistribution`; export contains the seed and
data hash.

**BLOCKER-D — Canonical integration against the real ledger not performed
(Codex-acknowledged).** The canonical fixture is synthetic with `dpp=2.0`
(micros). `test_canonical_dpp_must_match_explicit_registry` shows the loader
*rejects* any `dpp` that disagrees with the registry. If the real
`nq_es_margin_sim_master_2025_2026.csv` was generated on full-size NQ/ES
(`dpp` 20/50), the loader will reject it; if validation were relaxed, every
dollar figure is 10× off. V1 cannot be accepted until the loader is run against
the real upload. (See HIGH-E for the underlying design issue.)

**HIGH-E — Default registry hard-codes the micro contracts and conflates
*underlying* with *contract*.** Per the canonical tests + handoff, the default
registry maps underlying `NQ → MNQ @ $2/pt` and `ES → MES @ $5/pt`. These are
micro multipliers baked in as the default. A trade log is labeled by underlying
(NQ/ES); whether it was traded as full-size or micro is a property of the *data*,
not a constant. Baking micros into the default silently assumes the user's
strategies are micro and will reject (or 10×-misprice) full-size logs.
*Required:* the underlying→contract→`dollars_per_point` mapping must be declared
per scenario (or inferred from the file's `dpp`), not a built-in micro default;
if a default registry ships, it must be clearly labeled an assumption and
surfaced in the report. *Confirm with the user which contract the real ledger
represents.* (Final classification pending `instruments.py` + the real CSV.)

**HIGH-F — Flat months are silently dropped from the sampling pool unless the
user supplies `coverage`, with no warning.** `policies.py` →
`_sorted_source_months`: without `coverage`, the pool is exactly the months that
*contain trades*. A genuinely flat month (strategy live, zero trades) is invisible
and can never be drawn, biasing the bootstrap toward active months (drops the
calm periods — the opposite of conservative). The distinction flat-vs-missing
lives entirely in `StrategyCoverage` (unseen), which the user must hand-author;
nothing warns when coverage is absent. Additionally, `partial_months` and
`complete_months()` are **unioned across all strategies**, so a month that is
partial for *any* strategy is excluded for *all* (and complete for any adds it
for all) — an un-stated cross-strategy coupling.
*Required:* warn when no coverage is provided (pool = trade-bearing months only);
document/justify the cross-strategy union semantics; emit per-month support
counts so thin/zero support is visible (ADR-010 / KNOWN_LIMITATIONS WARN).
*Regression test:* a verified-flat month is sampleable only via coverage and a
missing-coverage run emits the warning; a month partial for one strategy is
excluded and the exclusion is reported.

**HIGH-G — Cross-path monthly percentiles use inconsistent, unlabeled sample
sizes and no equity carry-forward.** `monthly_equity_percentiles` records a
month-end equity for a path **only if that path has a settlement in that month**.
Paths with no trade in a month contribute nothing, so `np.percentile` for a
sparse month is computed over fewer paths than a dense month — different
denominators per month, unlabeled. A correct design carries each path's last
equity forward to define a month-end equity for every path across the whole
horizon. (The ADR-009 "difference-of-medians" trap is not yet triggered only
because monthly *change* is not reported at all — note that gap.)
*Required:* carry-forward equity per path across the full horizon before taking
per-month percentiles; report the per-month path count.
*Regression test:* a 3-path ensemble where one path is flat in month 2 yields a
month-2 percentile computed over all 3 paths (carried forward), not 2.

**MEDIUM-H — Moving/stationary blocks treat the gap-compacted month list as
contiguous.** `source_months` is the sorted set of *months that have trades*. If
the data has a calendar gap (e.g. no March), `source_months[start:start+k]` may
be `[Jan, Apr]` and is treated as a contiguous "block," fabricating adjacency
that never existed in time and silently bridging a regime gap.
*Required:* define block contiguity on the calendar month axis (including
empty/flat months from coverage), or explicitly document and warn that blocks
are over observed-trade months only. *Regression test:* a dataset with a missing
middle month does not place the pre-gap and post-gap months in one block without
a flag.

**MEDIUM-I — Two independent breakeven definitions; default tolerance is
sub-tick.** `trade_outcome_taxonomy(..., tolerance=1e-9)` classifies breakevens
at ~exact-zero, while `Trade.result_type` (set during normalization, unseen) is
asserted to be `"breakeven"` for `pnl==0` in `test_ingestion.py`. Two code paths
decide "breakeven" with possibly different tolerances and no shared,
instrument-aware `eps` (ADR-005 wanted ~half a tick, configurable per
instrument). They can diverge.
*Required:* one shared, configurable, instrument-aware `eps`; `result_type` and
the taxonomy must agree by construction. *Regression test:* a trade at +0.4 tick
is classified identically by `result_type` and the taxonomy at the configured
`eps`. (Final classification pending `models.py`/`csv_loader.py`.)

**MEDIUM-J — Exports omit assumptions** (subset of BLOCKER-C, called out
separately because it is the user-facing surface). Every exported CSV must carry
the seed, policy + params, data hash, and the realized-only/no-margin/no-cashflow
caveats. *Regression test:* exported report includes seed + data hash + a
limitations block.

**LOW-K — `sim_core/` vs target `core/`.** Cosmetic. **Accept as-is** —
do not rename; the engine/UI separation is intact. (Recorded in DECISIONS.)

**LOW-L — Commission model assumes linear, symmetric per-contract round-turn**
from the trade's recorded field, with no separation between *recorded* and
*modeled/stress* commission. Acceptable for V1 (no stress yet); note for V2 so
the stress layer keeps them distinct.

**OPTIONAL-M — Stationary bootstrap test is weak.**
`test_stationary_..._without_silent_wrap` only asserts non-monotonicity. Consider
asserting the geometric reset rate and that no source index silently wraps 0.

### T1–T20 acceptance matrix (interim)
Legend: PASS / PARTIAL / FAIL / BLOCKED(by finding) / PENDING(needs Batch 1).

| T | Requirement | Status | Note |
|---|---|---|---|
| T1 | same seed+config+data ⇒ identical distribution | **FAIL** | per-path RNG not independent (BLOCKER-A); no Scenario/hash (BLOCKER-C) |
| T2 | diff seed ⇒ diff paths, same invariants | PARTIAL | only single-call seed-vs-seed tested; support-count invariants not emitted (HIGH-F) |
| T3 | no global RNG | **PASS** | uses local `default_rng`; recommend CI grep guard |
| T4 | historical replay exact order + merged stream | PARTIAL | unit-level OK; needs real-ledger golden (BLOCKER-D) |
| T5 | seasonal month matching | PASS | `test_seasonal_month_matching_over_many_seeds` |
| T6 | synchronized source-month across strategies | PASS | `test_multiple_strategies_use_synchronized_source_months` |
| T7 | within-block order preserved | PARTIAL | no explicit multi-trade intra-month fixture |
| T8 | partial months excluded; support counts | PARTIAL | exclusion tested; support counts not emitted (HIGH-F); cross-strategy union unstated |
| T9 | flat verified month contributes zero | PARTIAL | works *with* coverage; silently wrong *without* it (HIGH-F) |
| T10 | merged-stream D1 ordering incl. ties | PASS | `test_stable_deterministic_tie_ordering_uses_source_row_id` |
| T11 | fixed-contract P&L = qty × per-contract | PASS | `test_fixed_size_replay...`, `test_mes_sizing_is_not_hard_coded...` |
| T12 | deposits≠P&L; withdrawals symmetric | N/A V1 | cash flows deferred (DECISIONS D2); fine if labeled |
| T13 | equity can go ≤0, not floored | PARTIAL | not floored (good); needs explicit negative-equity fixture |
| T14 | return measures differ on deposit | N/A V1 | deferred with T12 |
| T15 | five named rates around eps | PARTIAL | taxonomy correct; eps boundary + dual-definition gap (MEDIUM-I) |
| T16 | drawdown depth/duration/recovery | PARTIAL | depth/pct only; duration/recovery missing |
| T17 | cross-path monthly percentiles | **FAIL** | sample-size inconsistency + no carry-forward (HIGH-G); month bucketing inherits BLOCKER-B |
| T18 | validation ERROR rules reject bad CSV | PENDING | needs `csv_loader.py` |
| T19 | validation WARNING rules fire, don't abort | **FAIL** | no warning/report object exists |
| T20 | regression-test convention | **FAIL** | `tests/regression/` not created |

### Required corrections before V1 acceptance (ordered)
1. BLOCKER-A: independent per-path RNG via spawned streams + ensemble driver + test.
2. BLOCKER-B: authoritative `target_month` label + boundary-safe shift + tests.
3. BLOCKER-C: `Scenario`/`ResultDistribution` + JSON round-trip + data hash; exports carry them.
4. BLOCKER-D / HIGH-E: run loader against the **real** ledger; make underlying→contract→dpp a declared mapping, not a micro default; confirm contract size with the user.
5. HIGH-F: warn on missing coverage; per-month support counts; document cross-strategy union.
6. HIGH-G: equity carry-forward + per-month path counts in percentiles.
7. MEDIUM-H/I/J: calendar-axis block contiguity; unified instrument-aware breakeven `eps`; assumptions in exports.
8. T19/T20: warning-report object + table-driven validation tests; create `tests/regression/`.

### Pending files (required to finalize this audit)
`sim_core/models.py` (Trade, `shifted_to_month`, `result_type`/eps,
`StrategyCoverage.complete_months`, AccountConfig, validation errors),
`sim_core/ingestion/csv_loader.py` (normalization, `sort_trades_chronologically`,
ERROR/WARNING rules, timezone handling, duplicate detection),
`sim_core/instruments.py` (registry), `sample_data/*.csv`,
`configs/v1_example.json`. Findings BLOCKER-D, HIGH-E, MEDIUM-I, and rows
T18/T9/T15 are provisional until these are read.

### Process note
Codex's reconciliation overwrote Review 001 content in `HANDOFF.md` on
`codex/v1-core` with its own handoff notes. Keep this review log (Review 001 +
002, newest on top) as the canonical `HANDOFF.md`; fold Codex's status notes into
`PROJECT_STATUS.md` rather than replacing the review log.

### Do NOT proceed to V2
Reinvestment, margin, exposure, prop-firm, optimization, and Streamlit remain
gated until BLOCKER-A…D and HIGH-E…G are cleared and T1/T17/T19/T20 pass.

---

## Review 001 — 2026-06-30 — Baseline & V1 specification

### What was reviewed
Entire repository at `70708d7`. Finding: the repo is empty apart from a one-line
`README.md`. There is **no Codex V1 implementation to audit.** This review
therefore sets the foundation: architecture, domain model, CSV schema,
statistical methodology, the V1 acceptance contract, and the test plan Codex
must satisfy. New docs in this commit: `ARCHITECTURE.md`, `DECISIONS.md`,
`KNOWN_LIMITATIONS.md`, `PROJECT_STATUS.md`.

### Critical findings
Nothing to fault yet — but the following are the design decisions that, if
gotten wrong, produce an engine that runs and lies. They are stated as up-front
**BLOCKER-to-V1** requirements: V1 is not accepted unless each holds.

- **BLOCKER-1 — Per-contract P&L is the resizing primitive.** The CSV must carry
  (or let us derive) **per-contract** P&L. The simulator computes dollar P&L as
  `qty_sim × pnl_per_contract`. The historical row's contract count is metadata
  only; it must never be the quantity the sim books. Without this, every sizing
  policy is wrong. (KNOWN_LIMITATIONS → Sizing.)
- **BLOCKER-2 — Synchronized seasonal bootstrap, not IID.** Default generator is
  the same-calendar-month block bootstrap with one shared `(year, month)` draw
  applied to all strategies, then chronological merge. No IID shuffle anywhere.
  (ADR-002/003.)
- **BLOCKER-3 — Win-rate taxonomy.** No bare `win_rate`. Implement the five
  named rates with explicit denominators and the configurable breakeven `eps`.
  (ARCHITECTURE §6, ADR-005.)
- **BLOCKER-4 — Contributions ≠ P&L; equity uncapped.** Deposits/withdrawals on
  a separate ledger lane; no silent equity floor; ruin recorded.
  (ADR-006/007.)
- **BLOCKER-5 — Determinism.** One master seed, spawned streams, no global RNG;
  a determinism test proves same-seed reproducibility. (ADR-008.)
- **BLOCKER-6 — Cross-path percentiles.** Monthly metrics computed across the
  ensemble, never by differencing medians. (ADR-009.)

### Required fixes (for the V1 build, in order)
1. **Domain layer** (`core/domain/`) — typed entities per ARCHITECTURE §3.
   Value objects frozen; invariants enforced in `__post_init__` (e.g.
   `exit_ts >= entry_ts`, `point_value > 0`, tz-aware timestamps).
2. **CSV schema + loader + validation** (`core/io/`) — implement the schema in
   "CSV schema" below; fail-closed on errors, collect warnings, emit a typed
   `ValidationReport`. Compute and store a content hash of normalized input for
   the `Scenario`.
3. **Block primitives + seasonal resampler** (`core/resampling/`) — month
   indexing, partial-month flagging (ADR-010), support counts, synchronized draw,
   year-boundary chaining across the horizon.
4. **Chronological merge** (`core/engine/merge.py`) — single documented ordering
   key (see D1).
5. **Fixed-contract sizing** (`core/sizing/fixed.py`) — constant `qty` per
   strategy; independent per strategy (no cross-instrument coupling).
6. **Simulator + ledger** (`core/engine/simulator.py`) — events → equity curve;
   cash flows positioned per D2; contributions tracked separately.
7. **Metrics** — drawdown (depth/duration/recovery), cross-path monthly
   percentiles, the three return measures.
8. **Tests + fixtures** — see Test plan.

### Recommended (not blocking V1)
- Use `pydantic` or plain dataclasses + a thin validator; either is fine, but
  keep `core` import-light (no pandas in the hot simulation loop — pandas is OK
  in the loader, but the simulator should iterate typed objects / numpy arrays).
- Store the `Scenario` and `ResultDistribution` as JSON with a schema version
  field from day one to avoid a painful migration later.

### CSV schema (V1 proposal — Codex to confirm/adjust against real logs)
One file per strategy (preferred) or a combined file with a `strategy_id`
column. Required vs optional:

| column | req | type | notes |
|---|---|---|---|
| `strategy_id` | req* | str | *required if combined file; else from filename/arg |
| `symbol` | req | str | maps to an `Instrument` (point_value/tick_size lookup) |
| `entry_ts` | req | datetime tz-aware | parse with explicit tz; reject naive unless tz supplied |
| `exit_ts` | req | datetime tz-aware | must be `>= entry_ts` |
| `direction` | req | enum{long,short} | needed for exposure later; validate now |
| `qty_historical` | req | int > 0 | metadata only; used to derive per-contract P&L |
| `pnl_gross` | cond | float | realized gross currency P&L for the historical qty |
| `pnl_per_contract_gross` | cond | float | provide this OR `pnl_gross`+`qty_historical` |
| `commission` | opt | float | as recorded; modeled commission is a separate stress |
| `entry_price` / `exit_price` | opt | float | enables recompute + tick checks |
| `mae` / `mfe` | opt | float | per-contract excursions; needed for V2 exposure |

Loader derives `pnl_per_contract_gross = pnl_gross / qty_historical` when only
gross is given, and validates consistency when both are present.

**Validation rules (fail-closed = ERROR; otherwise WARNING):**
- ERROR: missing required column; unparseable/naive timestamp; `exit_ts <
  entry_ts`; `qty_historical <= 0`; neither P&L form present; unknown `symbol`
  with no `Instrument` definition; mixed currencies without conversion policy.
- ERROR: `pnl_gross` and `pnl_per_contract_gross` disagree beyond `eps`.
- WARNING: partial first/last month; gaps/overlaps in time; duplicate trade
  rows; a single year backing a month-of-year (thin support); commission column
  absent; timezone differs across rows of one strategy.

### Unresolved decisions (Codex/user to confirm — implementing the default and
### recording it in DECISIONS.md is acceptable)
- **D1 — Event ordering key.** Proposed: order the merged stream by `exit_ts`
  (P&L books at close), ties → `entry_ts` → `strategy_id`. Alternative: order by
  `entry_ts`. This matters for the equity path shape and for any future
  intratrade exposure. **Need a decision before merge.py is final.**
- **D2 — Cash-flow timing.** Proposed: apply a scheduled deposit/withdrawal at
  the start of its calendar date, before that date's first settlement. Confirm.
- **D3 — Horizon vs data span.** When the simulated horizon (e.g. 24 months)
  exceeds available distinct historical months, we resample with replacement
  across years for each month-of-year. Confirm this is intended (it is the point
  of the bootstrap) and confirm whether sampling is uniform over years or
  recency-weighted (proposed: uniform; recency-weighting is a labeled option).
- **D4 — Strategy flat in a drawn month.** ADR-004 says contribute zero. Confirm
  no backfill default.
- **D5 — Instrument reference data.** Where do `point_value`/`tick_size` come
  from — a checked-in `instruments.json`, or columns in the CSV? Proposed:
  checked-in registry, overridable per scenario.
- **D6 — Currency.** V1 assumes a single account currency and rejects mixed-
  currency inputs. Confirm.

### Test plan (V1 acceptance — all must pass before the Gate to V2)
Determinism & reproducibility
- T1: same seed + config + data ⇒ bit-identical `ResultDistribution` (hash equal).
- T2: different seed ⇒ different paths but identical summary invariants
  (trade universe, support counts).
- T3: no use of global RNG (`random`/`np.random` module functions) — grep guard
  in CI.

Resampling correctness
- T4: historical replay reproduces the exact original chronological trade order
  for each strategy and for the merged stream (golden fixture).
- T5: seasonal draw only ever selects blocks whose month-of-year matches the
  target slot (property test over many seeds).
- T6: synchronization — for a multi-strategy fixture, the source `(year, month)`
  chosen in a slot is identical across all strategies for the default mode, and
  independent mode is the only mode where they differ.
- T7: within-block order preserved (no intra-month shuffle).
- T8: partial months excluded from the pool; support counts correct (ADR-010).
- T9: a strategy flat in the drawn month contributes zero trades (ADR-004).

Merge & accounting
- T10: merged stream ordering obeys D1 key exactly (including tie-breaks).
- T11: fixed-contract dollar P&L = `qty_sim × pnl_per_contract` per trade; a
  strategy with `qty=2` books exactly 2× the `qty=1` path on the same draws.
- T12: deposit increases equity and contributions but not any P&L/return-from-
  trading measure; withdrawal symmetric. (ADR-007)
- T13: equity can go ≤ 0 (ruin fixture) and is recorded, not floored. (ADR-006)
- T14: the three return measures differ correctly on a deposit-mid-path fixture
  (TWR unaffected by timing of an external deposit; simple/MWR affected).

Win-rate & metrics
- T15: the five named rates match hand-computed values on a fixture containing
  wins, losses, and breakevens straddling `eps`.
- T16: drawdown depth/duration/recovery match a hand-built equity curve.
- T17: cross-path monthly percentiles equal the ensemble percentiles, and the
  "median monthly change" ≠ difference of medians on a skewed fixture. (ADR-009)

Validation
- T18: each ERROR rule rejects a crafted bad CSV with a precise message.
- T19: each WARNING rule fires without aborting the load.

Regression
- T20: a `tests/regression/` file is created now (empty), with the convention
  that every bug found later gets a named regression test here.

### What Codex should do next
1. Confirm or push back on D1–D6 (a single reply in this file is fine).
2. Confirm the CSV schema against at least one real trade log so the column
   mapping is right before building the loader.
3. Build in the order under "Required fixes", but **stop at the Gate to V2** —
   do not start reinvestment, margin, exposure, prop, optimization, or Streamlit.
4. When V1 is ready, post a handoff entry here listing which T1–T20 pass, the
   data hash + seed of a sample run, and any deviations from this spec.

### Gate to V2 (do not pass without lead sign-off)
All of T1–T20 green, BLOCKER-1..6 satisfied, D1–D6 recorded in `DECISIONS.md`,
and `KNOWN_LIMITATIONS.md` `[SCOPE]` caveats wired into the exported report.
Only then do reinvestment / margin / exposure / prop / optimization unlock.

---

### Questions for the user (non-blocking)
- Do you have a sample trade-log CSV (NQ/ES or similar) we can pin as the
  canonical fixture? It would let Codex lock the schema (D5/D6) immediately
  rather than guessing column names.
