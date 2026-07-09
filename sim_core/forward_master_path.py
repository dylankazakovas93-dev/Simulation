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
from sim_core.prop_rules import _trade_day

RRConfig = Literal["1rr", "1_5rr"]
PrefixApplicationBasis = Literal["ACCOUNT_STATE_BEFORE_PREFIX", "ACCOUNT_STATE_AFTER_PREFIX"]
GeometryPolicy = Literal["SOURCE_EXACT", "NORMALIZE_TO_FORWARD_RANGE"]

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
FORWARD_STRATEGY_LEDGER_COLUMNS = [
    "rr_config_id",
    "path_id",
    "sequence_number",
    "status",
    "record_type",
    "session_date",
    "entry_time",
    "exit_time",
    "holding_duration_minutes",
    "direction",
    "exit_reason",
    "effective_exit_reason",
    "pnl_points",
    "candidate_pnl_points",
    "executed_pnl_points",
    "raw_stop_points",
    "effective_stop_points",
    "target_points",
    "mae_points",
    "mfe_points",
    "cumulative_realized_points",
    "cumulative_forward_only_points",
    "cumulative_combined_points",
    "source_trade_packet_id",
    "source_session_date",
    "source_entry_time",
    "source_exit_time",
    "source_exit_reason",
    "source_ledger_id",
    "evidence_status",
    "excursion_confidence",
    "strict_barrier_status",
    "timestamp_policy",
    "pf_scenario",
    "expectancy_tilt",
    "expected_weighted_source_pf",
    "requested_target_pf",
    "achieved_weighted_source_pf",
    "normalized_source_packet_count",
    "july_source_packet_count",
    "august_source_packet_count",
    "july_source_wins",
    "july_source_losses",
    "july_source_breakevens",
    "august_source_wins",
    "august_source_losses",
    "august_source_breakevens",
    "calibration_winner_multiplier",
    "regime_scenario",
    "point_scale_scenario",
    "geometry_policy",
    "min_effective_stop_points",
    "max_effective_stop_points",
]


