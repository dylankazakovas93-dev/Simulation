# PROJECT_STATUS

_Last updated: 2026-06-30 — Architecture/Model-Risk lead_

## Current state

The repository is a blank slate.

- Contents: `README.md` (one line) only.
- Git: single `Initial commit` (`70708d7`); designated branch
  `claude/portfolio-sim-architecture-yq361s` is identical to `main`.
- No source code, no tests, no fixtures, no governance docs prior to this commit.

There is **no Codex Version 1 implementation to review yet.** This commit
establishes the architecture, domain model, statistical methodology, CSV
schema, and the V1 acceptance contract that Codex must build against, plus the
review scaffolding (`HANDOFF.md`, `DECISIONS.md`, `KNOWN_LIMITATIONS.md`).

## Milestone ladder

V1 (current target) — deterministic replay pipeline only:

```
CSV input
  → schema validation
  → synchronized same-calendar-month block generation
  → chronological multi-strategy merge
  → fixed-contract equity paths
  → monthly percentiles + drawdown metrics
  → reproducible (seeded) tests
```

V1 explicitly excludes: reinvestment, margin, forced size-down, exposure
engine, prop-firm mechanics, optimization, Streamlit UI. These are gated behind
V1 acceptance (see `HANDOFF.md` → "Gate to V2").

## Who owns what

- Architecture / spec / model-risk / review: this agent.
- Implementation / tests / fixtures / plumbing / UI: Codex.

## Next action

Codex: read `HANDOFF.md` first, then `ARCHITECTURE.md`. Resolve the open
decisions (HANDOFF → "Unresolved decisions") before writing engine code, or
implement the recommended defaults and record the choice in `DECISIONS.md`.
