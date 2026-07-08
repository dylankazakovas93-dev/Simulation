from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from sim_core.forward_master_path import (
    ForwardScenario,
    build_forward_account_trade_ledger,
    build_master_path,
    build_monte_carlo_paths,
    export_forward_artifacts,
    path_summary,
    run_forward_lifecycle_grid,
    strategy_path_manifest,
)
from sim_core.ingestion.csv_loader import normalize_trade_frame
from sim_core.lifecycle import (
    LifecycleSettings,
    default_lifecycle_plans,
    run_lifecycle_grid,
    summarize_monthly_paths,
)
from sim_core.models import TradeValidationError
from sim_core.prop_rules import (
    default_prop_rule_profiles,
    resolve_overlapping_trades,
)


ENTRY_COLUMNS = ("entry_time", "entry_utc", "entry_ts", "entry", "touched_at", "open_time")
EXIT_COLUMNS = ("exit_time", "exit_utc", "exit_ts", "exit", "closed_at", "close_time")
PNL_POINT_COLUMNS = ("pnl_points", "pnl_pts", "points", "pnl", "net_pts", "pnl_raw")
PNL_DOLLAR_COLUMNS = ("pnl_dollars", "pnl_usd", "pnl_$", "net_dollars")
MAE_COLUMNS = ("mae_points", "mae_pts", "mae")
MFE_COLUMNS = ("mfe_points", "mfe_pts", "mfe")
STOP_COLUMNS = ("stop_points", "stop_pts", "sl_points", "sl_pts")
DEFAULT_PROFILE_KEY = "Apex Trader Funding - EOD PA 50K"
RESULT_SCHEMA_VERSION = 4
RESULT_STATE_KEYS = (
    "lifecycle_ranking",
    "lifecycle_monthly",
    "lifecycle_monthly_summary",
    "lifecycle_events",
    "score_config",
    "lifecycle_result_mode",
)


def main() -> None:
    st.set_page_config(page_title="Prop Convexity Lab", layout="wide")
    apply_matrix_theme()
    clear_stale_lifecycle_results(st.session_state)
    st.title("Prop Convexity Lab")

    profiles = default_prop_rule_profiles()
    lifecycle_plans = default_lifecycle_plans()
    with st.sidebar:
        simulation_mode = st.radio(
            "Simulation mode",
            ["Historical lifecycle bootstrap", "July-August realized-prefix forward simulation"],
        )
        st.header("Ledger")
        uploaded = st.file_uploader(
            "12-month ledgers",
            type=["csv", "html", "htm"],
            accept_multiple_files=True,
        )
        source_timezone = st.text_input("Source timezone for naive timestamps", value="UTC")
        default_dpp = st.number_input("Dollars per point per micro", min_value=0.01, value=2.0, step=0.5)
        fallback_minutes = st.number_input("Fallback trade duration if exit is missing", 1, 1440, 60)
        page = st.radio(
            "Section",
            ["Funded Guidance", "Prop Comparison", "Ledger & Conflicts", "Path Explorer", "Rules & Assumptions"],
        )

    if simulation_mode == "July-August realized-prefix forward simulation":
        render_forward_master_path_lab(lifecycle_plans, default_dpp=float(default_dpp))
        return

    if not uploaded:
        st.info("Upload one or more CSV ledgers to begin.")
        show_rule_profiles(profiles)
        return

    loaded_frames: list[pd.DataFrame] = []
    errors: list[str] = []
    for file in uploaded:
        try:
            raw = read_uploaded_ledger(file)
            loaded_frames.append(
                coerce_uploaded_ledger(
                    raw,
                    strategy_id=Path(file.name).stem,
                    default_dpp=default_dpp,
                    fallback_minutes=int(fallback_minutes),
                )
            )
        except Exception as exc:  # noqa: BLE001 - display file-level load errors in UI
            errors.append(f"{file.name}: {exc}")

    if errors:
        st.error("\n".join(errors))
    if not loaded_frames:
        return

    normalized_frame = pd.concat(loaded_frames, ignore_index=True)
    try:
        trades = normalize_trade_frame(
            normalized_frame,
            source_timezone=source_timezone or None,
        )
    except TradeValidationError as exc:
        st.error("Ledger validation failed.")
        st.dataframe(pd.DataFrame([issue.__dict__ for issue in exc.issues]), use_container_width=True)
        return

    strategy_ids = sorted({trade.strategy_id for trade in trades})
    priority = strategy_priority_controls(strategy_ids, expanded=page == "Ledger & Conflicts")
    resolve_conflicts = st.checkbox(
        "Drop overlapping trades by priority",
        value=True,
        help="When two strategies overlap, keep the higher-priority strategy shown in Ledger & Conflicts.",
    )
    if resolve_conflicts:
        usable_trades, decisions = resolve_overlapping_trades(trades, priority)
    else:
        usable_trades = trades
        decisions = []

    render_ledger_summary(trades, usable_trades, decisions, strategy_ids)

    if page in {"Funded Guidance", "Prop Comparison"}:
        selected_plans, settings_by_plan, contract_values, paths, horizon_months, score_config, run_simulation = (
            simulation_controls(lifecycle_plans, mode=page)
        )
        effective_rows = (
            build_effective_current_state_rows(
                selected_plans,
                start_mode=settings_by_plan[next(iter(settings_by_plan))].start_mode
                if settings_by_plan
                else "new_eval",
                current_profit=float(st.session_state.get("current_profit", 0.0)),
                current_cushion=float(st.session_state.get("current_cushion", 0.0)),
            )
            if page == "Funded Guidance"
            else []
        )
        if effective_rows:
            st.subheader("Effective Current State")
            st.dataframe(pd.DataFrame(effective_rows), width="stretch", hide_index=True)
        if run_simulation and selected_plans:
            with st.spinner(f"Running {paths:,} bootstrapped paths per size..."):
                ranking, monthly, events = run_lifecycle_grid(
                    usable_trades,
                    selected_plans,
                    contract_values=contract_values,
                    paths=int(paths),
                    horizon_months=int(horizon_months),
                    seed=1729,
                    dollars_per_point=float(default_dpp),
                    settings_by_plan=settings_by_plan,
                )
                ranking = apply_score_config(ranking, score_config)
                st.session_state["lifecycle_ranking"] = ranking
                st.session_state["lifecycle_monthly"] = monthly
                st.session_state["lifecycle_monthly_summary"] = summarize_monthly_paths(monthly)
                st.session_state["lifecycle_events"] = events
                st.session_state["score_config"] = score_config
                st.session_state["lifecycle_result_mode"] = page
        if st.session_state.get("lifecycle_result_mode") == page:
            ranking = st.session_state.get("lifecycle_ranking", pd.DataFrame())
            monthly_summary = st.session_state.get("lifecycle_monthly_summary", pd.DataFrame())
        else:
            ranking = pd.DataFrame()
            monthly_summary = pd.DataFrame()
        if ranking.empty:
            st.info("Adjust inputs, then click Run simulation.")
        elif page == "Funded Guidance":
            render_funded_guidance(ranking, monthly_summary, score_config)
        else:
            render_prop_comparison(ranking, monthly_summary, score_config)
    elif page == "Ledger & Conflicts":
        render_ledger_page(trades, usable_trades, decisions, strategy_ids, priority)
    elif page == "Path Explorer":
        render_path_explorer()
    else:
        selected_plans = st.session_state.get("selected_plans", [])
        selected_funded_keys = [plan.funded_profile.key for plan in selected_plans]
        show_rule_profiles({key: profiles[key] for key in selected_funded_keys if key in profiles} or profiles)
        st.caption(
            "Apex PA scaling tiers are listed as rule assumptions. Current implementation uses micro-equivalent caps "
            "and does not dynamically reduce contract size by balance tier inside each simulated path."
    )


