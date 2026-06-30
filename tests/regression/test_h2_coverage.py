"""HIGH-2 — flat vs missing vs partial months; warn when coverage is absent.

Findings (HANDOFF Review 002):
- Verified-flat months (live, zero trades) must remain sampleable via coverage.
- Missing months (no data, not declared) and partial months must NOT be treated
  as verified-flat.
- Without coverage the pool is silently the trade-bearing months only; the engine
  must warn (today it is silent).
"""
from __future__ import annotations

import warnings

import pandas as pd
import pytest

from sim_core.ingestion.csv_loader import normalize_trade_frame
from sim_core.models import StrategyCoverage
from sim_core.resampling.policies import SameCalendarMonthBootstrap


def _jan_only():
    return normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2025-01-06 09:30",
                    "exit_time": "2025-01-06 10:00",
                    "pnl_dollars": 5,
                }
            ]
        )
    )


def test_verified_flat_month_is_sampleable():
    """GUARD: a Feb declared complete-but-flat via coverage is drawable."""
    coverage = [StrategyCoverage("s", "ES", "2025-01", "2025-02")]
    path = SameCalendarMonthBootstrap(months=2, start_month="2026-01").sample(
        _jan_only(), seed=1, coverage=coverage
    )
    assert any(b.source_month == pd.Period("2025-02", "M") for b in path.sampled_blocks)


def test_missing_month_is_not_treated_as_flat():
    """GUARD: with no Feb data and no coverage, Feb is not fabricated as flat."""
    with pytest.raises(ValueError):
        SameCalendarMonthBootstrap(months=2, start_month="2026-01").sample(_jan_only(), seed=1)


def test_partial_month_excluded_even_when_it_has_trades():
    """GUARD: a declared-partial Feb is excluded from the pool despite having trades."""
    trades = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2025-02-03 09:30",
                    "exit_time": "2025-02-03 10:00",
                    "pnl_dollars": 5,
                }
            ]
        )
    )
    coverage = [StrategyCoverage("s", "ES", "2025-02", "2025-02", {"2025-02"})]
    with pytest.raises(ValueError):
        SameCalendarMonthBootstrap(months=1, start_month="2026-02").sample(
            trades, seed=1, coverage=coverage
        )


def test_missing_coverage_emits_warning():
    """RED: sampling without coverage silently drops flat months; it must warn (HIGH-2)."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        SameCalendarMonthBootstrap(months=1, start_month="2026-01").sample(_jan_only(), seed=1)
    assert any("coverage" in str(w.message).lower() for w in caught), (
        "no coverage/support warning emitted when coverage is absent (HIGH-2)"
    )
