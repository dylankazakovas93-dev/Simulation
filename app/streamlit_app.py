from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

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


def main() -> None:
    st.set_page_config(page_title="Prop Convexity Lab", layout="wide")
    apply_matrix_theme()
    st.title("Prop Convexity Lab")

    profiles = default_prop_rule_profiles()
    lifecycle_plans = default_lifecycle_plans()
    with st.sidebar:
        st.header("Inputs")
        uploaded = st.file_uploader(
            "12-month ledgers",
            type=["csv", "html", "htm"],
            accept_multiple_files=True,
        )
        source_timezone = st.text_input("Source timezone for naive timestamps", value="UTC")
        default_dpp = st.number_input("Dollars per point per micro", min_value=0.01, value=2.0, step=0.5)
        fallback_minutes = st.number_input("Fallback trade duration if exit is missing", 1, 1440, 60)
        resolve_conflicts = st.checkbox("Drop overlapping trades by priority", value=True)

        st.header("Simulation")
        firm_options = sorted({plan.firm for plan in lifecycle_plans.values()})
        selected_firms = st.multiselect(
            "Firms",
            firm_options,
            default=["Apex Trader Funding"] if "Apex Trader Funding" in firm_options else firm_options[:1],
        )
        plan_options = [
            key for key, plan in lifecycle_plans.items()
            if plan.firm in set(selected_firms)
        ]
        default_plan = next(
            (key for key in plan_options if key.startswith("Apex Trader Funding - EOD 50K")),
            plan_options[0] if plan_options else None,
        )
        selected_plan_keys = st.multiselect(
            "Lifecycle accounts",
            plan_options,
            default=[default_plan] if default_plan is not None else [],
        )
        min_contracts, max_contracts = st.slider("Micro contracts", 1, 50, (1, 8))
        paths = st.slider("Bootstrap paths", 5, 500, 100, step=5)
        horizon_months = st.slider("Horizon months", 1, 24, 12)
        seed = st.number_input("Seed", value=12345, step=1)

        st.header("Current State")
        start_mode_label = st.selectbox(
            "Starting point",
            ["New eval", "Existing eval", "Funded / PA"],
            index=0,
        )
        start_mode = {
            "New eval": "new_eval",
            "Existing eval": "existing_eval",
            "Funded / PA": "funded",
        }[start_mode_label]
        current_balance = st.number_input(
            "Current balance, 0 = account start",
            min_value=0.0,
            value=0.0,
            step=100.0,
        )
        current_floor = st.number_input(
            "Current drawdown floor, 0 = default",
            min_value=0.0,
            value=0.0,
            step=100.0,
        )
        current_winning_days = st.number_input("Funded qualifying days already", 0, 30, 0)
        current_high_day = st.number_input("Highest winning day since payout", min_value=0.0, value=0.0, step=50.0)

        st.header("Target")
        desired_payout = st.number_input("Desired payout, 0 = take max allowed", min_value=0.0, value=0.0, step=100.0)
        required_cushion = st.number_input("Required cushion after payout", min_value=0.0, value=0.0, step=100.0)
        max_rebuy_capital = st.number_input("Max fee capital / rebuys", min_value=0.0, value=1000.0, step=50.0)
        allow_rebuys = st.checkbox("Allow eval rebuys after fail", value=True)

        selected_plans = [lifecycle_plans[key] for key in selected_plan_keys]
        firm_costs: dict[str, dict[str, float]] = {}
        with st.expander("Fees by firm", expanded=True):
            for firm_name in sorted({plan.firm for plan in selected_plans}):
                st.caption(firm_name)
                col_a, col_b, col_c = st.columns(3)
                firm_plans = [plan for plan in selected_plans if plan.firm == firm_name]
                default_eval = max((plan.default_eval_fee for plan in firm_plans), default=0.0)
                default_activation = max((plan.default_activation_fee for plan in firm_plans), default=0.0)
                default_reset = max((plan.default_reset_fee for plan in firm_plans), default=0.0)
                firm_costs[firm_name] = {
                    "eval_fee": col_a.number_input(
                        "Eval",
                        min_value=0.0,
                        value=float(default_eval),
                        step=10.0,
                        key=f"{firm_name}_eval_fee",
                    ),
                    "activation_fee": col_b.number_input(
                        "Activation",
                        min_value=0.0,
                        value=float(default_activation),
                        step=10.0,
                        key=f"{firm_name}_activation_fee",
                    ),
                    "reset_fee": col_c.number_input(
                        "Reset",
                        min_value=0.0,
                        value=float(default_reset),
                        step=10.0,
                        key=f"{firm_name}_reset_fee",
                    ),
                }
        run_simulation = st.button("Run simulation", type="primary", use_container_width=True)

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

    tabs = st.tabs(["Rankings", "Monthly", "Paths", "Events", "Ledger", "Conflicts", "Rules"])
    with tabs[0]:
        if not selected_plans:
            st.warning("Select at least one lifecycle account.")
        elif not run_simulation:
            st.info("Adjust inputs, then click Run simulation.")
        else:
            contract_values = list(range(int(min_contracts), int(max_contracts) + 1))
            with st.spinner("Running account/contract grid..."):
                settings_by_plan = {}
                for plan in selected_plans:
                    costs = firm_costs.get(plan.firm, {})
                    effective_start_mode = start_mode if plan.eval_profile is not None else "funded"
                    settings_by_plan[plan.key] = LifecycleSettings(
                        start_mode=effective_start_mode,
                        current_balance=float(current_balance) or None,
                        current_floor=float(current_floor) or None,
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
                ranking, monthly, events = run_lifecycle_grid(
                    usable_trades,
                    selected_plans,
                    contract_values=contract_values,
                    paths=int(paths),
                    horizon_months=int(horizon_months),
                    seed=int(seed),
                    dollars_per_point=float(default_dpp),
                    settings_by_plan=settings_by_plan,
                )
                st.session_state["lifecycle_ranking"] = ranking
                st.session_state["lifecycle_monthly"] = monthly
                st.session_state["lifecycle_monthly_summary"] = summarize_monthly_paths(monthly)
                st.session_state["lifecycle_events"] = events
            st.dataframe(format_lifecycle_ranking(ranking), width="stretch", hide_index=True)

            best = ranking.iloc[0]
            cols = st.columns(6)
            cols[0].metric("Best plan", str(best["account"]))
            cols[1].metric("Best contracts", int(best["contracts"]))
            cols[2].metric("P50 net cash", money(float(best["p50_net_cash"])))
            cols[3].metric("Target hit", pct(float(best["target_rate"])))
            cols[4].metric("Fail rate", pct(float(best["fail_rate"])))
            cols[5].metric("P50 first payout", month_or_dash(best["p50_month_to_first_payout"]))

    with tabs[1]:
        st.subheader("Month-by-month Path Distributions")
        monthly_summary = st.session_state.get("lifecycle_monthly_summary", pd.DataFrame())
        if monthly_summary.empty:
            st.info("Run a lifecycle simulation to see monthly PnL, drawdown, fail, and payout rates.")
        else:
            st.dataframe(format_monthly_summary(monthly_summary), width="stretch", hide_index=True)

    with tabs[2]:
        st.subheader("Real Sequence Examples")
        monthly = st.session_state.get("lifecycle_monthly", pd.DataFrame())
        if monthly.empty:
            st.info("Run a lifecycle simulation to inspect actual path sequences.")
        else:
            path_options = build_path_options(monthly)
            selected_path = st.selectbox("Path", path_options, format_func=lambda item: item[1])
            path_frame = monthly[monthly["path_id"] == selected_path[0]].copy()
            st.dataframe(format_path_frame(path_frame), width="stretch", hide_index=True)

    with tabs[3]:
        st.subheader("Lifecycle Events")
        events = st.session_state.get("lifecycle_events", pd.DataFrame())
        if events.empty:
            st.info("Run a lifecycle simulation to see eval passes, failures, fees, activations, and payouts.")
        else:
            st.dataframe(format_events(events), width="stretch", hide_index=True)

    with tabs[4]:
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
            width="stretch",
            hide_index=True,
        )

    with tabs[5]:
        st.subheader("Overlap Decisions")
        if not decisions:
            st.write("Conflict filtering is off or no overlaps were found.")
        else:
            st.dataframe(
                pd.DataFrame([decision.to_dict() for decision in decisions]),
                width="stretch",
                hide_index=True,
            )

    with tabs[6]:
        selected_funded_keys = [plan.funded_profile.key for plan in selected_plans]
        show_rule_profiles({key: profiles[key] for key in selected_funded_keys if key in profiles} or profiles)


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
        "fail_rate",
        "payout_rate",
        "target_rate",
        "p50_month_to_first_payout",
        "p50_net_cash",
        "p95_net_cash",
        "p05_net_cash",
        "mean_fees",
        "avg_attempts",
        "p95_max_drawdown",
        "convexity_score",
    ]
    formatted = frame[[column for column in columns if column in frame.columns]].copy()
    for column in ("fail_rate", "payout_rate", "target_rate"):
        if column in formatted:
            formatted[column] = formatted[column].map(pct)
    for column in ("p50_net_cash", "p95_net_cash", "p05_net_cash", "mean_fees", "p95_max_drawdown"):
        if column in formatted:
            formatted[column] = formatted[column].map(money)
    if "p50_month_to_first_payout" in formatted:
        formatted["p50_month_to_first_payout"] = formatted["p50_month_to_first_payout"].map(month_or_dash)
    if "avg_attempts" in formatted:
        formatted["avg_attempts"] = formatted["avg_attempts"].round(2)
    if "convexity_score" in formatted:
        formatted["convexity_score"] = formatted["convexity_score"].round(3)
    return formatted


