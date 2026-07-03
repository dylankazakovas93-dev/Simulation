# UI_GUIDE — Prop Sim Lab v2 (browser app)

Delivery framework: a self-contained browser app (Artifact) — zero install, same
engine semantics as sim_core, verified by acceptance tests. (ADR-025: chosen over
multipage Streamlit because the user operates via hosted artifacts; the Python
engine + Streamlit path remains available for local use.)

Pages:
1. **Setup** — ledger paste/upload (+unit), $/pt, contracts, horizon, paths, SEED,
   σ (order shuffle, one concept), withdrawal cushion (TOTAL above floor),
   payout request $, eval/activation discount %. Nothing recomputes until **Run**.
2. **Compare** — Funded and Eval are separate tabs, never blended. Funded columns:
   fail %, payout %, days→1st payout P50/P90, trading P&L, kept-in-account, net
   payouts, net personal (+ per-month average). Click a row → full accounting
   waterfall + "why this account differs" (its exact binding rules). Eval columns:
   pass %, days-to-pass P50/P90, cumulative pass by month 1/2/3, expected cost,
   cost per funded account.
3. **Monthly** — per-calendar-month distributions (P5/P25/P50/P75/P95) of trading
   P&L, monthly net payout P50, fail% in month + cumulative, first-payout timing,
   cumulative net payouts (labeled cumulative). Terminated months shown as
   terminated, never $0.
4. **My live account** — exact state (balance, peak, qualifying days done, payouts
   completed, contracts) → deterministic "now" panel (floor, room, eligible NOW,
   $ to next payout, qualifying days left, next cap, what you'd receive) + forward
   distribution from that exact state with the same engine: days→payout P50/P90,
   P(fail) by month + cumulative, net cash P5/P50/P95.
5. **Path inspector** — one path, event by event: trade, P&L, balance before/after,
   floor, eligibility, payout approvals, cash paid, failure/termination.

Performance: full 26-account × 300-path × 6-month run ≈ 4s (Node benchmark; browser
similar). Page switches and filters re-render cached results — no recompute.
