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

## Margin / exposure traps (V3)

- **[GUARD]** Margin is declared per contract symbol; a traded contract with no
  declared margin fails closed. No silent margin default. (ADR-017)
- **[SCOPE]** V3 margin is an **entry-time initial-margin cap only**. No intraday
  maintenance-margin call or forced liquidation is modeled — a position that would
  breach maintenance mid-trade is NOT liquidated in-sim (V3.1 candidate). Do not
  present V3 as intraday-margin-aware. (ADR-017)
- **[SCOPE]** Exposure is measured over each trade's **scheduled `[entry, exit]`**
  interval at simulated size — realized-only, consistent with V1/V2 booking. There
  is **no intratrade mark-to-market / MAE path**; peak open stop-risk uses declared
  `stop_points × dollars_per_point`, not realized excursion. Peak simultaneous
  margin/stop-risk therefore reflect scheduled overlap, not worst-case intratrade
  excursion. (ADR-018)
- **[SCOPE]** The **marginal portfolio contribution** of adding a strategy is not
  yet computed; it needs an A/B scenario diff (portfolio-comparison pass). (ADR-018)

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

## Prop-firm traps (V4)

- **[GUARD]** A notional prop-account balance is not personal wealth; the headline
  is `net_trader_cash = Σ(payout × split) − (eval + activation + reset fees)`. Every
  prop result carries a `notional_balance_note` and aggregates exclude notional
  balances. (ADR-020)
- **[GUARD]** No firm is hardcoded: all rules/costs are declared in `PropFirmRules`.
  (ADR-019)
- **[WARN/SCOPE]** Breach checks (trailing drawdown, daily loss) are **realized-only
  (end-of-trade)** — no intratrade excursion. Reported breach probability is a
  **lower bound** and survival an **upper bound**; surfaced as `realized_only_note`
  on every result. (ADR-020)
- **[SCOPE]** Payout timing is **greedy** (withdraw as soon as eligible, to start +
  buffer). Alternative withdrawal schedules are a later parameter.
- **[SCOPE]** A funded breach is terminal (no funded reset / re-buy modeled).
  Evaluation resets consume the same forward trade stream.
- **[SCOPE]** Copied accounts in a portfolio share one identical trade path (fully
  correlated) — a copy-trading model, not independent diversification; disclosed via
  `correlation_note`.

## Optimization traps (V5)

- **[GUARD]** The optimizer refuses single-objective runs by default (raises unless
  ≥2 objectives; single-objective requires an explicit flag and is warned). It must
  not optimize median terminal equity — or any lone metric — alone. (ADR-021)
- **[GUARD]** The decision output is the declared-constraint Pareto frontier, not a
  scalar winner. The scalarized ranking is a labeled secondary display aid only.
  (ADR-021)
- **[GUARD]** Rejected candidates report their exact binding constraints; missing
  objective/constraint metrics raise (no silent zero-fill); `expected_log_growth`
  returns `-inf` on a total-loss period rather than clipping. (ADR-021)
- **[SCOPE]** V5 is a selection layer over a provided candidate set (grid/list), not
  a continuous search (CMA-ES/Bayesian is a later add-on). It inherits every
  upstream realized-only / notional caveat of the metrics fed into it. (ADR-021)