def render_forward_master_path_lab(lifecycle_plans: dict[str, Any], *, default_dpp: float) -> None:
    st.header("July-August Realized-Prefix Forward Simulation")
    st.caption(
        "Uses the fixed user-confirmed July 7 and July 8 realized prefix, then samples July-August continuation "
        "from RR-specific historical packet libraries. Missing realized MAE/MFE is explicit and not fabricated."
    )

    controls, accounts = st.columns([1.0, 1.25])
    with controls:
        rr_label = st.selectbox("RR configuration", ["1RR / OG_OPERATIONAL_100R", "1.5RR / OG_PRIMARY_150R"])
        rr_config_id = "1rr" if rr_label.startswith("1RR") else "1_5rr"
        prefix_basis = st.radio(
            "Realized prefix application basis",
            ["ACCOUNT_STATE_BEFORE_PREFIX", "ACCOUNT_STATE_AFTER_PREFIX"],
            help="BEFORE applies July 7 and July 8 to the account. AFTER displays them but starts account processing after the prefix.",
        )
        master_seed = int(st.number_input("Master-path seed", value=1729, step=1))
        mc_seed = int(st.number_input("Monte Carlo seed", value=1730, step=1))
        path_count = int(st.slider("Monte Carlo paths", 10, 1000, 100, step=10))
        july_count = int(st.number_input("Remaining July candidate/trade count", min_value=0, value=8, step=1))
        august_count = int(st.number_input("August candidate/trade count", min_value=0, value=12, step=1))
        pf_scenario = st.selectbox("PF scenario", ["PF_1_35", "PF_1_50", "PF_1_65"])
        regime_scenario = st.selectbox("Regime scenario", ["stable", "gradual_degradation", "favourable_persistence", "abrupt_tail"])
        point_scale_scenario = st.selectbox("Point-scale scenario", ["current", "low", "high"])

    with accounts:
        firm_options = sorted({plan.firm for plan in lifecycle_plans.values()})
        default_firms = firm_options[:2] if len(firm_options) >= 2 else firm_options
        selected_firms = st.multiselect("Firms", firm_options, default=default_firms)
        plan_options = [
            key for key, plan in lifecycle_plans.items()
            if plan.firm in set(selected_firms) and plan_route_label(plan) == "Funded only"
        ]
        selected_plan_keys = st.multiselect("Lifecycle plans", plan_options, default=plan_options[:4])
        contract_values = st.multiselect("MNQ sizes", [1, 2, 3, 4], default=[1, 4])
        desired_payout = st.number_input("Desired payout, 0 = max allowed", min_value=0.0, value=0.0, step=100.0)
        required_cushion = st.number_input("Required cushion after payout", min_value=0.0, value=0.0, step=100.0)
        st.caption("Current account state")
        current_balance = st.number_input("Current balance", min_value=0.0, value=50_000.0, step=100.0)
        current_floor = st.number_input("Current floor / threshold", min_value=0.0, value=48_000.0, step=100.0)
        current_winning_days = int(st.number_input("Current qualifying days", min_value=0, value=0, step=1))
        current_high_day = st.number_input("Current highest winning day", min_value=0.0, value=0.0, step=50.0)
        current_daily_history = st.text_input("Current daily-profit history", value="")
        payouts_already_taken = int(st.number_input("Payouts already taken", min_value=0, value=0, step=1))
        prior_fees = st.number_input("Prior fees", min_value=0.0, value=0.0, step=10.0)

    scenario = ForwardScenario(
        rr_config_id=rr_config_id,
        july_candidate_count=july_count,
        august_candidate_count=august_count,
        master_seed=master_seed,
        mc_seed=mc_seed,
        path_count=path_count,
        pf_scenario=pf_scenario,
        regime_scenario=regime_scenario,
        point_scale_scenario=point_scale_scenario,
        prefix_application_basis=prefix_basis,
        current_balance=float(current_balance),
        current_floor=float(current_floor),
        current_winning_days=int(current_winning_days),
        current_highest_winning_day=float(current_high_day),
        current_daily_profits=tuple(parse_float_list(current_daily_history)),
        payouts_already_taken=int(payouts_already_taken),
        prior_fees=float(prior_fees),
    )
    selected_plans = [lifecycle_plans[key] for key in selected_plan_keys]
    settings_by_plan = {
        plan.key: LifecycleSettings(
            start_mode="funded",
            desired_payout=float(desired_payout),
            required_cushion=float(required_cushion),
            current_balance=float(current_balance),
            current_floor=float(current_floor),
            current_winning_days=int(current_winning_days),
            current_highest_winning_day=float(current_high_day),
        )
        for plan in selected_plans
    }

    master_path = build_master_path(scenario, path_id=0)
    realized = master_path[master_path["status"] == "REALIZED"]
    synthetic = master_path[master_path["status"] == "SYNTHETIC"]
    prefix_net = float(master_path["realized_prefix_net_points"].iloc[-1])
    forward_net = float(master_path["forward_only_net_points"].iloc[-1])
    combined_net = float(master_path["combined_net_points"].iloc[-1])

    st.subheader("Master Path")
    headline = st.columns(5)
    if rr_config_id == "1rr":
        headline[0].metric("July 7 realized", "+150 TP")
        headline[1].metric("July 8 realized", "-200 SL")
    else:
        headline[0].metric("July 7 realized", "0 BE")
        headline[1].metric("July 8 realized", "-200 SL")
    headline[2].metric("Realized prefix", f"{prefix_net:,.0f} pts")
    headline[3].metric("Forecast continuation", f"{forward_net:,.0f} pts")
    headline[4].metric("Combined", f"{combined_net:,.0f} pts")

    if realized["excursion_confidence"].eq("UNKNOWN_USER_CONFIRMED").any():
        st.warning(
            "The realized July 7/8 rows are user-confirmed outcomes with unknown MAE/MFE. Final P&L is applied, "
            "but strict intratrade barrier status is UNKNOWN for those rows."
        )

    display_columns = [
        "sequence_number",
        "status",
        "session_date",
        "pnl_points",
        "cumulative_realized_points",
        "cumulative_forward_only_points",
        "cumulative_combined_points",
        "exit_reason",
        "effective_stop_points",
        "target_points",
        "mae_points",
        "mfe_points",
        "evidence_status",
        "excursion_confidence",
        "strict_barrier_status",
        "source_trade_packet_id",
    ]
    st.dataframe(master_path[display_columns], width="stretch", hide_index=True)
    st.download_button(
        "Download deterministic Master Path CSV",
        master_path.to_csv(index=False),
        file_name=f"master_path_{rr_config_id}.csv",
        mime="text/csv",
    )

    st.subheader("Realized Prefix")
    st.dataframe(realized[display_columns], width="stretch", hide_index=True)

    st.subheader("Synthetic Continuation")
    st.dataframe(synthetic[display_columns], width="stretch", hide_index=True)

    if st.button("Run forward Monte Carlo", type="primary", use_container_width=True):
        with st.spinner(f"Running {path_count:,} paths with shared strategy paths across selected accounts..."):
            mc_paths = build_monte_carlo_paths(scenario)
            point_results = path_summary(mc_paths, scenario)
            if selected_plans and contract_values:
                lifecycle_summary, lifecycle_monthly, lifecycle_events = run_forward_lifecycle_grid(
                    mc_paths,
                    selected_plans,
                    contract_values=[int(value) for value in contract_values],
                    settings_by_plan=settings_by_plan,
                    dollars_per_point=float(default_dpp),
                    prefix_application_basis=prefix_basis,
                )
                per_trade_ledger = build_forward_account_trade_ledger(
                    mc_paths,
                    selected_plans,
                    contract_values=[int(value) for value in contract_values],
                    settings_by_plan=settings_by_plan,
                    dollars_per_point=float(default_dpp),
                    prefix_application_basis=prefix_basis,
                    scenario=scenario,
                )
            else:
                lifecycle_summary, lifecycle_monthly, lifecycle_events = pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
                per_trade_ledger = pd.DataFrame()
            outputs = export_forward_artifacts(
                scenario,
                master_path,
                mc_paths,
                lifecycle_summary,
                lifecycle_events,
                per_trade_ledger,
            )
            st.session_state["forward_point_results"] = point_results
            st.session_state["forward_manifest"] = strategy_path_manifest(mc_paths, scenario)
            st.session_state["forward_lifecycle_summary"] = lifecycle_summary
            st.session_state["forward_lifecycle_monthly"] = lifecycle_monthly
            st.session_state["forward_lifecycle_events"] = lifecycle_events
            st.session_state["forward_per_trade_ledger"] = per_trade_ledger
            st.session_state["forward_outputs"] = {key: str(path) for key, path in outputs.items()}

    point_results = st.session_state.get("forward_point_results", pd.DataFrame())
    if not point_results.empty:
        st.subheader("Monte Carlo Point Results")
        cols = st.columns(4)
        cols[0].metric("Paths", f"{len(point_results):,}")
        cols[1].metric("Median combined", f"{point_results['combined_net_points'].median():,.0f} pts")
        cols[2].metric("Median forward-only", f"{point_results['forward_only_net_points'].median():,.0f} pts")
        cols[3].metric("Prefix basis", prefix_basis)
        st.dataframe(point_results, width="stretch", hide_index=True)
        st.download_button("Download point results CSV", point_results.to_csv(index=False), "path_level_point_results.csv", "text/csv")

    manifest = st.session_state.get("forward_manifest", pd.DataFrame())
    if not manifest.empty:
        st.subheader("Strategy Path Manifest")
        st.caption("One packet sequence per strategy path ID; account, firm, lifecycle plan, and MNQ size do not resample it.")
        st.dataframe(manifest.head(100), width="stretch", hide_index=True)
        st.download_button("Download strategy-path manifest CSV", manifest.to_csv(index=False), "monte_carlo_strategy_path_manifest.csv", "text/csv")

    lifecycle_summary = st.session_state.get("forward_lifecycle_summary", pd.DataFrame())
    if not lifecycle_summary.empty:
        st.subheader("Lifecycle / Account Results")
        st.dataframe(format_lifecycle_ranking(lifecycle_summary), width="stretch", hide_index=True)
        st.download_button("Download lifecycle account results CSV", lifecycle_summary.to_csv(index=False), "lifecycle_account_results.csv", "text/csv")

    per_trade_ledger = st.session_state.get("forward_per_trade_ledger", pd.DataFrame())
    if not per_trade_ledger.empty:
        st.subheader("Path Inspector: Per-Trade Account Ledger")
        st.dataframe(per_trade_ledger.head(500), width="stretch", hide_index=True)
        st.download_button("Download per-trade account ledger CSV", per_trade_ledger.to_csv(index=False), "per_trade_account_ledger.csv", "text/csv")

    outputs = st.session_state.get("forward_outputs", {})
    if outputs:
        st.subheader("Committed Export Paths")
        st.dataframe(pd.DataFrame([{"artifact": key, "path": value} for key, value in outputs.items()]), width="stretch", hide_index=True)


