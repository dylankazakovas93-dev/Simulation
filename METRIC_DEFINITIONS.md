# METRIC_DEFINITIONS

Single authoritative definition for every displayed number. If a UI label and this
file disagree, this file wins and the UI is a bug.

## Per-month (calendar month of the simulation; month 1 = first month after start)
- **Monthly realized trading P&L** — closed-trade P&L booked in that month while the
  account is alive. May be negative; NEVER clamped to zero. A path that failed in an
  earlier month is *terminated* for this month: excluded from the trading distribution
  and counted in failure %, never shown as a $0 month.
- **Gross payout (month)** — payout dollars approved by the firm in that month, before split.
- **Net payout (month)** — gross × split; cash actually received that month.
- **Fail % in month** — share of paths whose failure event falls in that month
  (unconditional, denominated over all paths).
- **Cumulative X (months 1–N)** — always labeled "cumulative"; never presented as a
  monthly figure.

## Per-account (over the chosen horizon H, mean/quantiles across paths)
- **Trading P&L while active** — Σ realized trade P&L from sim start until failure/retirement/horizon.
- **P&L not earned (failed paths)** — Σ P&L the strategy would have produced after the
  account failed. Informational; not part of the cash identity.
- **Kept in account** — finalBalance − startBalance = trading P&L − gross payouts.
  Profit locked behind buffers/caps, or losses at failure. NOT personal cash.
- **Gross payouts** — Σ approved payout dollars before split.
- **Split withheld** — gross payouts − net payouts.
- **Net payouts** — Σ gross × split.
- **Net personal cash** — net payouts − (evaluation fees + rebills + activation + resets).
  Funded-stage tables show fees = 0 (fees live on the Eval tab and are charged once).
- **Eligible-to-withdraw** — max(0, profit − max(firmBuffer, userCushion)). A permission,
  not cash; never added to wealth.
- **Requested payout** — user's per-request amount; the approved amount is
  min(eligible, firm cap for that payout number, %-of-profit cap, request).
- **Post-payout cushion** — distance between post-payout balance and the active failure
  floor. The user cushion is a TOTAL: max(firm buffer, user cushion), never the sum.

## Timing / risk
- **Days → first payout P50/P90** — calendar days from sim start (or activation) to first
  approved payout, among paths that reached one.
- **P(fail, H months)** — share of paths breaching the drawdown/daily-loss within H months.
  Realized-only ⇒ a LOWER bound on true intratrade risk.
- **Days to pass P50/P90** — eval start to target hit (Apex needs no min days).
- **σ (order shuffle)** — ONE concept: Gaussian displacement of trade order (σ=0 exact
  history; σ≥2 ≈ full shuffle). Touches nothing else (not win rate, sizes, frequency).