@dataclass(frozen=True)
class ForwardScenario:
    rr_config_id: RRConfig = "1rr"
    july_candidate_count: int = 8
    august_candidate_count: int = 12
    master_seed: int = 1729
    mc_seed: int = 1730
    path_count: int = 100
    pf_scenario: str = "TARGET_PF"
    expectancy_tilt: float = 0.0
    target_expected_pf: float = 1.50
    regime_scenario: str = "stable"
    point_scale_scenario: str = "current"
    geometry_policy: GeometryPolicy = "NORMALIZE_TO_FORWARD_RANGE"
    min_effective_stop_points: float = 100.0
    max_effective_stop_points: float = 200.0
    allow_breakeven_packets: bool = True
    allow_cutoff_packets: bool = False
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
    source_pool = _apply_geometry_policy(source, scenario)
    source_stats = source_pool_diagnostics(source_pool, scenario)
    continuation = sample_scenario_continuation(
        source_pool,
        scenario,
        seed=scenario.master_seed if seed is None else seed,
        path_id=path_id,
    )
    master = pd.concat([_decorate_realized(realized, scenario, path_id), continuation], ignore_index=True)
    master["prefix_application_basis"] = scenario.prefix_application_basis
    master["pf_scenario"] = scenario.pf_scenario
    master["expectancy_tilt"] = float(scenario.expectancy_tilt)
    master["requested_target_pf"] = float(scenario.target_expected_pf)
    master["achieved_weighted_source_pf"] = source_stats["achieved_weighted_source_pf"]
    master["expected_weighted_source_pf"] = source_stats["achieved_weighted_source_pf"]
    master["normalized_source_packet_count"] = source_stats["normalized_source_packet_count"]
    master["july_source_packet_count"] = source_stats["july_source_packet_count"]
    master["august_source_packet_count"] = source_stats["august_source_packet_count"]
    master["july_source_wins"] = source_stats["july_source_wins"]
    master["july_source_losses"] = source_stats["july_source_losses"]
    master["july_source_breakevens"] = source_stats["july_source_breakevens"]
    master["august_source_wins"] = source_stats["august_source_wins"]
    master["august_source_losses"] = source_stats["august_source_losses"]
    master["august_source_breakevens"] = source_stats["august_source_breakevens"]
    master["calibration_winner_multiplier"] = source_stats["calibration_winner_multiplier"]
    master["regime_scenario"] = scenario.regime_scenario
    master["point_scale_scenario"] = scenario.point_scale_scenario
    master["geometry_policy"] = scenario.geometry_policy
    master["min_effective_stop_points"] = float(scenario.min_effective_stop_points)
    master["max_effective_stop_points"] = float(scenario.max_effective_stop_points)
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
        scenario=scenario,
    )
    august = _sample_month_packets(
        source,
        8,
        scenario.august_candidate_count,
        rng,
        scenario=scenario,
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
            )
        )
    out = pd.DataFrame(rows)
    _assert_synthetic_calendar(out)
    return out


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
                "expectancy_tilt": float(scenario.expectancy_tilt),
                "expected_weighted_source_pf": _path_expected_source_pf(path),
                "requested_target_pf": _path_scalar(path, "requested_target_pf"),
                "achieved_weighted_source_pf": _path_scalar(path, "achieved_weighted_source_pf"),
                "normalized_source_packet_count": _path_scalar(path, "normalized_source_packet_count"),
                "july_source_packet_count": _path_scalar(path, "july_source_packet_count"),
                "august_source_packet_count": _path_scalar(path, "august_source_packet_count"),
                "july_source_wins": _path_scalar(path, "july_source_wins"),
                "july_source_losses": _path_scalar(path, "july_source_losses"),
                "july_source_breakevens": _path_scalar(path, "july_source_breakevens"),
                "august_source_wins": _path_scalar(path, "august_source_wins"),
                "august_source_losses": _path_scalar(path, "august_source_losses"),
                "august_source_breakevens": _path_scalar(path, "august_source_breakevens"),
                "calibration_winner_multiplier": _path_scalar(path, "calibration_winner_multiplier"),
                "regime_scenario": scenario.regime_scenario,
                "point_scale_scenario": scenario.point_scale_scenario,
                "geometry_policy": scenario.geometry_policy,
                "min_effective_stop_points": float(scenario.min_effective_stop_points),
                "max_effective_stop_points": float(scenario.max_effective_stop_points),
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
                "pf_scenario": scenario.pf_scenario,
                "expectancy_tilt": float(scenario.expectancy_tilt),
                "expected_weighted_source_pf": _path_expected_source_pf(path),
                "requested_target_pf": _path_scalar(path, "requested_target_pf"),
                "achieved_weighted_source_pf": _path_scalar(path, "achieved_weighted_source_pf"),
                "normalized_source_packet_count": _path_scalar(path, "normalized_source_packet_count"),
                "july_source_packet_count": _path_scalar(path, "july_source_packet_count"),
                "august_source_packet_count": _path_scalar(path, "august_source_packet_count"),
                "calibration_winner_multiplier": _path_scalar(path, "calibration_winner_multiplier"),
                "regime_scenario": scenario.regime_scenario,
                "point_scale_scenario": scenario.point_scale_scenario,
                "geometry_policy": scenario.geometry_policy,
            }
        )
    return pd.DataFrame(rows)


def forward_strategy_ledger(path: pd.DataFrame) -> pd.DataFrame:
    """Return the clean two-month strategy ledger, with no prop-account fields."""

    out = path.copy()
    for column in FORWARD_STRATEGY_LEDGER_COLUMNS:
        if column not in out:
            out[column] = pd.NA
    return out[FORWARD_STRATEGY_LEDGER_COLUMNS].sort_values(["path_id", "sequence_number"]).reset_index(drop=True)


def forward_strategy_ledgers(paths: list[pd.DataFrame]) -> pd.DataFrame:
    if not paths:
        return pd.DataFrame(columns=FORWARD_STRATEGY_LEDGER_COLUMNS)
    return pd.concat([forward_strategy_ledger(path) for path in paths], ignore_index=True)


