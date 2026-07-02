# Project Status

Last updated: 2026-07-02

Branch: `codex/v2-live-account`

Approved V1 base: `8a81536e6335b5b4250b3ce9658fef3fe51af561`

First V2 milestone: `ecf5502291fb4ec554bc83a175137d2008176ffc`

Claude Review 007 verdict: CONDITIONAL APPROVAL for the first V2
live-account milestone.

## Current State

Version 2.1 is a narrow correction pass on the live-account layer. It addresses
only the four Review 007 findings and does not add margin, exposure,
prop-firm rules, optimization, or UI.

V1 behavior remains isolated and protected by `tests/regression/`. The V2
live-account implementation remains additive in `sim_core/live_account.py`.

## Review 007 Corrections

- Account-equity drawdown and flow-neutral trading drawdown are reported as
  separate metric families. Trading drawdown is the default risk drawdown for
  future sizing, margin, exposure, and optimization constraints.
- Operational ruin is an absorbing `equity <= threshold` path barrier. It
  records first timestamp, trigger event, and minimum equity, then remains
  ruined after recovery.
- Return metrics are explicitly named as `period_twr`,
  `period_money_weighted_return`, and `annualized_xirr`. Short-horizon
  annualization emits a warning, and unavailable XIRR cases return typed
  status fields.
- Live-account results include deterministic provenance hashes for inputs,
  configuration, cash flows, sizing, contracts, ruin settings, reinvestment,
  and the serialized result payload. Verification detects changed trades,
  deposits/withdrawals, sizing, reinvestment, ruin threshold, contract specs,
  and result payloads.

## Files Changed

- `sim_core/live_account.py`
- `sim_core/__init__.py`
- `tests/test_live_account.py`
- `ARCHITECTURE.md`
- `DECISIONS.md`
- `HANDOFF.md`
- `KNOWN_LIMITATIONS.md`
- `PROJECT_STATUS.md`
- `V2_METRICS.md`

## Tests Added

`tests/test_live_account.py` now includes Review 007 regression coverage for:

- withdrawal affecting account drawdown but not trading drawdown
- operational ruin breach followed by recovery remaining ruined
- exact-threshold operational ruin touch
- initial-threshold operational ruin touch
- ending-below-threshold operational ruin
- never-touching-threshold non-ruin
- stop-trading-after-ruin policy
- no-flow period MWR equaling period TWR
- annualized XIRR as a separate field from period return
- deposit timing changing XIRR while leaving TWR unchanged
- short-horizon annualization warnings
- unavailable XIRR status for non-unique sign patterns
- deterministic provenance verification and mismatch detection

## Test Results

Final V2.1 verification was run with:

```bash
python3 -m pytest tests/regression -q
python3 -m pytest -q
```

The exact final counts are recorded in the handoff and final report after the
verification run:

- Regression suite: `22 passed`
- Full suite: `122 passed, 1 skipped`

## Sample Artifacts

Review 007 sample JSON artifacts are exported under:

`/Users/mariusvidziunas/Documents/Codex/2026-06-30/x/outputs/v2_1_review007_samples/`

The sample set covers account-vs-trading drawdown, absorbing operational ruin,
period return naming, annualized XIRR warnings, unchanged provenance, and
provenance failures after deposit, sizing, and ruin-threshold changes.

Filenames:

- `sample_01_withdrawal_account_vs_trading_drawdown.json`
- `sample_02_mid_path_ruin_recovery.json`
- `sample_03_no_flow_period_mwr_equals_twr.json`
- `sample_04_annualized_xirr_labeled_and_warned.json`
- `sample_05_provenance_verification_passes.json`
- `sample_06_provenance_fails_changed_deposit.json`
- `sample_07_provenance_fails_changed_sizing.json`
- `sample_08_provenance_fails_changed_ruin_threshold.json`
- `sample_index.json`

## Remaining Limitations

- No prop-firm rules.
- No optimizer.
- No Streamlit/UI.
- No full margin/exposure model.
- No portfolio-level cross-strategy constraints.
- No intratrade mark-to-market; drawdown is based on realized account events.
- Period money-weighted return is available for supported cash-flow patterns;
  invalid or non-unique annualized XIRR cases are reported as unavailable.

## Recommendation

Stop after this V2.1 correction pass and send the branch plus sample artifacts
to Claude for Review 008.
