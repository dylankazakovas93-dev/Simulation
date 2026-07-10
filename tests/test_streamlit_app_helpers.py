from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

pytest.importorskip("streamlit")

from app.streamlit_app import (
    account_size_label,
    apply_score_config,
    build_rule_audit_rows,
    build_prop_plain_english_summary,
    build_prop_chart_frame,
    build_effective_current_state_rows,
    build_monthly_heatmap,
    coerce_uploaded_ledger,
    clear_stale_lifecycle_results,
    default_lifecycle_plans,
    current_state_overrides,
    filter_first_payout_rows,
    filter_withdrawal_rows,
    filter_viable_prop_rows,
    format_first_payout_comparison,
    format_prop_comparison,
    format_prop_summary,
    format_guidance_ranking,
    inspect_historical_upload_metadata,
    inspect_historical_upload_set_metadata,
    plan_route_label,
)


def test_effective_current_state_rows_convert_profit_and_cushion_to_balance_floor():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD 50K - Eval to funded"]

    rows = build_effective_current_state_rows(
        [plan],
        start_mode="funded",
        current_profit=500,
        current_cushion=1500,
    )

    assert rows[0]["effective_balance"] == "$50,500"
    assert rows[0]["effective_floor"] == "$49,000"


def test_uploaded_ledger_coercion_uses_per_file_instrument_and_point_value():
    frame = pd.DataFrame(
        [
            {
                "entry_time": "2026-01-02T10:00:00Z",
                "exit_time": "2026-01-02T11:00:00Z",
                "pnl_points": 10,
            }
        ]
    )

    nq = coerce_uploaded_ledger(
        frame,
        strategy_id="ledger-1",
        instrument="NQ",
        contract_symbol="MNQ",
        default_dpp=2.0,
        commission_round_turn=0.50,
        fallback_minutes=60,
    )
    es = coerce_uploaded_ledger(
        frame,
        strategy_id="ledger-2",
        instrument="ES",
        contract_symbol="MES",
        default_dpp=5.0,
        commission_round_turn=1.25,
        fallback_minutes=60,
    )

    assert nq.iloc[0]["instrument"] == "NQ"
    assert nq.iloc[0]["contract_symbol"] == "MNQ"
    assert nq.iloc[0]["dollars_per_point"] == 2.0
    assert nq.iloc[0]["commission_round_turn"] == 0.50
    assert es.iloc[0]["instrument"] == "ES"
    assert es.iloc[0]["contract_symbol"] == "MES"
    assert es.iloc[0]["dollars_per_point"] == 5.0
    assert es.iloc[0]["commission_round_turn"] == 1.25


def test_historical_upload_metadata_blocks_multiple_rr_configs():
    frame = pd.DataFrame({"rr_config_id": ["1rr", "1.5rr"], "entry_time": ["2026-01-01", "2026-01-02"]})

    errors, warnings = inspect_historical_upload_metadata(frame)

    assert len(errors) == 1
    assert "Multiple RR configurations" in errors[0]
    assert warnings == []


def test_historical_upload_metadata_blocks_multiple_simulated_paths():
    frame = pd.DataFrame({"path_id": [0, 1], "entry_time": ["2026-01-01", "2026-01-02"]})

    errors, warnings = inspect_historical_upload_metadata(frame)

    assert len(errors) == 1
    assert "Multiple simulated path IDs" in errors[0]
    assert warnings == []


def test_historical_upload_metadata_warns_for_single_forward_ledger():
    frame = pd.DataFrame(
        {
            "rr_config_id": ["1rr"],
            "path_id": [0],
            "status": ["SYNTHETIC"],
            "pf_scenario": ["realistic"],
        }
    )

    errors, warnings = inspect_historical_upload_metadata(frame)

    assert errors == []
    assert len(warnings) == 1
    assert "generated forward ledger" in warnings[0]


def test_historical_upload_set_accepts_single_and_matching_rr_metadata():
    one_rr = pd.DataFrame({"rr_config_id": ["1rr"], "entry_time": ["2026-01-01"]})

    assert inspect_historical_upload_set_metadata([("one.csv", one_rr)])[0] == []
    assert inspect_historical_upload_set_metadata([("one.csv", one_rr), ("two.csv", one_rr)])[0] == []