def simulation_controls(
    lifecycle_plans: dict[str, Any],
    *,
    mode: str,
) -> tuple[list[Any], dict[str, LifecycleSettings], list[int], int, int, dict[str, float], bool]:
    st.header(mode)
    if mode == "Funded Guidance":
        st.caption("Use this for one current funded/PA account. Use Prop Comparison for all-firm funded-only shopping.")
    else:
        st.caption("Use this to compare firms, account sizes, and funded/eval routes side by side.")
    account_col, sim_col = st.columns([1.15, 1.0])
    firm_options = sorted({plan.firm for plan in lifecycle_plans.values()})
    with account_col:
        selected_firms = st.multiselect(
            "Firms",
            firm_options,
            default=firm_options
            if mode == "Prop Comparison"
            else ["Apex Trader Funding"] if "Apex Trader Funding" in firm_options else firm_options[:1],
            key=f"{mode}_firms",
        )
        size_options = sorted(
            {account_size_label(plan.account_size) for plan in lifecycle_plans.values()},
            key=account_size_sort_key,
        )
        selected_sizes = st.multiselect(
            "Account sizes",
            size_options,
            default=["50K"] if "50K" in size_options else size_options[:1],
            key=f"{mode}_sizes",
        )
        route_options = ["Funded only", "Eval to funded"]
        selected_routes = st.multiselect(
            "Account route",
            route_options,
            default=["Funded only"] if mode == "Prop Comparison" else ["Eval to funded"],
            key=f"{mode}_routes",
        )
        plan_options = [
            key
            for key, plan in lifecycle_plans.items()
            if plan.firm in set(selected_firms)
            and account_size_label(plan.account_size) in set(selected_sizes)
            and plan_route_label(plan) in set(selected_routes)
        ]
        default_plan = next(
            (key for key in plan_options if key.startswith("Apex Trader Funding - EOD 50K")),
            plan_options[0] if plan_options else None,
        )
        default_accounts = plan_options if mode == "Prop Comparison" else [default_plan] if default_plan is not None else []
        selected_plan_keys = st.multiselect(
            "Accounts",
            plan_options,
            default=default_accounts,
            key=f"{mode}_accounts",
        )
        selected_route_set = set(selected_routes)
        if mode == "Funded Guidance" or selected_route_set == {"Funded only"}:
            start_labels = ["Funded / PA"]
        else:
            start_labels = ["New eval", "Existing eval", "Funded / PA"]
        start_mode_label = st.selectbox("Starting point", start_labels, key=f"{mode}_start_mode")
        start_mode = {"New eval": "new_eval", "Existing eval": "existing_eval", "Funded / PA": "funded"}[
            start_mode_label
        ]
    with sim_col:
        min_contracts, max_contracts = st.slider("Micro contracts", 1, 50, (1, 8), key=f"{mode}_contracts")
        paths = st.slider("Bootstrap paths", 25, 1000, 250, step=25, key=f"{mode}_paths")
        horizon_months = st.slider("Horizon months", 1, 24, 12, key=f"{mode}_horizon")
        st.caption(f"Results are shown across {paths:,} bootstrapped paths per account/size.")

    selected_plans = [lifecycle_plans[key] for key in selected_plan_keys]
    st.session_state["selected_plans"] = selected_plans
    st.session_state["current_profit"] = 0.0
    st.session_state["current_cushion"] = 0.0

    current_profit = 0.0
    current_cushion = 0.0
    current_winning_days = 0
    current_high_day = 0.0
    use_current_account_state = mode == "Funded Guidance" and start_mode in {"existing_eval", "funded"}
    if use_current_account_state:
        state_cols = st.columns(4)
        current_profit = state_cols[0].number_input(
            "Current profit above start",
            min_value=0.0,
            value=500.0 if mode == "Funded Guidance" else 0.0,
            step=100.0,
            key=f"{mode}_current_profit",
        )
        current_cushion = state_cols[1].number_input(
            "Drawdown cushion left",
            min_value=0.0,
            value=1500.0 if mode == "Funded Guidance" else 0.0,
            step=100.0,
            key=f"{mode}_current_cushion",
        )
        current_winning_days = int(
            state_cols[2].number_input("Qualifying days", 0, 30, 0, key=f"{mode}_winning_days")
        )
        current_high_day = state_cols[3].number_input(
            "Highest winning day", min_value=0.0, value=0.0, step=50.0, key=f"{mode}_high_day"
        )
        st.session_state["current_profit"] = float(current_profit)
        st.session_state["current_cushion"] = float(current_cushion)

    target_cols = st.columns(4)
    desired_payout = target_cols[0].number_input(
        "Desired payout, 0 = max allowed",
        min_value=0.0,
        value=1500.0 if mode == "Funded Guidance" else 0.0,
        step=100.0,
        key=f"{mode}_desired_payout",
    )
    required_cushion = target_cols[1].number_input(
        "Required cushion after payout",
        min_value=0.0,
        value=0.0,
        step=100.0,
        key=f"{mode}_required_cushion",
    )
    max_rebuy_capital = target_cols[2].number_input(
        "Max fee capital / rebuys",
        min_value=0.0,
        value=1000.0,
        step=50.0,
        key=f"{mode}_max_rebuy_capital",
    )
    allow_rebuys = target_cols[3].checkbox("Allow eval rebuys", value=True, key=f"{mode}_allow_rebuys")

    score_config = score_controls(mode)
    firm_costs = fee_controls(selected_plans, mode)
    settings_by_plan: dict[str, LifecycleSettings] = {}
    for plan in selected_plans:
        costs = firm_costs.get(plan.firm, {})
        effective_start_mode = start_mode if plan.eval_profile is not None else "funded"
        active_profile = (
            plan.eval_profile
            if effective_start_mode in {"new_eval", "existing_eval"} and plan.eval_profile is not None
            else plan.funded_profile
        )
        actual_current_balance, actual_current_floor = current_state_overrides(
            mode=mode,
            start_mode=effective_start_mode,
            starting_balance=float(active_profile.starting_balance),
            current_profit=float(current_profit),
            current_cushion=float(current_cushion),
        )
        settings_by_plan[plan.key] = LifecycleSettings(
            start_mode=effective_start_mode,
            current_balance=actual_current_balance,
            current_floor=actual_current_floor,
            current_winning_days=int(current_winning_days),
            current_highest_winning_day=float(current_high_day),
            desired_payout=float(desired_payout),
            required_cushion=float(required_cushion),
            allow_rebuys=bool(allow_rebuys),
            max_rebuy_capital=float(max_rebuy_capital),
            eval_fee=float(costs.get("eval_fee", plan.default_eval_fee)),
            activation_fee=float(costs.get("activation_fee", plan.default_activation_fee)),
            reset_fee=float(costs.get("reset_fee", plan.default_reset_fee)),
        )
    run_simulation = st.button("Run simulation", type="primary", use_container_width=True, key=f"{mode}_run")
    return (
        selected_plans,
        settings_by_plan,
        list(range(int(min_contracts), int(max_contracts) + 1)),
        int(paths),
        int(horizon_months),
        score_config,
        bool(run_simulation),
    )


def clear_stale_lifecycle_results(session_state: Any) -> bool:
    if session_state.get("result_schema_version") == RESULT_SCHEMA_VERSION:
        return False
    for key in RESULT_STATE_KEYS:
        session_state.pop(key, None)
    session_state["result_schema_version"] = RESULT_SCHEMA_VERSION
    return True


def parse_float_list(raw: str) -> list[float]:
    values: list[float] = []
    for item in raw.replace("\n", ",").split(","):
        item = item.strip()
        if not item:
            continue
        values.append(float(item))
    return values


def score_controls(mode: str) -> dict[str, float]:
    with st.expander("Score weights and risk gate", expanded=False):
        cols = st.columns(5)
        survival = cols[0].slider("Survival", 0, 100, 40, key=f"{mode}_survival_weight")
        ev = cols[1].slider("EV", 0, 100, 30, key=f"{mode}_ev_weight")
        speed = cols[2].slider("Speed", 0, 100, 15, key=f"{mode}_speed_weight")
        convexity = cols[3].slider("Convexity", 0, 100, 15, key=f"{mode}_convexity_weight")
        max_blow = cols[4].slider("Max blow before payout", 0, 100, 50, key=f"{mode}_max_blow")
    total = max(1, survival + ev + speed + convexity)
    return {
        "survival_weight": survival / total,
        "ev_weight": ev / total,
        "speed_weight": speed / total,
        "convexity_weight": convexity / total,
        "max_blow_rate": max_blow / 100.0,
    }


def current_state_overrides(
    *,
    mode: str,
    start_mode: str,
    starting_balance: float,
    current_profit: float,
    current_cushion: float,
) -> tuple[float | None, float | None]:
    if mode != "Funded Guidance" or start_mode not in {"existing_eval", "funded"}:
        return None, None
    current_balance = starting_balance + current_profit
    return current_balance, current_balance - current_cushion


def fee_controls(selected_plans: list[Any], mode: str) -> dict[str, dict[str, float]]:
    firm_costs: dict[str, dict[str, float]] = {}
    with st.expander("Fees by firm", expanded=False):
        for firm_name in sorted({plan.firm for plan in selected_plans}):
            st.caption(firm_name)
            col_a, col_b, col_c = st.columns(3)
            firm_plans = [plan for plan in selected_plans if plan.firm == firm_name]
            default_eval = max((plan.default_eval_fee for plan in firm_plans), default=0.0)
            default_activation = max((plan.default_activation_fee for plan in firm_plans), default=0.0)
            default_reset = max((plan.default_reset_fee for plan in firm_plans), default=0.0)
            firm_costs[firm_name] = {
                "eval_fee": col_a.number_input(
                    "Eval", min_value=0.0, value=float(default_eval), step=10.0, key=f"{mode}_{firm_name}_eval"
                ),
                "activation_fee": col_b.number_input(
                    "Activation",
                    min_value=0.0,
                    value=float(default_activation),
                    step=10.0,
                    key=f"{mode}_{firm_name}_activation",
                ),
                "reset_fee": col_c.number_input(
                    "Reset", min_value=0.0, value=float(default_reset), step=10.0, key=f"{mode}_{firm_name}_reset"
                ),
            }
    return firm_costs


