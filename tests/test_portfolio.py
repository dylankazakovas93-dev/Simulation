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
    canonical_asset_suggestion,
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


def _timed_ledger(
    strategy: str,
    date: str,
    pnl_points: float,
    *,
    entry: str,
    exit_: str,
    asset: str = "NQ",
    mae_points: float | None = 2.0,
) -> pd.DataFrame:
    row = {
        "trade_id": f"{strategy}-{date}-{entry}",
        "source_session_date": date,
        "entry_time": f"{date}T{entry}:00Z",
        "exit_time": f"{date}T{exit_}:00Z",
        "direction": "long",
        "pnl_points": pnl_points,
        "mfe_points": 12.0,
    }
    if mae_points is not None:
        row["mae_points"] = mae_points
    return pd.DataFrame([row])


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
        "mnq": pd.concat(
            [
                _ledger("mnq", "2025-01-02", 1),
                _ledger("mnq", "2025-01-03", 2),
                _ledger("mnq", "2025-02-03", 3),
                _ledger("mnq", "2025-02-04", 4),
            ],
            ignore_index=True,
        ),
        "mgc": pd.concat(
            [
                _ledger("mgc", "2025-01-02", 3, asset="GC"),
                _ledger("mgc", "2025-01-03", 4, asset="GC"),
                _ledger("mgc", "2025-02-03", 5, asset="GC"),
                _ledger("mgc", "2025-02-04", 6, asset="GC"),
            ],
            ignore_index=True,
        ),
    }
    first, manifest = build_joint_portfolio_paths(ledgers, path_count=2, seed=7, trades_per_path=3)
    second, _ = build_joint_portfolio_paths(ledgers, path_count=2, seed=7, trades_per_path=3)

    assert manifest["common_date_count"].iloc[0] == 4
    assert manifest["common_month_count"].iloc[0] == 2
    assert first[0]["mnq"]["source_session_date"].tolist() == first[0]["mgc"]["source_session_date"].tolist()
    assert first[0]["mnq"]["source_session_date"].tolist() == second[0]["mnq"]["source_session_date"].tolist()


def test_union_calendar_blocks_keep_single_strategy_dates_and_report_coverage():
    ledgers = {
        "mnq": pd.concat([_ledger("mnq", "2025-01-02", 1), _ledger("mnq", "2025-01-03", 2)], ignore_index=True),
        "mgc": pd.concat([_ledger("mgc", "2025-01-03", 3, asset="GC"), _ledger("mgc", "2025-01-06", 4, asset="GC")], ignore_index=True),
    }

    paths, manifest = build_joint_portfolio_paths(ledgers, path_count=1, seed=0, trades_per_path=6, forecast_start_date="2026-01-02")
    original_dates = pd.concat([paths[0]["mnq"], paths[0]["mgc"]], ignore_index=True)["original_source_session_date"].tolist()

    assert set(original_dates) == {"2025-01-02", "2025-01-03", "2025-01-06"}
    assert manifest["union_date_count"].iloc[0] == 3
    assert manifest["full_common_date_count"].iloc[0] == 1
    assert manifest["coactive_date_count"].iloc[0] == 1


def test_repeated_blocks_shift_to_unique_synthetic_dates_preserving_duration_and_overlap():
    ledgers = {
        "mnq": _timed_ledger("mnq", "2025-01-02", 1, entry="09:30", exit_="10:30"),
        "mgc": _timed_ledger("mgc", "2025-01-02", 2, entry="09:45", exit_="10:15", asset="GC"),
    }

    paths, _manifest = build_joint_portfolio_paths(ledgers, path_count=1, seed=1, trades_per_path=3, forecast_start_date="2026-01-02")
    combined = pd.concat([paths[0]["mnq"], paths[0]["mgc"]], ignore_index=True)

    assert combined.groupby("block_occurrence_id")["synthetic_account_date"].nunique().eq(1).all()
    assert combined[["block_occurrence_id", "synthetic_account_date"]].drop_duplicates()["synthetic_account_date"].is_unique
    assert (pd.to_datetime(paths[0]["mnq"]["exit_time"]) - pd.to_datetime(paths[0]["mnq"]["entry_time"])).dt.total_seconds().eq(3600).all()
    assert pd.to_datetime(paths[0]["mnq"]["entry_time"]).dt.time.astype(str).eq("09:30:00").all()
    assert pd.to_datetime(paths[0]["mgc"]["entry_time"]).dt.time.astype(str).eq("09:45:00").all()


