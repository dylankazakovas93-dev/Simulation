# HANDOFF

Review log between the Architecture/Model-Risk lead and Codex. Newest review on
top. Findings are classified `BLOCKER` / `HIGH` / `MEDIUM` / `LOW` / `OPTIONAL`.

---

## Review 003 — 2026-06-30 — Re-audit of `codex/v1-core` @ 094fe61 (FINAL, independently verified)

### Independent verification performed
Cloned the supplied bundle (`codex-v1-core-after-review002.bundle`), checked out
`codex/v1-core`, confirmed `HEAD == 094fe619be4ef2bf5ad711efbde1d882eec950fc`,
history intact (`70708d7 → b129fe5 → aab18a5 → fe408db → d5b3fcd → 094fe61`).
My `tests/regression/` suite on the branch is **byte-identical** to the `28b099e`
originals (Codex did not alter the tests). Independent runs here (numpy 2.4.6 /
pandas 3.0.3):
- `pytest tests/regression -q` → **22 passed**
- `pytest -q` → **58 passed**

Matches Codex's reported counts. I read the `d5b3fcd→094fe61` implementation,
not just the counts.

### Verdict: **CONDITIONAL APPROVAL** — five Review-002 blockers genuinely fixed; NOT full production acceptance
The blocker fixes are **general, not fixture-gamed** (V-7 clean). One HIGH
(ADR-011 silent instrument inference) remains, plus MEDIUM carryovers, and the
**real 1,150-row ledger integration is still OPEN**, so V1 is not production-
accepted yet.

### V-1…V-7 adjudication
| Item | Verdict | Evidence |
|---|---|---|
| V-1 RNG quality | **GENUINE** | `_rng_for_path = default_rng(SeedSequence(master_seed).spawn(path_index+1)[path_index])` — positional spawn keys ⇒ independent + reproducible, NOT `seed+path_index`. `test_ensemble_paths_are_independent_and_reproducible` checks reproducibility, cross-path divergence, and seed sensitivity generally. |
| V-2 data hash | **GENUINE (wiring caveat)** | `hash_trades` = real SHA-256 over sorted trade fields; changes with data; used via `_scenario(hash_trades(trades))` end-to-end and asserted in `test_batch_export_includes_result_distribution_provenance`. Caveat M-R3-A. |
| V-3 manifest content | **GENUINE** | `KNOWN_V1_LIMITATIONS` (realized-only P&L; no margin/prop/cash-flow; month-clamp) wired into every `ResultDistribution`; manifest carries seed/policy/params/hash/limitations. |
| V-4 silent inference removed | **NOT MET (HIGH)** | `normalize_canonical_margin_frame` still defaults to `_infer_strategy_specs(frame, DEFAULT_INSTRUMENT_REGISTRY)` ⇒ `NQ→MNQ`/`ES→MES` inferred from the symbol when no explicit mapping is passed. ADR-011 forbids silent inference. Blank `dpp` now correctly raises, and the `dpp`-vs-spec check catches wrong inferences, but explicit declaration is not required. |
| V-5 carry-forward | **GENUINE** | `monthly_equity_percentiles` builds a full month grid and carries each path's equity forward (initial-equity before first trade), constant denominator = n_paths. `test_monthly_percentiles_carry_forward_all_paths` → `[105,105,120]` proves the general case incl. pre-first-trade months. |
| V-6 clamp | **GENUINE** | `shifted_to_month` clamps `min(target_start+offset, target_end)`, tz-consistent; handles leap-day and cross-source-boundary trades (Codex tests + my B-3 suite). |
| V-6 naive rejection | **PARTIAL (MEDIUM)** | Rejection fires only when `source_timezone=None` is passed explicitly; the default is `source_timezone="UTC"`, so a naive ledger is silently localized to UTC (warning only) by default — inverse of "reject unless declared." |
| V-7 fixture-gaming | **CLEAN** | No hardcoded fixture dates / `if month==…` branches; all fixes are parametric (clamp via `min`, RNG via spawn, carry-forward via full grid, hash via field digest). |