def test_historical_upload_set_blocks_cross_file_rr_configs_with_file_names():
    errors, _ = inspect_historical_upload_set_metadata(
        [
            ("ledger_1rr.csv", pd.DataFrame({"rr_config_id": ["1rr"]})),
            ("ledger_1_5rr.csv", pd.DataFrame({"rr_config_id": ["1_5rr"]})),
        ]
    )

    assert errors == [
        "Multiple RR configurations detected across uploads:\n"
        "1_5rr: ledger_1_5rr.csv\n"
        "1rr: ledger_1rr.csv"
    ]


def test_historical_upload_set_blocks_cross_file_paths_and_accepts_matching_paths():
    path_zero = pd.DataFrame({"path_id": [0]})
    path_one = pd.DataFrame({"path_id": [1]})

    errors, _ = inspect_historical_upload_set_metadata([("path_0.csv", path_zero), ("path_1.csv", path_one)])

    assert errors == [
        "Multiple simulated path IDs detected across uploads:\n0: path_0.csv\n1: path_1.csv",
        "Generated forward bundles require rr_config_id and path_id in every uploaded file:\npath_0.csv\npath_1.csv",
    ]
    complete_path_zero = path_zero.assign(rr_config_id="1rr")
    assert inspect_historical_upload_set_metadata([("a.csv", complete_path_zero), ("b.csv", complete_path_zero)])[0] == []


def test_historical_upload_set_normalizes_rr_case_and_integer_like_path_ids():
    errors, _ = inspect_historical_upload_set_metadata(
        [
            ("first.csv", pd.DataFrame({"rr_config_id": [" 1RR "], "path_id": [0]})),
            ("second.csv", pd.DataFrame({"rr_config_id": ["1rr"], "path_id": [" 0 "]})),
            ("third.csv", pd.DataFrame({"rr_config_id": ["1Rr"], "path_id": ["0"]})),
        ]
    )

    assert errors == []


def test_historical_upload_set_has_order_independent_errors_and_warnings():
    first = ("generated.csv", pd.DataFrame({"rr_config_id": ["1RR"], "path_id": [0], "status": ["SYNTHETIC"]}))
    second = ("other.csv", pd.DataFrame({"rr_config_id": ["1_5rr"], "path_id": [1], "status": ["SYNTHETIC"]}))

    assert inspect_historical_upload_set_metadata([first, second]) == inspect_historical_upload_set_metadata([second, first])


def test_generated_multifile_bundle_requires_complete_provenance():
    errors, warnings = inspect_historical_upload_set_metadata(
        [
            ("generated.csv", pd.DataFrame({"rr_config_id": ["1rr"], "path_id": [0], "status": ["SYNTHETIC"]})),
            ("unproven.csv", pd.DataFrame({"entry_time": ["2026-01-01"], "pnl_points": [10]})),
        ]
    )

    assert errors == [
        "Generated forward bundles require rr_config_id and path_id in every uploaded file:\nunproven.csv"
    ]
    assert len(warnings) == 1


def test_path_id_alone_requires_complete_multifile_provenance_in_any_upload_order():
    generated_path = ("generated.csv", pd.DataFrame({"rr_config_id": ["1rr"], "path_id": [0]}))
    ordinary = ("ordinary.csv", pd.DataFrame({"entry_time": ["2026-01-01"], "pnl_points": [10]}))
    expected_errors = [
        "Generated forward bundles require rr_config_id and path_id in every uploaded file:\nordinary.csv"
    ]

    forward_errors, forward_warnings = inspect_historical_upload_set_metadata([generated_path, ordinary])
    reverse_errors, reverse_warnings = inspect_historical_upload_set_metadata([ordinary, generated_path])

    assert forward_errors == reverse_errors == expected_errors
    assert forward_warnings == reverse_warnings
    assert forward_warnings == []


