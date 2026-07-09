from __future__ import annotations

import pandas as pd
import pytest

from sim_core.forward_master_path import (
    ForwardScenario,
    _apply_geometry_policy,
    build_master_path,
    build_monte_carlo_paths,
    executable_packet_pool,
    expected_weighted_pf,
    forecast_trading_dates,
    forward_strategy_ledger,
    load_source_library,
    load_realized_master_path,
    path_summary,
    run_forward_lifecycle_grid,
    select_realized_prefix,
    strategy_path_manifest,
    strategy_sequence_hash,
    validate_prefix_mode,
)
from sim_core.lifecycle import LifecycleSettings, default_lifecycle_plans
from sim_core.prop_rules import default_prop_rule_profiles


def test_combined_realized_artifact_has_exactly_four_rows():
    frame = load_realized_master_path()

    assert len(frame) == 4
    assert set(frame["rr_config_id"]) == {"1rr", "1_5rr"}


def test_selecting_each_rr_returns_two_rows_and_expected_sequences():
    frame = load_realized_master_path()
    one_rr = select_realized_prefix(frame, "1rr")
    one_half_rr = select_realized_prefix(frame, "1_5rr")

    assert len(one_rr) == 2
    assert len(one_half_rr) == 2
    assert one_rr["pnl_points"].tolist() == [150.0, -200.0]
    assert one_half_rr["pnl_points"].tolist() == [0.0, -200.0]
    assert one_rr["pnl_points"].sum() == -50.0
    assert one_half_rr["pnl_points"].sum() == -200.0
    assert one_rr.iloc[0]["exit_reason"] == "TP"
    assert one_half_rr.iloc[0]["exit_reason"] == "BE"
    assert set(frame[frame["session_date"] == "2026-07-08"]["exit_reason"]) == {"SL"}
    assert one_rr["event_group_id"].tolist() == one_half_rr["event_group_id"].tolist()


def test_rr_alternatives_cannot_be_combined_as_four_portfolio_trades():
    frame = load_realized_master_path()

    with pytest.raises(ValueError, match="exactly two realized rows"):
        select_realized_prefix(frame.assign(rr_config_id="1rr"), "1rr")


def test_legacy_anchor_and_realized_prefix_cannot_both_apply():
    with pytest.raises(ValueError, match="cannot both be applied"):
        validate_prefix_mode(use_legacy_anchor=True, use_realized_master_prefix=True)


def test_realized_rows_precede_synthetic_and_synthetic_starts_at_three():
    path = build_master_path(ForwardScenario(rr_config_id="1rr", july_candidate_count=2, august_candidate_count=2))

    assert path.head(2)["status"].tolist() == ["REALIZED", "REALIZED"]
    assert path[path["status"] == "SYNTHETIC"]["sequence_number"].min() == 3
    assert path[path["status"] == "REALIZED"]["source_trade_packet_id"].isna().all()
    assert path[path["status"] == "SYNTHETIC"]["source_trade_packet_id"].notna().all()


def test_fixed_master_seed_reproduces_visible_master_path_and_different_seed_changes_it():
    scenario = ForwardScenario(rr_config_id="1rr", master_seed=10, july_candidate_count=3, august_candidate_count=3)
    first = build_master_path(scenario)
    second = build_master_path(scenario)
    changed = build_master_path(ForwardScenario(rr_config_id="1rr", master_seed=11, july_candidate_count=3, august_candidate_count=3))

    pd.testing.assert_frame_equal(first, second)
    assert first["source_trade_packet_id"].fillna("").tolist() != changed["source_trade_packet_id"].fillna("").tolist()


def test_fixed_mc_seed_reproduces_ensemble():
    scenario = ForwardScenario(rr_config_id="1_5rr", mc_seed=99, path_count=5, july_candidate_count=2, august_candidate_count=2)

    first = strategy_path_manifest(build_monte_carlo_paths(scenario), scenario)
    second = strategy_path_manifest(build_monte_carlo_paths(scenario), scenario)
    changed_scenario = ForwardScenario(rr_config_id="1_5rr", mc_seed=100, path_count=5, july_candidate_count=2, august_candidate_count=2)
    changed = strategy_path_manifest(build_monte_carlo_paths(changed_scenario), changed_scenario)

    pd.testing.assert_frame_equal(first, second)
    assert first["source_packet_sequence"].tolist() != changed["source_packet_sequence"].tolist()