def test_seasonal_sampling_uses_forecast_month_and_errors_when_unavailable():
    ledgers = {
        "mnq": pd.concat([_ledger("mnq", "2025-01-02", 1), _ledger("mnq", "2025-02-03", 2)], ignore_index=True),
        "mgc": pd.concat([_ledger("mgc", "2025-01-02", 3, asset="GC"), _ledger("mgc", "2025-02-03", 4, asset="GC")], ignore_index=True),
    }

    paths, _manifest = build_joint_portfolio_paths(
        ledgers,
        path_count=1,
        seed=7,
        trades_per_path=2,
        seasonal_month_aware=True,
        forecast_start_date="2026-02-02",
    )

    assert set(paths[0]["mnq"]["original_source_session_date"]) == {"2025-02-03"}
    with pytest.raises(ValueError, match="forecast month 3"):
        build_joint_portfolio_paths(
            ledgers,
            path_count=1,
            seed=7,
            trades_per_path=1,
            seasonal_month_aware=True,
            forecast_start_date="2026-03-02",
        )


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


def test_strategy_path_ensemble_is_generated_once_before_allocation_grid():
    ledgers = {
        "mnq": pd.concat(
            [
                _ledger("mnq", "2025-01-02", 1),
                _ledger("mnq", "2025-01-03", 2),
                _ledger("mnq", "2025-02-03", 3),
                _ledger("mnq", "2025-02-04", 4),
            ],
            ignore_index=True,
        ),
        "mgc": pd.concat(
            [
                _ledger("mgc", "2025-01-02", 3, asset="GC"),
                _ledger("mgc", "2025-01-03", 4, asset="GC"),
                _ledger("mgc", "2025-02-03", 5, asset="GC"),
                _ledger("mgc", "2025-02-04", 6, asset="GC"),
            ],
            ignore_index=True,
        ),
    }
    paths, manifest = build_joint_portfolio_paths(
        ledgers,
        path_count=3,
        seed=17,
        trades_per_path=4,
        seasonal_month_aware=True,
    )
    specs = {"mnq": _spec("mnq", "NQ", "MNQ", 2), "mgc": _spec("mgc", "GC", "MGC", 10)}
    allocations = build_allocation_grid({"mnq": [1], "mgc": [0, 1]}, max_combinations=2)

    path_zero_allocation_zero = combine_portfolio_path(paths[0], specs, allocations[0], portfolio_path_id=0)
    path_zero_allocation_one = combine_portfolio_path(paths[0], specs, allocations[1], portfolio_path_id=0)
    path_one_allocation_one = combine_portfolio_path(paths[1], specs, allocations[1], portfolio_path_id=1)

    assert len(paths) == 3
    assert set(path_zero_allocation_one["portfolio_path_id"]) == {0}
    assert set(path_one_allocation_one["portfolio_path_id"]) == {1}
    assert path_zero_allocation_zero[path_zero_allocation_zero["strategy_id"].eq("mnq")]["source_trade_id"].tolist() == path_zero_allocation_one[path_zero_allocation_one["strategy_id"].eq("mnq")]["source_trade_id"].tolist()
    assert manifest["seasonal_month_aware"].eq(True).all()


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