def apply_score_config(frame: pd.DataFrame, score_config: dict[str, float]) -> pd.DataFrame:
    if frame.empty:
        return frame
    scored = ensure_guidance_columns(frame)
    scored["display_composite_score"] = (
        score_config["survival_weight"] * scored["survival_score"].astype(float)
        + score_config["ev_weight"] * scored["ev_score"].astype(float)
        + score_config["speed_weight"] * scored["speed_score"].astype(float)
        + score_config["convexity_weight"] * scored["convexity_score"].astype(float)
    )
    scored["status"] = scored["blew_before_payout_rate"].apply(
        lambda rate: "Too aggressive" if float(rate) > score_config["max_blow_rate"] else "Candidate"
    )
    return scored.sort_values(
        ["status", "display_composite_score", "paid_before_first_blow_rate", "mean_net_cash"],
        ascending=[True, False, False, False],
    )


def ensure_guidance_columns(frame: pd.DataFrame) -> pd.DataFrame:
    scored = frame.copy()
    if "paid_before_first_blow_rate" not in scored:
        scored["paid_before_first_blow_rate"] = scored.get("current_account_paid_first_rate", 0.0)
    if "blew_before_payout_rate" not in scored:
        scored["blew_before_payout_rate"] = scored.get("current_account_blew_first_rate", 0.0)
    if "payout_after_rebuy_rate" not in scored:
        scored["payout_after_rebuy_rate"] = 0.0
    if "no_resolution_rate" not in scored:
        scored["no_resolution_rate"] = (
            1.0
            - scored["paid_before_first_blow_rate"].astype(float)
            - scored["blew_before_payout_rate"].astype(float)
            - scored["payout_after_rebuy_rate"].astype(float)
        ).clip(lower=0.0)
    if "paid_before_first_blow_paths" not in scored:
        paths = pd.to_numeric(
            scored.get("paths", pd.Series([0] * len(scored))),
            errors="coerce",
        ).fillna(0).astype(int)
        paid_counts = (scored["paid_before_first_blow_rate"].astype(float) * paths).round().astype(int)
        scored["paid_before_first_blow_paths"] = paid_counts.astype(str) + " / " + paths.astype(str)
    if "avg_net_cash" not in scored:
        scored["avg_net_cash"] = scored.get("mean_net_cash", 0.0)
    if "avg_fees" not in scored:
        scored["avg_fees"] = scored.get("mean_fees", 0.0)
    if "avg_withdrawal" not in scored:
        scored["avg_withdrawal"] = scored.get("p50_payouts", scored.get("p50_net_cash", 0.0))
    if "p50_withdrawal" not in scored:
        scored["p50_withdrawal"] = scored.get("p50_payouts", scored["avg_withdrawal"])
    if "p95_withdrawal" not in scored:
        scored["p95_withdrawal"] = scored.get("p95_payouts", scored["p50_withdrawal"])
    if "avg_payout_count" not in scored:
        scored["avg_payout_count"] = scored.get("payouts_taken", 0.0)
    if "p50_payout_count" not in scored:
        scored["p50_payout_count"] = scored["avg_payout_count"]
    if "survival_score" not in scored:
        scored["survival_score"] = 100.0 * scored["paid_before_first_blow_rate"].astype(float) * (
            1.0 - scored["blew_before_payout_rate"].astype(float)
        ).clip(lower=0.0)
    if "speed_score" not in scored:
        payout_month = pd.to_numeric(scored.get("p50_month_to_first_payout", pd.Series([None] * len(scored))), errors="coerce")
        scored["speed_score"] = (
            100.0
            * scored["paid_before_first_blow_rate"].astype(float)
            / payout_month.fillna(12.0).clip(lower=1.0)
        )
        scored.loc[payout_month.isna(), "speed_score"] = 0.0
    if "ev_score" not in scored:
        net = pd.to_numeric(scored.get("mean_net_cash", pd.Series([0.0] * len(scored))), errors="coerce").fillna(0.0)
        net_min = float(net.min()) if len(net) else 0.0
        net_max = float(net.max()) if len(net) else 0.0
        scored["ev_score"] = ((net - net_min) / (net_max - net_min) * 100.0) if net_max > net_min else 0.0
    if "convexity_score" not in scored:
        scored["convexity_score"] = 0.0
    return scored


def render_funded_guidance(
    ranking: pd.DataFrame,
    monthly_summary: pd.DataFrame,
    score_config: dict[str, float],
) -> None:
    st.subheader("Funded Guidance")
    st.caption("Fail/blow means blew before payout. Rebuy-inclusive payouts are shown separately.")
    candidates = ranking[ranking["blew_before_payout_rate"] <= score_config["max_blow_rate"]]
    best = candidates.iloc[0] if not candidates.empty else ranking.iloc[0]
    cols = st.columns(6)
    cols[0].metric("Best size", f"{int(best['contracts'])} micros")
    cols[1].metric("Paid before blow", pct(float(best["paid_before_first_blow_rate"])))
    cols[2].metric("Blew before payout", pct(float(best["blew_before_payout_rate"])))
    cols[3].metric("Avg net / path", money(float(best["avg_net_cash"])))
    cols[4].metric("Median first payout", month_or_dash(best["p50_month_to_first_payout"]))
    cols[5].metric("Status", str(best["status"]))

    dial_cols = st.columns(5)
    for col, label, field in (
        (dial_cols[0], "Survival", "survival_score"),
        (dial_cols[1], "EV", "ev_score"),
        (dial_cols[2], "Speed", "speed_score"),
        (dial_cols[3], "Convexity", "convexity_score"),
        (dial_cols[4], "Composite", "display_composite_score"),
    ):
        col.metric(label, f"{float(best[field]):.0f}/100")

    st.dataframe(format_guidance_ranking(ranking), width="stretch", hide_index=True)
    with st.expander("Detailed metrics"):
        st.dataframe(format_lifecycle_ranking(ranking), width="stretch", hide_index=True)
    render_heatmap_section(monthly_summary)


def render_prop_comparison(
    ranking: pd.DataFrame,
    monthly_summary: pd.DataFrame,
    score_config: dict[str, float],
) -> None:
    st.subheader("Prop Comparison")
    st.caption(
        "First-payout tabs answer speed and survival questions. The 12-month tab ranks cumulative withdrawals "
        "after payout splits, caps, fees, and rebuys."
    )
    first_payout_tab, withdrawal_tab, risk_tab, rules_tab = st.tabs(
        ["First payout", "12-month withdrawals", "Risk", "Rules"]
    )
    with first_payout_tab:
        table = filter_first_payout_rows(ranking)
        if table.empty:
            table = ranking
        render_prop_headlines(table)
        render_first_payout_charts(table)
        st.subheader("First-payout candidates")
        st.dataframe(format_first_payout_comparison(table), width="stretch", hide_index=True)
    with withdrawal_tab:
        table = filter_withdrawal_rows(ranking)
        if table.empty:
            table = ranking
        render_prop_charts(table)
        st.subheader("12-month withdrawal candidates")
        st.dataframe(format_prop_comparison(table), width="stretch", hide_index=True)
    with risk_tab:
        table = filter_viable_prop_rows(ranking, score_config)
        if table.empty:
            st.warning("No candidates pass the current max blow-before-payout threshold.")
            table = ranking
        st.subheader("Risk-gated candidates")
        st.dataframe(format_first_payout_comparison(table), width="stretch", hide_index=True)
        render_heatmap_section(monthly_summary, table, drilldown=True)
    with rules_tab:
        st.subheader("Rule Assumptions")
        st.dataframe(
            build_rule_audit_rows(st.session_state.get("selected_plans", [])),
            width="stretch",
            hide_index=True,
        )
    with st.expander("Detailed metrics"):
        st.dataframe(format_lifecycle_ranking(ranking), width="stretch", hide_index=True)