def test_same_strategy_paths_are_reused_across_contracts_firms_and_plans():
    plans_by_key = default_lifecycle_plans()
    plans = [
        plans_by_key["Apex Trader Funding - EOD PA 50K - Funded only"],
        plans_by_key["Alpha Futures - Advanced 50K - Funded only"],
    ]
    settings = {plan.key: LifecycleSettings(start_mode="funded") for plan in plans}
    scenario = ForwardScenario(rr_config_id="1rr", path_count=4, july_candidate_count=1, august_candidate_count=1)
    paths = build_monte_carlo_paths(scenario)
    summary, _monthly, _events, ledger = run_forward_lifecycle_grid(
        paths,
        plans,
        contract_values=[1, 4],
        settings_by_plan=settings,
        prefix_application_basis="ACCOUNT_STATE_BEFORE_PREFIX",
    )

    assert set(summary["contracts"]) == {1, 4}
    assert set(summary["firm"]) == {"Apex Trader Funding", "Alpha Futures"}
    assert summary["paths"].nunique() == 1
    assert int(summary["paths"].iloc[0]) == 4
    trade_ledger = ledger[ledger["record_type"] == "TRADE"]
    hashes = trade_ledger.groupby(["firm", "contracts", "account_path_id"])["source_trade_packet_id"].apply(lambda col: "|".join(col.fillna("").astype(str)))
    assert hashes.nunique() <= len(paths)


def test_prefix_application_basis_before_applies_and_after_does_not_double_count():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    settings = {plan.key: LifecycleSettings(start_mode="funded")}
    before_path = build_master_path(
        ForwardScenario(
            rr_config_id="1rr",
            july_candidate_count=0,
            august_candidate_count=0,
            prefix_application_basis="ACCOUNT_STATE_BEFORE_PREFIX",
        )
    )
    after_path = build_master_path(
        ForwardScenario(
            rr_config_id="1rr",
            july_candidate_count=0,
            august_candidate_count=0,
            prefix_application_basis="ACCOUNT_STATE_AFTER_PREFIX",
        )
    )

    before, _m1, _e1, _l1 = run_forward_lifecycle_grid(
        [before_path], [plan], contract_values=[1], settings_by_plan=settings, prefix_application_basis="ACCOUNT_STATE_BEFORE_PREFIX"
    )
    after, _m2, _e2, _l2 = run_forward_lifecycle_grid(
        [after_path], [plan], contract_values=[1], settings_by_plan=settings, prefix_application_basis="ACCOUNT_STATE_AFTER_PREFIX"
    )

    assert before.iloc[0]["avg_net_cash"] <= after.iloc[0]["avg_net_cash"]
    assert before_path["realized_prefix_net_points"].iloc[-1] == -50.0
    assert after_path["realized_prefix_net_points"].iloc[-1] == -50.0


def test_missing_realized_mae_mfe_is_explicit_and_1_5rr_be_is_gross_zero():
    path = build_master_path(ForwardScenario(rr_config_id="1_5rr", july_candidate_count=1, august_candidate_count=1))
    realized = path[path["status"] == "REALIZED"]

    assert realized["excursion_confidence"].eq("UNKNOWN_USER_CONFIRMED").all()
    assert realized["strict_barrier_status"].eq("UNKNOWN").all()
    assert realized.iloc[0]["exit_reason"] == "BE"
    assert realized.iloc[0]["pnl_points"] == 0.0


def test_historical_profiles_and_lifecycle_plans_remain_present():
    profiles = default_prop_rule_profiles()
    plans = default_lifecycle_plans()

    assert "Apex Trader Funding - EOD PA 50K" in profiles
    assert "Apex Trader Funding - EOD PA 50K - Funded only" in plans
    assert len(profiles) >= 1
    assert len(plans) >= len(profiles)


def test_no_payout_is_counted_after_failure_regression():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    settings = {plan.key: LifecycleSettings(start_mode="funded", desired_payout=1500)}
    scenario = ForwardScenario(rr_config_id="1_5rr", path_count=2, july_candidate_count=0, august_candidate_count=0)
    summary, _monthly, events, _ledger = run_forward_lifecycle_grid(
        build_monte_carlo_paths(scenario),
        [plan],
        contract_values=[4],
        settings_by_plan=settings,
        prefix_application_basis="ACCOUNT_STATE_BEFORE_PREFIX",
    )

    terminal_events = events[events["event"] == "terminal"] if not events.empty else pd.DataFrame()
    payout_events = events[events["event"] == "payout"] if not events.empty else pd.DataFrame()
    if not terminal_events.empty and not payout_events.empty:
        assert payout_events["event_order"].max() < terminal_events["event_order"].min()
    assert "paid_before_first_blow_rate" in summary