def test_realized_first_exit_breach_cannot_be_repaired_by_later_gain():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    settings = LifecycleSettings(start_mode="funded")
    losing = normalize_portfolio_ledger(_timed_ledger("mnq", "2025-01-02", -2100, entry="09:30", exit_="09:45"), _spec("mnq", "NQ", "MNQ", 1))
    later_gain = normalize_portfolio_ledger(_timed_ledger("mgc", "2025-01-02", 1000, entry="10:00", exit_="10:15", asset="GC"), _spec("mgc", "GC", "MGC", 1))

    summary, days, trace = simulate_portfolio_lifecycle(pd.concat([losing, later_gain], ignore_index=True), plan, settings)

    assert bool(summary.iloc[0]["failed"])
    assert summary.iloc[0]["ending_balance"] == 47_900
    assert days.iloc[0]["realized_only_failure"]
    assert "trade_skipped_after_failure" in set(trace["record_type"])


def test_missing_mae_never_reports_strict_survived():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    settings = LifecycleSettings(start_mode="funded")
    frame = normalize_portfolio_ledger(_timed_ledger("mnq", "2025-01-02", 0, entry="09:30", exit_="10:30", mae_points=None), _spec("mnq", "NQ", "MNQ", 1))

    days = build_portfolio_account_day_ledger(frame, plan, settings, risk_mode="CONSERVATIVE_OVERLAP_MAE_BOUND")

    assert days.iloc[0]["strict_status"] == "UNKNOWN"


def test_non_overlapping_same_day_mae_bounds_are_not_summed():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    settings = LifecycleSettings(start_mode="funded")
    first = normalize_portfolio_ledger(_timed_ledger("mnq", "2025-01-02", 0, entry="09:30", exit_="09:45", mae_points=1200), _spec("mnq", "NQ", "MNQ", 1))
    second = normalize_portfolio_ledger(_timed_ledger("mgc", "2025-01-02", 0, entry="10:00", exit_="10:15", asset="GC", mae_points=1200), _spec("mgc", "GC", "MGC", 1))

    summary, days, _trace = simulate_portfolio_lifecycle(pd.concat([first, second], ignore_index=True), plan, settings, risk_mode="CONSERVATIVE_OVERLAP_MAE_BOUND")

    assert not bool(summary.iloc[0]["failed"])
    assert not days.iloc[0]["conservative_bound_failure"]


def test_overlapping_mae_cluster_bound_can_fail_and_block_later_trades():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    settings = LifecycleSettings(start_mode="funded")
    first = normalize_portfolio_ledger(_timed_ledger("mnq", "2025-01-02", 0, entry="09:30", exit_="10:30", mae_points=1100), _spec("mnq", "NQ", "MNQ", 1))
    second = normalize_portfolio_ledger(_timed_ledger("mgc", "2025-01-02", 0, entry="09:45", exit_="10:15", asset="GC", mae_points=1100), _spec("mgc", "GC", "MGC", 1))
    later = normalize_portfolio_ledger(_timed_ledger("mes", "2025-01-02", 500, entry="11:00", exit_="11:15", asset="ES", mae_points=1), _spec("mes", "ES", "MES", 1))

    summary, days, trace = simulate_portfolio_lifecycle(pd.concat([first, second, later], ignore_index=True), plan, settings, risk_mode="CONSERVATIVE_OVERLAP_MAE_BOUND")

    assert bool(summary.iloc[0]["failed"])
    assert days.iloc[0]["conservative_bound_failure"]
    assert "trade_skipped_after_failure" in set(trace["record_type"])


def test_account_trace_day_ledger_and_result_reconcile():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    settings = LifecycleSettings(start_mode="funded")
    frame = normalize_portfolio_ledger(_ledger("mnq", "2025-01-02", 100), _spec("mnq", "NQ", "MNQ", 2))

    summary, days, trace = simulate_portfolio_lifecycle(frame, plan, settings)

    assert summary.iloc[0]["ending_balance"] == days.iloc[-1]["balance_after"]
    assert trace.iloc[-1]["balance_after"] == days.iloc[-1]["balance_after"]