def test_historical_upload_set_accepts_ordinary_ledgers_and_validates_raw_metadata_before_coercion():
    ordinary = pd.DataFrame({"entry_time": ["2026-01-01"], "pnl_points": [10]})
    one_rr = ordinary.assign(rr_config_id="1rr")
    one_half_rr = ordinary.assign(rr_config_id="1_5rr")

    assert inspect_historical_upload_set_metadata([("ordinary.csv", ordinary)])[0] == []
    coerced = coerce_uploaded_ledger(
        one_rr,
        strategy_id="test",
        default_dpp=2.0,
        fallback_minutes=60,
    )
    assert "rr_config_id" not in coerced
    assert inspect_historical_upload_set_metadata([("one.csv", one_rr), ("half.csv", one_half_rr)])[0]


def test_historical_bundle_error_stops_before_coercion_or_partial_acceptance():
    app = AppTest.from_file(str(Path(__file__).parents[1] / "app" / "streamlit_app.py")).run()
    app.file_uploader[0].upload("one.csv", b"rr_config_id,path_id\n1rr,0\n", "text/csv")
    app.file_uploader[0].upload("half.csv", b"rr_config_id,path_id\n1_5rr,1\n", "text/csv")
    app.run()

    assert not app.exception
    assert [error.value for error in app.error] == [
        "Multiple RR configurations detected across uploads:\n1_5rr: half.csv\n1rr: one.csv\n"
        "Multiple simulated path IDs detected across uploads:\n0: one.csv\n1: half.csv"
    ]
    assert not app.dataframe


def test_prop_comparison_uses_default_funded_floor_instead_of_zero_cushion_override():
    balance, floor = current_state_overrides(
        mode="Prop Comparison",
        start_mode="funded",
        starting_balance=50_000,
        current_profit=0,
        current_cushion=0,
    )

    assert balance is None
    assert floor is None


def test_funded_guidance_converts_current_profit_and_cushion_to_overrides():
    balance, floor = current_state_overrides(
        mode="Funded Guidance",
        start_mode="funded",
        starting_balance=50_000,
        current_profit=500,
        current_cushion=1_500,
    )

    assert balance == 50_500
    assert floor == 49_000


def test_guidance_ranking_is_compact_and_uses_new_outcome_labels():
    frame = pd.DataFrame(
        [
            {
                "contracts": 2,
                "paid_before_first_blow_paths": "65 / 100",
                "paid_before_first_blow_rate": 0.65,
                "blew_before_payout_rate": 0.35,
                "payout_after_rebuy_rate": 0.10,
                "avg_net_cash": 1200,
                "p50_net_cash": 1000,
                "avg_fees": 80,
                "p50_month_to_first_payout": 1,
                "display_composite_score": 72.5,
                "status": "Payout candidate",
            }
        ]
    )

    formatted = format_guidance_ranking(frame)

    assert list(formatted.columns) == [
        "micros",
        "paid paths",
        "paid before blow",
        "blew before payout",
        "paid after rebuy",
        "average realized cash after fees",
        "median realized",
        "avg fees",
        "median first payout",
        "composite",
        "status",
    ]
    assert formatted.iloc[0]["paid before blow"] == "65.0%"
    assert formatted.iloc[0]["average realized cash after fees"] == "$1,200"


def test_monthly_heatmap_formats_dollar_and_rate_values_without_matplotlib():
    frame = pd.DataFrame(
        [
            {
                "plan": "test-plan",
                "contracts": 2,
                "month_index": 1,
                "p50_pnl": 500,
                "p50_active_pnl": 650,
                "p50_net_cash": 1200,
                "active_path_rate": 0.75,
                "payout_month_rate": 0.25,
                "fail_month_rate": 0.10,
                "p95_drawdown": 800,
            }
        ]
    )

    dollars = build_monthly_heatmap(frame, "Median realized cash")
    rates = build_monthly_heatmap(frame, "Payout month rate")
    active = build_monthly_heatmap(frame, "Active median PnL")
    live = build_monthly_heatmap(frame, "Active path rate")

    assert dollars.iloc[0, 0] == "$1,200"
    assert rates.iloc[0, 0] == "25.0%"
    assert active.iloc[0, 0] == "$650"
    assert live.iloc[0, 0] == "75.0%"


