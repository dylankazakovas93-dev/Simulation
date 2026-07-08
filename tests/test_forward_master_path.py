from __future__ import annotations

import pandas as pd
import pytest

from sim_core.forward_master_path import (
    ForwardScenario,
    build_master_path,
    build_monte_carlo_paths,
    load_realized_master_path,
    path_summary,
    run_forward_lifecycle_grid,
    select_realized_prefix,
    strategy_path_manifest,
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
    summary, _monthly, _events = run_forward_lifecycle_grid(
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

    before, _m1, _e1 = run_forward_lifecycle_grid(
        [before_path], [plan], contract_values=[1], settings_by_plan=settings, prefix_application_basis="ACCOUNT_STATE_BEFORE_PREFIX"
    )
    after, _m2, _e2 = run_forward_lifecycle_grid(
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
    summary, _monthly, events = run_forward_lifecycle_grid(
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
