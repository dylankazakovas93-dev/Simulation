# Handoff artifacts — Review 004 V1.1 hardening

These artifacts carry the implemented V1.1 hardening work for the engine, which
lives on `codex/v1-core` (not on this governance branch). GitHub push of that
branch is still unavailable, so the work is delivered as a verifiable bundle +
patch.

- `codex-v1-core-review004.bundle` — full history; head
  `3387d3a` (`codex/v1-core`), parent `094fe61`.
- `094fe61..3387d3a.patch` — the V1.1 hardening diff (29 files, +1443/-85).

## Apply on a machine that can push

```bash
# Option A: fetch from the bundle into an existing clone
git fetch handoff_artifacts/codex-v1-core-review004.bundle codex/v1-core:codex/v1-core
git checkout codex/v1-core            # now at 3387d3a
python3 -m pytest -q                  # expect 88 passed, 1 skipped

# Option B: apply the patch on top of 094fe61
git checkout codex/v1-core            # at 094fe61
git apply handoff_artifacts/094fe61..3387d3a.patch

# Then push
git push -u origin codex/v1-core
```

## Run the real-ledger integration once the CSV is available

```bash
SIM_REAL_LEDGER_PATH=/path/to/nq_es_margin_sim_master_2025_2026.csv \
  python3 -m pytest tests/test_real_ledger_integration.py -q

# or directly:
python3 -m sim_core.integration.real_ledger \
  --csv /path/to/nq_es_margin_sim_master_2025_2026.csv \
  --mapping configs/nq_es_micro_contracts.yaml \
  --output reports/real_ledger_v1/
```

The mapping YAML uses example strategy IDs — the command prints every discovered
`strategy_id` and fails closed if any lacks a declared contract spec (ADR-011).

---

# V3 — margin & exposure (Review 009)

Implemented by the review lead (Codex offline). Additive on the V2.1 tree.

- `v3_files/` — full source of the changed/new engine files:
  - `exposure.py` (NEW) — `InstrumentMargin`, `MarginPolicy`, `apply_margin_cap`,
    `ExposureReport`, `build_exposure_report`.
  - `live_account.py` — V2.1 + optional `margin_policy=` entry-time cap
    (`margin_forced_reduction`, `initial_margin_used`, summary count).
  - `__init__.py` — exposure exports.
  - `test_exposure.py` (NEW, 4 tests), `test_live_account_v2_1.py` (6 tests).
- `v3_margin_exposure.patch` — V2.1→V3 diff of `live_account.py` plus the two new
  files as full additions.

## Verify

```bash
python3 -m pytest tests/regression -q          # 22 passed
python3 -m pytest -q                            # 122 passed, 1 skipped
python3 -m pytest tests/test_exposure.py tests/test_live_account_v2_1.py -q  # 10 passed
```

Governance: ADR-017 (declared margin, entry-time cap, fail-closed), ADR-018
(scheduled-interval exposure, realized-only). Disclosed limits in
KNOWN_LIMITATIONS: no intraday maintenance-call/liquidation, no intratrade MtM/MAE,
marginal-portfolio-contribution deferred.

---

# V4 — prop-firm engine (Review 010)

Implemented by the review lead (Codex offline). Additive; new module.

- `v4_files/` — `prop_firm.py` (NEW: `PropFirmRules`, `run_prop_account_path`,
  `run_prop_account_portfolio`, `summarize_prop_accounts`), `__init__.py` exports,
  `test_prop_firm.py` (11 tests).
- `v4_prop_firm.patch` — the two new files as full additions.

## Verify

```bash
python3 -m pytest tests/test_prop_firm.py -q     # 11 passed
python3 -m pytest tests/regression -q            # 22 passed
python3 -m pytest -q                              # 133 passed, 1 skipped
```