def run_forward_lifecycle_grid(
    paths: list[pd.DataFrame],
    plans: list[LifecyclePlan],
    *,
    contract_values: list[int],
    settings_by_plan: dict[str, LifecycleSettings],
    dollars_per_point: float = 2.0,
    prefix_application_basis: PrefixApplicationBasis = "ACCOUNT_STATE_BEFORE_PREFIX",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    results = []
    months = []
    events = []
    ledger = []
    account_path_id = 0
    for path in paths:
        strategy_path_id = int(path["path_id"].iloc[0])
        lifecycle_frame = path if prefix_application_basis == "ACCOUNT_STATE_BEFORE_PREFIX" else path[path["status"].eq("SYNTHETIC")]
        trades = path_to_trades(lifecycle_frame, dollars_per_point=dollars_per_point)
        for plan in plans:
            settings = settings_by_plan[plan.key]
            for contracts in contract_values:
                result, path_months, path_events, path_ledger = simulate_lifecycle_path(
                    trades,
                    plan,
                    contracts=contracts,
                    settings=settings,
                    path_id=account_path_id,
                    seed=strategy_path_id,
                    dollars_per_point=dollars_per_point,
                    return_trade_ledger=True,
                )
                result_dict = result.to_dict()
                result_dict["strategy_path_id"] = strategy_path_id
                result_dict["account_path_id"] = account_path_id
                result_dict["prefix_application_basis"] = prefix_application_basis
                result_dict["realized_prefix_net_points"] = float(path["realized_prefix_net_points"].iloc[-1])
                result_dict["forward_only_net_points"] = float(path["forward_only_net_points"].iloc[-1])
                result_dict["combined_net_points"] = float(path["combined_net_points"].iloc[-1])
                result_dict.update(_strict_trace_rates(path_ledger))
                results.append(result_dict)
                for month in path_months:
                    item = month.to_dict()
                    item["strategy_path_id"] = strategy_path_id
                    item["account_path_id"] = account_path_id
                    item["prefix_application_basis"] = prefix_application_basis
                    months.append(item)
                for event in path_events:
                    item = event.to_dict()
                    item["strategy_path_id"] = strategy_path_id
                    item["account_path_id"] = account_path_id
                    item["prefix_application_basis"] = prefix_application_basis
                    events.append(item)
                for row in path_ledger:
                    item = dict(row)
                    item["strategy_path_id"] = strategy_path_id
                    item["account_path_id"] = account_path_id
                    item["prefix_application_basis"] = prefix_application_basis
                    item["realized_prefix_net_points"] = float(path["realized_prefix_net_points"].iloc[-1])
                    item["forward_only_net_points"] = float(path["forward_only_net_points"].iloc[-1])
                    item["combined_net_points"] = float(path["combined_net_points"].iloc[-1])
                    ledger.append(item)
                account_path_id += 1
    return summarize_lifecycle_results_from_dicts(results), pd.DataFrame(months), pd.DataFrame(events), pd.DataFrame(ledger)


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
    strict_columns = {
        "strict_exact_known_trades",
        "strict_unknown_trades",
        "realized_only_trades",
        "strict_exact_failure_trades",
        "realized_only_failure_trades",
    }
    if strict_columns <= set(extras.columns):
        strict = (
            extras.groupby(["plan_key", "contracts"], sort=False)[sorted(strict_columns)]
            .sum()
            .reset_index()
        )
        strict["strict_exact_failure_rate"] = strict.apply(
            lambda row: row["strict_exact_failure_trades"] / row["strict_exact_known_trades"]
            if row["strict_exact_known_trades"]
            else None,
            axis=1,
        )
        strict["strict_unknown_rate"] = strict.apply(
            lambda row: row["strict_unknown_trades"] / row["realized_only_trades"]
            if row["realized_only_trades"]
            else None,
            axis=1,
        )
        strict["realized_only_failure_rate"] = strict.apply(
            lambda row: row["realized_only_failure_trades"] / row["realized_only_trades"]
            if row["realized_only_trades"]
            else None,
            axis=1,
        )
        summary = summary.merge(strict, left_on=["plan", "contracts"], right_on=["plan_key", "contracts"], how="left")
        summary = summary.drop(columns=["plan_key"])
    return summary


def path_to_trades(path: pd.DataFrame, *, dollars_per_point: float = 2.0) -> list[Trade]:
    trades = []
    for row in path.sort_values("sequence_number").itertuples(index=False):
        entry, exit_ = _accounting_times(row)
        if _trade_day(exit_, "America/New_York").date().isoformat() != str(row.session_date):
            raise ValueError(
                f"lifecycle trade day {str(_trade_day(exit_, 'America/New_York').date())} "
                f"does not match assigned session_date {row.session_date}"
            )
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
                    "sequence_number": int(row.sequence_number),
                    "candidate": True,
                    "was_executed": bool(row.was_executed),
                    "session_date": str(row.session_date),
                },
            )
        )
    _assert_trade_order_and_overlap(trades)
    return trades


