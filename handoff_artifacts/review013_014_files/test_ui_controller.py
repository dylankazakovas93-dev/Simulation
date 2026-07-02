"""V6 — UI controller tests. The controller is pure Python (no Streamlit)."""
from __future__ import annotations

import importlib.util

import pandas as pd
import pytest

from app import controller
from app.disclosures import DISCLOSURES, for_section
from sim_core.exposure import InstrumentMargin, MarginPolicy
from sim_core.live_account import FixedContractSizing, StrategyAllocation
from sim_core.models import Trade
from sim_core.optimize import Candidate, Constraint, Objective
from sim_core.prop_firm import PropFirmRules


def _trade(rid, strat, pnl, entry, exit_):
    return Trade(
        trade_id=f"{strat}-{rid}",
        source_row_id=str(rid),
        strategy_id=strat,
        instrument="ES",
        contract_symbol="MES",
        entry_time=pd.Timestamp(entry),
        exit_time=pd.Timestamp(exit_),
        pnl_dollars=pnl,
        stop_points=20.0,
        dollars_per_point=5.0,
    )


def _trades():
    out = []
    for m in range(1, 5):
        for d in (2, 9, 16, 23):
            out.append(
                _trade(
                    f"{m}{d}",
                    "s",
                    100 if d % 2 else -60,
                    f"2025-{m:02d}-{d:02d}T09:30:00Z",
                    f"2025-{m:02d}-{d:02d}T10:00:00Z",
                )
            )
    return out


def test_disclosures_are_declared_for_every_section():
    for section in ("ensemble", "drawdown", "live_account", "margin_exposure", "prop_firm", "optimizer"):
        assert for_section(section), f"section {section} must have disclosures"
    with pytest.raises(KeyError):
        for_section("does_not_exist")


def test_run_ensemble_returns_disclosed_provenanced_result():
    out = controller.run_ensemble(
        _trades(),
        method="same_calendar_month_bootstrap",
        resampling_params={"months": 4},
        number_of_paths=100,
        master_seed=1,
        starting_equity=50_000,
        ruin_threshold=0,
    )
    assert len(out["data_hash"]) == 64
    assert out["disclosures_ensemble"] == DISCLOSURES["ensemble"]
    assert out["disclosures_drawdown"] == DISCLOSURES["drawdown"]
    # coverage-absent is surfaced, not swallowed
    assert isinstance(out["engine_warnings"], list)
    assert 0.0 <= out["ruin_probability"] <= 1.0


def test_ensemble_percentile_fan_is_populated():
    # Review 013 regression: the headline chart must not be silently empty. The
    # controller defaults start_month + horizon_months so the fan is produced.
    out = controller.run_ensemble(
        _trades(),
        method="same_calendar_month_bootstrap",
        resampling_params={"months": 4},  # deliberately NO start_month supplied
        number_of_paths=100,
        master_seed=1,
        starting_equity=50_000,
        ruin_threshold=0,
    )
    rows = out["monthly_percentiles"]
    assert len(rows) == 4
    assert set(rows[0]) >= {"month", "p5", "p50", "p95"}


def test_run_live_account_attaches_exposure_and_disclosures_when_margin_given():
    out = controller.run_live_account(
        _trades(),
        starting_equity=50_000,
        allocations={"s": StrategyAllocation("s", FixedContractSizing(2))},
        margin_policy=MarginPolicy({"MES": InstrumentMargin("MES", 1_320, 1_200)}),
    )
    assert "exposure" in out
    assert out["disclosures_margin_exposure"] == DISCLOSURES["margin_exposure"]
    assert out["disclosures_live_account"] == DISCLOSURES["live_account"]


def test_run_prop_single_demotes_notional_balance():
    out = controller.run_prop_single(
        _trades(),
        PropFirmRules(account_size=50_000, profit_target=800, trailing_drawdown=2_000),
    )
    # headline is realized cash; notional balance carried under an explicit not-wealth key
    assert "headline_net_trader_cash" in out
    assert "notional_terminal_balance_not_wealth" in out
    assert out["disclosures_prop_firm"] == DISCLOSURES["prop_firm"]


def test_run_evaluation_stage_ensemble():
    out = controller.run_evaluation_stage_ensemble(
        _trades(),
        PropFirmRules(account_size=50_000, profit_target=200, trailing_drawdown=5_000, min_trading_days=1),
        method="same_calendar_month_bootstrap",
        resampling_params={"months": 4},
        number_of_paths=30,
        master_seed=1,
    )
    stage = out["evaluation_stage"]
    assert stage["num_accounts"] == 30
    assert 0.0 <= stage["pass_rate"] <= 1.0
    assert out["disclosures_prop_firm"] == DISCLOSURES["prop_firm"]


def test_run_funded_windows_table():
    out = controller.run_funded_windows(
        _trades(),
        PropFirmRules(account_size=50_000, profit_target=800, trailing_drawdown=2_000, min_trading_days=1),
        horizons_months=(2,),
        num_starts=20,
        seed=1,
    )
    df = controller.funded_windows_dataframe(out)
    assert "horizon_months" in df.columns
    assert out["disclosures_prop_firm"] == DISCLOSURES["prop_firm"]


def test_run_optimizer_returns_frontier_and_disclosures():
    cands = [
        Candidate("a", {}, {"net_cash": 1_000, "ruin": 0.02}),
        Candidate("b", {}, {"net_cash": 5_000, "ruin": 0.40}),
    ]
    out = controller.run_optimizer(
        cands,
        [Objective("net_cash", "max"), Objective("ruin", "min")],
        [Constraint("ruin", "<=", 0.10)],
    )
    assert {c["id"] for c in out["pareto_frontier"]} == {"a"}  # b fails constraint
    assert out["disclosures_optimizer"] == DISCLOSURES["optimizer"]
    frame = controller.frontier_dataframe(out)
    assert list(frame["id"]) == ["a"]


def test_controller_does_not_import_streamlit():
    # Engine/UI separation: the controller must be usable headless.
    import sys

    # importing the controller must not have pulled in streamlit
    assert "app.controller" in sys.modules
    # controller module has no streamlit attribute/dependency
    assert not hasattr(controller, "st")


def test_unknown_resampling_method_is_rejected():
    with pytest.raises(ValueError, match="unknown resampling method"):
        controller.build_policy("not_a_method", {})


@pytest.mark.skipif(
    importlib.util.find_spec("streamlit") is None, reason="streamlit not installed"
)
def test_streamlit_app_imports_when_available():
    import app.streamlit_app as view  # noqa: F401

    assert hasattr(view, "main")
