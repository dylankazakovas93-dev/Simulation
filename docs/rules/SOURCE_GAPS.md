# Source gaps and disabled mechanics

| firm/program | gap | handling |
| --- | --- | --- |
| TPT Test | Complete evaluation account-size, target, drawdown and transition table is absent. | Disabled with `SOURCE_GAP`; no lifecycle plan is published. |
| TPT PRO+ account table | The supplied pages establish funded-only manual invitation, EOD drawdown, 90/10 split and no buffer, but do not provide a complete account-size/drawdown table. | Retained as `SOURCE_GAP`; not selectable. |
| TPT news and price limits | A ledger ordinarily lacks the event calendar, instrument mapping and intratrade order evidence. | Strict outcome is `UNKNOWN` when evidence is missing. |
| FundedNext non-Rapid payout caps | The supplied source does not provide a complete compatible payout economics table for every variant. | Contract remains source-gapped for payout economics. |
| All firms | Copy trading, discretionary conduct, platform and identity rules are not inferable from trade ledgers. | Declared out of simulation scope, not assumed compliant. |
