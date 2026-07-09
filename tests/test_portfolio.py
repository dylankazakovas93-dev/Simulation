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
    portfolio_export_frames,
    is_supported_portfolio_lifecycle_plan,
    portfolio_lifecycle_plan_unsupported_reason,
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


def _multi_day_portfolio_ledger(values: list[float], *, start_day: str = "2025-01-02") -> pd.DataFrame:
    start = pd.Timestamp(start_day)
    frames = []
    for index, value in enumerate(values):
        day = (start + pd.offsets.BDay(index)).date().isoformat()
        frames.append(normalize_portfolio_ledger(_ledger("mnq", day, value), _spec("mnq", "NQ", "MNQ", 1)))
    return pd.concat(frames, ignore_index=True)


def _assert_portfolio_reconciles(summary: pd.DataFrame, days: pd.DataFrame, trace: pd.DataFrame) -> None:
    result = summary.iloc[0]
    executed_trade_pnl = trace[trace["record_type"].eq("trade")]["pnl_dollars"].sum()
    assert executed_trade_pnl == pytest.approx(days["net_pnl_dollars"].sum())
    for row in days.itertuples(index=False):
        assert row.balance_after == pytest.approx(row.balance_before + row.net_pnl_dollars - row.payout_gross_account_debit)
    assert trace[trace["record_type"].eq("payout")]["gross_account_debit"].sum() == pytest.approx(days["payout_gross_account_debit"].sum())
    assert trace[trace["record_type"].eq("payout")]["trader_cash"].sum() == pytest.approx(result["total_payouts"])
    assert days.iloc[-1]["balance_after"] == pytest.approx(result["ending_balance"])
    assert trace.iloc[-1]["balance_after"] == pytest.approx(result["ending_balance"])
    assert trace.iloc[-1]["total_payouts"] == pytest.approx(result["total_payouts"])
    assert trace.iloc[-1]["total_fees"] == pytest.approx(result["total_fees"])
    assert trace.iloc[-1]["net_cash"] == pytest.approx(result["net_cash"])
    assert result["net_cash"] == pytest.approx(result["total_payouts"] - result["total_fees"])


def test_intraday_trailing_funded_plan_is_excluded_and_backend_rejects_it():
    plan = default_lifecycle_plans()["TakeProfitTrader - PRO 50K - Funded only"]
    ledger = _multi_day_portfolio_ledger([500])

    assert not is_supported_portfolio_lifecycle_plan(plan)
    assert "Intraday-trailing" in (portfolio_lifecycle_plan_unsupported_reason(plan) or "")
    with pytest.raises(ValueError, match="Intraday-trailing"):
        simulate_portfolio_lifecycle(ledger, plan, LifecycleSettings(start_mode="funded"))


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


def test_failure_before_first_payout_blocks_later_payouts_and_winning_history():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    settings = LifecycleSettings(start_mode="funded", desired_payout=1500)
    losing = _multi_day_portfolio_ledger([-2100, 1000, 1000, 1000, 1000, 1000])

    summary, days, trace = simulate_portfolio_lifecycle(losing, plan, settings)

    assert bool(summary.iloc[0]["failed"])
    assert not trace[trace["record_type"].eq("failure")].empty
    assert trace[trace["record_type"].eq("payout")].empty
    assert summary.iloc[0]["total_payouts"] == 0
    assert days.iloc[-1]["winning_days_after"] == 0
    assert not days.iloc[-1]["payout_eligible_after_day"]
    assert trace[trace["record_type"].eq("trade_skipped_after_failure")]["pnl_dollars"].sum() == 0


def test_successful_portfolio_payout_profit_split_and_account_debit_reconcile():
    plan = default_lifecycle_plans()["Alpha Futures - Advanced 50K - Funded only"]
    settings = LifecycleSettings(start_mode="funded", desired_payout=900)
    ledger = _multi_day_portfolio_ledger([600, 600, 600, 600, 600])

    summary, days, trace = simulate_portfolio_lifecycle(ledger, plan, settings)
    payouts = trace[trace["record_type"].eq("payout")]

    assert not payouts.empty
    assert summary.iloc[0]["total_payouts"] == pytest.approx(900)
    assert payouts.iloc[0]["trader_cash"] == pytest.approx(900)
    assert payouts.iloc[0]["gross_account_debit"] == pytest.approx(1000)
    assert days.iloc[-1]["balance_after"] == pytest.approx(52_000)
    assert summary.iloc[0]["ending_balance"] == pytest.approx(days.iloc[-1]["balance_after"])


