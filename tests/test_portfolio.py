from __future__ import annotations

import pandas as pd
import pytest

from sim_core.forward_master_path import ForwardScenario, build_master_path
from sim_core.lifecycle import LifecycleSettings, default_lifecycle_plans, simulate_lifecycle_path
from sim_core.portfolio import (
    PortfolioAllocation,
    PortfolioInstrumentSpec,
    build_allocation_grid,
    build_joint_portfolio_paths,
    build_portfolio_account_day_ledger,
    combine_portfolio_path,
    normalize_portfolio_ledger,
    portfolio_trade_ledger_to_lifecycle_trades,
    resolve_portfolio_overlaps,
    simulate_portfolio_lifecycle,
)


def _ledger(strategy: str, date: str, pnl_points: float, *, asset: str = "NQ", overlap: bool = False) -> pd.DataFrame:
    entry = f"{date}T09:30:00Z"
    exit_ = f"{date}T10:30:00Z" if overlap else f"{date}T09:45:00Z"
    return pd.DataFrame(
        [
            {
                "trade_id": f"{strategy}-{date}",
                "source_session_date": date,
                "entry_time": entry,
                "exit_time": exit_,
                "direction": "long",
                "pnl_points": pnl_points,
                "mae_points": 2.0,
                "mfe_points": 12.0,
            }
        ]
    )


def _spec(strategy: str, asset: str, symbol: str, dpp: float, *, commission: float = 0.0) -> PortfolioInstrumentSpec:
    return PortfolioInstrumentSpec(
        strategy_id=strategy,
        asset_id=asset,
        asset_label=asset,
        contract_symbol=symbol,
        dollars_per_point_per_contract=dpp,
        commission_round_turn_per_contract=commission,
        source_timezone="UTC",
        default_contract_count=1,
        pnl_basis="points",
    )


def test_portfolio_unit_conversion_uses_per_ledger_dollars_per_point_not_combined_points():
    mnq = normalize_portfolio_ledger(_ledger("mnq", "2025-01-02", 10), _spec("mnq", "NQ", "MNQ", 2))
    mgc = normalize_portfolio_ledger(_ledger("mgc", "2025-01-02", 10, asset="GC"), _spec("mgc", "GC", "MGC", 10))
    combined = pd.concat([mnq, mgc], ignore_index=True)

    assert mnq["gross_pnl_dollars"].iloc[0] == 20
    assert mgc["gross_pnl_dollars"].iloc[0] == 100
    assert combined["net_pnl_dollars"].sum() == 120
    assert "combined_portfolio_points" not in combined.columns


def test_changing_only_mgc_dpp_changes_only_mgc_and_portfolio_dollars():
    mnq = normalize_portfolio_ledger(_ledger("mnq", "2025-01-02", 10), _spec("mnq", "NQ", "MNQ", 2))
    mgc_10 = normalize_portfolio_ledger(_ledger("mgc", "2025-01-02", 10, asset="GC"), _spec("mgc", "GC", "MGC", 10))
    mgc_12 = normalize_portfolio_ledger(_ledger("mgc", "2025-01-02", 10, asset="GC"), _spec("mgc", "GC", "MGC", 12))

    assert mnq["gross_pnl_dollars"].iloc[0] == 20
    assert mgc_10["gross_pnl_dollars"].iloc[0] == 100
    assert mgc_12["gross_pnl_dollars"].iloc[0] == 120
    assert mnq["gross_pnl_dollars"].iloc[0] + mgc_12["gross_pnl_dollars"].iloc[0] == 140


def test_commission_scales_by_contract_count_once_and_dollar_pnl_is_not_multiplied_by_dpp():
    points = normalize_portfolio_ledger(
        _ledger("mnq", "2025-01-02", 10),
        _spec("mnq", "NQ", "MNQ", 2, commission=1.25),
        contract_count=3,
    )
    dollar_frame = pd.DataFrame(
        [{"trade_id": "dollar-1", "entry_time": "2025-01-02T09:30:00Z", "exit_time": "2025-01-02T09:45:00Z", "pnl_dollars": 50.0}]
    )
    dollars = normalize_portfolio_ledger(
        dollar_frame,
        PortfolioInstrumentSpec("cash", "GC", "GC", "MGC", 10, pnl_basis="dollars", source_timezone="UTC"),
    )

    assert points["gross_pnl_dollars"].iloc[0] == 60
    assert points["commission_dollars"].iloc[0] == pytest.approx(3.75)
    assert points["net_pnl_dollars"].iloc[0] == pytest.approx(56.25)
    assert dollars["gross_pnl_dollars"].iloc[0] == 50.0