def test_points_dollars_conflict_blocks_without_confirmation_and_source_contract_scaling():
    frame = pd.DataFrame(
        [
            {
                "trade_id": "conflict",
                "source_session_date": "2025-01-02",
                "entry_time": "2025-01-02T09:30:00Z",
                "exit_time": "2025-01-02T09:45:00Z",
                "pnl_points": 10.0,
                "pnl_dollars": 100.0,
                "mae_points": 1.0,
                "mfe_points": 2.0,
            }
        ]
    )
    unconfirmed = PortfolioInstrumentSpec("mnq", "NQ", "NQ", "MNQ", 2, pnl_basis="dollars", source_contract_count=1)
    confirmed = PortfolioInstrumentSpec("mnq", "NQ", "NQ", "MNQ", 2, pnl_basis="dollars", source_contract_count=5, pnl_basis_confirmed=True)

    with pytest.raises(ValueError, match="explicitly confirm"):
        normalize_portfolio_ledger(frame, unconfirmed)
    normalized = normalize_portfolio_ledger(frame, confirmed, contract_count=2)

    assert normalized.iloc[0]["gross_pnl_dollars"] == 40.0
    assert normalized.iloc[0]["expected_source_pnl_dollars"] == 100.0


def test_mae_mfe_sign_convention_validation():
    frame = _timed_ledger("mnq", "2025-01-02", 1, entry="09:30", exit_="09:45", mae_points=-2)

    with pytest.raises(ValueError, match="POSITIVE_MAGNITUDES"):
        normalize_portfolio_ledger(frame, _spec("mnq", "NQ", "MNQ", 2))
    signed_spec = PortfolioInstrumentSpec("mnq", "NQ", "NQ", "MNQ", 2, mae_mfe_convention="SIGNED_MAE_NEGATIVE_MFE_POSITIVE")
    normalized = normalize_portfolio_ledger(frame, signed_spec)

    assert normalized.iloc[0]["adverse_excursion_points_abs"] == 2


def test_priority_displacement_rewrites_final_audit_decision():
    early = normalize_portfolio_ledger(_timed_ledger("mnq", "2025-01-02", 1, entry="09:30", exit_="11:00"), _spec("mnq", "NQ", "MNQ", 2))
    later_priority = normalize_portfolio_ledger(_timed_ledger("nq", "2025-01-02", 1, entry="09:45", exit_="10:00"), _spec("nq", "NQ", "NQ", 20))

    kept, audit = resolve_portfolio_overlaps(pd.concat([early, later_priority], ignore_index=True), policy="PRIORITY_KEEP_ONE", priority=["nq", "mnq"])

    assert kept["strategy_id"].tolist() == ["nq"]
    assert audit.set_index("strategies").loc["mnq|nq"]["decision"].tolist().count("DROP") == 1
    assert audit[audit["priority_reason"].eq("priority winner")]["decision"].tolist() == ["KEEP"]


def test_known_contract_symbols_suggest_underlying_asset_without_overriding_user_value():
    suggestion = canonical_asset_suggestion("MNQ", "MNQ")

    assert suggestion["suggested_asset_id"] == "NQ"
    assert suggestion["entered_asset_id"] == "MNQ"
    assert suggestion["warning"]


def test_existing_forward_prefix_and_target_pf_remain_unchanged():
    one_rr = build_master_path(ForwardScenario(rr_config_id="1rr", july_candidate_count=0, august_candidate_count=0))
    one_half = build_master_path(ForwardScenario(rr_config_id="1_5rr", july_candidate_count=0, august_candidate_count=0))
    calibrated = build_master_path(ForwardScenario(rr_config_id="1rr", july_candidate_count=2, august_candidate_count=2))

    assert one_rr["pnl_points"].astype(float).tolist() == [150.0, -200.0]
    assert one_half["pnl_points"].astype(float).tolist() == [0.0, -200.0]
    assert float(calibrated["achieved_weighted_source_pf"].iloc[0]) == pytest.approx(1.50, abs=0.01)