def test_successful_payout_then_later_trading_integrated_reconciliation():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    ledger = _multi_day_portfolio_ledger([1000, 1000, 1000, 1000, 1000, 100, 100])

    summary, days, trace = simulate_portfolio_lifecycle(ledger, plan, LifecycleSettings(start_mode="funded", desired_payout=1500, prior_fees=149))

    payout_rows = trace[trace["record_type"].eq("payout")]
    assert not payout_rows.empty
    assert len(days) >= 6
    assert days.iloc[-1]["payout_gross_account_debit"] == 0
    assert not bool(summary.iloc[0]["failed"])
    _assert_portfolio_reconciles(summary, days, trace)


def test_payout_cap_count_cap_and_required_cushion_are_enforced():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    eligible = _multi_day_portfolio_ledger([1000, 1000, 1000, 1000, 1000])

    capped_summary, _days, capped_trace = simulate_portfolio_lifecycle(eligible, plan, LifecycleSettings(start_mode="funded", desired_payout=0))
    payout = capped_trace[capped_trace["record_type"].eq("payout")].iloc[0]
    assert payout["gross_account_debit"] == pytest.approx(1500)
    assert capped_summary.iloc[0]["total_payouts"] == pytest.approx(1500)

    count_summary, _days, count_trace = simulate_portfolio_lifecycle(
        eligible,
        plan,
        LifecycleSettings(start_mode="funded", payouts_already_taken=6, desired_payout=0),
    )
    assert count_trace[count_trace.get("record_type", "").eq("payout")].empty
    assert count_summary.iloc[0]["total_payouts"] == 0

    cushion_summary, _days, cushion_trace = simulate_portfolio_lifecycle(
        eligible,
        plan,
        LifecycleSettings(start_mode="funded", desired_payout=1500, required_cushion=4_000),
    )
    assert cushion_trace[cushion_trace.get("record_type", "").eq("payout")].empty
    assert cushion_summary.iloc[0]["total_payouts"] == 0


def test_current_funded_account_state_changes_portfolio_result():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    ledger = _multi_day_portfolio_ledger([1000])
    fresh, _days, fresh_trace = simulate_portfolio_lifecycle(ledger, plan, LifecycleSettings(start_mode="funded", desired_payout=500))
    live, _days, live_trace = simulate_portfolio_lifecycle(
        ledger,
        plan,
        LifecycleSettings(
            start_mode="funded",
            current_balance=54_000,
            current_floor=50_100,
            current_winning_days=4,
            current_daily_profits=(300, 300, 300, 300),
            desired_payout=500,
        ),
    )

    assert fresh_trace[fresh_trace.get("record_type", "").eq("payout")].empty
    assert not live_trace[live_trace["record_type"].eq("payout")].empty
    assert live.iloc[0]["ending_balance"] != fresh.iloc[0]["ending_balance"]


def test_current_highest_winning_day_blocks_consistency_payout_when_history_is_incomplete():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    ledger = _multi_day_portfolio_ledger([300])
    settings = LifecycleSettings(
        start_mode="funded",
        current_balance=54_000,
        current_floor=50_100,
        current_winning_days=4,
        current_daily_profits=(),
        current_highest_winning_day=3_000,
        desired_payout=500,
    )

    summary, days, trace = simulate_portfolio_lifecycle(ledger, plan, settings)

    assert trace[trace["record_type"].eq("payout")].empty
    assert summary.iloc[0]["total_payouts"] == 0
    assert not days.iloc[-1]["payout_eligible_after_day"]


def test_payout_then_later_failure_ordering_and_failure_first_blocks_later_payouts():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    payout_then_fail = _multi_day_portfolio_ledger([1000, 1000, 1000, 1000, 1000, -4000])
    summary, days, trace = simulate_portfolio_lifecycle(payout_then_fail, plan, LifecycleSettings(start_mode="funded", desired_payout=1500))

    assert bool(summary.iloc[0]["failed"])
    assert summary.iloc[0]["total_payouts"] == pytest.approx(1500)
    assert summary.iloc[0]["first_payout_order"] < summary.iloc[0]["first_failure_order"]
    assert days.iloc[-1]["trade_count"] == 1

    failure_first = _multi_day_portfolio_ledger([-2100, 1000, 1000, 1000, 1000, 1000])
    first_summary, first_days, first_trace = simulate_portfolio_lifecycle(failure_first, plan, LifecycleSettings(start_mode="funded", desired_payout=1500))
    assert bool(first_summary.iloc[0]["failed"])
    assert first_summary.iloc[0]["total_payouts"] == 0
    assert first_trace[first_trace.get("record_type", "").eq("payout")].empty
    assert len(first_days) == 1