def render_prop_headlines(frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    payout_frame = frame[
        (frame.get("any_payout_rate", pd.Series([0.0] * len(frame))).astype(float) > 0)
        | (frame.get("avg_withdrawal", pd.Series([0.0] * len(frame))).astype(float) > 0)
    ].copy()
    if payout_frame.empty:
        st.warning(
            "No payouts were observed in the selected comparison. Showing the raw rows below, but there is no "
            "funded-withdrawal candidate to rank as best under these settings."
        )
        return
    best_by_plan = (
        payout_frame.sort_values(["plan", "display_composite_score"], ascending=[True, False])
        .groupby("plan", sort=False)
        .head(1)
        .copy()
    )
    fastest = best_by_plan.sort_values(
        ["p50_month_to_first_payout", "paid_before_first_blow_rate"],
        ascending=[True, False],
        na_position="last",
    ).iloc[0]
    safest = best_by_plan.sort_values("paid_before_first_blow_rate", ascending=False).iloc[0]
    best_ev = best_by_plan.sort_values("avg_net_cash", ascending=False).iloc[0]
    best_withdrawal = best_by_plan.sort_values(
        "avg_withdrawal" if "avg_withdrawal" in best_by_plan else "avg_net_cash",
        ascending=False,
    ).iloc[0]
    cols = st.columns(3)
    for col, title, row in (
        (cols[0], "Fastest payout", fastest),
        (cols[1], "Best survival", safest),
        (cols[2], "Highest 12-month withdrawal", best_withdrawal),
    ):
        col.metric(title, f"{row['firm']} {row['account']}")
        col.caption(
            f"{int(row['contracts'])} micros | paid {pct(float(row['paid_before_first_blow_rate']))} | "
            f"blow {pct(float(row['blew_before_payout_rate']))} | first payout {month_or_dash(row['p50_month_to_first_payout'])} | "
            f"avg withdrawal {money(float(row.get('avg_withdrawal', row['avg_net_cash'])))} | "
            f"avg payouts {float(row.get('avg_payout_count', 0.0)):.1f}"
        )
    st.subheader("Best size per account")
    st.dataframe(format_prop_summary(best_by_plan), width="stretch", hide_index=True)
    st.markdown(build_prop_plain_english_summary(fastest, safest, best_withdrawal))


def render_prop_charts(frame: pd.DataFrame) -> None:
    chart = build_prop_chart_frame(frame)
    if chart.empty:
        return
    st.subheader("Comparison Charts")
    cols = st.columns(2)
    cols[0].caption("Average withdrawal after splits/caps")
    cols[0].bar_chart(chart.set_index("label")["avg_withdrawal"], height=240)
    cols[1].caption("Payout-before-blow vs blow-before-payout")
    cols[1].bar_chart(
        chart.set_index("label")[["paid_before_first_blow_rate", "blew_before_payout_rate"]],
        height=240,
    )
    cols = st.columns(2)
    cols[0].caption("Median month to first payout")
    cols[0].bar_chart(chart.set_index("label")["p50_month_to_first_payout"], height=240)
    cols[1].caption("Average payout count per path")
    cols[1].bar_chart(chart.set_index("label")["avg_payout_count"], height=240)


def render_first_payout_charts(frame: pd.DataFrame) -> None:
    chart = build_prop_chart_frame(frame)
    if chart.empty:
        return
    st.subheader("First-Payout Charts")
    cols = st.columns(2)
    cols[0].caption("Payout-before-blow vs blow-before-payout")
    cols[0].bar_chart(
        chart.set_index("label")[["paid_before_first_blow_rate", "blew_before_payout_rate"]],
        height=240,
    )
    cols[1].caption("Median month to first payout")
    cols[1].bar_chart(chart.set_index("label")["p50_month_to_first_payout"], height=240)


def build_prop_chart_frame(frame: pd.DataFrame, *, limit: int = 14) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    chart = ensure_guidance_columns(frame).copy()
    chart["label"] = (
        chart["firm"].astype(str)
        + " "
        + chart["account"].astype(str)
        + " | "
        + chart["contracts"].astype(int).astype(str)
        + "m"
    )
    for column in (
        "avg_withdrawal",
        "paid_before_first_blow_rate",
        "blew_before_payout_rate",
        "p50_month_to_first_payout",
        "avg_payout_count",
    ):
        chart[column] = pd.to_numeric(chart.get(column, 0.0), errors="coerce").fillna(0.0)
    return chart.sort_values(["avg_withdrawal", "paid_before_first_blow_rate"], ascending=False).head(limit)


def filter_viable_prop_rows(frame: pd.DataFrame, score_config: dict[str, float]) -> pd.DataFrame:
    if frame.empty:
        return frame
    viable = frame[
        (frame["blew_before_payout_rate"].astype(float) <= score_config["max_blow_rate"])
        & (frame["paid_before_first_blow_rate"].astype(float) > 0)
        & (frame["any_payout_rate"].astype(float) > 0)
    ].copy()
    return viable.sort_values(
        ["display_composite_score", "paid_before_first_blow_rate", "avg_net_cash"],
        ascending=[False, False, False],
    )


def filter_first_payout_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    rows = ensure_guidance_columns(frame)
    rows = rows[
        (rows["paid_before_first_blow_rate"].astype(float) > 0)
        | (rows["any_payout_rate"].astype(float) > 0)
    ].copy()
    if rows.empty:
        return rows
    return rows.sort_values(
        ["paid_before_first_blow_rate", "p50_month_to_first_payout", "blew_before_payout_rate"],
        ascending=[False, True, True],
        na_position="last",
    )


def filter_withdrawal_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    frame = ensure_guidance_columns(frame)
    rows = frame[
        (frame["any_payout_rate"].astype(float) > 0)
        | (frame["avg_withdrawal"].astype(float) > 0)
    ].copy()
    if rows.empty:
        return rows
    return rows.sort_values(
        ["avg_withdrawal", "any_payout_rate", "p50_month_to_first_payout"],
        ascending=[False, False, True],
        na_position="last",
    )


def build_prop_plain_english_summary(fastest: pd.Series, safest: pd.Series, best_ev: pd.Series) -> str:
    return (
        f"At **{int(fastest['contracts'])} micros**, **{fastest['firm']} {fastest['account']}** is the fastest "
        f"candidate: {month_or_dash(fastest['p50_month_to_first_payout'])} median first payout, "
        f"{pct(float(fastest['paid_before_first_blow_rate']))} paid before blow, "
        f"{pct(float(fastest['blew_before_payout_rate']))} blow before payout. "
        f"Best survival is **{safest['firm']} {safest['account']}** at "
        f"{pct(float(safest['paid_before_first_blow_rate']))}; highest 12-month withdrawal is "
        f"**{best_ev['firm']} {best_ev['account']}** at {money(float(best_ev.get('avg_withdrawal', best_ev['avg_net_cash'])))} per path."
    )


def render_heatmap_section(
    monthly_summary: pd.DataFrame,
    ranking: pd.DataFrame | None = None,
    *,
    drilldown: bool = False,
) -> None:
    if monthly_summary.empty:
        return
    st.subheader("Month Drilldown" if drilldown else "Generalized Path Heatmap")
    selected_monthly = monthly_summary
    if drilldown and ranking is not None and not ranking.empty:
        options = [
            (
                str(row.plan),
                int(row.contracts),
                f"{row.firm} {row.account} | {int(row.contracts)} micros | paid "
                f"{pct(float(row.paid_before_first_blow_rate))} | payout {month_or_dash(row.p50_month_to_first_payout)}",
            )
            for row in ranking.sort_values("display_composite_score", ascending=False).itertuples(index=False)
        ]
        selected_plan, selected_contracts, _label = st.selectbox(
            "Account/size",
            options,
            format_func=lambda item: item[2],
        )
        selected_monthly = monthly_summary[
            (monthly_summary["plan"] == selected_plan)
            & (monthly_summary["contracts"].astype(int) == int(selected_contracts))
        ]
    st.caption(
        "Active PnL excludes paths that already hit terminal failure. All-path realized cash still includes payouts "
        "already banked, which is why it can stay positive after many paths stop trading."
    )
    pnl_tab, live_tab, cash_tab, risk_tab = st.tabs(["Active PnL", "Live Paths", "Realized Cash", "Payout / Blow"])
    with pnl_tab:
        st.dataframe(build_monthly_heatmap_styler(selected_monthly, "Active median PnL"), width="stretch")
    with live_tab:
        cols = st.columns(2)
        cols[0].dataframe(build_monthly_heatmap_styler(selected_monthly, "Active path rate"), width="stretch")
        cols[1].dataframe(build_monthly_heatmap_styler(selected_monthly, "Terminal path rate"), width="stretch")
    with cash_tab:
        st.dataframe(build_monthly_heatmap_styler(selected_monthly, "Median realized cash"), width="stretch")
    with risk_tab:
        cols = st.columns(2)
        cols[0].dataframe(build_monthly_heatmap_styler(selected_monthly, "Payout month rate"), width="stretch")
        cols[1].dataframe(build_monthly_heatmap_styler(selected_monthly, "Blow month rate"), width="stretch")
    with st.expander("Monthly detail table"):
        st.dataframe(format_monthly_summary(selected_monthly), width="stretch", hide_index=True)


def render_ledger_summary(
    trades: list[Any],
    usable_trades: list[Any],
    decisions: list[Any],
    strategy_ids: list[str],
) -> None:
    top = st.columns(5)
    top[0].metric("Uploaded trades", f"{len(trades):,}")
    top[1].metric("Used trades", f"{len(usable_trades):,}")
    top[2].metric("Dropped overlaps", f"{len([d for d in decisions if not d.kept]):,}")
    top[3].metric("Strategies", f"{len(strategy_ids):,}")
    top[4].metric("Span", trade_span_label(usable_trades))


def strategy_priority_controls(strategy_ids: list[str], *, expanded: bool) -> list[str]:
    if not strategy_ids:
        return []
    with st.expander("Strategy priority for overlaps", expanded=expanded):
        st.caption("Lower rank wins overlaps. Use unique ranks; ties fall back to strategy name.")
        ranks = {}
        cols = st.columns(min(4, max(1, len(strategy_ids))))
        for index, strategy_id in enumerate(strategy_ids):
            ranks[strategy_id] = cols[index % len(cols)].number_input(
                strategy_id,
                min_value=1,
                max_value=max(1, len(strategy_ids)),
                value=index + 1,
                step=1,
                key=f"priority_{strategy_id}",
            )
        with st.expander("Raw priority fallback", expanded=False):
            raw = st.text_input("Priority order, highest first", value=", ".join(strategy_ids))
            fallback = [item.strip() for item in raw.split(",") if item.strip()]
    ranked = sorted(strategy_ids, key=lambda strategy: (int(ranks[strategy]), strategy))
    return fallback if set(fallback) == set(strategy_ids) else ranked


def render_ledger_page(
    trades: list[Any],
    usable_trades: list[Any],
    decisions: list[Any],
    strategy_ids: list[str],
    priority: list[str],
) -> None:
    st.subheader("Ledger & Conflicts")
    counts = []
    for strategy_id in strategy_ids:
        counts.append(
            {
                "strategy": strategy_id,
                "priority": priority.index(strategy_id) + 1 if strategy_id in priority else "-",
                "uploaded_trades": sum(trade.strategy_id == strategy_id for trade in trades),
                "used_trades": sum(trade.strategy_id == strategy_id for trade in usable_trades),
            }
        )
    st.dataframe(pd.DataFrame(counts), width="stretch", hide_index=True)
    if decisions:
        st.subheader("Conflicts preview")
        st.dataframe(pd.DataFrame([decision.to_dict() for decision in decisions]), width="stretch", hide_index=True)
    st.subheader("Normalized ledger")
    st.dataframe(build_ledger_frame(usable_trades), width="stretch", hide_index=True)


def render_path_explorer() -> None:
    st.subheader("Path Explorer")
    monthly = st.session_state.get("lifecycle_monthly", pd.DataFrame())
    events = st.session_state.get("lifecycle_events", pd.DataFrame())
    if monthly.empty:
        st.info("Run a funded guidance or prop comparison simulation first.")
        return
    path_options = build_path_options(monthly)
    selected_path = st.selectbox("Path", path_options, format_func=lambda item: item[1])
    path_frame = monthly[monthly["path_id"] == selected_path[0]].copy()
    st.dataframe(format_path_frame(path_frame), width="stretch", hide_index=True)
    if not events.empty:
        st.subheader("Events")
        st.dataframe(format_events(events[events["path_id"] == selected_path[0]]), width="stretch", hide_index=True)


def build_ledger_frame(trades: list[Any]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "strategy": [trade.strategy_id for trade in trades],
            "entry": [trade.entry_time for trade in trades],
            "exit": [trade.exit_time for trade in trades],
            "pnl_points": [trade.pnl_points for trade in trades],
            "pnl_dollars_1x": [trade.pnl_dollars for trade in trades],
            "mae_points": [trade.mae_points for trade in trades],
            "mfe_points": [trade.mfe_points for trade in trades],
        }
    )