def export_forward_artifacts(
    scenario: ForwardScenario,
    master_path: pd.DataFrame,
    mc_paths: list[pd.DataFrame],
    lifecycle_summary: pd.DataFrame,
    lifecycle_monthly: pd.DataFrame,
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
        "forward_strategy_ledger": root / "forward_strategy_ledger.csv",
        "all_forward_strategy_ledgers": root / "all_forward_strategy_ledgers.csv",
        "monte_carlo_strategy_path_manifest": root / "monte_carlo_strategy_path_manifest.csv",
        "path_level_point_results": root / "path_level_point_results.csv",
        "lifecycle_account_results": root / "lifecycle_account_results.csv",
        "lifecycle_monthly": root / "lifecycle_monthly.csv",
        "lifecycle_events": root / "lifecycle_events.csv",
        "per_trade_account_ledger": root / "per_trade_account_ledger.csv",
        "summary": root / "summary.csv",
        "validation_report": root / "validation_report.csv",
    }
    selected_prefix.to_csv(outputs["selected_realized_prefix"], index=False)
    master_path.to_csv(outputs["deterministic_master_path"], index=False)
    forward_strategy_ledger(master_path).to_csv(outputs["forward_strategy_ledger"], index=False)
    forward_strategy_ledgers(mc_paths).to_csv(outputs["all_forward_strategy_ledgers"], index=False)
    strategy_path_manifest(mc_paths, scenario).to_csv(outputs["monte_carlo_strategy_path_manifest"], index=False)
    path_summary(mc_paths, scenario).to_csv(outputs["path_level_point_results"], index=False)
    lifecycle_summary.to_csv(outputs["lifecycle_account_results"], index=False)
    lifecycle_monthly.to_csv(outputs["lifecycle_monthly"], index=False)
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
    pf_scenario: str | None = None,
    expectancy_tilt: float = 0.0,
    regime_scenario: str | None = None,
    scenario: ForwardScenario | None = None,
) -> pd.DataFrame:
    if count <= 0:
        return source.head(0).copy()
    if "seasonality_month" in source:
        candidates = source[source["seasonality_month"].astype(int) == month]
    else:
        dates = pd.to_datetime(source["source_session_date"], errors="coerce")
        candidates = source[dates.dt.month == month]
    if candidates.empty:
        raise ValueError(f"no historical packets available for month {month} after forward geometry normalization")
    weights = _sampling_probabilities(candidates)
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
) -> dict[str, Any]:
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
        "pnl_points": _optional_float(row.get("pnl_points")) or 0.0,
        "candidate_pnl_points": _optional_float(row.get("pnl_points")) or 0.0,
        "executed_pnl_points": _optional_float(row.get("pnl_points")) or 0.0,
        "raw_stop_points": _optional_float(row.get("raw_stop_points")),
        "effective_stop_points": _optional_float(row.get("effective_stop_points")),
        "target_points": _optional_float(row.get("target_points")),
        "mae_points": _optional_float(row.get("mae_points")),
        "mfe_points": _optional_float(row.get("mfe_points")),
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
        "geometry_policy": pd.NA,
        "min_effective_stop_points": pd.NA,
        "max_effective_stop_points": pd.NA,
    }