def test_payout_then_later_failure_integrated_reconciliation_and_skips():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    ledger = _multi_day_portfolio_ledger([1000, 1000, 1000, 1000, 1000, -4000, 1000, 1000])

    summary, days, trace = simulate_portfolio_lifecycle(ledger, plan, LifecycleSettings(start_mode="funded", desired_payout=1500, prior_fees=149))

    failure_rows = trace[trace["record_type"].eq("failure")]
    skipped_rows = trace[trace["record_type"].eq("trade_skipped_after_failure")]
    assert not trace[trace["record_type"].eq("payout")].empty
    assert not failure_rows.empty
    assert summary.iloc[0]["first_payout_order"] < summary.iloc[0]["first_failure_order"]
    assert summary.iloc[0]["total_payouts"] == pytest.approx(1500)
    assert failure_rows.iloc[-1]["total_payouts"] == pytest.approx(summary.iloc[0]["total_payouts"])
    assert failure_rows.iloc[-1]["total_fees"] == pytest.approx(summary.iloc[0]["total_fees"])
    assert failure_rows.iloc[-1]["net_cash"] == pytest.approx(summary.iloc[0]["net_cash"])
    assert not skipped_rows.empty
    assert skipped_rows["pnl_dollars"].sum() == 0
    assert len(days) == 6
    assert days.iloc[-1]["trade_count"] == 1
    _assert_portfolio_reconciles(summary, days, trace)


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


def test_missing_mae_in_realized_only_is_strict_unknown_but_known_failure_overrides_unknown():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    missing = normalize_portfolio_ledger(_timed_ledger("mnq", "2025-01-02", 100, entry="09:30", exit_="09:45", mae_points=None), _spec("mnq", "NQ", "MNQ", 1))
    days = build_portfolio_account_day_ledger(missing, plan, LifecycleSettings(start_mode="funded"), risk_mode="REALIZED_PNL_ONLY")
    assert days.iloc[0]["strict_status"] == "UNKNOWN"
    assert days.iloc[0]["realized_only_status"] == "SURVIVED"
    assert days.iloc[0]["missing_mae_trade_count"] == 1

    known_loss = normalize_portfolio_ledger(_timed_ledger("mnq", "2025-01-02", -2100, entry="10:00", exit_="10:15", mae_points=None), _spec("mnq", "NQ", "MNQ", 1))
    fail_days = build_portfolio_account_day_ledger(pd.concat([missing, known_loss], ignore_index=True), plan, LifecycleSettings(start_mode="funded"), risk_mode="REALIZED_PNL_ONLY")
    assert fail_days.iloc[0]["strict_status"] == "FAILED"
    assert fail_days.iloc[0]["realized_only_status"] == "FAILED"


def test_realized_only_with_complete_mae_does_not_claim_strict_survival():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    frame = normalize_portfolio_ledger(
        _timed_ledger("mnq", "2025-01-02", 0, entry="09:30", exit_="09:45", mae_points=2500),
        _spec("mnq", "NQ", "MNQ", 1),
    )

    days = build_portfolio_account_day_ledger(frame, plan, LifecycleSettings(start_mode="funded"), risk_mode="REALIZED_PNL_ONLY")

    assert days.iloc[0]["realized_only_status"] == "SURVIVED"
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


