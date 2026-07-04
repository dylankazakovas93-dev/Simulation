from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from sim_core.ingestion.csv_loader import normalize_trade_frame
from sim_core.models import TradeValidationError
from sim_core.prop_rules import (
    default_prop_rule_profiles,
    resolve_overlapping_trades,
    run_prop_ensemble,
    simulate_prop_account,
)


ENTRY_COLUMNS = ("entry_time", "entry_utc", "entry_ts", "entry", "touched_at", "open_time")
EXIT_COLUMNS = ("exit_time", "exit_utc", "exit_ts", "exit", "closed_at", "close_time")
PNL_POINT_COLUMNS = ("pnl_points", "pnl_pts", "points", "pnl", "net_pts", "pnl_raw")
PNL_DOLLAR_COLUMNS = ("pnl_dollars", "pnl_usd", "pnl_$", "net_dollars")
MAE_COLUMNS = ("mae_points", "mae_pts", "mae")
MFE_COLUMNS = ("mfe_points", "mfe_pts", "mfe")
STOP_COLUMNS = ("stop_points", "stop_pts", "sl_points", "sl_pts")
DEFAULT_PROFILE_KEY = "Apex Trader Funding - EOD PA 50K"


def main() -> None:
    st.set_page_config(page_title="Prop Convexity Lab", layout="wide")
    st.title("Prop Convexity Lab")

    profiles = default_prop_rule_profiles()
    with st.sidebar:
        st.header("Inputs")
        uploaded = st.file_uploader("12-month ledgers", type=["csv"], accept_multiple_files=True)
        source_timezone = st.text_input("Source timezone for naive timestamps", value="UTC")
        default_dpp = st.number_input("Dollars per point per micro", min_value=0.01, value=2.0, step=0.5)
        fallback_minutes = st.number_input("Fallback trade duration if exit is missing", 1, 1440, 60)
        resolve_conflicts = st.checkbox("Drop overlapping trades by priority", value=True)

        st.header("Simulation")
        selected_profiles = st.multiselect(
            "Account profiles",
            list(profiles),
            default=default_profile_selection(profiles),
        )
        min_contracts, max_contracts = st.slider("Micro contracts", 1, 50, (1, 8))
        paths = st.slider("Bootstrap paths", 25, 2000, 250, step=25)
        horizon_months = st.slider("Horizon months", 1, 24, 12)
        seed = st.number_input("Seed", value=12345, step=1)

    if not uploaded:
        st.info("Upload one or more CSV ledgers to begin.")
        show_rule_profiles(profiles)
        return

    loaded_frames: list[pd.DataFrame] = []
    errors: list[str] = []
    for file in uploaded:
        try:
            raw = pd.read_csv(file)
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
    priority_text = st.text_input("Priority order, highest first", value=", ".join(strategy_ids))
    priority = [item.strip() for item in priority_text.split(",") if item.strip()]
    if resolve_conflicts:
        usable_trades, decisions = resolve_overlapping_trades(trades, priority)
    else:
        usable_trades = trades
        decisions = []

    top = st.columns(5)
    top[0].metric("Uploaded trades", f"{len(trades):,}")
    top[1].metric("Used trades", f"{len(usable_trades):,}")
    top[2].metric("Dropped overlaps", f"{len([d for d in decisions if not d.kept]):,}")
    top[3].metric("Strategies", f"{len(strategy_ids):,}")
    top[4].metric("Span", trade_span_label(usable_trades))

    tabs = st.tabs(["Rankings", "Ledger", "Conflicts", "Rules"])
    with tabs[0]:
        if not selected_profiles:
            st.warning("Select at least one account profile.")
        else:
            contract_values = list(range(int(min_contracts), int(max_contracts) + 1))
            with st.spinner("Running account/contract grid..."):
                ranked = []
                for key in selected_profiles:
                    ranked.append(
                        run_prop_ensemble(
                            usable_trades,
                            profiles[key],
                            contract_values=contract_values,
                            paths=int(paths),
                            horizon_months=int(horizon_months),
                            seed=int(seed),
                            dollars_per_point=float(default_dpp),
                        )
                    )
                ranking = pd.concat(ranked, ignore_index=True).sort_values(
                    ["convexity_score", "p50_cash"],
                    ascending=[False, False],
                )
            st.dataframe(format_ranking(ranking), use_container_width=True, hide_index=True)

            best = ranking.iloc[0]
            profile = profiles[str(best["profile"])]
            deterministic = simulate_prop_account(
                usable_trades,
                profile,
                contracts=int(best["contracts"]),
                dollars_per_point=float(default_dpp),
            )
            cols = st.columns(6)
            cols[0].metric("Best profile", profile.account_name)
            cols[1].metric("Best contracts", int(best["contracts"]))
            cols[2].metric("Historical net", money(deterministic.net_profit))
            cols[3].metric("Historical cash", money(deterministic.payout_after_split))
            cols[4].metric("Historical fail", "yes" if deterministic.failed else "no")
            cols[5].metric("Eligible day", deterministic.first_eligible_day or "-")

    with tabs[1]:
        st.subheader("Normalized Ledger")
        st.dataframe(
            pd.DataFrame(
                {
                    "strategy": [trade.strategy_id for trade in usable_trades],
                    "entry": [trade.entry_time for trade in usable_trades],
                    "exit": [trade.exit_time for trade in usable_trades],
                    "pnl_points": [trade.pnl_points for trade in usable_trades],
                    "pnl_dollars_1x": [trade.pnl_dollars for trade in usable_trades],
                    "mae_points": [trade.mae_points for trade in usable_trades],
                    "mfe_points": [trade.mfe_points for trade in usable_trades],
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

    with tabs[2]:
        st.subheader("Overlap Decisions")
        if not decisions:
            st.write("Conflict filtering is off or no overlaps were found.")
        else:
            st.dataframe(
                pd.DataFrame([decision.to_dict() for decision in decisions]),
                use_container_width=True,
                hide_index=True,
            )

    with tabs[3]:
        show_rule_profiles({key: profiles[key] for key in selected_profiles} or profiles)


def coerce_uploaded_ledger(
    frame: pd.DataFrame,
    *,
    strategy_id: str,
    default_dpp: float,
    fallback_minutes: int,
) -> pd.DataFrame:
    columns = {str(column).strip().lower(): column for column in frame.columns}
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


def first_present(columns: dict[str, Any], candidates: tuple[str, ...]) -> Any | None:
    for candidate in candidates:
        if candidate in columns:
            return columns[candidate]
    return None


def show_rule_profiles(profiles: dict[str, Any]) -> None:
    st.subheader("Rule Profiles")
    rows = [profile.to_dict() for profile in profiles.values()]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


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


def trade_span_label(trades: list[Any]) -> str:
    if not trades:
        return "-"
    start = min(trade.entry_time for trade in trades).date()
    end = max(trade.exit_time for trade in trades).date()
    return f"{start} to {end}"


def money(value: float) -> str:
    return f"${value:,.0f}"


if __name__ == "__main__":
    main()