def build_effective_current_state_rows(
    selected_plans: list[Any],
    *,
    start_mode: str,
    current_profit: float,
    current_cushion: float,
) -> list[dict[str, Any]]:
    if not selected_plans or start_mode not in {"existing_eval", "funded"}:
        return []
    rows: list[dict[str, Any]] = []
    for plan in selected_plans:
        effective_start_mode = start_mode if plan.eval_profile is not None else "funded"
        active_profile = (
            plan.eval_profile
            if effective_start_mode == "existing_eval" and plan.eval_profile is not None
            else plan.funded_profile
        )
        effective_balance = active_profile.starting_balance + current_profit
        effective_floor = effective_balance - current_cushion
        rows.append(
            {
                "account": plan.account_name,
                "stage": "Existing eval" if active_profile is plan.eval_profile else "Funded / PA",
                "current_profit": money(current_profit),
                "current_cushion": money(current_cushion),
                "effective_balance": money(effective_balance),
                "effective_floor": money(effective_floor),
            }
        )
    return rows


def coerce_uploaded_ledger(
    frame: pd.DataFrame,
    *,
    strategy_id: str,
    default_dpp: float,
    fallback_minutes: int,
) -> pd.DataFrame:
    columns = {normalize_column_name(str(column)): column for column in frame.columns}
    entry_col = first_present(columns, ENTRY_COLUMNS)
    if entry_col is None:
        raise ValueError(f"no entry timestamp column found; tried {', '.join(ENTRY_COLUMNS)}")
    exit_col = first_present(columns, EXIT_COLUMNS)
    pnl_points_col = first_present(columns, PNL_POINT_COLUMNS)
    pnl_dollars_col = first_present(columns, PNL_DOLLAR_COLUMNS)
    if pnl_points_col is None and pnl_dollars_col is None:
        raise ValueError("no PnL column found; provide raw point PnL or dollar PnL")

    out = pd.DataFrame(index=frame.index)
    strategy_col = columns.get("strategy_id") or columns.get("strategy")
    out["strategy_id"] = frame[strategy_col].astype(str) if strategy_col is not None else strategy_id
    out["instrument"] = frame[columns["instrument"]].astype(str) if "instrument" in columns else "NQ"
    out["contract_symbol"] = (
        frame[columns["contract_symbol"]].astype(str) if "contract_symbol" in columns else "MNQ"
    )
    out["entry_time"] = frame[entry_col]
    if exit_col is not None:
        out["exit_time"] = frame[exit_col]
    else:
        parsed = pd.to_datetime(frame[entry_col], errors="coerce")
        out["exit_time"] = parsed + pd.to_timedelta(fallback_minutes, unit="m")
    if pnl_points_col is not None:
        out["pnl_points"] = frame[pnl_points_col]
        out["dollars_per_point"] = default_dpp
    else:
        out["pnl_dollars"] = frame[pnl_dollars_col]
    for target, candidates in (
        ("mae_points", MAE_COLUMNS),
        ("mfe_points", MFE_COLUMNS),
        ("stop_points", STOP_COLUMNS),
    ):
        source = first_present(columns, candidates)
        if source is not None:
            out[target] = frame[source]
    source_id_col = columns.get("source_row_id") or columns.get("trade_id") or columns.get("id")
    if source_id_col is not None:
        out["source_row_id"] = strategy_id + ":" + frame[source_id_col].astype(str)
    else:
        out["source_row_id"] = [f"{strategy_id}:{index + 2}" for index in range(len(frame))]
    out["trade_id"] = out["source_row_id"]
    return out


def read_uploaded_ledger(file: Any) -> pd.DataFrame:
    suffix = Path(file.name).suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(file)
    if suffix in {".html", ".htm"}:
        content = file.getvalue()
        text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
        tables = HTMLTableExtractor().parse(text)
        candidates = [table_to_frame(table) for table in tables if len(table) >= 2]
        candidates = [frame for frame in candidates if not frame.empty]
        if not candidates:
            raise ValueError("no HTML tables found")
        candidates.sort(key=ledger_table_score, reverse=True)
        best = candidates[0]
        if ledger_table_score(best) <= 0:
            raise ValueError("no ledger-like HTML table found with entry and PnL columns")
        return best
    raise ValueError(f"unsupported file type: {suffix or 'unknown'}")


def table_to_frame(rows: list[list[str]]) -> pd.DataFrame:
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    header = padded[0]
    body = padded[1:]
    return pd.DataFrame(body, columns=dedupe_columns(header))


def dedupe_columns(columns: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out: list[str] = []
    for index, column in enumerate(columns):
        label = str(column).strip() or f"column_{index + 1}"
        count = seen.get(label, 0)
        seen[label] = count + 1
        out.append(label if count == 0 else f"{label}_{count + 1}")
    return out


def ledger_table_score(frame: pd.DataFrame) -> int:
    columns = {normalize_column_name(str(column)) for column in frame.columns}
    score = 0
    if columns & set(ENTRY_COLUMNS):
        score += 4
    if columns & set(EXIT_COLUMNS):
        score += 2
    if columns & set(PNL_POINT_COLUMNS):
        score += 4
    if columns & set(PNL_DOLLAR_COLUMNS):
        score += 3
    if columns & set(MAE_COLUMNS):
        score += 1
    if columns & set(MFE_COLUMNS):
        score += 1
    score += min(len(frame) // 25, 4)
    return score


def normalize_column_name(column: str) -> str:
    normalized = column.strip().lower()
    for old, new in (("$", "dollars"), ("%", "pct"), ("/", "_"), ("-", "_"), (" ", "_")):
        normalized = normalized.replace(old, new)
    return "_".join(part for part in normalized.split("_") if part)


class HTMLTableExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table_depth = 0
        self._rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def parse(self, text: str) -> list[list[list[str]]]:
        self.feed(text)
        return self.tables

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            if self._table_depth == 0:
                self._rows = []
            self._table_depth += 1
        elif tag == "tr" and self._table_depth:
            self._row = []
        elif tag in {"th", "td"} and self._table_depth and self._row is not None:
            self._cell_parts = []

    def handle_data(self, data: str) -> None:
        if self._cell_parts is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"th", "td"} and self._cell_parts is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell_parts).split()))
            self._cell_parts = None
        elif tag == "tr" and self._row is not None:
            if any(cell != "" for cell in self._row):
                self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._table_depth:
            self._table_depth -= 1
            if self._table_depth == 0 and self._rows:
                self.tables.append(self._rows)
                self._rows = []


def first_present(columns: dict[str, Any], candidates: tuple[str, ...]) -> Any | None:
    for candidate in candidates:
        if candidate in columns:
            return columns[candidate]
    return None


def show_rule_profiles(profiles: dict[str, Any]) -> None:
    st.subheader("Rule Profiles")
    rows = [profile.to_dict() for profile in profiles.values()]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