def test_synthetic_forecast_dates_are_unique_increasing_and_do_not_wrap():
    path = build_master_path(ForwardScenario(rr_config_id="1rr", july_candidate_count=5, august_candidate_count=5))
    synthetic = path[path["status"] == "SYNTHETIC"]

    dates = synthetic["session_date"].tolist()
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates))
    assert min(dates) > "2026-07-08"
    assert max(dates) <= "2026-08-31"
    with pytest.raises(ValueError, match="exceeds available trading days"):
        build_master_path(ForwardScenario(rr_config_id="1rr", july_candidate_count=len(forecast_trading_dates(7)) + 1))


def test_source_packet_fields_stay_together_and_timestamps_are_shifted():
    path = build_master_path(ForwardScenario(rr_config_id="1rr", master_seed=123, july_candidate_count=1, august_candidate_count=0))
    row = path[path["status"] == "SYNTHETIC"].iloc[0]

    assert row["source_trade_packet_id"]
    assert row["source_entry_time"]
    assert row["source_exit_time"]
    assert row["timestamp_policy"] == "SYNTHETIC_SHIFTED_SOURCE_TIME_OF_DAY"
    assert pd.Timestamp(row["exit_time"]) > pd.Timestamp(row["entry_time"])


def test_historical_flat_rows_are_not_executable_packets():
    frame = pd.DataFrame(
        [
            {"rolling_pf_is_flat": True, "rolling_pf_switch_state": "FLAT", "effective_exit_reason": "FLAT"},
            {"rolling_pf_is_flat": False, "rolling_pf_switch_state": "ON", "effective_exit_reason": "TP"},
        ]
    )

    assert len(executable_packet_pool(frame)) == 1


def _normalization_fixture() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "trade_packet_id": "tiny_win",
                "source_session_date": "2024-07-01",
                "seasonality_month": 7,
                "entry_time": "2024-07-01T09:00:00-04:00",
                "exit_time": "2024-07-01T10:00:00-04:00",
                "direction": "long",
                "exit_reason": "TP",
                "effective_exit_reason": "TP",
                "pnl_points": 6.0,
                "raw_stop_points": 12.0,
                "effective_stop_points": 12.0,
                "target_points": 18.0,
                "mae_points": 3.0,
                "mfe_points": 24.0,
                "source_ledger_id": "fixture",
            },
            {
                "trade_packet_id": "wide_loss",
                "source_session_date": "2024-08-01",
                "seasonality_month": 8,
                "entry_time": "2024-08-01T09:00:00-04:00",
                "exit_time": "2024-08-01T10:00:00-04:00",
                "direction": "short",
                "exit_reason": "SL",
                "effective_exit_reason": "SL",
                "pnl_points": -10.0,
                "raw_stop_points": 250.0,
                "effective_stop_points": 250.0,
                "target_points": 375.0,
                "mae_points": 260.0,
                "mfe_points": 20.0,
                "source_ledger_id": "fixture",
            },
            {
                "trade_packet_id": "small_loss",
                "source_session_date": "2024-07-02",
                "seasonality_month": 7,
                "entry_time": "2024-07-02T09:00:00-04:00",
                "exit_time": "2024-07-02T10:00:00-04:00",
                "direction": "long",
                "exit_reason": "SL",
                "effective_exit_reason": "SL",
                "pnl_points": -1.0,
                "raw_stop_points": 50.0,
                "effective_stop_points": 50.0,
                "target_points": 75.0,
                "mae_points": 55.0,
                "mfe_points": 5.0,
                "source_ledger_id": "fixture",
            },
            {
                "trade_packet_id": "breakeven",
                "source_session_date": "2024-08-02",
                "seasonality_month": 8,
                "entry_time": "2024-08-02T09:00:00-04:00",
                "exit_time": "2024-08-02T10:00:00-04:00",
                "direction": "short",
                "exit_reason": "BE",
                "effective_exit_reason": "BE",
                "pnl_points": 0.0,
                "raw_stop_points": 40.0,
                "effective_stop_points": 40.0,
                "target_points": 60.0,
                "mae_points": 12.0,
                "mfe_points": 8.0,
                "source_ledger_id": "fixture",
            },
        ]
    )