def _apply_geometry_policy(candidates: pd.DataFrame, scenario: ForwardScenario | None) -> pd.DataFrame:
    if candidates.empty:
        return candidates.copy()
    out = candidates.copy()
    reason = out["effective_exit_reason"].astype(str).str.upper()
    if scenario is not None and not scenario.allow_cutoff_packets:
        out = out[~reason.eq("CUTOFF")].copy()
    if scenario is None or scenario.geometry_policy == "SOURCE_EXACT" or out.empty:
        return _attach_sampling_weights(out, scenario)

    source_stop = pd.to_numeric(out["effective_stop_points"], errors="coerce")
    valid_stop = source_stop.notna() & (source_stop > 0)
    out = out[valid_stop].copy()
    source_stop = source_stop[valid_stop]
    desired_stop = (source_stop * _scale_multiplier(scenario.point_scale_scenario)).clip(
        lower=float(scenario.min_effective_stop_points),
        upper=float(scenario.max_effective_stop_points),
    )
    scale_factor = desired_stop / source_stop
    out["source_effective_stop_points"] = source_stop.to_numpy(dtype=float)
    out["desired_effective_stop_points"] = desired_stop.to_numpy(dtype=float)
    out["normalization_scale_factor"] = scale_factor.to_numpy(dtype=float)
    for column in ["pnl_points", "raw_stop_points", "effective_stop_points", "target_points", "mae_points", "mfe_points"]:
        values = pd.to_numeric(out[column], errors="coerce")
        out[column] = values * scale_factor
    out["effective_stop_points"] = desired_stop.to_numpy(dtype=float)
    pnl = pd.to_numeric(out["pnl_points"], errors="coerce").fillna(0.0)
    out.loc[pnl.abs() <= 1e-9, "pnl_points"] = 0.0
    return _attach_sampling_weights(out.reset_index(drop=True), scenario)


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


def _attach_sampling_weights(candidates: pd.DataFrame, scenario: ForwardScenario | None) -> pd.DataFrame:
    out = candidates.copy().reset_index(drop=True)
    if out.empty:
        out["sampling_weight"] = pd.Series(dtype=float)
        out["calibration_winner_multiplier"] = pd.Series(dtype=float)
        return out
    target_pf = 1.50 if scenario is None else float(scenario.target_expected_pf)
    multiplier = _calibration_winner_multiplier(out, target_pf, scenario)
    pnl = pd.to_numeric(out["pnl_points"], errors="coerce").fillna(0.0)
    out["sampling_weight"] = np.where(pnl > 0, multiplier, 1.0)
    out["calibration_winner_multiplier"] = multiplier
    out["source_pool_expected_weight"] = _expected_sampling_exposure_weights(out, scenario)
    return out


def _calibration_winner_multiplier(
    candidates: pd.DataFrame,
    target_pf: float,
    scenario: ForwardScenario | None = None,
) -> float:
    pnl = pd.to_numeric(candidates["pnl_points"], errors="coerce").fillna(0.0)
    if float(pnl[pnl > 0].sum()) <= 0 or float((-pnl[pnl < 0]).sum()) <= 0:
        return 1.0
    low, high = 0.0, 1.0
    while (_expected_pf_for_multiplier(candidates, high, scenario) or 0.0) < float(target_pf):
        high *= 2.0
        if high > 1_000_000:
            break
    for _ in range(80):
        mid = (low + high) / 2.0
        pf = _expected_pf_for_multiplier(candidates, mid, scenario)
        if pf is None:
            return 1.0
        if pf < float(target_pf):
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def _expected_pf_for_multiplier(
    candidates: pd.DataFrame,
    winner_multiplier: float,
    scenario: ForwardScenario | None = None,
) -> float | None:
    out = candidates.copy()
    pnl = pd.to_numeric(out["pnl_points"], errors="coerce").fillna(0.0)
    out["sampling_weight"] = np.where(pnl > 0, float(winner_multiplier), 1.0)
    weights = _expected_sampling_exposure_weights(out, scenario)
    gross_profit = float((np.where(pnl > 0, pnl, 0.0) * weights).sum())
    gross_loss = float((np.where(pnl < 0, -pnl, 0.0) * weights).sum())
    if gross_loss == 0:
        return None
    return gross_profit / gross_loss


def _expected_sampling_exposure_weights(candidates: pd.DataFrame, scenario: ForwardScenario | None = None) -> np.ndarray:
    if candidates.empty:
        return np.array([], dtype=float)
    months = _source_months(candidates)
    if "sampling_weight" in candidates:
        weights = pd.to_numeric(candidates["sampling_weight"], errors="coerce").fillna(1.0).to_numpy(dtype=float)
    else:
        weights = np.ones(len(candidates), dtype=float)
    exposure = np.zeros(len(candidates), dtype=float)
    if scenario is None:
        planned_counts = {7: 1.0, 8: 1.0}
    else:
        planned_counts = {7: float(scenario.july_candidate_count), 8: float(scenario.august_candidate_count)}
    for month, count in planned_counts.items():
        mask = (months == month).to_numpy()
        if not mask.any() or count <= 0:
            continue
        month_weights = weights[mask]
        total = float(month_weights.sum())
        if total > 0:
            exposure[mask] = count * month_weights / total
    if not exposure.any():
        total = float(weights.sum())
        return weights / total if total > 0 else np.ones(len(candidates), dtype=float) / len(candidates)
    return exposure