### Remaining findings
- **HIGH-R3-1 (V-4 / ADR-011):** make the contract mapping explicit-or-error.
  `load_canonical_margin_csv` / `normalize_canonical_margin_frame` must require
  `contract_specs_by_strategy` (or at minimum warn loudly) instead of silently
  inferring from `DEFAULT_INSTRUMENT_REGISTRY`. Mitigation today: blank or
  contradictory `dpp` fails closed, so a *wrong* inference is usually caught — but
  governance (ADR-011) requires no silent inference. *Regression:*
  `load_canonical_margin_csv(path)` with no declared mapping raises (or warns)
  rather than silently mapping NQ→MNQ.
- **MEDIUM-R3-A (V-2 provenance integrity):** `run_simulation_ensemble` trusts the
  caller-supplied `scenario.input_data_hash`; it should compute
  `hash_trades(trades)` and verify/populate it, so a stale/empty/mismatched hash
  cannot flow into the manifest unchecked. *Regression:* ensemble with a wrong
  `input_data_hash` raises or overwrites with the computed digest.
- **MEDIUM-R3-B (V-6 naive default):** flip `normalize_trade_frame`'s default to
  `source_timezone=None` so genuinely ambiguous naive input is rejected unless a tz
  is declared.
- **MEDIUM-R3-C (H-2 incomplete):** the "coverage absent" `RuntimeWarning` fires
  only in `SameCalendarMonthBootstrap`, not Moving/Stationary; `_sorted_source_months`
  still unions partial/complete across strategies and gap-compacts the month axis;
  per-month support counts are still not emitted. Centralize the warning; add
  support counts; document the cross-strategy union.
- **MEDIUM-R3-D (carryover Review-002 MEDIUM-2):** breakeven `eps` is a single
  `1e-9` (now consistent across `classify_result`/`_normalize_result`/taxonomy) but
  still sub-tick and not instrument-aware (ADR-005 wanted ~½ tick).
- **MEDIUM-R3-E (carryover Review-002 MEDIUM-1):** Moving/Stationary blocks still
  operate on the gap-compacted month list, fabricating contiguity across data gaps.
- **LOW-R3-F (clamp clustering):** clamped overflow trades pile onto the target
  month's final instant (correct for month attribution, distorts intra-month
  timing). Disclosed in `KNOWN_V1_LIMITATIONS` — acceptable; note for V2.
- **LOW-R3-G (perf):** `_rng_for_path` spawns `path_index+1` children per call
  (O(n²) across an ensemble); spawn once per ensemble instead.

### Status of Review-002 blockers
B-1 ✓ · B-2 ✓ · B-3 ✓ · B-4 ✓ · H-1 partial (blank-`dpp` ✓; ADR-011 inference ✗ =
HIGH-R3-1) · H-2 partial (warning added; support counts / union open) · H-3 ✓.

### Still OPEN before full V1 production acceptance
1. **Real 1,150-row canonical ledger integration** — synthetic fixture only; must
   load the real upload under an **explicit** declared mapping and complete
   historical + seasonal replay.
2. **HIGH-R3-1** (ADR-011 silent inference).
3. The MEDIUM items above (recommended before V2; not all strictly blocking).

### Recommendation
Accept the blocker fixes as genuine and well-tested. Authorize Codex to close
HIGH-R3-1 and the MEDIUM items and to run the real-ledger integration. Do **not**
open V2 (reinvestment / margin / exposure / prop / optimization / Streamlit) until
the real-ledger run passes and HIGH-R3-1 is closed.

---

## Review 002 — 2026-06-30 — Audit of `codex/v1-core` @ fe408db (FINAL)

### Executive verdict: **CONDITIONAL APPROVAL** — V1 milestone NOT acceptable yet
Foundation is genuinely strong and must be preserved: typed frozen domain, **no
IID**, synchronized seasonal default, correct outcome taxonomy with explicit
denominators, deterministic tie-ordering incl. `source_row_id`, equity never
floored, **no global RNG state**, `mult` kept as metadata, and a `dpp`-vs-registry
cross-check that *fails closed* on mispriced rows. However, five blockers and
several HIGH defects remain; two were reproduced directly against pandas
(mirroring `models.py`). All Batch 1–3 files received; this verdict is final
pending the fixes below and a real-ledger run.