def test_forward_geometry_normalizes_small_and_wide_packets_without_outcome_filtering():
    scenario = ForwardScenario(min_effective_stop_points=100, max_effective_stop_points=200)
    normalized = _apply_geometry_policy(_normalization_fixture(), scenario)

    assert set(normalized["trade_packet_id"]) == {"tiny_win", "wide_loss", "small_loss", "breakeven"}
    tiny = normalized[normalized["trade_packet_id"] == "tiny_win"].iloc[0]
    wide = normalized[normalized["trade_packet_id"] == "wide_loss"].iloc[0]
    assert tiny["effective_stop_points"] == 100.0
    assert tiny["normalization_scale_factor"] == pytest.approx(100.0 / 12.0)
    assert wide["effective_stop_points"] == 200.0
    assert wide["normalization_scale_factor"] == pytest.approx(200.0 / 250.0)
    assert normalized.loc[normalized["trade_packet_id"] == "small_loss", "pnl_points"].iloc[0] == pytest.approx(-2.0)


def test_normalization_uses_one_scale_factor_and_preserves_packet_result_identity():
    scenario = ForwardScenario(min_effective_stop_points=100, max_effective_stop_points=200)
    source = _normalization_fixture()
    normalized = _apply_geometry_policy(source, scenario)
    row = normalized[normalized["trade_packet_id"] == "tiny_win"].iloc[0]
    source_row = source[source["trade_packet_id"] == "tiny_win"].iloc[0]

    factor = row["normalization_scale_factor"]
    for column in ["pnl_points", "raw_stop_points", "effective_stop_points", "target_points", "mae_points", "mfe_points"]:
        assert row[column] == pytest.approx(float(source_row[column]) * factor)
    assert row["exit_reason"] == source_row["exit_reason"]
    assert row["effective_exit_reason"] == source_row["effective_exit_reason"]
    assert row["pnl_points"] > 0
    assert normalized.loc[normalized["trade_packet_id"] == "wide_loss", "pnl_points"].iloc[0] < 0
    assert normalized.loc[normalized["trade_packet_id"] == "breakeven", "pnl_points"].iloc[0] == 0.0


def test_point_scale_changes_geometry_but_not_packet_eligibility():
    base = build_master_path(ForwardScenario(rr_config_id="1_5rr", master_seed=42, july_candidate_count=6, august_candidate_count=6))
    high = build_master_path(
        ForwardScenario(rr_config_id="1_5rr", master_seed=42, july_candidate_count=6, august_candidate_count=6, point_scale_scenario="high")
    )

    assert base["source_trade_packet_id"].fillna("").tolist() == high["source_trade_packet_id"].fillna("").tolist()
    synthetic = high[high["status"] == "SYNTHETIC"]
    assert synthetic["effective_stop_points"].max() <= 200
    assert synthetic["effective_stop_points"].min() >= 100
    midrange = _normalization_fixture().iloc[[0]].copy()
    midrange[["pnl_points", "raw_stop_points", "effective_stop_points", "target_points", "mae_points", "mfe_points"]] = [
        [75.0, 150.0, 150.0, 225.0, 30.0, 240.0]
    ]
    current_packet = _apply_geometry_policy(midrange, ForwardScenario(point_scale_scenario="current")).iloc[0]
    high_packet = _apply_geometry_policy(midrange, ForwardScenario(point_scale_scenario="high")).iloc[0]
    assert current_packet["trade_packet_id"] == high_packet["trade_packet_id"]
    assert high_packet["normalization_scale_factor"] > current_packet["normalization_scale_factor"]
    assert high_packet["target_points"] > current_packet["target_points"]


def test_strategy_sequence_hash_is_identical_for_account_variants():
    scenario = ForwardScenario(rr_config_id="1rr", path_count=1, july_candidate_count=2, august_candidate_count=2)
    path = build_monte_carlo_paths(scenario)[0]

    assert strategy_sequence_hash(path) == strategy_sequence_hash(path.copy())


def test_expectancy_manifest_exports_weighted_source_pool_pf_not_path_pf():
    scenario = ForwardScenario(rr_config_id="1rr", path_count=3, july_candidate_count=2, august_candidate_count=2)
    paths = build_monte_carlo_paths(scenario)
    manifest = strategy_path_manifest(paths, scenario)
    results = path_summary(paths, scenario)

    assert manifest["expected_weighted_source_pf"].notna().all()
    assert manifest["expected_weighted_source_pf"].nunique() == 1
    assert results["expected_weighted_source_pf"].nunique() == 1


