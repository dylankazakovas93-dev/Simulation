"""Real-ledger integration harness tests.

The harness is exercised end-to-end against the canonical-schema fixture. The
`real_ledger`-marked test runs only when SIM_REAL_LEDGER_PATH points at the real
1,150-row ledger, and skips cleanly otherwise.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from sim_core.integration.real_ledger import (
    IntegrationError,
    build_integration_report,
    discover_strategy_ids,
    main,
)

FIXTURE = "sample_data/nq_es_margin_sim_master_2025_2026.csv"


def _mapping(tmp_path: Path, strategies: dict) -> Path:
    path = tmp_path / "mapping.yaml"
    path.write_text(yaml.safe_dump({"strategies": strategies}), encoding="utf-8")
    return path


def _both() -> dict:
    return {
        "nq_open": {"underlying": "NQ", "contract_symbol": "MNQ", "dollars_per_point": 2, "currency": "USD"},
        "es_open": {"underlying": "ES", "contract_symbol": "MES", "dollars_per_point": 5, "currency": "USD"},
    }


def test_discovery_lists_strategy_ids():
    assert discover_strategy_ids(FIXTURE) == ["es_open", "nq_open"]


def test_harness_runs_against_canonical_schema_fixture(tmp_path):
    report = build_integration_report(FIXTURE, _mapping(tmp_path, _both()))
    assert report["row_count"] == 4
    assert set(report["strategy_ids"]) == {"nq_open", "es_open"}
    assert report["timezone_validation"]["all_utc"] is True
    assert report["chronological_order_valid"] is True
    for name in ("seasonal_bootstrap", "moving_block", "stationary_block"):
        assert report["smoke_tests"][name]["ok"] is True
    assert report["historical_replay_pnl_by_strategy"]["nq_open"] == 20.0
    assert report["historical_replay_pnl_by_strategy"]["es_open"] == 10.0
    assert report["trade_count_by_strategy"] == {"nq_open": 2, "es_open": 2}
    assert report["data_hash"] and report["scenario_hash"]


def test_unmapped_strategy_fails_with_useful_message(tmp_path):
    only_nq = {"nq_open": _both()["nq_open"]}
    with pytest.raises(IntegrationError) as exc:
        build_integration_report(FIXTURE, _mapping(tmp_path, only_nq))
    assert "es_open" in str(exc.value)


def test_cli_writes_report(tmp_path):
    out = tmp_path / "out"
    rc = main(["--csv", FIXTURE, "--mapping", str(_mapping(tmp_path, _both())), "--output", str(out)])
    assert rc == 0
    data = json.loads((out / "integration_report.json").read_text())
    assert data["row_count"] == 4
    assert data["timezone_validation"]["all_utc"] is True


@pytest.mark.real_ledger
def test_real_ledger_when_available(tmp_path):
    csv_path = os.environ.get("SIM_REAL_LEDGER_PATH")
    if not csv_path:
        pytest.skip("SIM_REAL_LEDGER_PATH not set; real 1,150-row ledger unavailable")
    mapping = os.environ.get("SIM_REAL_LEDGER_MAPPING", "configs/nq_es_micro_contracts.yaml")
    report = build_integration_report(csv_path, mapping)
    assert report["row_count"] > 0
    assert report["strategy_ids"]
    assert report["timezone_validation"]["all_utc"] is True
    assert report["chronological_order_valid"] is True
    for name in ("seasonal_bootstrap", "moving_block", "stationary_block"):
        assert report["smoke_tests"][name]["ok"] is True