def _sampling_probabilities(candidates: pd.DataFrame) -> np.ndarray | None:
    if candidates.empty:
        return None
    if "sampling_weight" in candidates:
        weights = pd.to_numeric(candidates["sampling_weight"], errors="coerce").fillna(1.0).to_numpy(dtype=float)
    else:
        weights = np.ones(len(candidates), dtype=float)
    total = float(weights.sum())
    return weights / total if total > 0 else None


def _scale_multiplier(point_scale_scenario: str) -> float:
    return {"low": 0.75, "current": 1.0, "high": 1.15}.get(point_scale_scenario, 1.0)


def _shift_packet_times(row: pd.Series, event_date: str) -> tuple[pd.Timestamp, pd.Timestamp, float]:
    source_entry = pd.Timestamp(row.get("entry_time"))
    source_exit = pd.Timestamp(row.get("exit_time"))
    if source_entry.tzinfo is None:
        source_entry = source_entry.tz_localize("UTC")
    if source_exit.tzinfo is None:
        source_exit = source_exit.tz_localize("UTC")
    source_session = pd.Timestamp(row.get("source_session_date"))
    if source_session.tzinfo is None:
        source_session = source_session.tz_localize(source_exit.tz)
    else:
        source_session = source_session.tz_convert(source_exit.tz)
    duration = source_exit - source_entry
    if duration <= pd.Timedelta(0):
        duration = pd.Timedelta(hours=1)
    event_midnight = pd.Timestamp(event_date, tz=source_exit.tz)
    exit_offset = source_exit - source_session.normalize()
    shifted_exit = event_midnight + exit_offset
    if _trade_day(shifted_exit, "America/New_York").date().isoformat() != str(event_date):
        shifted_exit = pd.Timestamp(f"{event_date} 09:00", tz="America/New_York").tz_convert(source_exit.tz)
    shifted_entry = shifted_exit - duration
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


def source_pool_diagnostics(frame: pd.DataFrame, scenario: ForwardScenario) -> dict[str, float | int | None]:
    achieved_pf = expected_weighted_pf(
        frame,
        scenario.pf_scenario,
        scenario.regime_scenario,
        expectancy_tilt=scenario.expectancy_tilt,
        target_expected_pf=scenario.target_expected_pf,
    )
    months = _source_months(frame)
    pnl = pd.to_numeric(frame["pnl_points"], errors="coerce").fillna(0.0)
    stats: dict[str, float | int | None] = {
        "requested_target_pf": float(scenario.target_expected_pf),
        "achieved_weighted_source_pf": achieved_pf,
        "normalized_source_packet_count": int(len(frame)),
        "july_source_packet_count": int((months == 7).sum()),
        "august_source_packet_count": int((months == 8).sum()),
        "calibration_winner_multiplier": float(frame["calibration_winner_multiplier"].iloc[0])
        if "calibration_winner_multiplier" in frame and not frame.empty
        else None,
    }
    for month, label in [(7, "july"), (8, "august")]:
        month_pnl = pnl[months == month]
        stats[f"{label}_source_wins"] = int((month_pnl > 0).sum())
        stats[f"{label}_source_losses"] = int((month_pnl < 0).sum())
        stats[f"{label}_source_breakevens"] = int((month_pnl.abs() <= 1e-9).sum())
    return stats


def _source_months(frame: pd.DataFrame) -> pd.Series:
    if "seasonality_month" in frame:
        return pd.to_numeric(frame["seasonality_month"], errors="coerce")
    return pd.to_datetime(frame["source_session_date"], errors="coerce").dt.month


