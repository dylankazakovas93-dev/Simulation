"""Compare prop-firm presets on a given trade ledger.

Runs, per preset: an evaluation stage from many random historical starts (pass rate,
time-to-pass) and a funded-stage random-start window analysis (blow rate, payout
probability, expected realized net cash). Combines them into decision-oriented rows.

Everything routes through `sim_core`; this module only orchestrates and tabulates.
The output is only as valid as the ledger fed in — with a synthetic ledger it shows
the MACHINERY and the firms' *relative* behavior under one assumed edge, NOT a
forecast. Feed a real ledger for personalized numbers.
"""
from __future__ import annotations

import random
from typing import Any, Sequence

import pandas as pd

from sim_core.models import Trade
from sim_core.prop_firm import (
    PHASE_EVALUATION,
    funded_window_analysis,
    run_prop_account_path,
    summarize_evaluation_stage,
)

from app.prop_presets import PropFirmPreset


def _ordered(trades: Sequence[Trade]) -> list[Trade]:
    return sorted(trades, key=lambda t: (t.exit_time, t.entry_time, t.trade_id))


def evaluation_stage_from_random_starts(
    trades: Sequence[Trade],
    rules,
    *,
    num_starts: int = 200,
    start_fraction: float = 0.7,
    seed: int = 0,
) -> dict[str, Any]:
    """Run an evaluation account from many random historical starts to end-of-data.

    Starts are drawn from the first ``start_fraction`` of the timeline so each eval
    has runway. Answers pass rate + time-to-pass without needing declared coverage.
    """

    ordered = _ordered(trades)
    if not ordered:
        return {"num_accounts": 0}
    cutoff_index = max(1, int(len(ordered) * start_fraction))
    candidate_starts = [t.entry_time for t in ordered[:cutoff_index]]
    rng = random.Random(seed)
    if num_starts < len(candidate_starts):
        starts = rng.sample(candidate_starts, num_starts)
    else:
        starts = candidate_starts
    results = []
    for start in starts:
        sub = [t for t in ordered if t.entry_time >= start]
        if sub:
            results.append(run_prop_account_path(sub, rules, verify_hash=False,
                                                 initial_phase=PHASE_EVALUATION))
    return summarize_evaluation_stage(results)


def compare_presets(
    trades: Sequence[Trade],
    presets: Sequence[PropFirmPreset],
    *,
    funded_horizon_months: int = 6,
    all_horizons: Sequence[int] = (2, 4, 6, 12),
    num_starts: int = 200,
    seed: int = 0,
) -> list[dict[str, Any]]:
    """One decision row per preset. `funded_horizon_months` is the headline horizon."""

    rows: list[dict[str, Any]] = []
    for preset in presets:
        ev = evaluation_stage_from_random_starts(
            trades, preset.rules, num_starts=num_starts, seed=seed
        )
        fw = funded_window_analysis(
            list(trades), preset.rules,
            horizons_months=tuple(sorted(set(all_horizons) | {funded_horizon_months})),
            num_starts=num_starts, seed=seed,
        )
        headline = fw["horizons"].get(str(funded_horizon_months), {})
        pass_rate = ev.get("pass_rate")
        blow_rate = headline.get("blow_rate")
        funded_net = headline.get("expected_net_trader_cash")
        # Expected net cash from a purchase = P(pass) * E[funded net over horizon]
        # minus cost to get funded. Rough, assumptions-heavy — shown with components.
        combined = None
        if pass_rate is not None and funded_net is not None:
            combined = pass_rate * funded_net - preset.cost_to_funded
        rows.append({
            "firm": preset.firm,
            "plan": preset.plan,
            "account_size": preset.account_size,
            "cost_to_funded": preset.cost_to_funded,
            "recurring_monthly": preset.recurring_monthly,
            "eval_pass_rate": pass_rate,
            "eval_median_days_to_pass": ev.get("median_days_to_pass"),
            f"funded_{funded_horizon_months}mo_blow_rate": blow_rate,
            f"funded_{funded_horizon_months}mo_prob_payout": headline.get("prob_payout"),
            f"funded_{funded_horizon_months}mo_E_net_cash": funded_net,
            "expected_net_after_cost": combined,
            "per_horizon": {h: fw["horizons"].get(str(h), {}) for h in all_horizons},
            "payout_cadence": preset.payout_cadence,
            "profit_split_note": preset.profit_split_note,
        })
    return rows


def comparison_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    drop = {"per_horizon"}
    return pd.DataFrame([{k: v for k, v in r.items() if k not in drop} for r in rows])
