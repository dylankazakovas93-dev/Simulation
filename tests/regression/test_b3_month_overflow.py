"""BLOCKER-3 — month shifting must never place a trade outside its target month.

Finding (HANDOFF Review 002): `Trade.shifted_to_month` adds the source-month
offset to the target month-start, so a day-of-month beyond the target month's
length overflows into the next calendar month. Reproduced:
  Jan-31 -> Feb target  lands 2025-03-03
  Jan-31 -> Apr target  lands 2025-05-01
  Feb-29(2024) -> Feb(2025) target  lands 2025-03-01

Target: carry the block's authoritative `target_month` and clamp/scale the shift
so a trade can never cross the target boundary; bucket month metrics on that
label, not on the shifted wall-clock.
"""
from __future__ import annotations

import pandas as pd
import pytest

from sim_core.models import Trade


def _trade(entry: str) -> Trade:
    e = pd.Timestamp(entry)
    return Trade(
        trade_id="t",
        source_row_id="r",
        strategy_id="s",
        instrument="ES",
        contract_symbol=None,
        entry_time=e,
        exit_time=e + pd.Timedelta(minutes=30),
        pnl_dollars=1.0,
    )


@pytest.mark.parametrize(
    "entry,target",
    [
        ("2025-01-31 14:35", "2025-02"),  # 31-day source -> 28-day target
        ("2025-01-31 14:35", "2025-04"),  # 31-day source -> 30-day target
        ("2024-02-29 14:35", "2025-02"),  # leap Feb -> non-leap Feb (seasonal default)
        ("2025-03-31 23:59", "2025-06"),  # boundary near midnight
    ],
)
def test_shift_never_leaves_target_month(entry, target):
    """RED: shifted entry/exit land outside the target month today."""
    tgt = pd.Period(target, "M")
    shifted = _trade(entry).shifted_to_month(tgt)
    assert shifted.entry_time.to_period("M") == tgt, "entry overflowed target month (BLOCKER-3)"
    assert shifted.exit_time.to_period("M") == tgt, "exit overflowed target month (BLOCKER-3)"