def test_terminal_accounting_reconciles_executed_trace_only():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    first = normalize_portfolio_ledger(_timed_ledger("mnq", "2025-01-02", -2100, entry="09:30", exit_="09:45"), _spec("mnq", "NQ", "MNQ", 1))
    later = normalize_portfolio_ledger(_timed_ledger("mgc", "2025-01-02", 1000, entry="10:00", exit_="10:15", asset="GC"), _spec("mgc", "GC", "MGC", 1))

    summary, days, trace = simulate_portfolio_lifecycle(pd.concat([first, later], ignore_index=True), plan, LifecycleSettings(start_mode="funded"))

    executed_trade_pnl = trace[trace["record_type"].eq("trade")]["pnl_dollars"].sum()
    assert executed_trade_pnl == pytest.approx(days["net_pnl_dollars"].sum())
    assert days.iloc[0]["trade_count"] == 1
    assert days.iloc[0]["gross_pnl_dollars"] == pytest.approx(-2100)
    assert summary.iloc[0]["ending_balance"] == days.iloc[-1]["balance_after"]
    assert trace[trace["record_type"].eq("payout")]["trader_cash"].sum() == summary.iloc[0]["total_payouts"]
    assert summary.iloc[0]["net_cash"] == pytest.approx(summary.iloc[0]["total_payouts"] - summary.iloc[0]["total_fees"])


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


def test_three_trade_same_asset_chain_keeps_non_overlapping_a_and_c():
    a = normalize_portfolio_ledger(_timed_ledger("a", "2025-01-02", 1, entry="09:30", exit_="10:00"), _spec("a", "NQ", "MNQ", 2))
    b = normalize_portfolio_ledger(_timed_ledger("b", "2025-01-02", 1, entry="09:45", exit_="10:15"), _spec("b", "NQ", "MNQ", 2))
    c = normalize_portfolio_ledger(_timed_ledger("c", "2025-01-02", 1, entry="10:00", exit_="10:30"), _spec("c", "NQ", "MNQ", 2))

    kept, audit = resolve_portfolio_overlaps(pd.concat([a, b, c], ignore_index=True), policy="REJECT_SAME_ASSET_OVERLAP")
    assert kept["strategy_id"].tolist() == ["a", "c"]
    assert audit[audit["decision"].eq("DROP")]["strategies"].tolist() == ["a|b|c"]

    priority_kept, _priority_audit = resolve_portfolio_overlaps(pd.concat([a, b, c], ignore_index=True), policy="PRIORITY_KEEP_ONE", priority=["b", "a", "c"])
    intervals = priority_kept.sort_values("entry_time")
    assert all(pd.Timestamp(left.exit_time) <= pd.Timestamp(right.entry_time) for left, right in zip(intervals.itertuples(), list(intervals.itertuples())[1:], strict=False))


def test_portfolio_exports_preserve_per_path_contributions_and_allocation_rates():
    trade_ledger = pd.concat(
        [
            normalize_portfolio_ledger(_ledger("mnq", "2025-01-02", 10), _spec("mnq", "NQ", "MNQ", 2), portfolio_path_id=0, allocation_id=0),
            normalize_portfolio_ledger(_ledger("mgc", "2025-01-02", 10, asset="GC"), _spec("mgc", "GC", "MGC", 10), portfolio_path_id=1, allocation_id=0),
        ],
        ignore_index=True,
    )
    path_results = pd.DataFrame(
        [
            {"portfolio_path_id": 0, "allocation_id": 0, "ending_balance": 51_000, "net_cash": 500, "total_payouts": 500, "failed": False, "first_payout_order": 2, "first_failure_order": pd.NA},
            {"portfolio_path_id": 1, "allocation_id": 0, "ending_balance": 49_000, "net_cash": 0, "total_payouts": 0, "failed": True, "first_payout_order": pd.NA, "first_failure_order": 1},
        ]
    )
    frames = portfolio_export_frames(
        specs=[_spec("mnq", "NQ", "MNQ", 2), _spec("mgc", "GC", "MGC", 10)],
        allocations=[PortfolioAllocation(0, {"mnq": 1, "mgc": 1})],
        dependency_manifest=pd.DataFrame(),
        strategy_path_manifest=pd.DataFrame(),
        trade_ledger=trade_ledger,
        overlap_audit=pd.DataFrame(),
        account_day_ledger=pd.DataFrame(),
        account_trace=pd.DataFrame(),
        path_results=path_results,
    )

    assert {"portfolio_path_id", "allocation_id", "strategy_id"} <= set(frames["portfolio_per_path_strategy_contribution"].columns)
    assert {"portfolio_path_id", "allocation_id", "asset_id"} <= set(frames["portfolio_per_path_asset_contribution"].columns)
    assert frames["portfolio_allocation_summary"].iloc[0]["path_count"] == 2
    assert frames["portfolio_allocation_summary"].iloc[0]["first_payout_rate"] == pytest.approx(0.5)


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
