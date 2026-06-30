# HANDOFF

Review log between the Architecture/Model-Risk lead and Codex. Newest review on
top. Findings are classified `BLOCKER` / `HIGH` / `MEDIUM` / `LOW` / `OPTIONAL`.

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
