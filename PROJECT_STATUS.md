# PROJECT_STATUS

_Last updated: 2026-07-02 — Architecture/Model-Risk lead_

This file tracks **current state and implementation progress**. The full review
history lives in `HANDOFF.md` (Reviews 001–012, newest on top) and the design
decisions in `DECISIONS.md` (ADR-001…022). Do not overwrite that history here.

## Governance note (important)

Codex (the original implementer) went permanently offline mid-project. From the V2.1
correction pass onward, the review lead **implemented** the milestones directly, so
Reviews 008–012 are implementer == reviewer. Acceptance for those rests on objective
tests + the charter's requirements, not self-assessment. **An independent review of
V3–V6 is recommended** before this is trusted for real capital decisions.

## Milestone ladder — COMPLETE

| Milestone | Scope | Status | Review / ADRs |
|-----------|-------|--------|---------------|
| V1 | Ingestion → validation → synchronized seasonal/block resampling → fixed-contract ensemble → monthly percentiles/drawdown → provenance | ✅ APPROVED | R002–R004; ADR-001…016 |
| V2 / V2.1 | Live brokerage account: cash flows (deposit/withdrawal), sizing policies (fixed contract/dollar-risk/percent-equity + reinvestment), TWR vs MWR/XIRR, absorbing-barrier ruin | ✅ APPROVED | R005–R008 |
| V3 | Declared per-contract margin (entry-time cap, fail-closed) + exposure measurement (scheduled-interval sweep) | ✅ DONE | R009; ADR-017/018 |
| V4 | Prop-firm engine: evaluation→funded→retired state machine, trailing/EOD drawdown, daily-loss, min days, consistency, payout split/cap/buffer, resets, multi-account; realized `net_trader_cash` economics | ✅ DONE | R010; ADR-019/020 |
| V5 | Multi-objective optimizer: declared objectives + constraints, Pareto frontier, refuses single-objective by default | ✅ DONE | R011; ADR-021 |
| V6 | Streamlit UI on the headless core: pure controller + thin view + mandatory disclosure registry | ✅ DONE | R012; ADR-022 |

## Test status (whole suite)

From the reconstructed engine tree: **`pytest -q` = 149 passed, 2 skipped**;
**`tests/regression -q` = 22 passed**. New since V2.1: 4 (V3) + 11 (V4) + 9 (V5) +
7 (V6 controller) tests. The Streamlit view is byte-compiled; its import test skips
where streamlit is absent.

## Branches

- `claude/portfolio-sim-architecture-yq361s` (governance): `ARCHITECTURE.md`,
  `DECISIONS.md`, `KNOWN_LIMITATIONS.md`, `HANDOFF.md`, `PROJECT_STATUS.md`, and
  `handoff_artifacts/` (per-milestone source + patches + verification steps). The
  engine source is delivered as files/patches under `handoff_artifacts/` because the
  `codex/v1-core` implementation branch was never pushable from this environment.
- Engine tree (reconstructed and verified locally): `sim_core/` (V1–V5) + `app/`
  (V6), with `tests/`.

## Recommended next steps

1. **Independent review** of the implementer==reviewer milestones (V3–V6).
2. Optional depth items already logged in `KNOWN_LIMITATIONS.md`: intraday
   maintenance-margin call/liquidation (V3.1), MAE-based intratrade drawdown,
   continuous optimizer search (CMA-ES/Bayesian), in-UI coverage declaration,
   guided optimizer candidate-builder, and alternative (non-greedy) prop payout
   timing.