def default_profile_selection(profiles: dict[str, Any]) -> list[str]:
    if DEFAULT_PROFILE_KEY in profiles:
        return [DEFAULT_PROFILE_KEY]
    for key in profiles:
        if key.startswith("Apex Trader Funding"):
            return [key]
    if profiles:
        return [next(iter(profiles))]
    return []


def format_ranking(frame: pd.DataFrame) -> pd.DataFrame:
    formatted = frame.copy()
    for column in ("fail_rate", "eligible_rate"):
        formatted[column] = (formatted[column] * 100).round(1).astype(str) + "%"
    for column in (
        "p05_cash",
        "p50_cash",
        "p95_cash",
        "mean_cash",
        "p05_net_profit",
        "p50_net_profit",
        "p95_net_profit",
    ):
        formatted[column] = formatted[column].map(money)
    formatted["convexity_score"] = formatted["convexity_score"].round(3)
    return formatted


def format_lifecycle_ranking(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "firm",
        "account",
        "contracts",
        "paths",
        "paid_before_first_blow_paths",
        "paid_before_first_blow_rate",
        "blew_before_payout_rate",
        "payout_after_rebuy_rate",
        "no_resolution_rate",
        "any_payout_rate",
        "capital_exhausted_rate",
        "p50_month_to_first_payout",
        "p50_days_to_first_payout",
        "mean_net_cash",
        "p50_net_cash",
        "p95_net_cash",
        "p05_net_cash",
        "mean_fees",
        "avg_attempts",
        "p95_max_drawdown",
        "survival_score",
        "ev_score",
        "speed_score",
        "convexity_score",
        "display_composite_score",
        "status",
    ]
    formatted = frame[[column for column in columns if column in frame.columns]].copy()
    for column in (
        "paid_before_first_blow_rate",
        "blew_before_payout_rate",
        "payout_after_rebuy_rate",
        "no_resolution_rate",
        "any_payout_rate",
        "capital_exhausted_rate",
    ):
        if column in formatted:
            formatted[column] = formatted[column].map(pct)
    for column in (
        "mean_net_cash",
        "p50_net_cash",
        "p95_net_cash",
        "p05_net_cash",
        "mean_fees",
        "p95_max_drawdown",
    ):
        if column in formatted:
            formatted[column] = formatted[column].map(money)
    if "p50_month_to_first_payout" in formatted:
        formatted["p50_month_to_first_payout"] = formatted["p50_month_to_first_payout"].map(month_or_dash)
    if "avg_attempts" in formatted:
        formatted["avg_attempts"] = formatted["avg_attempts"].round(2)
    for column in ("survival_score", "ev_score", "speed_score", "convexity_score", "display_composite_score"):
        if column in formatted:
            formatted[column] = formatted[column].round(1)
    return formatted


def format_guidance_ranking(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "contracts",
        "paid_before_first_blow_paths",
        "paid_before_first_blow_rate",
        "blew_before_payout_rate",
        "payout_after_rebuy_rate",
        "avg_net_cash",
        "p50_net_cash",
        "avg_fees",
        "p50_month_to_first_payout",
        "display_composite_score",
        "status",
    ]
    formatted = frame[[column for column in columns if column in frame.columns]].copy()
    formatted = formatted.rename(
        columns={
            "contracts": "micros",
            "paid_before_first_blow_paths": "paid paths",
            "paid_before_first_blow_rate": "paid before blow",
            "blew_before_payout_rate": "blew before payout",
            "payout_after_rebuy_rate": "paid after rebuy",
            "avg_net_cash": "avg net",
            "p50_net_cash": "median realized",
            "avg_fees": "avg fees",
            "p50_month_to_first_payout": "median first payout",
            "display_composite_score": "composite",
        }
    )
    for column in ("paid before blow", "blew before payout", "paid after rebuy"):
        if column in formatted:
            formatted[column] = formatted[column].map(pct)
    for column in ("avg net", "median realized", "avg fees"):
        if column in formatted:
            formatted[column] = formatted[column].map(money)
    if "median first payout" in formatted:
        formatted["median first payout"] = formatted["median first payout"].map(month_or_dash)
    if "composite" in formatted:
        formatted["composite"] = formatted["composite"].round(1)
    return formatted


def format_first_payout_comparison(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "firm",
        "account",
        "contracts",
        "paid_before_first_blow_rate",
        "blew_before_payout_rate",
        "payout_after_rebuy_rate",
        "p50_month_to_first_payout",
        "p50_days_to_first_payout",
        "survival_score",
        "speed_score",
        "display_composite_score",
        "status",
    ]
    formatted = frame[[column for column in columns if column in frame.columns]].copy()
    formatted = formatted.rename(
        columns={
            "contracts": "micros",
            "paid_before_first_blow_rate": "paid before blow",
            "blew_before_payout_rate": "blew before payout",
            "payout_after_rebuy_rate": "paid after rebuy",
            "p50_month_to_first_payout": "median first payout",
            "p50_days_to_first_payout": "median days",
            "display_composite_score": "composite",
        }
    )
    for column in ("paid before blow", "blew before payout", "paid after rebuy"):
        if column in formatted:
            formatted[column] = formatted[column].map(pct)
    if "median first payout" in formatted:
        formatted["median first payout"] = formatted["median first payout"].map(month_or_dash)
    if "median days" in formatted:
        formatted["median days"] = formatted["median days"].map(days_or_dash)
    for column in ("survival_score", "speed_score", "composite"):
        if column in formatted:
            formatted[column] = formatted[column].round(1)
    return formatted


def format_prop_comparison(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "firm",
        "account",
        "contracts",
        "any_payout_rate",
        "paid_before_first_blow_rate",
        "blew_before_payout_rate",
        "avg_withdrawal",
        "p50_withdrawal",
        "p95_withdrawal",
        "avg_payout_count",
        "avg_net_cash",
        "avg_fees",
        "p50_month_to_first_payout",
        "survival_score",
        "ev_score",
        "speed_score",
        "convexity_score",
        "display_composite_score",
        "status",
    ]
    formatted = frame[[column for column in columns if column in frame.columns]].copy()
    formatted = formatted.rename(
        columns={
            "contracts": "micros",
            "any_payout_rate": "any payout",
            "paid_before_first_blow_rate": "paid before blow",
            "blew_before_payout_rate": "blew before payout",
            "avg_withdrawal": "avg withdrawal",
            "p50_withdrawal": "median withdrawal",
            "p95_withdrawal": "p95 withdrawal",
            "avg_payout_count": "avg payouts",
            "avg_net_cash": "avg net",
            "avg_fees": "avg fees",
            "p50_month_to_first_payout": "median first payout",
            "display_composite_score": "composite",
        }
    )
    for column in ("any payout", "paid before blow", "blew before payout"):
        if column in formatted:
            formatted[column] = formatted[column].map(pct)
    for column in ("avg withdrawal", "median withdrawal", "p95 withdrawal", "avg net", "avg fees"):
        if column in formatted:
            formatted[column] = formatted[column].map(money)
    if "median first payout" in formatted:
        formatted["median first payout"] = formatted["median first payout"].map(month_or_dash)
    for column in ("survival_score", "ev_score", "speed_score", "convexity_score", "composite"):
        if column in formatted:
            formatted[column] = formatted[column].round(1)
    if "avg payouts" in formatted:
        formatted["avg payouts"] = formatted["avg payouts"].round(1)
    return formatted


def format_prop_summary(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "firm",
        "account",
        "contracts",
        "any_payout_rate",
        "paid_before_first_blow_rate",
        "blew_before_payout_rate",
        "avg_withdrawal",
        "p50_withdrawal",
        "avg_payout_count",
        "avg_net_cash",
        "p50_month_to_first_payout",
        "display_composite_score",
        "status",
    ]
    formatted = frame[[column for column in columns if column in frame.columns]].copy()
    formatted = formatted.rename(
        columns={
            "contracts": "best micros",
            "any_payout_rate": "any payout",
            "paid_before_first_blow_rate": "paid before blow",
            "blew_before_payout_rate": "blew before payout",
            "avg_withdrawal": "avg withdrawal",
            "p50_withdrawal": "median withdrawal",
            "avg_payout_count": "avg payouts",
            "avg_net_cash": "avg net",
            "p50_month_to_first_payout": "median first payout",
            "display_composite_score": "composite",
        }
    )
    for column in ("any payout", "paid before blow", "blew before payout"):
        if column in formatted:
            formatted[column] = formatted[column].map(pct)
    for column in ("avg withdrawal", "median withdrawal", "avg net"):
        if column in formatted:
            formatted[column] = formatted[column].map(money)
    if "median first payout" in formatted:
        formatted["median first payout"] = formatted["median first payout"].map(month_or_dash)
    if "composite" in formatted:
        formatted["composite"] = formatted["composite"].round(1)
    if "avg payouts" in formatted:
        formatted["avg payouts"] = formatted["avg payouts"].round(1)
    return formatted


def format_monthly_summary(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "plan",
        "contracts",
        "month_index",
        "paths",
        "active_paths",
        "active_path_rate",
        "terminal_path_rate",
        "p05_active_pnl",
        "p50_active_pnl",
        "p95_active_pnl",
        "p05_pnl",
        "p50_pnl",
        "p95_pnl",
        "p50_net_cash",
        "p95_drawdown",
        "fail_month_rate",
        "payout_month_rate",
    ]
    formatted = frame[[column for column in columns if column in frame.columns]].copy()
    formatted = formatted.rename(columns={"p50_net_cash": "p50_realized_cash"})
    for column in (
        "p05_active_pnl",
        "p50_active_pnl",
        "p95_active_pnl",
        "p05_pnl",
        "p50_pnl",
        "p95_pnl",
        "p50_realized_cash",
        "p95_drawdown",
    ):
        if column in formatted:
            formatted[column] = formatted[column].map(money)
    for column in ("active_path_rate", "terminal_path_rate", "fail_month_rate", "payout_month_rate"):
        if column in formatted:
            formatted[column] = formatted[column].map(pct)
    return formatted


