"""HIGH-3 — cross-path monthly percentiles need consistent denominators
(equity carry-forward).

Finding (HANDOFF Review 002): `monthly_equity_percentiles` records a month-end
equity for a path only if it settled a trade that month, so a month where some
paths are flat is computed over fewer paths than a busy month — inconsistent,
unlabeled denominators. The fix is to carry each path's last equity forward
across the horizon so every month's percentile uses every path.
"""
from __future__ import annotations

import pandas as pd

from sim_core.execution.replay import run_fixed_contract_simulation
from sim_core.ingestion.csv_loader import normalize_trade_frame
from sim_core.metrics.reports import monthly_equity_percentiles
from sim_core.models import AccountConfig


def _path(entries):
    rows = [
        {
            "strategy_id": "s",
            "instrument": "ES",
            "entry_time": f"2025-{month:02d}-05 09:30",
            "exit_time": f"2025-{month:02d}-05 10:00",
            "pnl_dollars": pnl,
            "source_row_id": f"r{i}",
        }
        for i, (month, pnl) in enumerate(entries)
    ]
    return run_fixed_contract_simulation(
        normalize_trade_frame(pd.DataFrame(rows)),
        account=AccountConfig(initial_equity=1_000),
    )


def test_monthly_percentiles_carry_forward_flat_paths():
    """RED: a path flat in Feb is dropped from Feb's percentile today (HIGH-3).

    Path A: Jan +100 -> 1100, Feb +50 -> 1150 (Feb-end = 1150).
    Path B: Jan -30 -> 970, flat in Feb (carry-forward Feb-end = 970).
    With carry-forward, median Feb equity = median(1150, 970) = 1060.
    Current code drops B and reports 1150.
    """
    path_a = _path([(1, 100), (2, 50)])
    path_b = _path([(1, -30)])
    pct = monthly_equity_percentiles([path_a, path_b], percentiles=(50,))
    feb = pct.loc[pct["month"] == "2025-02"]
    assert not feb.empty, "February row missing from percentiles"
    assert float(feb["p50"].iloc[0]) == 1060.0, (
        "Feb percentile ignored the flat path (no equity carry-forward, HIGH-3)"
    )
