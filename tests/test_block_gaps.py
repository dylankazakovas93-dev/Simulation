"""MEDIUM-R3-E — moving/stationary blocks must not bridge calendar gaps.

Source coverage with a missing middle month splits into two consecutive runs;
blocks traverse only verified-consecutive months, a too-long block fails, and
restarts at a gap/boundary are recorded in diagnostics.
"""
from __future__ import annotations

import pandas as pd
import pytest

from sim_core.ingestion.csv_loader import normalize_trade_frame
from sim_core.resampling.policies import MovingBlockBootstrap, StationaryBlockBootstrap

VALID = {pd.Period(p, "M") for p in ("2024-01", "2024-02", "2024-04", "2024-05")}


def _gapped_trades():
    # 2024-03 is deliberately absent -> runs are [Jan,Feb] and [Apr,May].
    rows = [
        {
            "strategy_id": "s",
            "instrument": "ES",
            "entry_time": f"2024-{month:02d}-05T09:30:00Z",
            "exit_time": f"2024-{month:02d}-05T10:00:00Z",
            "pnl_dollars": float(month),
        }
        for month in (1, 2, 4, 5)
    ]
    return normalize_trade_frame(pd.DataFrame(rows))


def test_moving_block_never_bridges_a_gap():
    path = MovingBlockBootstrap(months=4, block_length=2, start_month="2026-01").sample(
        _gapped_trades(), seed=3
    )
    assert path.diagnostics["consecutive_runs"] == 2
    blocks = path.sampled_blocks
    # length-2 blocks are aligned at indices (0,1) and (2,3); each pair must be
    # calendar-consecutive (never the Feb->Apr jump across the missing March).
    for lo, hi in ((0, 1), (2, 3)):
        assert blocks[hi].source_month == blocks[lo].source_month + 1
    assert all(b.source_month in VALID for b in blocks)


def test_moving_block_too_long_for_any_run_fails():
    with pytest.raises(ValueError, match="long enough"):
        MovingBlockBootstrap(months=3, block_length=3, start_month="2026-01").sample(
            _gapped_trades(), seed=1
        )


def test_stationary_block_restarts_at_gap_and_never_uses_missing_month():
    path = StationaryBlockBootstrap(months=8, expected_block_length=2.0, start_month="2026-01").sample(
        _gapped_trades(), seed=7
    )
    assert path.diagnostics["consecutive_runs"] == 2
    assert "restarts_due_to_boundary" in path.diagnostics
    # The missing month (2024-03) must never appear as a source month.
    assert all(b.source_month in VALID for b in path.sampled_blocks)