def test_clean_forward_strategy_ledger_has_no_prop_account_fields():
    path = build_master_path(ForwardScenario(rr_config_id="1rr", july_candidate_count=2, august_candidate_count=2))
    ledger = forward_strategy_ledger(path)

    forbidden = {
        "firm",
        "account",
        "plan_key",
        "balance_before",
        "balance_after",
        "floor_before",
        "floor_after",
        "payout_number",
        "trader_cash",
        "gross_account_debit",
    }
    assert forbidden.isdisjoint(set(ledger.columns))
    assert ledger["pnl_points"].tolist() == path.sort_values("sequence_number")["pnl_points"].tolist()
    assert {"raw_stop_points", "effective_stop_points", "target_points", "mae_points", "mfe_points"} <= set(ledger.columns)


def test_target_pf_calibrates_weighted_source_pool_for_both_rr_configs_and_blocks_old_defaults():
    for rr_config_id, old_pf in [("1rr", 3.0298388701), ("1_5rr", 2.0278424872)]:
        path = build_master_path(ForwardScenario(rr_config_id=rr_config_id, july_candidate_count=4, august_candidate_count=4))
        achieved = float(path["achieved_weighted_source_pf"].iloc[0])
        assert achieved == pytest.approx(1.50, abs=0.01)
        assert achieved != pytest.approx(old_pf, abs=0.001)
        assert float(path["requested_target_pf"].iloc[0]) == 1.50


def test_target_pf_monotonically_increases_winner_weights_and_expected_pf():
    source = load_source_library("1rr")
    rows = []
    for target_pf in [1.20, 1.50, 1.80]:
        scenario = ForwardScenario(rr_config_id="1rr", target_expected_pf=target_pf)
        pool = _apply_geometry_policy(source, scenario)
        rows.append((target_pf, float(pool["calibration_winner_multiplier"].iloc[0]), expected_weighted_pf(pool, None, None)))

    assert [row[1] for row in rows] == sorted(row[1] for row in rows)
    assert [row[2] for row in rows] == sorted(row[2] for row in rows)
    assert [row[2] for row in rows] == pytest.approx([1.20, 1.50, 1.80], abs=0.01)


def test_default_forward_geometry_normalizes_tiny_nonzero_packets():
    path = build_master_path(ForwardScenario(rr_config_id="1rr", july_candidate_count=8, august_candidate_count=8))
    synthetic = path[path["status"] == "SYNTHETIC"]
    nonzero = synthetic[synthetic["pnl_points"].astype(float).abs() > 1e-9]

    assert not nonzero.empty
    assert synthetic["effective_stop_points"].between(100, 200).all()
    assert not synthetic["effective_exit_reason"].astype(str).str.lower().eq("cutoff").any()


def test_source_exact_geometry_can_still_show_old_small_packets_when_selected():
    path = build_master_path(
        ForwardScenario(
            rr_config_id="1rr",
            master_seed=1729,
            july_candidate_count=8,
            august_candidate_count=8,
            geometry_policy="SOURCE_EXACT",
        )
    )
    synthetic = path[path["status"] == "SYNTHETIC"]
    nonzero = synthetic[synthetic["pnl_points"].astype(float).abs() > 1e-9]

    assert nonzero["pnl_points"].abs().min() < 100


def test_july_and_august_are_not_independently_forced_to_target_pf():
    scenario = ForwardScenario(rr_config_id="1rr", target_expected_pf=1.50)
    pool = _apply_geometry_policy(load_source_library("1rr"), scenario)

    month_pfs = []
    for month in [7, 8]:
        month_pool = pool[pool["seasonality_month"].astype(int) == month]
        month_pfs.append(expected_weighted_pf(month_pool, None, None))
    assert expected_weighted_pf(pool, None, None) == pytest.approx(1.50, abs=0.01)
    assert any(abs(float(month_pf) - 1.50) > 0.01 for month_pf in month_pfs)