def test_apply_score_config_upgrades_legacy_ranking_columns():
    frame = pd.DataFrame(
        [
            {
                "plan": "test-plan",
                "contracts": 1,
                "paths": 100,
                "current_account_paid_first_rate": 0.60,
                "current_account_blew_first_rate": 0.30,
                "mean_net_cash": 1000,
                "p50_month_to_first_payout": 2,
            }
        ]
    )
    config = {
        "survival_weight": 0.5,
        "ev_weight": 0.2,
        "speed_weight": 0.25,
        "convexity_weight": 0.05,
        "max_blow_rate": 0.45,
    }

    scored = apply_score_config(frame, config)

    assert "survival_score" in scored
    assert scored.iloc[0]["status"] == "Payout candidate"


def test_guidance_statuses_do_not_label_zero_payout_rows_as_candidates():
    frame = pd.DataFrame(
        [
            {
                "contracts": 1,
                "paths": 100,
                "paid_before_first_blow_count": 0,
                "blew_before_payout_count": 0,
                "paid_after_rebuy_count": 0,
                "no_resolution_count": 100,
                "paid_before_first_blow_rate": 0.0,
                "blew_before_payout_rate": 0.0,
                "payout_after_rebuy_rate": 0.0,
                "no_resolution_rate": 1.0,
                "any_payout_rate": 0.0,
                "mean_net_cash": 0,
            },
            {
                "contracts": 2,
                "paths": 100,
                "paid_before_first_blow_count": 0,
                "blew_before_payout_count": 25,
                "paid_after_rebuy_count": 0,
                "no_resolution_count": 75,
                "paid_before_first_blow_rate": 0.0,
                "blew_before_payout_rate": 0.25,
                "payout_after_rebuy_rate": 0.0,
                "no_resolution_rate": 0.75,
                "any_payout_rate": 0.0,
                "mean_net_cash": 0,
            },
            {
                "contracts": 3,
                "paths": 100,
                "paid_before_first_blow_count": 0,
                "blew_before_payout_count": 100,
                "paid_after_rebuy_count": 0,
                "no_resolution_count": 0,
                "paid_before_first_blow_rate": 0.0,
                "blew_before_payout_rate": 1.0,
                "payout_after_rebuy_rate": 0.0,
                "no_resolution_rate": 0.0,
                "any_payout_rate": 0.0,
                "mean_net_cash": 0,
            },
            {
                "contracts": 4,
                "paths": 100,
                "paid_before_first_blow_count": 5,
                "blew_before_payout_count": 20,
                "paid_after_rebuy_count": 0,
                "no_resolution_count": 75,
                "paid_before_first_blow_rate": 0.05,
                "blew_before_payout_rate": 0.20,
                "payout_after_rebuy_rate": 0.0,
                "no_resolution_rate": 0.75,
                "any_payout_rate": 0.05,
                "mean_net_cash": 100,
            },
            {
                "contracts": 5,
                "paths": 100,
                "paid_before_first_blow_count": 0,
                "blew_before_payout_count": 0,
                "paid_after_rebuy_count": 10,
                "no_resolution_count": 90,
                "paid_before_first_blow_rate": 0.0,
                "blew_before_payout_rate": 0.0,
                "payout_after_rebuy_rate": 0.10,
                "no_resolution_rate": 0.90,
                "any_payout_rate": 0.10,
                "mean_net_cash": 100,
            },
        ]
    )
    config = {
        "survival_weight": 0.4,
        "ev_weight": 0.3,
        "speed_weight": 0.15,
        "convexity_weight": 0.15,
        "max_blow_rate": 0.5,
    }

    scored = apply_score_config(frame, config).sort_values("contracts")

    assert scored["status"].tolist() == [
        "No payout observed",
        "No payout observed",
        "All paths failed",
        "Payout candidate",
        "Payout only after failure/rebuy",
    ]


def test_prop_comparison_helpers_support_size_and_route_filters():
    plans = default_lifecycle_plans()
    eval_plan = plans["Apex Trader Funding - EOD 50K - Eval to funded"]
    funded_plan = plans["Apex Trader Funding - EOD PA 50K - Funded only"]

    assert account_size_label(eval_plan.account_size) == "50K"
    assert plan_route_label(eval_plan) == "Eval to funded"
    assert plan_route_label(funded_plan) == "Funded only"


