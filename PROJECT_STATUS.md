# Project Status

Last updated: 2026-06-30

Branch: `codex/v1-core`

## Current State

Version 1.1 hardening from Claude Review 004 has been imported and verified
locally. No Version 2 work was started.

Imported Claude head:

```text
3387d3a V1.1 hardening: close HIGH-R3-1 + MEDIUM findings; add integration harness
```

Codex then performed the real-ledger integration pass against:

```text
/Users/mariusvidziunas/Downloads/nq_es_margin_sim_master_2025_2026.csv
```

## Codex Post-Review Fixes

- Expanded `configs/nq_es_micro_contracts.yaml` to cover all 5 real strategy IDs.
- Fixed packaging discovery in `pyproject.toml` so `pip install -e .` does not
  fail on the repository's flat layout.
- Fixed canonical ingestion so the source `exit` column is treated as raw
  exit reason metadata, not as normalized `win/loss/breakeven` outcome. Outcomes
  are now classified from signed P&L dollars.
- Fixed duplicate-trade detection so explicit source identities distinguish
  legitimate repeated same-economics ledger rows, while un-identified duplicate
  semantic rows still fail closed.
- Made integration exports lazy in `sim_core.__init__` and
  `sim_core.integration.__init__` to avoid importing the real-ledger module as a
  side effect.

## Real-Ledger Integration

Command:

```bash
python3 -m sim_core.integration.real_ledger \
  --csv /Users/mariusvidziunas/Downloads/nq_es_margin_sim_master_2025_2026.csv \
  --mapping configs/nq_es_micro_contracts.yaml \
  --output reports/real_ledger_v1/
```

Result:

```text
Discovered 5 strategy_id(s): ES_EXPANDED_19_15_X2_0, ES_LATE_11_15_X2_0, ES_NORMAL_19_11_X2_0, ES_PROFIT_ONLY_NQ_INTERRUPT_NORMAL_19_11_X1_5, NQ_LOCKED_19_11_X1_5
Rows: 1150  Strategies: 5
Chronological order valid: True
All timestamps UTC: True
data_hash: 4225435567c0d659a8dd7ea57cf707cc51c843268c34c0b9fa2db5191d006c7a
Report written to reports/real_ledger_v1/integration_report.json
```

Generated report:

```text
reports/real_ledger_v1/integration_report.json
size: 31,803 bytes
scenario_hash: 991287215fa022c493d8f0d1f940c98f0b38ac1df19514072e5441f0bbff2b86
```

Strategy row counts:

```text
ES_EXPANDED_19_15_X2_0: 328
ES_LATE_11_15_X2_0: 105
ES_NORMAL_19_11_X2_0: 237
ES_PROFIT_ONLY_NQ_INTERRUPT_NORMAL_19_11_X1_5: 224
NQ_LOCKED_19_11_X1_5: 256
```

Historical replay P&L by strategy:

```text
ES_EXPANDED_19_15_X2_0: 12732.64924036323
ES_LATE_11_15_X2_0: 2593.225451870194
ES_NORMAL_19_11_X2_0: 10241.244004279119
ES_PROFIT_ONLY_NQ_INTERRUPT_NORMAL_19_11_X1_5: 7273.739271916729
NQ_LOCKED_19_11_X1_5: 17615.52307950767
```

Bootstrap smoke checks:

```text
seasonal_bootstrap: ok=True, sampled_trades=168, sampled_blocks=3, terminal_equity=105773.69073936349
moving_block: ok=True, sampled_trades=226, sampled_blocks=3, terminal_equity=117333.88044192355
stationary_block: ok=True, sampled_trades=191, sampled_blocks=3, terminal_equity=105140.14454454568
```

## Test Results

Claude regression suite:

```bash
python3 -m pytest tests/regression -q
```

Result:

```text
22 passed
```

Ordinary full suite:

```bash
python3 -m pytest
```

Result:

```text
90 passed, 1 skipped, 71 warnings
```

Real-ledger-enabled full suite:

```bash
SIM_REAL_LEDGER_PATH=/Users/mariusvidziunas/Downloads/nq_es_margin_sim_master_2025_2026.csv \
SIM_REAL_LEDGER_MAPPING=configs/nq_es_micro_contracts.yaml \
python3 -m pytest
```

Result:

```text
91 passed, 72 warnings
```

## Remaining Defects / Warnings

- No coverage metadata was supplied with the real ledger, so the integration
  report correctly warns that missing months cannot be distinguished from
  verified-flat months.
- Seasonal support is thin for calendar months July through December because the
  real ledger covers January 2025 through partial June 2026.
- Drawdown duration/recovery metrics remain unimplemented V1 follow-up work.
- Scenario config file loading remains manual/programmatic.
- The real CSV is local-only and is not committed.

## Codex Recommendation

No known V1.1 blockers remain after the real 1,150-row ledger integration.
Codex recommendation: proceed to Claude's independent final review of this new
source commit for V1 approval. Do not begin V2 until that review is complete.
