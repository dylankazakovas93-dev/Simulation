from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from sim_core.lifecycle import (
    LifecyclePlan,
    LifecycleSettings,
    simulate_lifecycle_path,
    summarize_lifecycle_results,
)
from sim_core.models import Trade
from sim_core.prop_rules import _floor_ceiling, _gross_cash_available, _is_payout_eligible

RRConfig = Literal["1rr", "1_5rr"]
PrefixApplicationBasis = Literal["ACCOUNT_STATE_BEFORE_PREFIX", "ACCOUNT_STATE_AFTER_PREFIX"]

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "forward_master_path"
DEFAULT_EXPORT_DIR = REPO_ROOT / "artifacts" / "forward_master_path"
REQUIRED_PREFIX_COLUMNS = {
    "master_path_version",
    "master_path_id",
    "rr_config_id",
    "config",
    "config_label",
    "sequence_number",
    "event_group_id",
    "configuration_alternative_group_id",
    "status",
    "record_type",
    "session_date",
    "entry_time",
    "exit_time",
    "direction",
    "exit_reason",
    "effective_exit_reason",
    "pnl_points",
    "raw_stop_points",
    "effective_stop_points",
    "target_points",
    "mae_points",
    "mfe_points",
    "source_trade_packet_id",
    "source_type",
    "evidence_status",
    "mutually_exclusive_config_alternative",
}


@dataclass(frozen=True)
class ForwardScenario:
    rr_config_id: RRConfig = "1rr"
    july_candidate_count: int = 8
    august_candidate_count: int = 12
    master_seed: int = 1729
    mc_seed: int = 1730
    path_count: int = 100
    pf_scenario: str = "PF_1_35"
    regime_scenario: str = "stable"
    point_scale_scenario: str = "current"
    prefix_application_basis: PrefixApplicationBasis = "ACCOUNT_STATE_BEFORE_PREFIX"
    use_realized_master_prefix: bool = True
    use_legacy_anchor: bool = False
    rolling_gating_enabled: bool = False
    current_balance: float | None = None
    current_floor: float | None = None
    current_winning_days: int = 0
    current_highest_winning_day: float = 0.0
    current_daily_profits: tuple[float, ...] = ()
    payouts_already_taken: int = 0
    prior_fees: float = 0.0


def load_realized_master_path(path: str | Path | None = None) -> pd.DataFrame:
    frame = pd.read_csv(path or DEFAULT_DATA_DIR / "realized_master_path.csv")
    validate_realized_master_path(frame)
    return frame


def validate_realized_master_path(frame: pd.DataFrame) -> None:
    missing = sorted(REQUIRED_PREFIX_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"realized master path missing required columns: {missing}")
    if len(frame) != 4:
        raise ValueError(f"realized master path must contain exactly four comparison rows, got {len(frame)}")
    if set(frame["rr_config_id"]) != {"1rr", "1_5rr"}:
        raise ValueError("realized master path must contain 1rr and 1_5rr alternatives")
    if not frame["mutually_exclusive_config_alternative"].astype(bool).all():
        raise ValueError("all realized rows must be mutually exclusive RR alternatives")
    for rr, expected in {"1rr": [150.0, -200.0], "1_5rr": [0.0, -200.0]}.items():
        selected = select_realized_prefix(frame, rr)
        points = selected["pnl_points"].astype(float).tolist()
        if points != expected:
            raise ValueError(f"{rr} prefix sequence must be {expected}, got {points}")
    if frame.groupby("event_group_id")["rr_config_id"].nunique().min() != 2:
        raise ValueError("both RR alternatives must share the same realized event group IDs")


def select_realized_prefix(frame: pd.DataFrame, rr_config_id: RRConfig) -> pd.DataFrame:
    selected = frame[frame["rr_config_id"] == rr_config_id].copy()
    selected["sequence_number"] = selected["sequence_number"].astype(int)
    selected = selected.sort_values("sequence_number").reset_index(drop=True)
    if len(selected) != 2:
        raise ValueError(f"selected RR {rr_config_id} must have exactly two realized rows")
    if selected["sequence_number"].tolist() != [1, 2]:
        raise ValueError(f"selected RR {rr_config_id} must use sequence numbers [1, 2]")
    return selected


def prefix_net_points(prefix: pd.DataFrame) -> float:
    return float(prefix["pnl_points"].astype(float).sum())


def validate_prefix_mode(*, use_legacy_anchor: bool, use_realized_master_prefix: bool) -> None:
    if use_legacy_anchor and use_realized_master_prefix:
        raise ValueError("legacy anchor and realized master prefix cannot both be applied")