def test_cross_asset_overlap_retained_and_same_asset_overlap_policies_are_asset_based():
    mnq = normalize_portfolio_ledger(_ledger("mnq", "2025-01-02", 10, overlap=True), _spec("mnq", "NQ", "MNQ", 2))
    mgc = normalize_portfolio_ledger(_ledger("mgc", "2025-01-02", 10, asset="GC", overlap=True), _spec("mgc", "GC", "MGC", 10))
    kept_cross, audit_cross = resolve_portfolio_overlaps(pd.concat([mnq, mgc], ignore_index=True))

    assert len(kept_cross) == 2
    assert "cross_asset" in set(audit_cross["overlap_type"])

    nq = normalize_portfolio_ledger(_ledger("nq", "2025-01-02", 10, overlap=True), _spec("nq", "NQ", "NQ", 20))
    same = pd.concat([mnq, nq], ignore_index=True)
    rejected, audit_reject = resolve_portfolio_overlaps(same, policy="REJECT_SAME_ASSET_OVERLAP")
    priority, audit_priority = resolve_portfolio_overlaps(same, policy="PRIORITY_KEEP_ONE", priority=["nq", "mnq"])
    stacked, audit_stack = resolve_portfolio_overlaps(same, policy="ALLOW_STACKING")

    assert len(rejected) == 1
    assert audit_reject.iloc[-1]["decision"] == "DROP"
    assert priority["strategy_id"].tolist() == ["nq"]
    assert audit_priority.iloc[-1]["priority_reason"] == "priority winner"
    assert len(stacked) == 2
    assert audit_stack.iloc[-1]["gross_asset_exposure"] == 2
    assert audit_stack.iloc[-1]["net_asset_exposure"] == 2


def test_account_day_aggregation_counts_one_winning_day_and_one_eod_floor_move():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    settings = LifecycleSettings(start_mode="funded")
    mnq = normalize_portfolio_ledger(_ledger("mnq", "2025-01-02", 200, overlap=True), _spec("mnq", "NQ", "MNQ", 2))
    mgc = normalize_portfolio_ledger(_ledger("mgc", "2025-01-02", -10, asset="GC", overlap=True), _spec("mgc", "GC", "MGC", 10))
    account_days = build_portfolio_account_day_ledger(pd.concat([mnq, mgc], ignore_index=True), plan, settings, risk_mode="REALIZED_PNL_ONLY")

    assert len(account_days) == 1
    assert account_days.iloc[0]["net_pnl_dollars"] == 300
    assert account_days.iloc[0]["winning_days_after"] == 1
    assert account_days.iloc[0]["floor_after"] == 48300


def test_no_payout_after_failure_in_portfolio_lifecycle_trace():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    settings = LifecycleSettings(start_mode="funded", desired_payout=1500)
    losing = normalize_portfolio_ledger(_ledger("mnq", "2025-01-02", -2000), _spec("mnq", "NQ", "MNQ", 2))

    summary, _days, trace = simulate_portfolio_lifecycle(losing, plan, settings)

    assert bool(summary.iloc[0]["failed"])
    payout_rows = trace[pd.to_numeric(trace.get("trader_cash", 0), errors="coerce").fillna(0) > 0]
    failure_rows = trace[trace.get("failure", False).astype(str).str.lower().isin({"true", "1"})]
    if not payout_rows.empty and not failure_rows.empty:
        assert payout_rows["payout_event_order"].max() < failure_rows["payout_event_order"].min()


def test_failure_ordering_with_overlapping_positions_marks_missing_mae_unknown_and_conservative_bound_separate():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    settings = LifecycleSettings(start_mode="funded")
    frame = pd.concat(
        [
            normalize_portfolio_ledger(_ledger("mnq", "2025-01-02", 10, overlap=True).drop(columns=["mae_points"]), _spec("mnq", "NQ", "MNQ", 2)),
            normalize_portfolio_ledger(_ledger("mgc", "2025-01-02", -10, asset="GC", overlap=True), _spec("mgc", "GC", "MGC", 10)),
        ],
        ignore_index=True,
    )

    days = build_portfolio_account_day_ledger(frame, plan, settings, risk_mode="CONSERVATIVE_OVERLAP_MAE_BOUND")

    assert days.iloc[0]["strict_status"] == "UNKNOWN"
    assert days.iloc[0]["risk_mode_label"] == "conservative bound, not exact"
    with pytest.raises(ValueError, match="EXACT_INTRATRADE"):
        simulate_portfolio_lifecycle(frame, plan, settings, risk_mode="EXACT_INTRATRADE")