def format_monthly_summary(frame: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "plan",
        "contracts",
        "month_index",
        "paths",
        "p05_pnl",
        "p50_pnl",
        "p95_pnl",
        "p50_net_cash",
        "p95_drawdown",
        "fail_month_rate",
        "payout_month_rate",
    ]
    formatted = frame[[column for column in columns if column in frame.columns]].copy()
    for column in ("p05_pnl", "p50_pnl", "p95_pnl", "p50_net_cash", "p95_drawdown"):
        if column in formatted:
            formatted[column] = formatted[column].map(money)
    for column in ("fail_month_rate", "payout_month_rate"):
        if column in formatted:
            formatted[column] = formatted[column].map(pct)
    return formatted


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


def apply_matrix_theme() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            background: #020604;
            color: #d7ffe4;
        }
        [data-testid="stSidebar"] {
            background: #07100b;
            border-right: 1px solid #123b26;
        }
        h1, h2, h3, .stMarkdown, label {
            color: #dcffe8 !important;
        }
        div[data-testid="stMetric"] {
            background: #050b08;
            border: 1px solid #174c30;
            border-radius: 6px;
            padding: 10px 12px;
        }
        div[data-testid="stMetricValue"] {
            color: #37ff8b;
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        }
        .stButton > button {
            background: #00b25c;
            color: #001b0d;
            border: 1px solid #37ff8b;
            font-weight: 800;
        }
        .stDataFrame, [data-testid="stDataFrame"] {
            border: 1px solid #123b26;
            border-radius: 6px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