### Scope reviewed (complete)
All of `sim_core/` (`models.py`, `instruments.py`, `ingestion/csv_loader.py`,
`resampling/policies.py`, `execution/replay.py`, `metrics/reports.py`,
`exports.py`), the canonical fixture, `pyproject.toml`, all 25 tests.

### Reproduced defects (run against pandas, mirroring `Trade.shifted_to_month`)
```
(1) tz-aware ledger: pd.Timestamp('2025-01-06T14:35Z').to_period('M').to_timestamp()
    is tz-NAIVE (tz silently dropped, UserWarning); entry_time - month_start
    -> TypeError: Cannot subtract tz-naive and tz-aware datetime-like objects
(2) overflow: Jan-31 shifted to a Feb target lands 2025-03-03; to an Apr target
    lands 2025-05-01; seasonal Feb-29(2024) -> Feb target(2025) lands 2025-03-01
```

### Findings

**BLOCKER-1 — The real (UTC) ledger crashes every bootstrap.**
`Trade.shifted_to_month` builds `source_start = self.source_month.to_timestamp()`
(tz-**naive**) and subtracts it from `self.entry_time`. The canonical/real ledger
is tz-aware UTC (`...Z`); `to_period('M')` silently drops the tz, then the
subtraction raises `TypeError`. So historical replay works but
seasonal/moving/stationary resampling of the **real data is impossible** — the
core product path is untested against the only data that matters, because every
resampling test uses tz-naive fixtures. *Fix:* normalize to one tz at ingest
(store tz-aware UTC) and make `shifted_to_month` tz-consistent. *Regression:*
resample the canonical UTC fixture through each bootstrap; assert no raise and
correct target months.

**BLOCKER-2 — Path ensemble collapses: `path_index` never reaches the RNG.**
Every `sample()` does `rng = np.random.default_rng(seed)`; `path_index` is only a
`SampledBlock` label. `sample(seed=s, path_index=0)` and `(…, path_index=1)` are
**identical**. `monthly_equity_percentiles` / `ruin_probability` /
`summarize_paths` consume an ensemble, but nothing builds it with independent
per-path randomness, and the inert `path_index` invites the wrong usage. The
cross-path distribution is degenerate unless the caller hand-varies `seed`.
*Fix:* `SeedSequence(master).spawn(n_paths)` → one `Generator` per path + a tested
batch runner (ADR-008). *Regression:* an N-path ensemble at one master seed has
non-identical sampled months and reproduces bit-for-bit.

**BLOCKER-3 — Calendar-month timestamp shifting leaks trades across month
boundaries.** Confirmed in code + repro (2). `shifted_to_month` adds the source
offset to the target month-start; any day-of-month beyond the target month's
length overflows into the next month, and `monthly_equity_percentiles` buckets on
that overflowed timestamp. General for moving/stationary; the seasonal default
still leaks **Feb-29 → March**. *Fix:* carry the block's authoritative
`target_month` on each shifted trade and bucket on it; clamp/scale so a trade
cannot cross the target boundary. *Regression:* the repro cases stay in target.

**BLOCKER-4 — No `Scenario`/`ResultDistribution`/serialization/data hash.**
No serializable run config, no result-with-embedded-assumptions, no input hash;
`exports.py` writes CSVs with no seed/policy/params/hash/limitations. A result
can't be tied to what produced it (violates ARCHITECTURE principles 4–5,
BLOCKER-5). *Fix:* serializable `Scenario` (master seed, policy+params, account,
portfolio, coverage, data hash) + `ResultDistribution` embedding it; exports
carry both. *Regression:* `Scenario` JSON round-trips; rerun reproduces identical
distribution; export contains seed + hash.

**BLOCKER-5 — Real-ledger integration not performed (and currently blocked by
BLOCKER-1).** Fixture is synthetic (`dpp=2.0`). The loader's `dpp`-vs-registry
check rejects mismatches (good), but the real file has never been loaded or
resampled, and BLOCKER-1 guarantees resampling it would crash. Load + resample
the real upload after BLOCKER-1.

