"""BLOCKER-1 / HIGH-4 — timezone-aware ingestion and resampling.

Findings (HANDOFF Review 002):
- BLOCKER-1: `Trade.shifted_to_month` subtracts a tz-NAIVE month-start
  (`Period.to_timestamp()`) from a tz-AWARE `entry_time`. For the canonical/real
  UTC ledger this raises `TypeError: Cannot subtract tz-naive and tz-aware
  datetime-like objects` (and `to_period('M')` first drops the tz with a
  UserWarning). Every bootstrap on UTC data therefore crashes.
- HIGH-4: naive timestamps are accepted silently, so the engine mixes tz-aware
  and tz-naive Trades by source.

Target: normalize all timestamps to one tz policy at ingest (store tz-aware UTC)
and make `shifted_to_month` tz-consistent.
"""
from __future__ import annotations

import pandas as pd

from sim_core.ingestion.csv_loader import load_canonical_margin_csv, normalize_trade_frame
from sim_core.resampling.policies import HistoricalReplay, SameCalendarMonthBootstrap

CANONICAL = "sample_data/nq_es_margin_sim_master_2025_2026.csv"


def _utc_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "strategy_id": "s",
                "instrument": "ES",
                "entry_time": "2025-01-06T14:35:00Z",
                "exit_time": "2025-01-06T15:00:00Z",
                "pnl_dollars": 100,
            },
            {
                "strategy_id": "s",
                "instrument": "ES",
                "entry_time": "2025-02-10T14:35:00Z",
                "exit_time": "2025-02-10T15:00:00Z",
                "pnl_dollars": -40,
            },
        ]
    )


def test_utc_timestamps_preserved_on_ingest():
    """GUARD: ingest keeps UTC tz (the loss happens later, in resampling)."""
    trades = normalize_trade_frame(_utc_frame())
    assert trades[0].entry_time.tz is not None, "ingest dropped tz (HIGH-4)"
    assert str(trades[0].entry_time.tz) == "UTC"


def test_utc_trades_resample_without_tz_error():
    """RED: SameCalendarMonthBootstrap on UTC trades raises TypeError today (BLOCKER-1)."""
    trades = normalize_trade_frame(_utc_frame())
    path = SameCalendarMonthBootstrap(months=2, start_month="2026-01").sample(trades, seed=1)
    assert all(t.entry_time.tz is not None for t in path.trades), "resampling dropped tz"


def test_canonical_ledger_historical_and_seasonal_complete():
    """RED: canonical UTC ledger completes historical replay but seasonal raises today (BLOCKER-1)."""
    trades = load_canonical_margin_csv(CANONICAL)
    HistoricalReplay().sample(trades)  # no timestamp shift -> already works
    path = SameCalendarMonthBootstrap(months=2, start_month="2026-01").sample(trades, seed=1)
    assert len(path.trades) >= 1
