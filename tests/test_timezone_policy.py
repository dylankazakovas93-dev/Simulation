"""MEDIUM-R3-B — explicit timezone policy for ingestion.

Default `source_timezone=None`:
  * UTC-aware timestamp -> accepted and normalized to UTC.
  * Naive timestamp with explicit source_timezone -> localized then UTC.
  * Naive timestamp with no declaration -> validation error.
  * DST-ambiguous / nonexistent local time -> clear error unless dst_resolution.
"""
from __future__ import annotations

import pandas as pd
import pytest

from sim_core.ingestion.csv_loader import normalize_trade_frame
from sim_core.models import TradeValidationError


def _frame(entry: str, exit_: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "strategy_id": "s",
                "instrument": "ES",
                "entry_time": entry,
                "exit_time": exit_,
                "pnl_dollars": 1,
            }
        ]
    )


def test_utc_aware_accepted_and_normalized():
    trades = normalize_trade_frame(_frame("2025-01-02T09:30:00Z", "2025-01-02T10:00:00Z"))
    assert str(trades[0].entry_time.tz) == "UTC"
    assert trades[0].entry_time.isoformat() == "2025-01-02T09:30:00+00:00"


def test_naive_with_explicit_timezone_localized_then_utc():
    # 09:30 America/Chicago (CST, UTC-6 in January) -> 15:30 UTC.
    trades = normalize_trade_frame(
        _frame("2025-01-02 09:30", "2025-01-02 10:00"), source_timezone="America/Chicago"
    )
    assert str(trades[0].entry_time.tz) == "UTC"
    assert trades[0].entry_time.hour == 15 and trades[0].entry_time.minute == 30


def test_naive_without_timezone_declaration_is_rejected():
    with pytest.raises(TradeValidationError) as exc:
        normalize_trade_frame(_frame("2025-01-02 09:30", "2025-01-02 10:00"))
    assert "naive timestamp" in str(exc.value)


def test_dst_nonexistent_local_time_fails_without_policy_and_resolves_with_one():
    # 2025-03-09 02:30 America/Chicago is in the spring-forward gap (does not exist).
    gap = _frame("2025-03-09 02:30", "2025-03-09 02:45")
    with pytest.raises(TradeValidationError) as exc:
        normalize_trade_frame(gap, source_timezone="America/Chicago")
    assert "dst_resolution" in str(exc.value)

    resolved = normalize_trade_frame(
        gap, source_timezone="America/Chicago", dst_resolution="shift_forward"
    )
    assert str(resolved[0].entry_time.tz) == "UTC"


def test_dst_ambiguous_local_time_fails_without_policy_and_resolves_with_one():
    # 2025-11-02 01:30 America/Chicago is ambiguous (fall-back overlap).
    overlap = _frame("2025-11-02 01:30", "2025-11-02 01:45")
    with pytest.raises(TradeValidationError):
        normalize_trade_frame(overlap, source_timezone="America/Chicago")

    resolved = normalize_trade_frame(
        overlap, source_timezone="America/Chicago", dst_resolution="earliest"
    )
    assert str(resolved[0].entry_time.tz) == "UTC"