**HIGH-1 — Default registry hard-codes micros; blank `dpp` silently falls back to
micro.** `DEFAULT_INSTRUMENT_REGISTRY` maps `NQ→MNQ $2`, `ES→MES $5`. Credit:
a present, disagreeing `dpp` *raises* (no silent 10× error). But (a) the default
presumes micros and will reject a full-size NQ/ES ledger until edited; (b) in
`normalize_canonical_margin_frame`, a blank/NaN `dpp` cell does
`dpp = spec.dollars_per_point` — silently micro, no warning. underlying→contract→
`dollars_per_point` must be a **declared per-scenario** input, not a built-in
micro default. *Confirm with the user which contract the real ledger represents.*
*Regression:* full-size dpp loads when declared full-size; blank dpp warns.

**HIGH-2 — Flat months silently dropped without `coverage`; coverage fully
trusted and unioned across strategies.** `_sorted_source_months` without coverage
= only months with trades, so genuine flat months can never be drawn (bias toward
active months; no warning). With coverage, `complete_months()` enumerates the
whole declared span — including no-trade and out-of-data months — as
verified-flat with no validation against actual gaps; and
`partial_months`/`complete_months()` are **unioned across all strategies**, so a
month partial for *any* strategy is excluded for *all*. *Fix:* warn when coverage
absent; validate coverage vs observed data; document the union; emit per-month
support counts (ADR-010). *Regression:* missing-coverage run warns; flat month
sampleable only via coverage; partial-for-one exclusion reported.

**HIGH-3 — Cross-path percentiles use inconsistent unlabeled denominators; no
equity carry-forward.** `monthly_equity_percentiles` records a month-end equity
for a path only if it settled that month, so a sparse month's percentile is over
fewer paths than a dense month's — different, unlabeled denominators — and
inherits BLOCKER-3's bucketing. *Fix:* carry each path's last equity forward
across the horizon; report per-month path count. (Monthly *change* isn't reported
at all, so the ADR-009 difference-of-medians trap isn't triggered yet — keep it
that way when change is added.) *Regression:* 3-path ensemble, one path flat in
month 2, yields a month-2 percentile over all 3 paths.

**HIGH-4 — Naive timestamps accepted silently; mixed tz across rows possible.**
`_parse_timestamp_column` uses `to_datetime(..., format="mixed")`, accepting
tz-naive strings and returning tz-naive Trades, while the canonical path yields
tz-aware — so the engine mixes tz-aware/naive Trades by source, and the schema's
"reject naive unless tz supplied" rule is unenforced. Input-side of BLOCKER-1.
*Fix:* enforce one tz policy at ingest; reject or explicitly localize naive.
*Regression:* naive input without declared tz is rejected (or localized).

**MEDIUM-1 — Moving/stationary blocks treat the gap-compacted month list as
contiguous.** `source_months` is the sorted set of months-with-trades;
`source_months[start:start+k]` can splice non-adjacent calendar months (`[Jan,
Apr]`) into one "contiguous" block, fabricating adjacency across a regime gap.
*Fix:* define contiguity on the calendar axis (incl. coverage flat months) or warn
that blocks are over observed-trade months only. *Regression:* a missing middle
month isn't placed in one block without a flag.

**MEDIUM-2 — Breakeven `eps` is sub-tick, not instrument-aware; two un-unified
knobs.** `classify_result` (sets `result_type`) and `trade_outcome_taxonomy` both
default `tolerance=1e-9` — currently consistent, but independent params with no
shared config, and `1e-9` is far below a tick (ADR-005 wanted ~½ tick,
per-instrument). A custom taxonomy tolerance would diverge from `result_type`.
*Fix:* one shared, configurable, instrument-aware `eps`; agreement by
construction. *Regression:* a +0.4-tick trade classifies identically in both.

**MEDIUM-3 — Exports omit assumptions** (subset of BLOCKER-4; user-facing
surface). Each export must carry seed, policy+params, data hash, and the
realized-only/no-margin/no-cashflow caveats.

