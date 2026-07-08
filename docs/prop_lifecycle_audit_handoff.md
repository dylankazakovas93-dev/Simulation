# Prop Lifecycle Audit Handoff

## Goal
Run a fresh audit before trusting the simulator for live sizing or prop-firm purchase decisions. The app currently has useful lifecycle machinery, but the UI mixes first-payout guidance, 12-month total withdrawals, net cash, and account comparison in ways that can mislead the user.

## Current Repo
- App: `app/streamlit_app.py`
- Lifecycle engine: `sim_core/lifecycle.py`
- Prop rule presets: `sim_core/prop_rules.py`
- Main tests: `tests/test_lifecycle.py`, `tests/test_prop_rules.py`, `tests/test_streamlit_app_helpers.py`
- Latest ledger used by user: `/Users/mariusvidziunas/Downloads/nq_fwd_proplab2.html`

## Known Validation
- `python3 -m pytest -q` passes.
- New ledger parses as 164 trades with `PnL pts`, `MAE`, and `MFE` detected as point values.
- For the user's current Apex EOD PA 50K scenario:
  - Starting balance/floor: `$50,500 / $49,000`
  - Qualifying days: `1`
  - Highest winning day: `$800`
  - Desired payout: `$1,500`
  - Apex reserve model requires ending balance at least `$52,100` after payout.
  - Therefore a `$1,500` payout requires reaching `$53,600`.

## Immediate Audit Questions
1. Verify Apex EOD PA payout rules:
   - First payout cap is `$1,500` on 50K.
   - Confirm exact later payout cap ladder and when/if cap lifts.
   - Confirm safety-net calculation for current PA: payout requires post-withdrawal balance >= account start + max loss + `$100`.
   - Confirm whether highest-winning-day consistency resets after payout and whether intraday order matters.
2. Verify Alpha/FundedNext/TPT rules:
   - Payout split vs withdrawable amount.
   - Whether withdrawals can occur daily, weekly, or only at defined cadence.
   - Whether there are min trading day, consistency, buffer, or payout cadence restrictions not modeled.
3. Verify TPT buffer logic:
   - Current patch treats buffer as eligibility threshold, not as a post-withdrawal reserve.
   - Confirm this from rules and test against a small deterministic fixture.
4. Verify bootstrap realism:
   - Current `MonthBlockSampler` can create favorable early sequences.
   - Add path diagnostics showing source months/trades for top, median, and failed paths.
   - Consider showing “source-month composition” so user can see whether a result is one lucky block.

## Metric Cleanup Needed
Separate these concepts everywhere:
- `first_payout_probability`: chance of any payout before first blow.
- `median_days_to_first_payout`: speed to first payout.
- `single_first_payout_amount`: first payout amount/cap.
- `total_withdrawals_12m`: cumulative payouts over full horizon.
- `avg_payout_count_12m`: number of payouts over full horizon.
- `net_cash_12m`: withdrawals minus fees/resets.
- `survival_after_first_payout`: optional second-stage survival metric.

Do not show `avg net`, `avg withdrawal`, and `median payout` together without clarifying horizon.

## UI Redesign Targets
1. Funded Guidance should be a single-account decision tool:
   - Show one chosen account.
   - Show candidate sizes as cards or rows with account name visible.
   - Emphasize: “chance to first payout before blow,” “median days,” “needed profit,” and “expected first payout.”
   - Add path examples: best, median, and fail-before-payout.
2. Prop Comparison should be a shopping/comparison tool:
   - Compare accounts with separate tabs for `First payout`, `12-month withdrawals`, `Risk`, and `Rules`.
   - No huge cramped tables by default.
   - Use readable cards, compact ranked lists, and full-width charts.
3. Charts:
   - Payout-before-blow vs blow-before-payout.
   - Median days/months to first payout.
   - 12-month total withdrawals.
   - Payout count distribution.
   - Path distribution histogram for first payout day.
4. Add an audit drawer per row:
   - Why this row ranks here.
   - Exact rule gates applied.
   - First payout math.
   - Sample path IDs.

## Specific Confusions To Fix
- `avg payouts = 0` while `any payout` is high likely means stale session data or old ranking rows. Make the app invalidate cached/session ranking when schema version changes.
- Funded Guidance compact table can show multiple `1 micro` rows without account labels. Include account/firms or force a single selected account.
- Apex sometimes appears best at 2 micros while other EOD accounts appear best at 1 micro. Explain this is due to payout caps/splits/reserves/consistency/composite weighting, not just drawdown mode.
- The current “highest expected withdrawal” line uses total 12-month withdrawal, not first payout. Rename and split.

## Suggested Implementation Order
1. Add a `RESULT_SCHEMA_VERSION` in Streamlit session state and clear stale lifecycle results when it changes.
2. Add deterministic unit tests for Apex 50K first payout math: `$50,500 -> $53,600 -> $1,500 payout -> $52,100`.
3. Add payout cadence/rule fields to `PropRuleProfile`, even if initially approximate.
4. Split ranking summaries into:
   - first-payout summary
   - horizon/12-month summary
5. Redesign Funded Guidance UI around first payout only.
6. Redesign Prop Comparison UI around cards + charts, with tables behind expanders.
7. Add path explorer links from each metric row/card.

## User Priority
The user cares most about:
- Getting startup money quickly without going broke.
- Current Apex PA 50K sizing with `$50,500` balance and `$1,500` cushion.
- Comparing which funded account to buy/copy-trade later.
- Avoiding misleading metrics that make a risky setup look good because later/horizon payouts are mixed into first-payout decisions.