Governance: ADR-019 (declared prop state machine, no firm hardcoded), ADR-020
(realized `net_trader_cash` headline; notional balance ≠ wealth; realized-only
breach ⇒ breach prob is a lower bound). Disclosed limits in KNOWN_LIMITATIONS:
realized-only breach checks, greedy payout, funded-breach-terminal, fixed per-trade
sizing, copied accounts fully correlated.

---

# V5 — multi-objective optimizer (Review 011)

Implemented by the review lead (Codex offline). Additive; engine-agnostic module.

- `v5_files/` — `optimize.py` (NEW: `Objective`, `Constraint`, `Candidate`,
  `evaluate_candidates`, `apply_constraints`, `pareto_frontier`, `optimize`,
  `expected_log_growth`), `__init__.py` exports, `test_optimize.py` (9 tests).
- `v5_optimize.patch` — the two new files as full additions.

## Verify

```bash
python3 -m pytest tests/test_optimize.py -q      # 9 passed
python3 -m pytest -q                              # 142 passed, 1 skipped
```

Governance: ADR-021 (multi-objective Pareto selection; refuses single-objective by
default; frontier is the decision, scalarized rank is a labeled display aid only;
missing metrics raise; log-growth returns -inf on total-loss periods).

---

# V6 — Streamlit UI (Review 012) — LADDER COMPLETE

Implemented by the review lead (Codex offline). New `app/` package; engine/UI
strictly separate; Streamlit is an optional extra (`.[ui]`).

- `v6_files/app/` — `disclosures.py` (mandatory model-risk caveats),
  `controller.py` (pure Python bridge, no Streamlit), `streamlit_app.py` (thin
  view), `__init__.py`.
- `v6_files/test_ui_controller.py` — 7 controller tests (+1 streamlit-import test
  that skips when streamlit is absent).
- `v6_ui.patch` — all V6 files as full additions.

## Run the UI locally

```bash
pip install -e '.[ui]'
streamlit run app/streamlit_app.py
```

## Verify (headless — no streamlit needed)

```bash
python3 -m pytest tests/test_ui_controller.py -q   # 7 passed, 1 skipped
python3 -m py_compile app/streamlit_app.py         # view parses
python3 -m pytest -q                                # 149 passed, 2 skipped
```

Governance: ADR-022 (engine/UI separation; controller has no Streamlit import;
disclosures attached to every result; notional prop balance demoted).

## Milestone ladder — COMPLETE
V1 ingestion/resampling/ensemble · V2/V2.1 live account · V3 margin/exposure ·
V4 prop-firm cash economics · V5 multi-objective optimizer · V6 UI. Recommended
next: an INDEPENDENT review of the implementer==reviewer milestones (V3–V6).

---

# Review 013 (independent audit + 2 fixes) & Review 014 (prop enhancements)

Implemented by the review lead (Codex offline).

**Review 013 — fixes**
- Prop `_maybe_payout`: a withdrawal was miscounted as a daily loss → fixed by
  lowering the day's opening baseline on payout.
- UI `run_ensemble`: percentile fan was always empty → controller now defaults
  `start_month` + `horizon_months`.

**Review 014 — features**
- `PropFirmRules.payout_mode` = "standard" | "daily".
- `run_prop_account_path(initial_phase="funded")`.
- `summarize_evaluation_stage` (pass rate, time-to-pass, resets).
- `funded_window_analysis` (blow rate / survival / payout economics over
  2/4/6/8/12-month windows from random historical starts).
- UI prop tab: two configurable systems (A standard / B daily) + eval-stage stats +
  funded-window table.

- `review013_014_files/` — full source of the changed files (`prop_firm.py`,
  `__init__.py`, `app/controller.py`, `app/streamlit_app.py`, tests).

## Verify
```bash
python3 -m pytest tests/test_prop_firm.py tests/test_ui_controller.py -q  # 18 + 11
python3 -m pytest -q                                                       # 159 passed, 2 skipped
```

Governance: ADR-023; KNOWN_LIMITATIONS updated (withdrawal≠daily-loss guard;
overlapping-window caveat). Reviews 013/014 in HANDOFF.
