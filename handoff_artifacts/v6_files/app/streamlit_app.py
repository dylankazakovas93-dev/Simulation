"""V6 — Streamlit view (thin). All computation is delegated to ``app.controller``.

Run with:  streamlit run app/streamlit_app.py

This file contains NO modelling logic. Its only jobs are (1) collect inputs,
(2) call the controller, (3) render results, and (4) render the mandatory
model-risk disclosures the controller returns. Per the charter, assumptions and
limitations are shown WITH every number, not hidden.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app import controller
from app.disclosures import for_section
from sim_core.exposure import InstrumentMargin, MarginPolicy
from sim_core.ingestion.csv_loader import load_trade_csv
from sim_core.live_account import FixedContractSizing, StrategyAllocation
from sim_core.optimize import Candidate, Constraint, Objective
from sim_core.prop_firm import PropFirmRules


def _render_disclosures(items: list[str], *, title: str = "Model-risk disclosures") -> None:
    with st.expander(f"⚠️ {title} (read before trusting these numbers)", expanded=True):
        for note in items:
            st.markdown(f"- {note}")


def _load_trades(upload, source_timezone: str):
    if upload is None:
        return None
    return load_trade_csv(upload, source_timezone=source_timezone)


def main() -> None:
    st.set_page_config(page_title="Strategy Simulation Laboratory", layout="wide")
    st.title("Strategy & Prop-Firm Simulation Laboratory")
    st.caption(
        "A model-risk-first laboratory. Every output ships with its assumptions. "
        "The engine (sim_core) is fully separate from this UI."
    )

    with st.sidebar:
        st.header("1 · Upload trade log")
        upload = st.file_uploader("Trade CSV (per-contract P&L)", type=["csv"])
        source_tz = st.selectbox("Source timezone", ["UTC", "America/New_York", "Europe/London"])
        st.caption(
            "Contract mapping is declared, never inferred (ADR-011). A blank/contradictory "
            "dollars-per-point fails validation."
        )

    if upload is None:
        st.info("Upload a trade CSV to begin.")
        st.subheader("What this tool will and will not tell you")
        for section in ("ensemble", "drawdown", "prop_firm", "optimizer"):
            _render_disclosures(for_section(section), title=f"{section} caveats")
        return

    try:
        trades = _load_trades(upload, source_tz)
    except Exception as exc:  # validation is intentionally strict
        st.error(f"Validation failed (this is by design — the loader fails closed): {exc}")
        return

    st.success(f"Loaded {len(trades)} trades across {len({t.strategy_id for t in trades})} strategies.")

    tab_ens, tab_live, tab_prop, tab_opt = st.tabs(
        ["Ensemble (Monte Carlo)", "Live account", "Prop firm", "Optimizer"]
    )

    # --- Ensemble ---------------------------------------------------------------
    with tab_ens:
        st.subheader("Synchronized seasonal / block resampling")
        method = st.selectbox("Resampling method", controller.available_resampling_methods())
        months = st.number_input("Horizon (months)", 1, 60, 12)
        paths = st.number_input("Paths", 100, 20000, 2000, step=100)
        seed = st.number_input("Master seed", 0, 10_000_000, 12345)
        equity = st.number_input("Starting equity ($)", 1000.0, 1e7, 50_000.0, step=1000.0)
        ruin = st.number_input("Ruin threshold ($)", 0.0, 1e7, 0.0, step=1000.0)
        params: dict = {"months": int(months)}
        if method == "moving_block_bootstrap":
            params["block_length"] = st.number_input("Block length (months)", 1, 24, 3)
        elif method == "stationary_block_bootstrap":
            params["expected_block_length"] = st.number_input("Expected block length", 1.0, 24.0, 3.0)
        if st.button("Run ensemble"):
            out = controller.run_ensemble(
                trades,
                method=method,
                resampling_params=params,
                number_of_paths=int(paths),
                master_seed=int(seed),
                starting_equity=float(equity),
                ruin_threshold=float(ruin),
            )
            st.metric("Risk of ruin", f"{out['ruin_probability']:.2%}")
            if out["monthly_percentiles"]:
                st.line_chart(pd.DataFrame(out["monthly_percentiles"]).set_index("month"))
            st.write("Terminal equity distribution", out["terminal_equity_distribution"])
            for note in out["engine_warnings"] + out["coverage_warnings"]:
                st.warning(note)
            _render_disclosures(out["disclosures_ensemble"] + out["disclosures_drawdown"])
            st.caption(f"Input data hash: `{out['data_hash']}`")

    # --- Live account -----------------------------------------------------------
    with tab_live:
        st.subheader("Live brokerage account")
        equity_l = st.number_input("Starting equity ($)", 1000.0, 1e7, 50_000.0, key="live_eq")
        contracts = st.number_input("Contracts per trade (fixed)", 1, 100, 1)
        use_margin = st.checkbox("Apply a declared margin cap")
        margin_policy = None
        if use_margin:
            sym = st.text_input("Contract symbol", "MES")
            im = st.number_input("Initial margin ($)", 1.0, 1e6, 1320.0)
            mm = st.number_input("Maintenance margin ($)", 1.0, 1e6, 1200.0)
            margin_policy = MarginPolicy({sym: InstrumentMargin(sym, im, mm)})
        if st.button("Run live account"):
            allocations = {
                s: StrategyAllocation(s, FixedContractSizing(int(contracts)))
                for s in {t.strategy_id for t in trades}
            }
            out = controller.run_live_account(
                trades,
                starting_equity=float(equity_l),
                allocations=allocations,
                margin_policy=margin_policy,
            )
            st.write("Summary", out["summary"])
            if "exposure" in out:
                st.write("Exposure", out["exposure"])
                _render_disclosures(out["disclosures_margin_exposure"], title="margin/exposure caveats")
            _render_disclosures(out["disclosures_live_account"], title="live-account caveats")

    # --- Prop firm --------------------------------------------------------------
    with tab_prop:
        st.subheader("Prop / funded account")
        st.error(
            "The account balance below is NOTIONAL and is NOT your money. Only "
            "**net trader cash** (payouts × split − fees) is real."
        )
        acct = st.number_input("Account size ($)", 1000.0, 1e6, 50_000.0)
        target = st.number_input("Profit target ($)", 100.0, 1e6, 3_000.0)
        trail = st.number_input("Trailing drawdown ($)", 100.0, 1e6, 2_000.0)
        split = st.slider("Profit split (trader)", 0.5, 1.0, 0.9)
        eval_fee = st.number_input("Evaluation fee ($)", 0.0, 1e5, 150.0)
        act_fee = st.number_input("Activation fee ($)", 0.0, 1e5, 0.0)
        contracts_p = st.number_input("Contracts per trade", 1, 100, 1, key="prop_ct")
        if st.button("Run prop account"):
            rules = PropFirmRules(
                account_size=float(acct),
                profit_target=float(target),
                trailing_drawdown=float(trail),
                profit_split=float(split),
                evaluation_fee=float(eval_fee),
                activation_fee=float(act_fee),
                first_payout_threshold=float(target),
                contracts_per_trade=int(contracts_p),
            )
            out = controller.run_prop_single(trades, rules)
            st.metric("Net trader cash (real)", f"${out['headline_net_trader_cash']:,.2f}")
            st.caption(
                f"Notional terminal balance (NOT wealth): "
                f"${out['notional_terminal_balance_not_wealth']:,.2f}"
            )
            st.write("Summary", out["summary"])
            _render_disclosures(out["disclosures_prop_firm"], title="prop-firm caveats")

    # --- Optimizer --------------------------------------------------------------
    with tab_opt:
        st.subheader("Multi-objective optimizer (Pareto frontier)")
        st.info(
            "The optimizer selects a Pareto frontier over declared objectives + "
            "constraints. It refuses to optimize a single metric alone by default."
        )
        st.caption(
            "Wire candidate configurations (each already evaluated by the engines) "
            "into controller.run_optimizer(candidates, objectives, constraints)."
        )
        _render_disclosures(for_section("optimizer"), title="optimizer caveats")


if __name__ == "__main__":
    main()
