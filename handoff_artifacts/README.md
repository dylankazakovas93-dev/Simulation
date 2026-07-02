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
