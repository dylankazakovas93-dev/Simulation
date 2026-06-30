"""MEDIUM-R3-C — coverage diagnostics distinguish missing vs verified-flat.

Strategy 'a' is declared covered Jan-Feb but trades only in January, so its
February is a verified flat month. Strategy 'b' trades only in January with no
coverage, so its February is missing (cannot be assumed flat).
"""
from __future__ import annotations

import pandas as pd

from sim_core.diagnostics.coverage import build_coverage_report
from sim_core.ingestion.csv_loader import normalize_trade_frame
from sim_core.models import StrategyCoverage


def _trades():
    rows = [
        {
            "strategy_id": "a",
            "instrument": "ES",
            "entry_time": "2025-01-06T09:30:00Z",
            "exit_time": "2025-01-06T10:00:00Z",
            "pnl_dollars": 5,
        },
        {
            "strategy_id": "b",
            "instrument": "NQ",
            "entry_time": "2025-01-06T09:30:00Z",
            "exit_time": "2025-01-06T10:00:00Z",
            "pnl_dollars": -3,
        },
    ]
    return normalize_trade_frame(pd.DataFrame(rows))


def test_missing_and_verified_flat_are_distinct():
    coverage = [StrategyCoverage("a", "ES", "2025-01", "2025-02")]
    report = build_coverage_report(_trades(), coverage)

    assert report.status_for("a", "2025-01") == "complete"
    assert report.status_for("a", "2025-02") == "verified_flat"
    assert report.status_for("b", "2025-01") == "complete"
    assert report.status_for("b", "2025-02") == "missing"


def test_report_records_support_counts_and_coverage_span():
    coverage = [StrategyCoverage("a", "ES", "2025-01", "2025-02")]
    report = build_coverage_report(_trades(), coverage)
    records = {(r["strategy_id"], r["month"]): r for r in report.to_records()}

    # January for 'a' is eligible with one same-calendar-month source instance.
    assert records[("a", "2025-01")]["seasonal_eligible"] is True
    assert records[("a", "2025-01")]["source_month_support"] == 1
    # 'b' has no coverage; its missing February is not seasonal-eligible.
    assert records[("b", "2025-02")]["seasonal_eligible"] is False

    assert report.strategies["a"]["has_coverage"] is True
    assert report.strategies["a"]["coverage_end"] == "2025-02"
    assert report.strategies["b"]["has_coverage"] is False


def test_warnings_flag_missing_months_and_absent_coverage():
    report = build_coverage_report(_trades(), [StrategyCoverage("a", "ES", "2025-01", "2025-02")])
    joined = " | ".join(report.warnings())
    assert "no coverage declared" in joined  # strategy b
    assert "missing month" in joined  # strategy b February