def expected_weighted_pf(
    frame: pd.DataFrame,
    pf_scenario: str | None,
    regime_scenario: str | None,
    *,
    expectancy_tilt: float = 0.0,
    target_expected_pf: float = 1.50,
) -> float | None:
    synthetic = frame[frame["status"].eq("SYNTHETIC")] if "status" in frame else frame
    if synthetic.empty:
        return None
    if "sampling_weight" not in synthetic:
        synthetic = _attach_sampling_weights(synthetic, ForwardScenario(target_expected_pf=float(target_expected_pf)))
    if "source_pool_expected_weight" in synthetic:
        weights = pd.to_numeric(synthetic["source_pool_expected_weight"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
        if float(weights.sum()) > 0:
            weights = weights / float(weights.sum())
        else:
            weights = None
    else:
        weights = _sampling_probabilities(synthetic)
    pnl = pd.to_numeric(synthetic["pnl_points"], errors="coerce").fillna(0.0).to_numpy()
    if weights is None:
        weights = np.ones(len(synthetic), dtype=float) / len(synthetic)
    gross_profit = float((np.where(pnl > 0, pnl, 0.0) * weights).sum())
    gross_loss = float((np.where(pnl < 0, -pnl, 0.0) * weights).sum())
    if gross_loss == 0:
        return None
    return gross_profit / gross_loss


def _path_expected_source_pf(path: pd.DataFrame) -> float | None:
    if "expected_weighted_source_pf" not in path or path["expected_weighted_source_pf"].dropna().empty:
        return None
    return float(path["expected_weighted_source_pf"].dropna().iloc[0])


def _path_scalar(path: pd.DataFrame, column: str) -> Any:
    if column not in path or path[column].dropna().empty:
        return None
    value = path[column].dropna().iloc[0]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _assert_synthetic_calendar(frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    if frame["session_date"].duplicated().any():
        raise ValueError("synthetic continuation assigns more than one trade to the same lifecycle day")
    ordered = frame.sort_values("sequence_number")
    if ordered["session_date"].tolist() != sorted(ordered["session_date"].tolist()):
        raise ValueError("synthetic continuation session dates must follow sequence order")
    intervals = []
    for row in ordered.itertuples(index=False):
        entry = pd.Timestamp(row.entry_time)
        exit_ = pd.Timestamp(row.exit_time)
        if _trade_day(exit_, "America/New_York").date().isoformat() != str(row.session_date):
            raise ValueError("synthetic exit trade day does not match assigned session_date")
        intervals.append((entry, exit_))
    for (_, previous_exit), (next_entry, _) in zip(intervals, intervals[1:]):
        if next_entry < previous_exit:
            raise ValueError("synthetic positions overlap")


def _assert_trade_order_and_overlap(trades: list[Trade]) -> None:
    if not trades:
        return
    sequence = [_metadata_int_like(trade.metadata.get("sequence_number")) for trade in trades]
    if sequence != sorted(sequence):
        raise ValueError("lifecycle trade processing order must equal sequence_number order")
    days = [trade.metadata.get("session_date") for trade in trades]
    if len(days) != len(set(days)):
        raise ValueError("one lifecycle day per trade invariant failed")
    for previous, current in zip(trades, trades[1:]):
        if current.entry_time < previous.exit_time:
            raise ValueError("lifecycle trades overlap")


def _metadata_int_like(value: Any) -> int:
    if value is None or pd.isna(value):
        return -1
    return int(value)


def _strict_trace_rates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    trade_rows = [row for row in rows if row.get("record_type") == "TRADE" and row.get("account_taken")]
    exact = [row for row in trade_rows if row.get("strict_account_result") != "UNKNOWN"]
    unknown = [row for row in trade_rows if row.get("strict_account_result") == "UNKNOWN"]
    realized_only_failures = [row for row in trade_rows if row.get("realized_pnl_only_result") == "FAILED"]
    exact_failures = [row for row in exact if row.get("strict_account_result") == "FAILED"]
    return {
        "strict_exact_known_trades": len(exact),
        "strict_unknown_trades": len(unknown),
        "realized_only_trades": len(trade_rows),
        "strict_exact_failure_trades": len(exact_failures),
        "realized_only_failure_trades": len(realized_only_failures),
        "strict_exact_failure_rate": (len(exact_failures) / len(exact)) if exact else None,
        "strict_unknown_rate": (len(unknown) / len(trade_rows)) if trade_rows else None,
        "realized_only_failure_rate": (len(realized_only_failures) / len(trade_rows)) if trade_rows else None,
    }