def build_rule_audit_rows(selected_plans: list[Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for plan in selected_plans:
        profile = plan.funded_profile
        rows.append(
            {
                "firm": plan.firm,
                "account": plan.account_name,
                "route": plan_route_label(plan),
                "split": pct(float(profile.profit_split)),
                "min payout": money(float(profile.min_payout)),
                "first cap": money(float(profile.max_payout)) if profile.max_payout is not None else "uncapped",
                "cap ladder": ", ".join(money(float(value)) for value in profile.payout_cap_schedule) or "-",
                "payout cap count": profile.payout_count_cap or "-",
                "reserve/buffer": money(float(max(profile.payout_reserve, profile.withdrawal_buffer))),
                "min winning days": profile.min_winning_days,
                "winning day threshold": money(float(profile.winning_day_threshold)),
                "consistency": pct(float(profile.consistency_pct)) if profile.consistency_pct is not None else "-",
                "cadence": profile.payout_cadence or "-",
            }
        )
    return pd.DataFrame(rows)


def build_monthly_heatmap(frame: pd.DataFrame, value_label: str = "Median monthly PnL"):
    value_map = {
        "Active median PnL": ("p50_active_pnl", money),
        "Median monthly PnL": ("p50_pnl", money),
        "Median realized cash": ("p50_net_cash", money),
        "Active path rate": ("active_path_rate", pct),
        "Terminal path rate": ("terminal_path_rate", pct),
        "Payout month rate": ("payout_month_rate", pct),
        "Blow month rate": ("fail_month_rate", pct),
        "P95 drawdown": ("p95_drawdown", money),
    }
    value_column, formatter = value_map.get(value_label, value_map["Median monthly PnL"])
    heatmap = frame.pivot_table(
        index=["plan", "contracts"],
        columns="month_index",
        values=value_column,
        aggfunc="first",
    ).rename(columns=lambda month: f"M{int(month)}")
    return heatmap.map(formatter)


def build_monthly_heatmap_styler(frame: pd.DataFrame, value_label: str = "Active median PnL"):
    value_map = {
        "Active median PnL": ("p50_active_pnl", money, "pnl"),
        "Median monthly PnL": ("p50_pnl", money, "pnl"),
        "Median realized cash": ("p50_net_cash", money, "positive"),
        "Active path rate": ("active_path_rate", pct, "positive"),
        "Terminal path rate": ("terminal_path_rate", pct, "negative"),
        "Payout month rate": ("payout_month_rate", pct, "positive"),
        "Blow month rate": ("fail_month_rate", pct, "negative"),
        "P95 drawdown": ("p95_drawdown", money, "negative"),
    }
    value_column, formatter, palette = value_map.get(value_label, value_map["Active median PnL"])
    values = monthly_heatmap_values(frame, value_column)
    return style_heatmap(values, formatter, palette)


def monthly_heatmap_values(frame: pd.DataFrame, value_column: str) -> pd.DataFrame:
    if frame.empty or value_column not in frame:
        return pd.DataFrame()
    return (
        frame.pivot_table(
            index=["plan", "contracts"],
            columns="month_index",
            values=value_column,
            aggfunc="first",
        )
        .rename(columns=lambda month: f"M{int(month)}")
        .astype(float)
    )


def style_heatmap(values: pd.DataFrame, formatter: Any, palette: str):
    if values.empty:
        return values
    finite = values.stack().dropna()
    max_abs = float(finite.abs().max()) if not finite.empty else 0.0
    min_value = float(finite.min()) if not finite.empty else 0.0
    max_value = float(finite.max()) if not finite.empty else 0.0

    def cell_style(value: float) -> str:
        if pd.isna(value):
            return "background-color: #11161a; color: #6f7f78;"
        if palette == "pnl":
            intensity = min(1.0, abs(float(value)) / max(1.0, max_abs))
            color = "0, 185, 105" if value >= 0 else "220, 55, 65"
        elif palette == "negative":
            intensity = (float(value) - min_value) / max(1e-9, max_value - min_value)
            color = "220, 55, 65"
        else:
            intensity = (float(value) - min_value) / max(1e-9, max_value - min_value)
            color = "0, 185, 105"
        alpha = 0.16 + 0.60 * max(0.0, min(1.0, intensity))
        return f"background-color: rgba({color}, {alpha:.3f}); color: #f2fff7;"

    return values.style.format(formatter).map(cell_style)


def format_path_frame(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "month_index",
        "stage",
        "attempt",
        "starting_balance",
        "ending_balance",
        "pnl",
        "max_drawdown",
        "floor",
        "payouts",
        "fees",
        "cumulative_payouts",
        "cumulative_fees",
        "net_cash",
        "status",
    ]
    formatted = frame[[column for column in columns if column in frame.columns]].copy()
    for column in (
        "starting_balance",
        "ending_balance",
        "pnl",
        "max_drawdown",
        "floor",
        "payouts",
        "fees",
        "cumulative_payouts",
        "cumulative_fees",
        "net_cash",
    ):
        if column in formatted:
            formatted[column] = formatted[column].map(money)
    return formatted


def format_events(frame: pd.DataFrame) -> pd.DataFrame:
    formatted = frame.copy()
    for column in ("amount", "balance", "floor"):
        if column in formatted:
            formatted[column] = formatted[column].map(money)
    return formatted


def build_path_options(monthly: pd.DataFrame) -> list[tuple[int, str]]:
    summary = (
        monthly.groupby("path_id")
        .agg(
            plan=("plan_key", "first"),
            net_cash=("net_cash", "last"),
            payouts=("cumulative_payouts", "last"),
            fees=("cumulative_fees", "last"),
            failed=("status", lambda values: any("failed" in str(value) for value in values)),
        )
        .reset_index()
        .sort_values(["net_cash", "payouts"], ascending=[False, False])
    )
    return [
        (
            int(row.path_id),
            f"path {int(row.path_id)} | {row.plan} | net {money(float(row.net_cash))} | payouts {money(float(row.payouts))}",
        )
        for row in summary.itertuples()
    ]


def trade_span_label(trades: list[Any]) -> str:
    if not trades:
        return "-"
    start = min(trade.entry_time for trade in trades).date()
    end = max(trade.exit_time for trade in trades).date()
    return f"{start} to {end}"


def account_size_label(account_size: float) -> str:
    return f"{int(account_size / 1000)}K" if account_size >= 1000 else str(int(account_size))


def account_size_sort_key(label: str) -> int:
    return int(label.rstrip("K")) if label.endswith("K") and label.rstrip("K").isdigit() else 0


def plan_route_label(plan: Any) -> str:
    return "Eval to funded" if plan.eval_profile is not None else "Funded only"


def money(value: float) -> str:
    if pd.isna(value):
        return "-"
    return f"${value:,.0f}"


def pct(value: float) -> str:
    if pd.isna(value):
        return "-"
    return f"{value * 100:.1f}%"


def month_or_dash(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"M{int(round(float(value)))}"


def days_or_dash(value: Any) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{int(round(float(value)))}d"


def apply_matrix_theme() -> None:
    st.markdown(
        """
        <style>
        :root {
            --matrix-bg: #020604;
            --matrix-panel: #050b08;
            --matrix-soft: #07100b;
            --matrix-line: #0f3d25;
            --matrix-green: #00ff88;
            --matrix-green-dim: #18a85d;
            --matrix-text: #d9ffe8;
        }
        .stApp {
            background: var(--matrix-bg);
            color: var(--matrix-text);
        }
        [data-testid="stSidebar"] {
            background: var(--matrix-soft);
            border-right: 1px solid var(--matrix-line);
        }
        h1, h2, h3, .stMarkdown, label {
            color: var(--matrix-text) !important;
        }
        div[data-testid="stMetric"] {
            background: var(--matrix-panel);
            border: 1px solid var(--matrix-line);
            border-radius: 6px;
            padding: 10px 12px;
        }
        div[data-testid="stMetricValue"] {
            color: var(--matrix-green);
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        }
        .stButton > button, .stButton > button[kind="primary"] {
            background: var(--matrix-green-dim) !important;
            color: #001b0d !important;
            border: 1px solid var(--matrix-green) !important;
            font-weight: 800;
        }
        .stButton > button:hover, .stButton > button[kind="primary"]:hover {
            background: var(--matrix-green) !important;
            color: #001b0d !important;
            border-color: var(--matrix-green) !important;
        }
        div[data-baseweb="select"] > div,
        input,
        textarea,
        [data-testid="stNumberInput"] input {
            background-color: #030806 !important;
            border-color: var(--matrix-line) !important;
            color: var(--matrix-text) !important;
        }
        [data-testid="stSlider"] div[role="slider"] {
            background-color: var(--matrix-green) !important;
        }
        [data-testid="stSlider"] div[data-testid="stTickBar"] {
            background-color: var(--matrix-line) !important;
        }
        .stDataFrame, [data-testid="stDataFrame"] {
            border: 1px solid var(--matrix-line);
            border-radius: 6px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
