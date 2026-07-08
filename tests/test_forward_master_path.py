from __future__ import annotations

import pandas as pd
import pytest

from sim_core.forward_master_path import (
    ForwardScenario,
    build_master_path,
    build_monte_carlo_paths,
    executable_packet_pool,
    forecast_trading_dates,
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


def test_enabled_scenario_controls_materially_change_outputs_and_point_scale_caps():
    base = build_master_path(ForwardScenario(rr_config_id="1_5rr", master_seed=42, july_candidate_count=6, august_candidate_count=6))
    pf = build_master_path(
        ForwardScenario(rr_config_id="1_5rr", master_seed=42, july_candidate_count=6, august_candidate_count=6, pf_scenario="HIGHER_EXPECTANCY")
    )
    regime = build_master_path(
        ForwardScenario(rr_config_id="1_5rr", master_seed=42, july_candidate_count=6, august_candidate_count=6, regime_scenario="abrupt_tail")
    )
    high = build_master_path(
        ForwardScenario(rr_config_id="1_5rr", master_seed=42, july_candidate_count=6, august_candidate_count=6, point_scale_scenario="high")
    )

    assert base["source_trade_packet_id"].fillna("").tolist() != pf["source_trade_packet_id"].fillna("").tolist()
    assert base["source_trade_packet_id"].fillna("").tolist() != regime["source_trade_packet_id"].fillna("").tolist()
    synthetic = high[high["status"] == "SYNTHETIC"]
    assert synthetic["raw_stop_points"].max() <= 200
    assert synthetic["effective_stop_points"].max() <= 200
    assert synthetic["target_points"].max() <= 300


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