**LOW-1 — `sim_core/` vs target `core/`:** cosmetic. **Accept as-is; do not
rename.**

**LOW-2 — Commission model** assumes linear, symmetric per-contract round-turn
from the recorded field, with no recorded-vs-modeled separation. Fine for V1;
keep distinct when the stress layer lands.

**LOW-3 — Duplicate trades are hard ERRORs.** The dedup key `(strategy,
instrument, entry, exit, pnl)` raises on collision, dropping two genuinely
distinct trades that share timestamps and P&L (plausible for fast scalping).
Prefer WARN + keep, or fold `source_row_id` into identity.

**OPTIONAL-1 — Stationary-bootstrap test** only asserts non-monotonicity; assert
the geometric reset rate and that no source index silently wraps.

### T1–T20 acceptance matrix (final)
| T | Requirement | Status | Note |
|---|---|---|---|
| T1 | same seed+config+data ⇒ identical distribution | **FAIL** | per-path RNG not independent (B-2); no Scenario/hash (B-4) |
| T2 | diff seed ⇒ diff paths, same invariants | PARTIAL | single-call only; support invariants not emitted (H-2) |
| T3 | no global RNG | **PASS** | local `default_rng`; add CI grep guard |
| T4 | historical replay exact order + merged stream | PARTIAL | unit OK; real-ledger golden blocked by B-1 |
| T5 | seasonal month matching | PASS | over many seeds |
| T6 | synchronized source-month across strategies | PASS | |
| T7 | within-block order preserved | PARTIAL | no multi-trade intra-month fixture |
| T8 | partial months excluded; support counts | PARTIAL | exclusion ok; counts missing; union unstated (H-2) |
| T9 | flat verified month contributes zero | PARTIAL | ok with coverage; silently wrong without (H-2) |
| T10 | merged-stream D1 ordering incl. ties | PASS | source_row_id tie-break |
| T11 | fixed-contract P&L = qty × per-contract | PASS | incl. MES≠MNQ test |
| T12 | deposits≠P&L; withdrawals symmetric | N/A V1 | cash flows deferred (D2) |
| T13 | equity ≤0, not floored | PARTIAL | not floored (good); add negative-equity fixture |
| T14 | return measures differ on deposit | N/A V1 | deferred |
| T15 | five named rates around eps | PARTIAL | taxonomy correct; eps sub-tick/dual-knob (M-2) |
| T16 | drawdown depth/duration/recovery | PARTIAL | depth/pct only |
| T17 | cross-path monthly percentiles | **FAIL** | denominator inconsistency + no carry-forward (H-3); bucketing inherits B-3 |
| T18 | validation ERROR rules | PARTIAL (good) | broad ERROR coverage; make table-driven |
| T19 | validation WARNING rules fire, don't abort | **FAIL** | no warning tier — all issues fatal |
| T20 | regression-test convention | **FAIL** | `tests/regression/` not created |

### Required corrections before V1 acceptance (ordered)
1. **B-1 + H-4:** one tz policy at ingest (UTC tz-aware), tz-consistent
   `shifted_to_month`; resample the canonical fixture in tests.
2. **B-3:** authoritative `target_month` label + boundary-safe shift.
3. **B-2:** spawned per-path streams + tested batch runner.
4. **B-4 / M-3:** `Scenario`/`ResultDistribution` + JSON + data hash; exports carry them.
5. **B-5 / H-1:** run the real ledger; make underlying→contract→dpp declared, not
   a micro default; warn on blank dpp; confirm contract size with the user.
6. **H-2:** coverage warning + validation + support counts + documented union.
7. **H-3:** equity carry-forward + per-month path counts.
8. **M-1/M-2, T19/T20:** calendar-axis contiguity; unified instrument-aware `eps`;
   a warning-report tier; create `tests/regression/`.

### Process note
Codex's reconciliation overwrote Review 001 in `HANDOFF.md` on `codex/v1-core`
with its own notes. Keep this review log (Review 001 + 002, newest on top)
canonical; fold Codex's status into `PROJECT_STATUS.md`, not over the review log.

