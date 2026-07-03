# ACCOUNTING_IDENTITIES

Enforced by the engine and asserted in the acceptance suite (see
handoff_artifacts/prop_lab_v2/accept_tests.js). Any violation is a defect.

1. finalBalance = startBalance + Σ(tradingP&L while active) − Σ(gross payouts)
2. netPayouts   = Σ(gross_i × split)          (split applied per payout event)
3. splitWithheld = grossPayouts − netPayouts
4. netPersonalCash = netPayouts − (evalFees + rebills + activation + resets)
5. keptInAccount = finalBalance − startBalance = tradingP&L − grossPayouts
6. Waterfall: tradingP&L − keptInAccount = grossPayouts; grossPayouts − split = netPayouts;
   netPayouts − fees = netPersonalCash.
7. Cross-firm: with failure disabled, grossTradingP&L is IDENTICAL across firms for the
   same path/sizing (test B1). All differences must therefore come from a visible rule,
   cap, split, buffer, failure or fee.
8. Deposits are never profit; withdrawals are never trading losses; a payout reduces the
   account balance at its event time and never lowers the failure floor (floor monotone).
9. Negative monthly trading P&L is preserved; a failed path is terminated (inactive), not
   a $0 month.
