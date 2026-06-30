# PROJECT_STATUS

_Last updated: 2026-06-30 — Architecture/Model-Risk lead_

This file tracks **current state and implementation progress**. The review
history lives in `HANDOFF.md` (Reviews 001 + 002) and must not be overwritten
here or there.

## Branches

- `claude/portfolio-sim-architecture-yq361s` (governance): the spec/review set —
  `ARCHITECTURE.md`, `DECISIONS.md`, `KNOWN_LIMITATIONS.md`, `HANDOFF.md`, and the
  `tests/regression/` acceptance suite. Does **not** contain `sim_core`.
- `codex/v1-core` @ `fe408db` (implementation): Codex's V1 core (`sim_core/`,
  fixtures, 25 passing tests). Audited in `HANDOFF.md` → Review 002.

## V1 audit status: CONDITIONAL APPROVAL — milestone NOT yet acceptable

Review 002 (see `HANDOFF.md`) found 5 BLOCKERs, 4 HIGH, 3 MEDIUM. Two were
reproduced directly against pandas (tz-aware crash in `shifted_to_month`;
calendar-month overflow). The `tests/regression/` suite encodes every blocker
and HIGH as an executable target.

### Open blockers (must clear before V2)
1. B-1/H-4 — UTC ledger crashes resampling (tz-naive vs tz-aware); enforce one tz policy.
2. B-3 — month-shift overflow leaks trades across calendar boundaries.
3. B-2 — `path_index` inert ⇒ degenerate ensemble; add spawned per-path streams + batch runner.
4. B-4/M-3 — no `Scenario`/`ResultDistribution`/serialization/data hash; exports carry no assumptions.
5. B-5/H-1 — run the real ledger; contract mapping must be declared (ADR-011), not a micro default.

Plus H-2 (coverage warning + support counts), H-3 (percentile carry-forward),
M-1/M-2 (block contiguity; unified instrument-aware breakeven `eps`), T19/T20
(warning tier; `tests/regression/` convention — now seeded).

## Regression suite (target for Codex)

`tests/regression/` — run on `codex/v1-core` from repo root:
`python3 -m pytest tests/regression -q`. RED tests must be made to pass by fixing
the implementation; GUARD tests must keep passing. Index in `HANDOFF.md` →
Review 002 → "Regression suite".

## Next action for Codex

Fix in the order under "Open blockers". Record each fix's design choice in
`DECISIONS.md`; report which RED tests turn green in a new `HANDOFF.md` entry.
Do **not** start reinvestment / margin / exposure / prop / optimization /
Streamlit until B-1…B-5 and H-1…H-4 are cleared and T1/T17/T19/T20 pass.
