"""BLOCKER-4 / MEDIUM-3 — Scenario & ResultDistribution serialization; exports
carry their assumptions.

Finding (HANDOFF Review 002): there is no serializable run config, no
result-with-embedded-assumptions, and no input data hash; exports write CSVs with
no seed/policy/params/hash/limitations, so a result cannot be tied to what
produced it.

Target: serializable `Scenario` + `ResultDistribution`; `export_simulation_batch`
emits a `run_manifest.json` carrying the assumptions.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def _one_path():
    from sim_core.execution.replay import run_fixed_contract_simulation  # noqa: PLC0415
    from sim_core.ingestion.csv_loader import normalize_trade_frame  # noqa: PLC0415
    from sim_core.models import AccountConfig  # noqa: PLC0415

    trades = normalize_trade_frame(
        pd.DataFrame(
            [
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": "2025-01-02 09:30",
                    "exit_time": "2025-01-02 10:00",
                    "pnl_dollars": 100,
                    "source_row_id": "r1",
                }
            ]
        ),
        source_timezone="UTC",
    )
    return run_fixed_contract_simulation(trades, account=AccountConfig(initial_equity=100_000))


def test_scenario_round_trips():
    """RED: Scenario not implemented yet (BLOCKER-4)."""
    from sim_core.models import (  # noqa: PLC0415
        AccountConfig,
        FixedContractPortfolio,
        Scenario,
    )

    scenario = Scenario(
        master_seed=42,
        resampling_policy="same_calendar_month_bootstrap",
        policy_params={"months": 12, "start_month": "2026-01"},
        account=AccountConfig(initial_equity=100_000),
        portfolio=FixedContractPortfolio(default_contracts=1),
        data_hash="abc123",
    )
    assert Scenario.from_dict(scenario.to_dict()) == scenario


def test_result_distribution_embeds_and_round_trips_scenario():
    """RED: ResultDistribution not implemented yet (BLOCKER-4)."""
    from sim_core.models import (  # noqa: PLC0415
        AccountConfig,
        FixedContractPortfolio,
        ResultDistribution,
        Scenario,
    )

    scenario = Scenario(
        master_seed=7,
        resampling_policy="historical_replay",
        policy_params={},
        account=AccountConfig(),
        portfolio=FixedContractPortfolio(),
        data_hash="hhh",
    )
    dist = ResultDistribution(scenario=scenario, paths=[_one_path()])
    as_dict = dist.to_dict()
    assert as_dict["scenario"]["master_seed"] == 7
    assert ResultDistribution.from_dict(as_dict).scenario == scenario


def test_export_manifest_contains_assumptions(tmp_path):
    """RED: exports do not emit a manifest with seed/policy/params/hash/limitations (BLOCKER-4/MEDIUM-3)."""
    from sim_core.exports import export_simulation_batch  # noqa: PLC0415
    from sim_core.models import (  # noqa: PLC0415
        AccountConfig,
        FixedContractPortfolio,
        ResultDistribution,
        Scenario,
    )

    scenario = Scenario(
        master_seed=99,
        resampling_policy="same_calendar_month_bootstrap",
        policy_params={"months": 6, "start_month": "2026-01"},
        account=AccountConfig(),
        portfolio=FixedContractPortfolio(),
        data_hash="deadbeef",
    )
    dist = ResultDistribution(scenario=scenario, paths=[_one_path()])

    out = export_simulation_batch(dist, tmp_path)
    manifest_path = (
        Path(out["run_manifest"]) if isinstance(out, dict) and "run_manifest" in out
        else Path(tmp_path) / "run_manifest.json"
    )
    data = json.loads(manifest_path.read_text())
    for key in ("master_seed", "resampling_policy", "policy_params", "data_hash", "limitations"):
        assert key in data, f"manifest missing {key}"
    assert data["master_seed"] == 99
    assert data["data_hash"] == "deadbeef"
    assert isinstance(data["limitations"], list) and data["limitations"]
