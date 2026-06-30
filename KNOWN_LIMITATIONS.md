# KNOWN_LIMITATIONS

Model-risk register. Every entry is a limitation or trap that MUST be either
prevented in code, or made explicit in output/report. "We'll remember" is not
acceptable — each item needs a guard, a warning, or a documented scope note.

Legend: **[GUARD]** enforced in code · **[WARN]** surfaced to user · **[SCOPE]**
out of V1 scope, documented.

## Statistical traps

- **[GUARD]** IID trade shuffling is not implemented and must never be added as
  "the" bootstrap. (ADR-002)
- **[GUARD]** Independent per-strategy sampling is not the default; when used it
  is labeled as correlation-destroying. (ADR-002)
- **[WARN]** Thin seasonal support: if a month-of-year is backed by few
  historical instances, or one year dominates, the percentile fan is unreliable.
  Report support counts and effective sample size per month. (ADR-010)
- **[GUARD]** Partial first/last months excluded from the pool by default.
  (ADR-010)
- **[GUARD]** Cross-path percentiles computed across the ensemble, not by
  differencing medians. (ADR-009)
- **[SCOPE]** No out-of-sample / strategy-degradation control in V1. The sim
  assumes the historical edge persists. This is a strong assumption and must be
  stated on every export. Degradation/haircut controls are a later milestone.
- **[SCOPE]** Survivorship: uploaded logs are presumably surviving strategies;
  the sim cannot correct for strategies that died and were never uploaded.
  State it.

## Win-rate / classification traps

- **[GUARD]** Bare `win_rate` label forbidden; every rate names its denominator.
  (ARCHITECTURE §6)
- **[GUARD]** Breakevens classified by explicit tolerance, never folded into
  losses. (ADR-005)

## Stress traps

- **[GUARD]** Stress operators are orthogonal and individually toggled;
  compound stresses are labeled compound. (ARCHITECTURE §7)
- **[WARN]** Lowering true win rate while also shrinking winners is a compound
  stress; the report must say so.

## Accounting traps

- **[GUARD]** Deposits are not profit; withdrawals are not losses. (ADR-007)
- **[GUARD]** Equity not silently capped/floored; ruin is recorded. (ADR-006)
- **[SCOPE]** V1 books realized P&L at trade exit only. Intratrade exposure,
  MAE-based drawdown, and margin-during-trade are NOT modeled in V1. Realized-
  only drawdown **understates** true peak-to-trough risk — state this on every
  drawdown number until the exposure engine lands.
- **[SCOPE]** Margin checked only at trade settlement boundaries until the
  exposure/margin engine exists. Do not present V1 as margin-aware.

## Sizing traps

- **[GUARD]** Contract counts are derived per-strategy from per-contract P&L and
  each strategy's own sizing policy. MES is never mechanically tied 1:1 to MNQ
  (or any pair) unless the user explicitly selects a coupling rule.
- **[SCOPE]** Reinvestment, percentage-equity sizing, forced size-down, and
  size-up/size-down symmetry are out of V1. Do not enable compounding outputs in
  V1.

## Output / forecasting traps

- **[SCOPE/WARN]** No compounding in V1 ⇒ no uncapped exponential equity curves.
  When reinvestment lands, huge uncapped compounded terminal equities must be
  presented with explicit caveats, never as point forecasts.
- **[WARN]** Risk-of-ruin and drawdown definitions must be defined once and held
  constant across every report. Any report-specific variant is a defect.

## Prop / optimization traps (forward-looking, V1 out of scope)

- **[SCOPE]** A notional prop account balance is not personal wealth; only
  realized net cash (payouts − fees − resets − activations) counts. To be
  enforced when the prop engine lands.
- **[SCOPE]** The optimizer must not optimize median terminal equity alone, and
  must not be allowed to exploit any of the above traps (e.g. capped equity,
  realized-only drawdown). Constraints + Pareto required before optimizer ships.