def load_source_library(rr_config_id: RRConfig, data_dir: str | Path | None = None) -> pd.DataFrame:
    root = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR
    filename = "forward_1rr.csv" if rr_config_id == "1rr" else "forward_1_5rr.csv"
    frame = pd.read_csv(root / filename)
    required = {
        "trade_packet_id",
        "source_session_date",
        "entry_time",
        "exit_time",
        "direction",
        "exit_reason",
        "effective_exit_reason",
        "pnl_points",
        "raw_stop_points",
        "effective_stop_points",
        "target_points",
        "mae_points",
        "mfe_points",
        "source_ledger_id",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{filename} missing required historical packet columns: {missing}")
    return executable_packet_pool(frame)


def executable_packet_pool(frame: pd.DataFrame) -> pd.DataFrame:
    """Remove historical diagnostic FLAT rows from executable sampling.

    Forward rolling-PF replay is not enabled in this implementation, so old
    historical FLAT labels remain metadata only and are excluded from the
    executable packet pool instead of becoming zero-PnL BE trades.
    """

    out = frame.copy()
    flat = pd.Series(False, index=out.index)
    if "rolling_pf_is_flat" in out:
        flat |= out["rolling_pf_is_flat"].astype(str).str.lower().isin({"true", "1", "yes"})
    if "rolling_pf_switch_state" in out:
        flat |= out["rolling_pf_switch_state"].astype(str).str.upper().eq("FLAT")
    if "effective_exit_reason" in out:
        flat |= out["effective_exit_reason"].astype(str).str.upper().eq("FLAT")
    return out[~flat].reset_index(drop=True)


def build_master_path(
    scenario: ForwardScenario,
    *,
    path_id: int = 0,
    seed: int | None = None,
    data_dir: str | Path | None = None,
) -> pd.DataFrame:
    validate_prefix_mode(
        use_legacy_anchor=scenario.use_legacy_anchor,
        use_realized_master_prefix=scenario.use_realized_master_prefix,
    )
    realized = select_realized_prefix(load_realized_master_path(Path(data_dir) / "realized_master_path.csv" if data_dir else None), scenario.rr_config_id)
    source = load_source_library(scenario.rr_config_id, data_dir)
    continuation = sample_scenario_continuation(
        source,
        scenario,
        seed=scenario.master_seed if seed is None else seed,
        path_id=path_id,
    )
    master = pd.concat([_decorate_realized(realized, scenario, path_id), continuation], ignore_index=True)
    master["prefix_application_basis"] = scenario.prefix_application_basis
    master["pf_scenario"] = scenario.pf_scenario
    master["regime_scenario"] = scenario.regime_scenario
    master["point_scale_scenario"] = scenario.point_scale_scenario
    master["master_seed"] = scenario.master_seed
    master["mc_seed"] = scenario.mc_seed
    return add_path_totals(master)


def build_monte_carlo_paths(
    scenario: ForwardScenario,
    *,
    data_dir: str | Path | None = None,
) -> list[pd.DataFrame]:
    return [
        build_master_path(scenario, path_id=path_id, seed=scenario.mc_seed + path_id, data_dir=data_dir)
        for path_id in range(int(scenario.path_count))
    ]


def sample_historical_continuation(
    source: pd.DataFrame,
    *,
    rr_config_id: RRConfig,
    july_count: int,
    august_count: int,
    seed: int,
    path_id: int,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    july_dates = forecast_trading_dates(7)
    august_dates = forecast_trading_dates(8)
    if int(july_count) > len(july_dates):
        raise ValueError(f"requested July count {july_count} exceeds available trading days {len(july_dates)}")
    if int(august_count) > len(august_dates):
        raise ValueError(f"requested August count {august_count} exceeds available trading days {len(august_dates)}")
    july = _sample_month_packets(source, 7, int(july_count), rng, pf_scenario=None, regime_scenario=None)
    august = _sample_month_packets(source, 8, int(august_count), rng, pf_scenario=None, regime_scenario=None)
    sampled = pd.concat([july, august], ignore_index=True)
    rows: list[dict[str, Any]] = []
    for offset, row in sampled.iterrows():
        target_month = 7 if offset < len(july) else 8
        month_offset = offset if target_month == 7 else offset - len(july)
        event_date = (july_dates if target_month == 7 else august_dates)[month_offset]
        rows.append(_synthetic_row(row, rr_config_id, path_id, sequence_number=3 + offset, event_date=event_date, seed=seed))
    return pd.DataFrame(rows)


def sample_scenario_continuation(
    source: pd.DataFrame,
    scenario: ForwardScenario,
    *,
    seed: int,
    path_id: int,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    july_dates = forecast_trading_dates(7)
    august_dates = forecast_trading_dates(8)
    if scenario.july_candidate_count > len(july_dates):
        raise ValueError(
            f"requested July count {scenario.july_candidate_count} exceeds available trading days {len(july_dates)}"
        )
    if scenario.august_candidate_count > len(august_dates):
        raise ValueError(
            f"requested August count {scenario.august_candidate_count} exceeds available trading days {len(august_dates)}"
        )
    july = _sample_month_packets(
        source,
        7,
        scenario.july_candidate_count,
        rng,
        pf_scenario=scenario.pf_scenario,
        regime_scenario=scenario.regime_scenario,
    )
    august = _sample_month_packets(
        source,
        8,
        scenario.august_candidate_count,
        rng,
        pf_scenario=scenario.pf_scenario,
        regime_scenario=scenario.regime_scenario,
    )
    sampled = pd.concat([july, august], ignore_index=True)
    rows: list[dict[str, Any]] = []
    for offset, row in sampled.iterrows():
        dates = july_dates if offset < len(july) else august_dates
        month_offset = offset if offset < len(july) else offset - len(july)
        rows.append(
            _synthetic_row(
                row,
                scenario.rr_config_id,
                path_id,
                sequence_number=3 + offset,
                event_date=dates[month_offset],
                seed=seed,
                point_scale_scenario=scenario.point_scale_scenario,
            )
        )
    return pd.DataFrame(rows)


def add_path_totals(path: pd.DataFrame) -> pd.DataFrame:
    out = path.copy()
    pnl = out["pnl_points"].astype(float)
    realized_mask = out["status"].eq("REALIZED")
    synthetic_mask = out["status"].eq("SYNTHETIC")
    out["realized_prefix_net_points"] = float(pnl[realized_mask].sum())
    out["forward_only_net_points"] = float(pnl[synthetic_mask].sum())
    out["combined_net_points"] = float(pnl.sum())
    out["cumulative_realized_points"] = pnl.where(realized_mask, 0.0).cumsum()
    out["cumulative_forward_only_points"] = pnl.where(synthetic_mask, 0.0).cumsum()
    out["cumulative_combined_points"] = pnl.cumsum()
    return out


def path_summary(paths: list[pd.DataFrame], scenario: ForwardScenario) -> pd.DataFrame:
    rows = []
    for path in paths:
        rows.append(
            {
                "rr_config_id": scenario.rr_config_id,
                "path_id": int(path["path_id"].iloc[0]),
                "master_seed": scenario.master_seed,
                "mc_seed": scenario.mc_seed,
                "prefix_application_basis": scenario.prefix_application_basis,
                "pf_scenario": scenario.pf_scenario,
                "regime_scenario": scenario.regime_scenario,
                "point_scale_scenario": scenario.point_scale_scenario,
                "realized_prefix_net_points": float(path["realized_prefix_net_points"].iloc[-1]),
                "forward_only_net_points": float(path["forward_only_net_points"].iloc[-1]),
                "combined_net_points": float(path["combined_net_points"].iloc[-1]),
                "synthetic_trades": int(path["status"].eq("SYNTHETIC").sum()),
                "unknown_realized_excursions": int(
                    (path["status"].eq("REALIZED") & path["excursion_confidence"].eq("UNKNOWN_USER_CONFIRMED")).sum()
                ),
            }
        )
    return pd.DataFrame(rows)


def strategy_path_manifest(paths: list[pd.DataFrame], scenario: ForwardScenario) -> pd.DataFrame:
    rows = []
    for path in paths:
        synthetic = path[path["status"].eq("SYNTHETIC")]
        rows.append(
            {
                "rr_config_id": scenario.rr_config_id,
                "path_id": int(path["path_id"].iloc[0]),
                "source_packet_sequence": "|".join(synthetic["source_trade_packet_id"].fillna("").astype(str)),
                "master_seed": scenario.master_seed,
                "mc_seed": scenario.mc_seed,
                "prefix_application_basis": scenario.prefix_application_basis,
                "july_candidate_count": scenario.july_candidate_count,
                "august_candidate_count": scenario.august_candidate_count,
            }
        )
    return pd.DataFrame(rows)


def run_forward_lifecycle_grid(
    paths: list[pd.DataFrame],
    plans: list[LifecyclePlan],
    *,
    contract_values: list[int],
    settings_by_plan: dict[str, LifecycleSettings],
    dollars_per_point: float = 2.0,
    prefix_application_basis: PrefixApplicationBasis = "ACCOUNT_STATE_BEFORE_PREFIX",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    results = []
    months = []
    events = []
    for path in paths:
        strategy_path_id = int(path["path_id"].iloc[0])
        lifecycle_frame = path if prefix_application_basis == "ACCOUNT_STATE_BEFORE_PREFIX" else path[path["status"].eq("SYNTHETIC")]
        trades = path_to_trades(lifecycle_frame, dollars_per_point=dollars_per_point)
        for plan in plans:
            settings = settings_by_plan[plan.key]
            for contracts in contract_values:
                result, path_months, path_events = simulate_lifecycle_path(
                    trades,
                    plan,
                    contracts=contracts,
                    settings=settings,
                    path_id=strategy_path_id,
                    seed=strategy_path_id,
                    dollars_per_point=dollars_per_point,
                )
                result_dict = result.to_dict()
                result_dict["strategy_path_id"] = strategy_path_id
                result_dict["prefix_application_basis"] = prefix_application_basis
                result_dict["realized_prefix_net_points"] = float(path["realized_prefix_net_points"].iloc[-1])
                result_dict["forward_only_net_points"] = float(path["forward_only_net_points"].iloc[-1])
                result_dict["combined_net_points"] = float(path["combined_net_points"].iloc[-1])
                results.append(result_dict)
                for month in path_months:
                    item = month.to_dict()
                    item["strategy_path_id"] = strategy_path_id
                    item["prefix_application_basis"] = prefix_application_basis
                    months.append(item)
                for event in path_events:
                    item = event.to_dict()
                    item["strategy_path_id"] = strategy_path_id
                    item["prefix_application_basis"] = prefix_application_basis
                    events.append(item)
    return summarize_lifecycle_results_from_dicts(results), pd.DataFrame(months), pd.DataFrame(events)


def build_forward_account_trade_ledger(
    paths: list[pd.DataFrame],
    plans: list[LifecyclePlan],
    *,
    contract_values: list[int],
    settings_by_plan: dict[str, LifecycleSettings],
    dollars_per_point: float = 2.0,
    prefix_application_basis: PrefixApplicationBasis = "ACCOUNT_STATE_BEFORE_PREFIX",
    scenario: ForwardScenario | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    account_path_id = 0
    for path in paths:
        strategy_path_id = int(path["path_id"].iloc[0])
        account_frame = path if prefix_application_basis == "ACCOUNT_STATE_BEFORE_PREFIX" else path[path["status"].eq("SYNTHETIC")]
        for plan in plans:
            settings = settings_by_plan[plan.key]
            for contracts in contract_values:
                rows.extend(
                    _simulate_trade_ledger_for_account(
                        account_frame,
                        full_path=path,
                        plan=plan,
                        settings=settings,
                        contracts=int(contracts),
                        dollars_per_point=dollars_per_point,
                        strategy_path_id=strategy_path_id,
                        account_path_id=account_path_id,
                        prefix_application_basis=prefix_application_basis,
                        scenario=scenario,
                    )
                )
                account_path_id += 1
    return pd.DataFrame(rows)


def _simulate_trade_ledger_for_account(
    path: pd.DataFrame,
    *,
    full_path: pd.DataFrame,
    plan: LifecyclePlan,
    settings: LifecycleSettings,
    contracts: int,
    dollars_per_point: float,
    strategy_path_id: int,
    account_path_id: int,
    prefix_application_basis: PrefixApplicationBasis,
    scenario: ForwardScenario | None,
) -> list[dict[str, Any]]:
    profile = plan.funded_profile
    balance = float(settings.current_balance if settings.current_balance is not None else profile.starting_balance)
    floor = float(settings.current_floor if settings.current_floor is not None else profile.starting_floor)
    running_peak = max(balance, profile.starting_balance)
    eod_peak = running_peak
    day_pnl = 0.0
    current_day: str | None = None
    winning_days = int(settings.current_winning_days)
    daily_profits = list(getattr(settings, "current_daily_profits", []) or [])
    if settings.current_highest_winning_day > 0:
        daily_profits.append(float(settings.current_highest_winning_day))
    payouts_taken = int(scenario.payouts_already_taken if scenario else 0)
    total_payouts = 0.0
    total_fees = float(scenario.prior_fees if scenario else 0.0)
    failed = False
    failure_reason = ""
    rows: list[dict[str, Any]] = []
    for row in path.sort_values("sequence_number").itertuples(index=False):
        session_date = str(row.session_date)
        if current_day is not None and session_date != current_day:
            if day_pnl >= profile.winning_day_threshold and day_pnl > 0:
                winning_days += 1
            daily_profits.append(day_pnl)
            eod_peak = max(eod_peak, balance)
            if profile.drawdown_mode == "eod_trailing":
                floor = max(floor, min(_floor_ceiling(profile), eod_peak - profile.max_loss))
            day_pnl = 0.0
        current_day = session_date

        balance_before = balance
        floor_before = floor
        running_peak_before = running_peak
        eod_peak_before = eod_peak
        gross = 0.0
        commission = 0.0
        net = 0.0
        payout_amount = 0.0
        threshold_touched = False
        estimated_low = pd.NA
        estimated_high = pd.NA

        if not failed and bool(row.was_executed):
            pnl_points = float(row.pnl_points)
            gross = pnl_points * dollars_per_point * contracts
            net = gross - commission
            mae = _optional_float(row.mae_points)
            mfe = _optional_float(row.mfe_points)
            if mae is not None:
                estimated_low = balance + min(0.0, -abs(mae) * dollars_per_point * contracts)
            if mfe is not None:
                estimated_high = balance + max(0.0, abs(mfe) * dollars_per_point * contracts)
            if profile.drawdown_mode == "intraday_trailing" and mfe is not None:
                running_peak = max(running_peak, float(estimated_high))
                floor = max(floor, min(_floor_ceiling(profile), running_peak - profile.max_loss))
            if mae is not None and float(estimated_low) <= floor:
                threshold_touched = True
                failed = True
                failure_reason = "threshold touched by MAE"
            else:
                balance += net
                day_pnl += net
                running_peak = max(running_peak, balance)
                if balance <= floor:
                    threshold_touched = True
                    failed = True
                    failure_reason = "threshold touched by closing balance"

        consistency_ratio = _consistency_ratio(daily_profits, balance, profile.starting_balance)
        payout_eligible = (
            not failed
            and _is_payout_eligible(
                balance=balance,
                profile=profile,
                winning_days=winning_days,
                daily_profits=daily_profits,
            )
        )
        rows.append(
            {
                "strategy_path_id": strategy_path_id,
                "account_path_id": account_path_id,
                "plan_key": plan.key,
                "firm": plan.firm,
                "account": plan.account_name,
                "contracts": contracts,
                "sequence_number": int(row.sequence_number),
                "record_type": row.record_type,
                "was_executed": bool(row.was_executed),
                "status": row.status,
                "session_date": session_date,
                "entry_time": row.entry_time,
                "exit_time": row.exit_time,
                "source_packet_id": row.source_trade_packet_id,
                "result_type": _result_type(float(row.pnl_points)),
                "pnl_points": float(row.pnl_points),
                "gross_pnl_dollars": gross,
                "commissions": commission,
                "net_pnl_dollars": net,
                "balance_before": balance_before,
                "floor_before": floor_before,
                "running_peak_before": running_peak_before,
                "eod_peak_before": eod_peak_before,
                "estimated_intratrade_low": estimated_low,
                "estimated_intratrade_high": estimated_high,
                "threshold_touched": threshold_touched,
                "balance_after": balance,
                "floor_after": floor,
                "running_peak_after": running_peak,
                "eod_peak_after": eod_peak,
                "day_pnl_after": day_pnl,
                "winning_days_after": winning_days,
                "highest_winning_day": max([0.0, *daily_profits]),
                "consistency_ratio": consistency_ratio,
                "payout_eligibility": payout_eligible,
                "payout_amount": payout_amount,
                "account_stage": "funded",
                "account_attempt": 1,
                "failed": failed,
                "failure_reason": failure_reason,
                "excursion_confidence": row.excursion_confidence,
                "prefix_application_basis": prefix_application_basis,
                "realized_prefix_net_points": float(full_path["realized_prefix_net_points"].iloc[-1]),
                "forward_only_net_points": float(full_path["forward_only_net_points"].iloc[-1]),
                "combined_net_points": float(full_path["combined_net_points"].iloc[-1]),
                "total_payouts": total_payouts,
                "total_fees": total_fees,
            }
        )
    return rows


def summarize_lifecycle_results_from_dicts(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    from sim_core.lifecycle import LifecyclePathResult

    core_fields = LifecyclePathResult.__dataclass_fields__.keys()
    core_results = [LifecyclePathResult(**{key: row[key] for key in core_fields}) for row in rows]
    summary = summarize_lifecycle_results(core_results)
    extras = pd.DataFrame(rows)
    if not summary.empty and "prefix_application_basis" not in summary:
        basis = extras.groupby(["plan_key", "contracts"], sort=False)["prefix_application_basis"].first().reset_index()
        summary = summary.merge(basis, left_on=["plan", "contracts"], right_on=["plan_key", "contracts"], how="left").drop(columns=["plan_key"])
    return summary


def path_to_trades(path: pd.DataFrame, *, dollars_per_point: float = 2.0) -> list[Trade]:
    trades = []
    for row in path.sort_values("sequence_number").itertuples(index=False):
        entry, exit_ = _accounting_times(row)
        pnl_points = float(row.pnl_points)
        trades.append(
            Trade(
                trade_id=f"{row.rr_config_id}|path{row.path_id}|seq{int(row.sequence_number)}",
                source_row_id=f"{row.rr_config_id}|path{row.path_id}|seq{int(row.sequence_number)}",
                strategy_id=str(row.rr_config_id),
                instrument="NQ",
                contract_symbol="MNQ",
                entry_time=entry,
                exit_time=exit_,
                pnl_dollars=pnl_points * dollars_per_point,
                direction=None if pd.isna(row.direction) else str(row.direction),
                pnl_points=pnl_points,
                stop_points=_optional_float(row.effective_stop_points),
                target_points=_optional_float(row.target_points),
                mae_points=_optional_float(row.mae_points),
                mfe_points=_optional_float(row.mfe_points),
                result_type=_result_type(pnl_points),
                session=str(row.session_date),
                dollars_per_point=dollars_per_point,
                metadata={
                    "status": row.status,
                    "event_group_id": row.event_group_id,
                    "evidence_status": row.evidence_status,
                    "excursion_confidence": row.excursion_confidence,
                    "strict_barrier_status": row.strict_barrier_status,
                    "source_trade_packet_id": None if pd.isna(row.source_trade_packet_id) else row.source_trade_packet_id,
                },
            )
        )
    return trades


def export_forward_artifacts(
    scenario: ForwardScenario,
    master_path: pd.DataFrame,
    mc_paths: list[pd.DataFrame],
    lifecycle_summary: pd.DataFrame,
    lifecycle_events: pd.DataFrame,
    per_trade_account_ledger: pd.DataFrame | None = None,
    *,
    export_dir: str | Path | None = None,
) -> dict[str, Path]:
    root = Path(export_dir) if export_dir is not None else DEFAULT_EXPORT_DIR
    root.mkdir(parents=True, exist_ok=True)
    selected_prefix = master_path[master_path["status"].eq("REALIZED")]
    all_paths = pd.concat(mc_paths, ignore_index=True) if mc_paths else pd.DataFrame()
    outputs = {
        "selected_realized_prefix": root / "selected_realized_prefix.csv",
        "deterministic_master_path": root / "deterministic_master_path.csv",
        "monte_carlo_strategy_path_manifest": root / "monte_carlo_strategy_path_manifest.csv",
        "path_level_point_results": root / "path_level_point_results.csv",
        "lifecycle_account_results": root / "lifecycle_account_results.csv",
        "lifecycle_events": root / "lifecycle_events.csv",
        "per_trade_account_ledger": root / "per_trade_account_ledger.csv",
        "summary": root / "summary.csv",
        "validation_report": root / "validation_report.csv",
    }
    selected_prefix.to_csv(outputs["selected_realized_prefix"], index=False)
    master_path.to_csv(outputs["deterministic_master_path"], index=False)
    strategy_path_manifest(mc_paths, scenario).to_csv(outputs["monte_carlo_strategy_path_manifest"], index=False)
    path_summary(mc_paths, scenario).to_csv(outputs["path_level_point_results"], index=False)
    lifecycle_summary.to_csv(outputs["lifecycle_account_results"], index=False)
    lifecycle_events.to_csv(outputs["lifecycle_events"], index=False)
    (per_trade_account_ledger if per_trade_account_ledger is not None else pd.DataFrame()).to_csv(
        outputs["per_trade_account_ledger"], index=False
    )
    path_summary([master_path], scenario).to_csv(outputs["summary"], index=False)
    validation_report(master_path, mc_paths, scenario).to_csv(outputs["validation_report"], index=False)
    if not all_paths.empty:
        all_paths.to_csv(root / "all_strategy_paths.csv", index=False)
        outputs["all_strategy_paths"] = root / "all_strategy_paths.csv"
    return outputs


def validation_report(master_path: pd.DataFrame, mc_paths: list[pd.DataFrame], scenario: ForwardScenario) -> pd.DataFrame:
    checks = [
        ("realized_prefix_rows", int(master_path["status"].eq("REALIZED").sum()) == 2, "selected RR has two realized rows"),
        ("synthetic_starts_at_3", int(master_path.loc[master_path["status"].eq("SYNTHETIC"), "sequence_number"].min()) == 3, "continuation sequence begins at 3"),
        ("legacy_anchor_disabled", not scenario.use_legacy_anchor, "legacy anchor is not applied"),
        ("unknown_realized_excursion_flagged", master_path.loc[master_path["status"].eq("REALIZED"), "strict_barrier_status"].eq("UNKNOWN").all(), "missing realized MAE/MFE is explicit"),
        ("synthetic_packets_have_source", all(path.loc[path["status"].eq("SYNTHETIC"), "source_trade_packet_id"].notna().all() for path in mc_paths), "synthetic rows reference packet IDs"),
    ]
    return pd.DataFrame({"check": [c[0] for c in checks], "passed": [c[1] for c in checks], "detail": [c[2] for c in checks]})


def _decorate_realized(realized: pd.DataFrame, scenario: ForwardScenario, path_id: int) -> pd.DataFrame:
    out = realized.copy()
    out["path_id"] = int(path_id)
    out["was_executed"] = True
    out["candidate_pnl_points"] = out["pnl_points"].astype(float)
    out["executed_pnl_points"] = out["pnl_points"].astype(float)
    out["timestamp_policy"] = "USER_CONFIRMED_DATE_ONLY"
    out["holding_duration_minutes"] = pd.NA
    out["rolling_pf_before"] = pd.NA
    out["gate_state_before"] = "CONFIRMED_REALIZED_EXECUTED"
    out["source_ledger_id"] = pd.NA
    out["source_session_date"] = pd.NA
    out["source_month"] = pd.NA
    out["source_entry_time"] = pd.NA
    out["source_exit_time"] = pd.NA
    out["source_exit_reason"] = pd.NA
    out["master_seed"] = scenario.master_seed
    out["mc_seed"] = scenario.mc_seed
    out["prefix_application_basis"] = scenario.prefix_application_basis
    out["pf_scenario"] = scenario.pf_scenario
    out["regime_scenario"] = scenario.regime_scenario
    out["point_scale_scenario"] = scenario.point_scale_scenario
    out["strict_barrier_status"] = "UNKNOWN"
    if "excursion_confidence" not in out:
        out["excursion_confidence"] = "UNKNOWN_USER_CONFIRMED"
    return out


def _sample_month_packets(
    source: pd.DataFrame,
    month: int,
    count: int,
    rng: np.random.Generator,
    *,
    pf_scenario: str | None,
    regime_scenario: str | None,
) -> pd.DataFrame:
    if count <= 0:
        return source.head(0).copy()
    if "seasonality_month" in source:
        candidates = source[source["seasonality_month"].astype(int) == month]
    else:
        dates = pd.to_datetime(source["source_session_date"], errors="coerce")
        candidates = source[dates.dt.month == month]
    if candidates.empty:
        raise ValueError(f"no historical packets available for month {month}")
    weights = _scenario_weights(candidates, pf_scenario=pf_scenario, regime_scenario=regime_scenario)
    indexes = rng.choice(np.arange(len(candidates)), size=count, replace=True, p=weights)
    return candidates.iloc[indexes].reset_index(drop=True)


def forecast_trading_dates(month: int) -> list[str]:
    if month == 7:
        start, end = "2026-07-09", "2026-07-31"
    elif month == 8:
        start, end = "2026-08-03", "2026-08-31"
    else:
        raise ValueError("forecast trading dates are only defined for July and August 2026")
    return [str(day.date()) for day in pd.bdate_range(start, end)]


def _synthetic_row(
    row: pd.Series,
    rr_config_id: RRConfig,
    path_id: int,
    *,
    sequence_number: int,
    event_date: str,
    seed: int,
    point_scale_scenario: str = "current",
) -> dict[str, Any]:
    scaled = _scaled_packet_values(row, rr_config_id, point_scale_scenario)
    entry_time, exit_time, duration_minutes = _shift_packet_times(row, event_date)
    return {
        "master_path_version": "2026-07-08.v1",
        "master_path_id": "JULY_AUGUST_REALIZED_PREFIX",
        "rr_config_id": rr_config_id,
        "config": row.get("config"),
        "config_label": row.get("config_label"),
        "path_id": int(path_id),
        "sequence_number": int(sequence_number),
        "event_group_id": f"SYNTHETIC_PATH_{path_id:05d}_SEQ_{sequence_number:03d}",
        "configuration_alternative_group_id": "CURRENT_REALIZED_RR_ALTERNATIVES",
        "status": "SYNTHETIC",
        "record_type": "HISTORICAL_PACKET",
        "was_executed": True,
        "session_date": event_date,
        "entry_time": entry_time.isoformat(),
        "exit_time": exit_time.isoformat(),
        "timestamp_policy": "SYNTHETIC_SHIFTED_SOURCE_TIME_OF_DAY",
        "direction": row.get("direction"),
        "exit_reason": row.get("exit_reason"),
        "effective_exit_reason": row.get("effective_exit_reason"),
        "pnl_points": scaled["pnl_points"],
        "candidate_pnl_points": scaled["pnl_points"],
        "executed_pnl_points": scaled["pnl_points"],
        "raw_stop_points": scaled["raw_stop_points"],
        "effective_stop_points": scaled["effective_stop_points"],
        "target_points": scaled["target_points"],
        "mae_points": scaled["mae_points"],
        "mfe_points": scaled["mfe_points"],
        "holding_duration_minutes": duration_minutes,
        "source_trade_packet_id": row.get("trade_packet_id"),
        "source_type": "HISTORICAL_FORWARD_PACKET",
        "source_ledger_id": row.get("source_ledger_id"),
        "source_session_date": row.get("source_session_date"),
        "source_month": row.get("source_month"),
        "source_entry_time": row.get("entry_time"),
        "source_exit_time": row.get("exit_time"),
        "source_exit_reason": row.get("exit_reason"),
        "evidence_status": "SOURCE_VERIFIED",
        "excursion_confidence": "EXACT_SOURCE_VERIFIED",
        "mutually_exclusive_config_alternative": True,
        "strict_barrier_status": "AVAILABLE",
        "rolling_pf_before": pd.NA,
        "gate_state_before": "GATING_DISABLED_FLAT_ROWS_EXCLUDED",
        "master_seed": seed,
        "mc_seed": seed,
        "prefix_application_basis": pd.NA,
        "pf_scenario": pd.NA,
        "regime_scenario": pd.NA,
        "point_scale_scenario": pd.NA,
    }


def _accounting_times(row: Any) -> tuple[pd.Timestamp, pd.Timestamp]:
    if getattr(row, "entry_time", None) is not None and not pd.isna(row.entry_time):
        entry = pd.Timestamp(row.entry_time)
        if entry.tzinfo is None:
            entry = entry.tz_localize("UTC")
        else:
            entry = entry.tz_convert("UTC")
        exit_ = pd.Timestamp(row.exit_time)
        if exit_.tzinfo is None:
            exit_ = exit_.tz_localize("UTC")
        else:
            exit_ = exit_.tz_convert("UTC")
        return entry, exit_
    date = pd.Timestamp(str(row.session_date), tz="UTC")
    entry = date + pd.Timedelta(hours=14)
    exit_ = entry + pd.Timedelta(hours=1)
    return entry, exit_


def _optional_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _result_type(pnl_points: float) -> str:
    if pnl_points > 0:
        return "win"
    if pnl_points < 0:
        return "loss"
    return "breakeven"


def _consistency_ratio(daily_profits: list[float], balance: float, starting_balance: float) -> float | None:
    profit = balance - starting_balance
    positive = [value for value in daily_profits if value > 0]
    if profit <= 0 or not positive:
        return None
    return max(positive) / profit


def _scenario_weights(
    candidates: pd.DataFrame,
    *,
    pf_scenario: str | None,
    regime_scenario: str | None,
) -> np.ndarray | None:
    if candidates.empty:
        return None
    weights = np.ones(len(candidates), dtype=float)
    pnl = pd.to_numeric(candidates["pnl_points"], errors="coerce").fillna(0.0).to_numpy()
    if pf_scenario == "PF_1_35":
        weights *= np.where(pnl > 0, 0.85, np.where(pnl < 0, 1.15, 1.05))
    elif pf_scenario == "PF_1_50":
        weights *= np.where(pnl > 0, 1.0, 1.0)
    elif pf_scenario == "PF_1_65":
        weights *= np.where(pnl > 0, 1.15, np.where(pnl < 0, 0.85, 0.95))

    if regime_scenario == "gradual_degradation":
        year = pd.to_numeric(candidates.get("source_year", 2024), errors="coerce").fillna(2024).to_numpy()
        weights *= np.where(year >= 2024, 1.35, 0.85)
    elif regime_scenario == "favourable_persistence":
        regime = candidates.get("volatility_regime_by_stop", pd.Series([""] * len(candidates))).astype(str)
        weights *= np.where(regime.str.contains("low|mid", case=False, regex=True).to_numpy(), 1.25, 0.9)
        weights *= np.where(pnl > 0, 1.1, 0.95)
    elif regime_scenario == "abrupt_tail":
        weights *= np.where(pnl < 0, 1.55, np.where(pnl > 0, 0.75, 1.05))
    total = float(weights.sum())
    return weights / total if total > 0 else None


def _scale_multiplier(point_scale_scenario: str) -> float:
    return {"low": 0.75, "current": 1.0, "high": 1.15}.get(point_scale_scenario, 1.0)


def _scaled_packet_values(row: pd.Series, rr_config_id: RRConfig, point_scale_scenario: str) -> dict[str, float | None]:
    multiplier = _scale_multiplier(point_scale_scenario)
    raw_stop = _optional_float(row.get("raw_stop_points"))
    effective_stop = _optional_float(row.get("effective_stop_points"))
    target = _optional_float(row.get("target_points"))
    pnl = _optional_float(row.get("pnl_points"))
    mae = _optional_float(row.get("mae_points"))
    mfe = _optional_float(row.get("mfe_points"))
    raw_cap = 200.0
    target_cap = 200.0 if rr_config_id == "1rr" else 300.0
    raw_scaled = min(raw_cap, raw_stop * multiplier) if raw_stop is not None else None
    stop_ratio = (raw_scaled / raw_stop) if raw_stop and raw_scaled is not None else multiplier
    return {
        "raw_stop_points": raw_scaled,
        "effective_stop_points": min(200.0, effective_stop * stop_ratio) if effective_stop is not None else None,
        "target_points": min(target_cap, target * stop_ratio) if target is not None else None,
        "pnl_points": pnl * stop_ratio if pnl is not None else 0.0,
        "mae_points": mae * stop_ratio if mae is not None else None,
        "mfe_points": mfe * stop_ratio if mfe is not None else None,
    }


def _shift_packet_times(row: pd.Series, event_date: str) -> tuple[pd.Timestamp, pd.Timestamp, float]:
    source_entry = pd.Timestamp(row.get("entry_time"))
    source_exit = pd.Timestamp(row.get("exit_time"))
    if source_entry.tzinfo is None:
        source_entry = source_entry.tz_localize("UTC")
    if source_exit.tzinfo is None:
        source_exit = source_exit.tz_localize("UTC")
    duration = source_exit - source_entry
    if duration <= pd.Timedelta(0):
        duration = pd.Timedelta(hours=1)
    event_midnight = pd.Timestamp(event_date, tz=source_entry.tz)
    shifted_entry = event_midnight + (source_entry - source_entry.normalize())
    shifted_exit = shifted_entry + duration
    return shifted_entry.tz_convert("UTC"), shifted_exit.tz_convert("UTC"), duration.total_seconds() / 60.0


def strategy_sequence_hash(path: pd.DataFrame) -> str:
    material = "|".join(
        path.sort_values("sequence_number")[
            ["source_trade_packet_id", "session_date", "was_executed", "pnl_points"]
        ]
        .fillna("")
        .astype(str)
        .agg(":".join, axis=1)
    )
    return sha256(material.encode("utf-8")).hexdigest()