def test_normalized_source_pool_is_materially_broader_than_previous_filtered_subset():
    scenario = ForwardScenario(rr_config_id="1rr")
    source = load_source_library("1rr")
    pool = _apply_geometry_policy(source, scenario)
    old_stop = source["effective_stop_points"].between(100, 200, inclusive="both")
    old_pnl = source["pnl_points"].abs().le(1e-9) | source["pnl_points"].abs().ge(100)
    old_subset = source[old_stop & old_pnl & ~source["effective_exit_reason"].astype(str).str.upper().eq("CUTOFF")]

    assert len(pool) > len(old_subset) * 2
    assert pool["trade_packet_id"].nunique() > old_subset["trade_packet_id"].nunique() * 2


def test_large_deterministic_sampling_smoke_approximately_reproduces_target_pf():
    scenario = ForwardScenario(rr_config_id="1rr", path_count=800, july_candidate_count=8, august_candidate_count=12, target_expected_pf=1.50)
    ledgers = pd.concat(build_monte_carlo_paths(scenario), ignore_index=True)
    synthetic = ledgers[ledgers["status"] == "SYNTHETIC"]
    gross_profit = synthetic.loc[synthetic["pnl_points"] > 0, "pnl_points"].sum()
    gross_loss = -synthetic.loc[synthetic["pnl_points"] < 0, "pnl_points"].sum()

    assert gross_profit / gross_loss == pytest.approx(1.50, abs=0.18)


def test_per_trade_ledger_reconciles_after_prefix_basis_and_current_state():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    scenario = ForwardScenario(
        rr_config_id="1rr",
        path_count=1,
        july_candidate_count=0,
        august_candidate_count=0,
        current_balance=50_500,
        current_floor=48_500,
        prefix_application_basis="ACCOUNT_STATE_BEFORE_PREFIX",
    )
    path = build_monte_carlo_paths(scenario)[0]
    settings = {
        plan.key: LifecycleSettings(
            start_mode="funded",
            current_balance=50_500,
            current_floor=48_500,
        )
    }

    _summary, monthly, _events, ledger = run_forward_lifecycle_grid(
        [path],
        [plan],
        contract_values=[1],
        settings_by_plan=settings,
        prefix_application_basis="ACCOUNT_STATE_BEFORE_PREFIX",
    )

    assert ledger.iloc[0]["balance_before"] == 50_500
    assert ledger.iloc[-1]["balance_after"] == 50_400
    assert ledger.iloc[-1]["floor_after"] == 48_800
    assert ledger.iloc[-1]["balance_after"] == monthly.iloc[-1]["ending_balance"]


def test_lifecycle_summary_exposes_strict_unknown_and_realized_only_rates():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]
    scenario = ForwardScenario(rr_config_id="1rr", path_count=2, july_candidate_count=0, august_candidate_count=0)
    settings = {plan.key: LifecycleSettings(start_mode="funded")}

    summary, _monthly, _events, ledger = run_forward_lifecycle_grid(
        build_monte_carlo_paths(scenario),
        [plan],
        contract_values=[1],
        settings_by_plan=settings,
        prefix_application_basis="ACCOUNT_STATE_BEFORE_PREFIX",
    )

    row = summary.iloc[0]
    assert row["strict_unknown_trades"] == 4
    assert row["realized_only_trades"] == 4
    assert row["strict_unknown_rate"] == 1
    assert row["realized_only_failure_rate"] == 0
    assert (ledger["strict_account_result"] == "UNKNOWN").sum() == 4


def test_non_apex_lifecycle_payouts_use_profit_split_as_trader_cash():
    plan = default_lifecycle_plans()["TakeProfitTrader - PRO 50K - Funded only"]
    settings = {
        plan.key: LifecycleSettings(
            start_mode="funded",
            current_balance=52_000,
            current_floor=48_000,
            current_winning_days=0,
        )
    }
    path = build_master_path(ForwardScenario(rr_config_id="1rr", july_candidate_count=0, august_candidate_count=0))
    # Use AFTER so the zero-continuation path is just account-status evaluation.
    summary, _monthly, events, _ledger = run_forward_lifecycle_grid(
        [path],
        [plan],
        contract_values=[1],
        settings_by_plan=settings,
        prefix_application_basis="ACCOUNT_STATE_AFTER_PREFIX",
    )

    if not events.empty and (events["event"] == "payout").any():
        payout = events[events["event"] == "payout"].iloc[0]
        assert payout["amount"] <= 1_600
    assert "avg_net_cash" in summary


def test_apex_50k_frozen_floor_boundary():
    profile = default_prop_rule_profiles()["Apex Trader Funding - EOD PA 50K"]

    assert profile.floor_ceiling == 50_100