### Regression suite (added 2026-06-30) — `tests/regression/`
Red-by-design acceptance targets for the findings above (run on `codex/v1-core`:
`python3 -m pytest tests/regression -q`). RED = fails today, Codex must fix;
GUARD = passes today, must not regress. Contract for not-yet-built APIs
(`run_path_ensemble`, `Scenario`, `ResultDistribution`, manifest export) is in
`tests/regression/README.md`.

| File · test | Finding | Type | Expected failure today |
|---|---|---|---|
| `test_b1_timezone::test_utc_timestamps_preserved_on_ingest` | H-4 | GUARD | passes (ingest keeps tz) |
| `test_b1_timezone::test_utc_trades_resample_without_tz_error` | B-1 | RED | `TypeError: Cannot subtract tz-naive and tz-aware` in `shifted_to_month` |
| `test_b1_timezone::test_canonical_ledger_historical_and_seasonal_complete` | B-1/B-5 | RED | same `TypeError` at the seasonal step (historical step passes) |
| `test_b3_month_overflow::test_shift_never_leaves_target_month` (×4) | B-3 | RED | shifted entry/exit land in the month after target (e.g. Jan-31→Feb gives 2025-03) |
| `test_b2_rng_streams::test_existing_sample_path_index_is_currently_inert` | B-2 | RED | assertion: all 8 paths identical (`path_index` inert) |
| `test_b2_rng_streams::test_run_path_ensemble_gives_independent_streams` | B-2 | RED | `ModuleNotFoundError: sim_core.execution.ensemble` |
| `test_b2_rng_streams::test_same_master_seed_reproduces_full_ensemble` | B-2/T1 | RED | `ModuleNotFoundError: sim_core.execution.ensemble` |
| `test_b2_rng_streams::test_path_indices_produce_non_identical_valid_paths` | B-2 | RED | `ModuleNotFoundError: sim_core.execution.ensemble` |
| `test_b4_scenario_and_exports::test_scenario_round_trips` | B-4 | RED | `ImportError: cannot import name 'Scenario'` |
| `test_b4_scenario_and_exports::test_result_distribution_embeds_and_round_trips_scenario` | B-4 | RED | `ImportError: cannot import name 'ResultDistribution'` |
| `test_b4_scenario_and_exports::test_export_manifest_contains_assumptions` | B-4/M-3 | RED | `ImportError` (Scenario/ResultDistribution); no `run_manifest.json` emitted |
| `test_h1_instrument_mapping::test_blank_dpp_fails_validation` | H-1 | RED | DID NOT RAISE — blank `dpp` silently defaults to micro |
| `test_h1_instrument_mapping::test_declared_micro_mapping_passes` | H-1 | GUARD | passes (declared NQ→MNQ $2 / ES→MES $5) |
| `test_h1_instrument_mapping::test_dpp_disagreeing_with_declaration_is_rejected` | H-1 | GUARD | passes (dpp mismatch fails closed) |
| `test_h2_coverage::test_verified_flat_month_is_sampleable` | H-2 | GUARD | passes (coverage flat month drawable) |
| `test_h2_coverage::test_missing_month_is_not_treated_as_flat` | H-2 | GUARD | passes (missing month raises, not fabricated) |
| `test_h2_coverage::test_partial_month_excluded_even_when_it_has_trades` | H-2 | GUARD | passes (declared-partial excluded) |
| `test_h2_coverage::test_missing_coverage_emits_warning` | H-2 | RED | no warning emitted when coverage absent |
| `test_h3_percentile_carryforward::test_monthly_percentiles_carry_forward_flat_paths` | H-3 | RED | Feb p50 == 1150 (flat path dropped); expected 1060 with carry-forward |

Note: on the governance branch `sim_core` is absent, so the suite errors at
collection here by design — it travels with the implementation on `codex/v1-core`.

### Do NOT proceed to V2
Reinvestment, margin, exposure, prop-firm, optimization, and Streamlit stay gated
until B-1…B-5 and H-1…H-4 are cleared and T1/T17/T19/T20 pass.

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