def test_prop_summary_formats_one_best_row_per_account():
    frame = pd.DataFrame(
        [
            {
                "firm": "Apex Trader Funding",
                "account": "EOD 50K",
                "contracts": 2,
                "paid_before_first_blow_rate": 0.65,
                "any_payout_rate": 0.70,
                "blew_before_payout_rate": 0.35,
                "avg_withdrawal": 1500,
                "p50_withdrawal": 1500,
                "avg_payout_count": 2.25,
                "avg_net_cash": 1200,
                "p50_month_to_first_payout": 1,
                "display_composite_score": 72.5,
                "status": "Candidate",
            }
        ]
    )

    formatted = format_prop_summary(frame)

    assert formatted.iloc[0]["best micros"] == 2
    assert formatted.iloc[0]["median first payout"] == "M1"
    assert formatted.iloc[0]["avg withdrawal"] == "$1,500"
    assert formatted.iloc[0]["avg payouts"] == 2.2
    assert formatted.iloc[0]["avg net"] == "$1,200"


def test_viable_prop_rows_exclude_dead_zero_payout_rows():
    frame = pd.DataFrame(
        [
            {
                "plan": "dead",
                "display_composite_score": 100,
                "paid_before_first_blow_rate": 0.0,
                "any_payout_rate": 0.0,
                "blew_before_payout_rate": 0.0,
                "avg_net_cash": 0,
            },
            {
                "plan": "real",
                "display_composite_score": 50,
                "paid_before_first_blow_rate": 0.55,
                "any_payout_rate": 0.60,
                "blew_before_payout_rate": 0.35,
                "avg_net_cash": 1000,
            },
        ]
    )
    config = {"max_blow_rate": 0.45}

    viable = filter_viable_prop_rows(frame, config)

    assert viable["plan"].tolist() == ["real"]


def test_prop_plain_english_summary_mentions_fastest_survival_and_realized_cash():
    row = pd.Series(
        {
            "firm": "Apex",
            "account": "EOD 50K",
            "contracts": 2,
            "paid_before_first_blow_rate": 0.65,
            "blew_before_payout_rate": 0.35,
            "p50_month_to_first_payout": 1,
            "avg_withdrawal": 1500,
            "avg_net_cash": 1200,
        }
    )

    summary = build_prop_plain_english_summary(row, row, row)

    assert "2 micros" in summary
    assert "M1 median first payout" in summary
    assert "$1,500 per path" in summary


def test_withdrawal_rows_include_any_payout_even_if_risk_gate_would_fail():
    frame = pd.DataFrame(
        [
            {
                "plan": "some-payout",
                "any_payout_rate": 0.25,
                "avg_withdrawal": 500,
                "p50_month_to_first_payout": 3,
            },
            {
                "plan": "no-payout",
                "any_payout_rate": 0.0,
                "avg_withdrawal": 0,
                "p50_month_to_first_payout": None,
            },
        ]
    )

    rows = filter_withdrawal_rows(frame)

    assert rows["plan"].tolist() == ["some-payout"]


def test_first_payout_rows_rank_first_payout_probability_before_withdrawal_amount():
    frame = pd.DataFrame(
        [
            {
                "plan": "large-withdrawal",
                "paid_before_first_blow_rate": 0.40,
                "any_payout_rate": 0.45,
                "blew_before_payout_rate": 0.55,
                "avg_withdrawal": 3_000,
                "p50_month_to_first_payout": 1,
            },
            {
                "plan": "safer-first",
                "paid_before_first_blow_rate": 0.70,
                "any_payout_rate": 0.70,
                "blew_before_payout_rate": 0.30,
                "avg_withdrawal": 1_000,
                "p50_month_to_first_payout": 2,
            },
        ]
    )

    rows = filter_first_payout_rows(frame)

    assert rows["plan"].tolist() == ["safer-first", "large-withdrawal"]