def test_paired_date_resampling_preserves_same_date_combinations_and_seed_reproducibility():
    ledgers = {
        "mnq": pd.concat([_ledger("mnq", "2025-01-02", 1), _ledger("mnq", "2025-02-03", 2)], ignore_index=True),
        "mgc": pd.concat([_ledger("mgc", "2025-01-02", 3, asset="GC"), _ledger("mgc", "2025-02-03", 4, asset="GC")], ignore_index=True),
    }
    first, manifest = build_joint_portfolio_paths(ledgers, path_count=2, seed=7, trades_per_path=3)
    second, _ = build_joint_portfolio_paths(ledgers, path_count=2, seed=7, trades_per_path=3)

    assert manifest["common_date_count"].iloc[0] == 2
    assert manifest["common_month_count"].iloc[0] == 2
    assert first[0]["mnq"]["source_session_date"].tolist() == first[0]["mgc"]["source_session_date"].tolist()
    assert first[0]["mnq"]["source_session_date"].tolist() == second[0]["mnq"]["source_session_date"].tolist()


def test_allocation_changes_reuse_source_paths_and_independent_mode_is_labelled_unverified():
    ledgers = {
        "mnq": pd.concat([_ledger("mnq", "2025-01-02", 1), _ledger("mnq", "2025-02-03", 2)], ignore_index=True),
        "mgc": pd.concat([_ledger("mgc", "2025-01-02", 3, asset="GC"), _ledger("mgc", "2025-02-03", 4, asset="GC")], ignore_index=True),
    }
    paths, _paired_manifest = build_joint_portfolio_paths(ledgers, path_count=1, seed=9, trades_per_path=2)
    _independent_paths, independent_manifest = build_joint_portfolio_paths(ledgers, path_count=1, seed=9, mode="INDEPENDENT_SOURCE_PATHS", trades_per_path=2)
    specs = {"mnq": _spec("mnq", "NQ", "MNQ", 2), "mgc": _spec("mgc", "GC", "MGC", 10)}
    allocations = build_allocation_grid({"mnq": [0, 1], "mgc": [0, 1]}, max_combinations=4)
    allocation_one = combine_portfolio_path(paths[0], specs, allocations[1], portfolio_path_id=0)
    allocation_three = combine_portfolio_path(paths[0], specs, allocations[3], portfolio_path_id=0)

    assert allocation_one["source_sequence_hash"].unique().tolist() == allocation_three["source_sequence_hash"].unique().tolist()[:1]
    assert set(independent_manifest["dependence_label"]) == {"CROSS_STRATEGY_DEPENDENCE_UNVERIFIED"}
    with pytest.raises(ValueError, match="cap"):
        build_allocation_grid({"mnq": [0, 1, 2], "mgc": [0, 1, 2]}, max_combinations=4)


def test_single_non_overlapping_mnq_strategy_matches_existing_lifecycle_result():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    settings = LifecycleSettings(start_mode="funded")
    ledger = normalize_portfolio_ledger(_ledger("mnq", "2025-01-02", 100), _spec("mnq", "NQ", "MNQ", 2))
    portfolio_summary, _days, _trace = simulate_portfolio_lifecycle(ledger, plan, settings)
    legacy_result, _months, _events = simulate_lifecycle_path(
        portfolio_trade_ledger_to_lifecycle_trades(ledger),
        plan,
        contracts=1,
        settings=settings,
        dollars_per_point=1.0,
    )

    assert portfolio_summary.iloc[0]["ending_balance"] == legacy_result.ending_balance
    assert portfolio_summary.iloc[0]["net_cash"] == legacy_result.net_cash


def test_existing_forward_prefix_and_target_pf_remain_unchanged():
    one_rr = build_master_path(ForwardScenario(rr_config_id="1rr", july_candidate_count=0, august_candidate_count=0))
    one_half = build_master_path(ForwardScenario(rr_config_id="1_5rr", july_candidate_count=0, august_candidate_count=0))
    calibrated = build_master_path(ForwardScenario(rr_config_id="1rr", july_candidate_count=2, august_candidate_count=2))

    assert one_rr["pnl_points"].astype(float).tolist() == [150.0, -200.0]
    assert one_half["pnl_points"].astype(float).tolist() == [0.0, -200.0]
    assert float(calibrated["achieved_weighted_source_pf"].iloc[0]) == pytest.approx(1.50, abs=0.01)