def test_first_payout_comparison_omits_12_month_withdrawal_columns():
    frame = pd.DataFrame(
        [
            {
                "firm": "Apex",
                "account": "EOD 50K",
                "contracts": 2,
                "paid_before_first_blow_rate": 0.65,
                "blew_before_payout_rate": 0.35,
                "payout_after_rebuy_rate": 0.10,
                "p50_month_to_first_payout": 1,
                "p50_days_to_first_payout": 6,
                "avg_withdrawal": 1500,
                "avg_payout_count": 2.25,
                "survival_score": 40,
                "speed_score": 60,
                "display_composite_score": 45,
                "status": "Candidate",
            }
        ]
    )

    formatted = format_first_payout_comparison(frame)

    assert "avg withdrawal" not in formatted.columns
    assert "avg payouts" not in formatted.columns
    assert formatted.iloc[0]["median first payout"] == "M1"
    assert formatted.iloc[0]["median days"] == "6d"


def test_withdrawal_rows_upgrade_old_rankings_without_withdrawal_columns():
    frame = pd.DataFrame(
        [
            {
                "plan": "old-shape",
                "paths": 100,
                "any_payout_rate": 0.25,
                "current_account_paid_first_rate": 0.20,
                "current_account_blew_first_rate": 0.50,
                "mean_net_cash": 900,
                "p50_net_cash": 700,
                "p50_payouts": 500,
                "p50_month_to_first_payout": 3,
            }
        ]
    )

    rows = filter_withdrawal_rows(frame)

    assert rows.iloc[0]["avg_withdrawal"] == 500
    assert rows.iloc[0]["p50_withdrawal"] == 500


def test_prop_comparison_shows_expected_withdrawal_columns():
    frame = pd.DataFrame(
        [
            {
                "firm": "Apex",
                "account": "EOD 50K",
                "contracts": 2,
                "any_payout_rate": 0.70,
                "paid_before_first_blow_rate": 0.65,
                "blew_before_payout_rate": 0.35,
                "avg_withdrawal": 1500,
                "p50_withdrawal": 1500,
                "p95_withdrawal": 3000,
                "avg_payout_count": 2.25,
                "avg_net_cash": 1200,
                "avg_fees": 50,
                "p50_month_to_first_payout": 1,
                "survival_score": 40,
                "ev_score": 50,
                "speed_score": 60,
                "convexity_score": 10,
                "display_composite_score": 45,
                "status": "Candidate",
            }
        ]
    )

    formatted = format_prop_comparison(frame)

    assert formatted.iloc[0]["avg withdrawal"] == "$1,500"
    assert formatted.iloc[0]["any payout"] == "70.0%"
    assert formatted.iloc[0]["avg payouts"] == 2.2


def test_prop_chart_frame_keeps_core_comparison_metrics_numeric():
    frame = pd.DataFrame(
        [
            {
                "firm": "Apex",
                "account": "EOD 50K",
                "contracts": 2,
                "paths": 100,
                "any_payout_rate": 0.70,
                "paid_before_first_blow_rate": 0.65,
                "blew_before_payout_rate": 0.35,
                "avg_withdrawal": 1500,
                "avg_payout_count": 2.25,
                "p50_month_to_first_payout": 1,
            }
        ]
    )

    chart = build_prop_chart_frame(frame)

    assert chart.iloc[0]["label"] == "Apex EOD 50K | 2m"
    assert chart.iloc[0]["avg_withdrawal"] == 1500
    assert chart.iloc[0]["avg_payout_count"] == 2.25


def test_stale_lifecycle_results_are_cleared_when_schema_changes():
    state = {
        "result_schema_version": -1,
        "lifecycle_ranking": "old",
        "lifecycle_monthly": "old",
        "score_config": "old",
    }

    changed = clear_stale_lifecycle_results(state)

    assert changed is True
    assert "lifecycle_ranking" not in state
    assert "lifecycle_monthly" not in state
    assert state["result_schema_version"] > 0


def test_rule_audit_rows_expose_cadence_reserve_and_cap_ladder():
    plan = default_lifecycle_plans()["Apex Trader Funding - EOD PA 50K - Funded only"]

    rows = build_rule_audit_rows([plan])

    assert rows.iloc[0]["reserve/buffer"] == "$2,100"
    assert rows.iloc[0]["first cap"] == "$1,500"
    assert "$3,000" in rows.iloc[0]["cap ladder"]
    assert "weekly" in rows.iloc[0]["cadence"].lower()
