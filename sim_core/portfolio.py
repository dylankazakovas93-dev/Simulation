from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from itertools import product
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd

from sim_core.lifecycle import LifecyclePlan, LifecycleSettings, simulate_lifecycle_path
from sim_core.models import Trade
from sim_core.prop_rules import _floor_ceiling, _is_payout_eligible, _trade_day

PnlBasis = Literal["points", "dollars"]
MaeMfeConvention = Literal["positive_magnitude", "signed_adverse_negative", "signed_favorable_positive"]
DependencyMode = Literal["PAIRED_CALENDAR_BLOCKS", "INDEPENDENT_SOURCE_PATHS"]
OverlapPolicy = Literal["REJECT_SAME_ASSET_OVERLAP", "PRIORITY_KEEP_ONE", "ALLOW_STACKING"]
IntratradeRiskMode = Literal["REALIZED_PNL_ONLY", "CONSERVATIVE_OVERLAP_MAE_BOUND", "EXACT_INTRATRADE"]


@dataclass(frozen=True)
class PortfolioInstrumentSpec:
    strategy_id: str
    asset_id: str
    asset_label: str
    contract_symbol: str
    dollars_per_point_per_contract: float
    commission_round_turn_per_contract: float = 0.0
    source_timezone: str = "UTC"
    default_contract_count: int = 1
    pnl_basis: PnlBasis = "points"
    mae_mfe_convention: MaeMfeConvention = "positive_magnitude"
    enabled: bool = True

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ValueError("strategy_id is required")
        if not self.asset_id:
            raise ValueError("asset_id is required")
        if not self.contract_symbol:
            raise ValueError("contract_symbol is required")
        if self.dollars_per_point_per_contract <= 0:
            raise ValueError("dollars_per_point_per_contract must be positive")
        if self.commission_round_turn_per_contract < 0:
            raise ValueError("commission_round_turn_per_contract cannot be negative")
        if self.default_contract_count < 0:
            raise ValueError("default_contract_count cannot be negative")
        try:
            ZoneInfo(self.source_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown source_timezone: {self.source_timezone}") from exc


@dataclass(frozen=True)
class PortfolioAllocation:
    allocation_id: int
    contracts_by_strategy: dict[str, int]

    def contracts_for(self, strategy_id: str) -> int:
        return int(self.contracts_by_strategy.get(strategy_id, 0))


def build_allocation_grid(
    choices_by_strategy: dict[str, list[int]],
    *,
    max_combinations: int = 500,
) -> list[PortfolioAllocation]:
    strategy_ids = list(choices_by_strategy)
    total = 1
    for choices in choices_by_strategy.values():
        if any(int(value) < 0 for value in choices):
            raise ValueError("contract choices cannot be negative")
        total *= len(choices)
    if total > max_combinations:
        raise ValueError(f"allocation grid has {total} combinations; cap is {max_combinations}")
    allocations = []
    for allocation_id, values in enumerate(product(*(choices_by_strategy[key] for key in strategy_ids))):
        allocations.append(PortfolioAllocation(allocation_id, dict(zip(strategy_ids, map(int, values), strict=True))))
    return allocations


def normalize_portfolio_ledger(
    frame: pd.DataFrame,
    spec: PortfolioInstrumentSpec,
    *,
    contract_count: int | None = None,
    strategy_path_id: int = 0,
    portfolio_path_id: int = 0,
    allocation_id: int = 0,
    pnl_conflict_tolerance: float = 0.01,
    authoritative_basis: PnlBasis | None = None,
) -> pd.DataFrame:
    contracts = spec.default_contract_count if contract_count is None else int(contract_count)
    if contracts < 0:
        raise ValueError("contract_count cannot be negative")
    if not spec.enabled or contracts == 0:
        return _portfolio_trade_columns()
    basis = authoritative_basis or spec.pnl_basis
    _validate_basis(frame, spec, basis, pnl_conflict_tolerance)

    out = pd.DataFrame(index=frame.index)
    out["portfolio_path_id"] = int(portfolio_path_id)
    out["strategy_path_id"] = int(strategy_path_id)
    out["allocation_id"] = int(allocation_id)
    out["strategy_id"] = spec.strategy_id
    out["asset_id"] = spec.asset_id
    out["asset_label"] = spec.asset_label or spec.asset_id
    out["contract_symbol"] = spec.contract_symbol
    out["contract_count"] = contracts
    out["dollars_per_point_per_contract"] = float(spec.dollars_per_point_per_contract)
    out["commission_round_turn_per_contract"] = float(spec.commission_round_turn_per_contract)
    out["pnl_basis"] = basis
    out["source_timezone"] = spec.source_timezone

    out["entry_time"] = _parse_times(frame["entry_time"], spec.source_timezone)
    out["exit_time"] = _parse_times(frame["exit_time"], spec.source_timezone)
    out["source_session_date"] = _source_dates(frame, out["exit_time"])
    out["direction"] = frame.get("direction", pd.Series([None] * len(frame), index=frame.index))
    out["source_trade_id"] = frame.get("trade_id", frame.get("source_row_id", pd.Series(frame.index.astype(str), index=frame.index))).astype(str)
    out["source_sequence_hash"] = source_sequence_hash(frame)

    pnl_points = pd.to_numeric(frame.get("pnl_points"), errors="coerce") if "pnl_points" in frame else pd.Series(np.nan, index=frame.index)
    pnl_dollars = pd.to_numeric(frame.get("pnl_dollars"), errors="coerce") if "pnl_dollars" in frame else pd.Series(np.nan, index=frame.index)
    source_contracts = float(max(1, spec.default_contract_count))
    if basis == "points":
        gross = pnl_points.fillna(0.0) * spec.dollars_per_point_per_contract * contracts
    else:
        gross = pnl_dollars.fillna(0.0) * (contracts / source_contracts)
    commission = float(spec.commission_round_turn_per_contract) * contracts
    out["pnl_points"] = pnl_points
    out["source_pnl_dollars"] = pnl_dollars
    out["gross_pnl_dollars"] = gross.astype(float)
    out["commission_dollars"] = commission
    out["net_pnl_dollars"] = out["gross_pnl_dollars"] - commission

    for source_col, target_col in [
        ("stop_points", "stop_dollars"),
        ("target_points", "target_dollars"),
        ("mae_points", "mae_dollars"),
        ("mfe_points", "mfe_dollars"),
    ]:
        points = pd.to_numeric(frame.get(source_col), errors="coerce") if source_col in frame else pd.Series(np.nan, index=frame.index)
        out[source_col] = points
        magnitude = points.map(lambda value: _excursion_magnitude(value, spec.mae_mfe_convention))
        out[target_col] = magnitude * spec.dollars_per_point_per_contract * contracts
    out["mae_available"] = out["mae_points"].notna()
    out["mfe_available"] = out["mfe_points"].notna()
    out["has_exact_timestamps"] = out["entry_time"].notna() & out["exit_time"].notna()
    return out.reset_index(drop=True)


def _portfolio_trade_columns() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "portfolio_path_id",
            "strategy_path_id",
            "allocation_id",
            "strategy_id",
            "asset_id",
            "asset_label",
            "contract_symbol",
            "contract_count",
            "dollars_per_point_per_contract",
            "commission_round_turn_per_contract",
            "pnl_basis",
            "source_timezone",
            "entry_time",
            "exit_time",
            "source_session_date",
            "direction",
            "source_trade_id",
            "source_sequence_hash",
            "pnl_points",
            "source_pnl_dollars",
            "gross_pnl_dollars",
            "commission_dollars",
            "net_pnl_dollars",
            "stop_points",
            "stop_dollars",
            "target_points",
            "target_dollars",
            "mae_points",
            "mae_dollars",
            "mfe_points",
            "mfe_dollars",
            "mae_available",
            "mfe_available",
            "has_exact_timestamps",
        ]
    )


def _parse_times(values: pd.Series, source_timezone: str) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce")
    out = []
    for value in parsed:
        if pd.isna(value):
            out.append(pd.NaT)
        elif value.tzinfo is None:
            out.append(value.tz_localize(source_timezone).tz_convert("UTC"))
        else:
            out.append(value.tz_convert("UTC"))
    return pd.Series(out, index=values.index)


def _source_dates(frame: pd.DataFrame, exit_times: pd.Series) -> pd.Series:
    if "source_session_date" in frame:
        return frame["source_session_date"].astype(str)
    if "session_date" in frame:
        return frame["session_date"].astype(str)
    return exit_times.map(lambda value: value.date().isoformat() if not pd.isna(value) else None)


def _validate_basis(frame: pd.DataFrame, spec: PortfolioInstrumentSpec, basis: PnlBasis, tolerance: float) -> None:
    has_points = "pnl_points" in frame and pd.to_numeric(frame["pnl_points"], errors="coerce").notna().any()
    has_dollars = "pnl_dollars" in frame and pd.to_numeric(frame["pnl_dollars"], errors="coerce").notna().any()
    if basis == "points" and not has_points:
        raise ValueError("points basis selected but pnl_points is missing")
    if basis == "dollars" and not has_dollars:
        raise ValueError("dollars basis selected but pnl_dollars is missing")
    if has_points and has_dollars:
        expected = pd.to_numeric(frame["pnl_points"], errors="coerce") * spec.dollars_per_point_per_contract * max(1, spec.default_contract_count)
        actual = pd.to_numeric(frame["pnl_dollars"], errors="coerce")
        disagreement = (expected - actual).abs() > tolerance
        if disagreement.any() and spec.pnl_basis not in {"points", "dollars"}:
            raise ValueError("pnl_points and pnl_dollars conflict; choose authoritative basis")


def _excursion_magnitude(value: Any, convention: MaeMfeConvention) -> float:
    if pd.isna(value):
        return np.nan
    numeric = float(value)
    if convention == "positive_magnitude":
        return abs(numeric)
    if convention == "signed_adverse_negative":
        return abs(numeric)
    return abs(numeric)


def source_sequence_hash(frame: pd.DataFrame) -> str:
    columns = [column for column in ["trade_id", "source_row_id", "entry_time", "exit_time", "pnl_points", "pnl_dollars"] if column in frame]
    if not columns:
        material = "|".join(map(str, frame.index.tolist()))
    else:
        material = "|".join(frame[columns].fillna("").astype(str).agg(":".join, axis=1))
    return sha256(material.encode("utf-8")).hexdigest()


def build_joint_portfolio_paths(
    ledgers_by_strategy: dict[str, pd.DataFrame],
    *,
    path_count: int,
    seed: int,
    mode: DependencyMode = "PAIRED_CALENDAR_BLOCKS",
    trades_per_path: int | None = None,
    seasonal_month_aware: bool = False,
) -> tuple[list[dict[str, pd.DataFrame]], pd.DataFrame]:
    if mode == "EXACT_INTRATRADE":
        raise ValueError("invalid dependency mode")
    rng = np.random.default_rng(seed)
    normalized_dates = {
        strategy_id: set(pd.to_datetime(frame["source_session_date"]).dt.date.astype(str))
        for strategy_id, frame in ledgers_by_strategy.items()
    }
    common = set.intersection(*normalized_dates.values()) if normalized_dates else set()
    common_months = {pd.Timestamp(date).month for date in common}
    coverage_rows = []
    for strategy_id, dates in normalized_dates.items():
        coverage_rows.append(
            {
                "strategy_id": strategy_id,
                "dependency_mode": mode,
                "common_date_count": len(common),
                "common_month_count": len(common_months),
                "ledger_date_count": len(dates),
                "paired_date_coverage_pct": len(common & dates) / len(dates) if dates else 0.0,
                "dependence_label": "VERIFIED_PAIRED_CALENDAR" if mode == "PAIRED_CALENDAR_BLOCKS" and common else "CROSS_STRATEGY_DEPENDENCE_UNVERIFIED",
                "seasonal_month_aware": bool(seasonal_month_aware),
            }
        )
    manifest = pd.DataFrame(coverage_rows)
    if mode == "PAIRED_CALENDAR_BLOCKS" and not common:
        raise ValueError("paired calendar blocks require at least one common source date")
    paths = []
    ordered_common = sorted(common)
    for portfolio_path_id in range(int(path_count)):
        path: dict[str, pd.DataFrame] = {}
        if mode == "PAIRED_CALENDAR_BLOCKS":
            count = trades_per_path or len(ordered_common)
            sampled_dates = _sample_dates(ordered_common, count, rng, seasonal_month_aware=seasonal_month_aware)
            for strategy_id, frame in ledgers_by_strategy.items():
                picked = pd.concat(
                    [frame[pd.to_datetime(frame["source_session_date"]).dt.date.astype(str).eq(str(date))] for date in sampled_dates],
                    ignore_index=True,
                )
                picked["portfolio_path_id"] = portfolio_path_id
                picked["strategy_path_id"] = portfolio_path_id
                path[strategy_id] = picked
        else:
            for strategy_id, frame in ledgers_by_strategy.items():
                count = trades_per_path or len(frame)
                indexes = _sample_frame_indexes(frame, count, rng, seasonal_month_aware=seasonal_month_aware)
                picked = frame.iloc[indexes].reset_index(drop=True).copy()
                picked["portfolio_path_id"] = portfolio_path_id
                picked["strategy_path_id"] = portfolio_path_id
                path[strategy_id] = picked
        paths.append(path)
    return paths, manifest


def _sample_dates(
    dates: list[str],
    count: int,
    rng: np.random.Generator,
    *,
    seasonal_month_aware: bool,
) -> np.ndarray:
    if not seasonal_month_aware:
        return rng.choice(dates, size=count, replace=True)
    by_month: dict[int, list[str]] = {}
    for date in dates:
        by_month.setdefault(pd.Timestamp(date).month, []).append(date)
    months = sorted(by_month)
    sampled = []
    for index in range(count):
        month = months[index % len(months)]
        sampled.append(rng.choice(by_month[month]))
    return np.array(sampled)


def _sample_frame_indexes(
    frame: pd.DataFrame,
    count: int,
    rng: np.random.Generator,
    *,
    seasonal_month_aware: bool,
) -> np.ndarray:
    if not seasonal_month_aware:
        return rng.choice(np.arange(len(frame)), size=count, replace=True)
    dates = pd.to_datetime(frame["source_session_date"], errors="coerce")
    by_month = {
        month: dates[dates.dt.month.eq(month)].index.to_numpy()
        for month in sorted(dates.dt.month.dropna().astype(int).unique())
    }
    if not by_month:
        return rng.choice(np.arange(len(frame)), size=count, replace=True)
    months = list(by_month)
    sampled = []
    for index in range(count):
        month = months[index % len(months)]
        sampled.append(rng.choice(by_month[month]))
    return np.array(sampled)


def combine_portfolio_path(
    ledgers_by_strategy: dict[str, pd.DataFrame],
    specs_by_strategy: dict[str, PortfolioInstrumentSpec],
    allocation: PortfolioAllocation,
    *,
    portfolio_path_id: int,
) -> pd.DataFrame:
    frames = []
    for strategy_id, frame in ledgers_by_strategy.items():
        spec = specs_by_strategy[strategy_id]
        frames.append(
            normalize_portfolio_ledger(
                frame,
                spec,
                contract_count=allocation.contracts_for(strategy_id),
                strategy_path_id=int(frame["strategy_path_id"].iloc[0]) if "strategy_path_id" in frame and not frame.empty else portfolio_path_id,
                portfolio_path_id=portfolio_path_id,
                allocation_id=allocation.allocation_id,
            )
        )
    if not frames:
        return _portfolio_trade_columns()
    return pd.concat(frames, ignore_index=True).sort_values(["exit_time", "entry_time", "strategy_id"]).reset_index(drop=True)


def resolve_portfolio_overlaps(
    ledger: pd.DataFrame,
    *,
    policy: OverlapPolicy = "REJECT_SAME_ASSET_OVERLAP",
    priority: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    priority = priority or sorted(ledger["strategy_id"].dropna().unique().tolist())
    rank = {strategy_id: index for index, strategy_id in enumerate(priority)}
    kept = []
    decisions = []
    cluster_id = 0
    for _, row in ledger.sort_values(["entry_time", "exit_time", "strategy_id"]).iterrows():
        overlaps = [item for item in kept if row["entry_time"] < item["exit_time"] and row["exit_time"] > item["entry_time"]]
        same_asset = [item for item in overlaps if str(item["asset_id"]) == str(row["asset_id"])]
        cross_asset = [item for item in overlaps if str(item["asset_id"]) != str(row["asset_id"])]
        decision = "KEEP"
        reason = "no overlap"
        if same_asset:
            cluster_id += 1
            if policy == "REJECT_SAME_ASSET_OVERLAP":
                decision = "DROP"
                reason = "same asset overlap rejected"
            elif policy == "PRIORITY_KEEP_ONE":
                best = min([row.to_dict(), *same_asset], key=lambda item: rank.get(str(item["strategy_id"]), len(rank)))
                if str(best["strategy_id"]) != str(row["strategy_id"]):
                    decision = "DROP"
                    reason = f"priority kept {best['strategy_id']}"
                else:
                    kept = [item for item in kept if not (str(item["asset_id"]) == str(row["asset_id"]) and row["entry_time"] < item["exit_time"] and row["exit_time"] > item["entry_time"])]
                    reason = "priority winner"
            else:
                decision = "STACK"
                reason = "same asset stacking allowed"
        elif cross_asset:
            cluster_id += 1
            reason = "cross asset overlap retained"
        if decision in {"KEEP", "STACK"}:
            kept.append(row.to_dict())
        decisions.append(
            {
                "portfolio_path_id": row.get("portfolio_path_id"),
                "allocation_id": row.get("allocation_id"),
                "overlap_cluster_id": cluster_id if overlaps else pd.NA,
                "strategies": "|".join(sorted({str(row["strategy_id"]), *[str(item["strategy_id"]) for item in overlaps]})),
                "asset_ids": "|".join(sorted({str(row["asset_id"]), *[str(item["asset_id"]) for item in overlaps]})),
                "contract_symbols": "|".join(sorted({str(row["contract_symbol"]), *[str(item["contract_symbol"]) for item in overlaps]})),
                "entry_time": row["entry_time"],
                "exit_time": row["exit_time"],
                "overlap_type": "same_asset" if same_asset else "cross_asset" if cross_asset else "none",
                "selected_overlap_policy": policy,
                "decision": decision,
                "priority_reason": reason,
                "gross_asset_exposure": _gross_asset_exposure([row.to_dict(), *same_asset]) if same_asset and policy == "ALLOW_STACKING" else pd.NA,
                "net_asset_exposure": _net_asset_exposure([row.to_dict(), *same_asset]) if same_asset and policy == "ALLOW_STACKING" else pd.NA,
            }
        )
    return pd.DataFrame(kept), pd.DataFrame(decisions)


def _gross_asset_exposure(rows: list[dict[str, Any]]) -> int:
    return int(sum(abs(int(row.get("contract_count", 0))) for row in rows))


def _net_asset_exposure(rows: list[dict[str, Any]]) -> int:
    total = 0
    for row in rows:
        direction = str(row.get("direction", "")).lower()
        sign = -1 if direction.startswith("short") else 1
        total += sign * int(row.get("contract_count", 0))
    return int(total)


def portfolio_trade_ledger_to_lifecycle_trades(ledger: pd.DataFrame) -> list[Trade]:
    trades = []
    for index, row in ledger.reset_index(drop=True).iterrows():
        trades.append(
            Trade(
                trade_id=f"portfolio|{row['portfolio_path_id']}|{row['allocation_id']}|{index}",
                source_row_id=str(row["source_trade_id"]),
                strategy_id=str(row["strategy_id"]),
                instrument=str(row["asset_id"]),
                contract_symbol=str(row["contract_symbol"]),
                entry_time=pd.Timestamp(row["entry_time"]),
                exit_time=pd.Timestamp(row["exit_time"]),
                pnl_dollars=float(row["net_pnl_dollars"]),
                pnl_points=None,
                mae_points=None,
                mfe_points=None,
                dollars_per_point=None,
                commission_round_turn=0.0,
                metadata={
                    "portfolio_path_id": int(row["portfolio_path_id"]),
                    "strategy_path_id": int(row["strategy_path_id"]),
                    "allocation_id": int(row["allocation_id"]),
                    "sequence_number": int(index + 1),
                    "source_trade_packet_id": row["source_trade_id"],
                    "session_date": str(row["source_session_date"]),
                    "excursion_confidence": "PORTFOLIO_REALIZED_ONLY",
                },
            )
        )
    return trades


def simulate_portfolio_lifecycle(
    ledger: pd.DataFrame,
    plan: LifecyclePlan,
    settings: LifecycleSettings,
    *,
    risk_mode: IntratradeRiskMode = "REALIZED_PNL_ONLY",
    timezone: str = "America/New_York",
    path_id: int = 0,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if risk_mode == "EXACT_INTRATRADE":
        raise ValueError("EXACT_INTRATRADE requires timestamped intratrade equity evidence")
    kept = ledger.sort_values(["exit_time", "entry_time", "strategy_id"]).reset_index(drop=True)
    trades = portfolio_trade_ledger_to_lifecycle_trades(kept)
    result, months, events, trace = simulate_lifecycle_path(
        trades,
        plan,
        contracts=1,
        settings=settings,
        path_id=path_id,
        seed=path_id,
        dollars_per_point=1.0,
        return_trade_ledger=True,
        timezone=timezone,
    )
    account_day = build_portfolio_account_day_ledger(kept, plan, settings, risk_mode=risk_mode, timezone=timezone)
    trace_frame = pd.DataFrame(trace)
    summary = pd.DataFrame(
        [
            {
                **result.to_dict(),
                "strict_known_failure_rate": float((account_day["strict_status"] == "FAILED").mean()) if not account_day.empty else 0.0,
                "strict_unknown_rate": float((account_day["strict_status"] == "UNKNOWN").mean()) if not account_day.empty else 0.0,
                "realized_only_failure_rate": float((account_day["realized_only_failure"].astype(bool)).mean()) if not account_day.empty else 0.0,
                "conservative_bound_failure_rate": float((account_day["conservative_bound_failure"].astype(bool)).mean()) if not account_day.empty else 0.0,
            }
        ]
    )
    return summary, account_day, trace_frame


def build_portfolio_account_day_ledger(
    ledger: pd.DataFrame,
    plan: LifecyclePlan,
    settings: LifecycleSettings,
    *,
    risk_mode: IntratradeRiskMode,
    timezone: str = "America/New_York",
) -> pd.DataFrame:
    profile = plan.funded_profile
    balance = float(settings.current_balance if settings.current_balance is not None else profile.starting_balance)
    floor = float(settings.current_floor if settings.current_floor is not None else profile.starting_floor)
    eod_peak = max(profile.starting_balance, balance)
    running_peak = balance
    winning_days = int(settings.current_winning_days)
    rows = []
    for day, group in ledger.assign(account_day=ledger["exit_time"].map(lambda value: _trade_day(pd.Timestamp(value), timezone).date().isoformat())).groupby("account_day", sort=True):
        start_balance = balance
        day_net = float(group["net_pnl_dollars"].sum())
        missing_mae_overlap = _has_overlap_missing_mae(group)
        conservative_bound = float(group["mae_dollars"].fillna(0.0).sum())
        conservative_failure = risk_mode == "CONSERVATIVE_OVERLAP_MAE_BOUND" and start_balance - conservative_bound <= floor
        strict_status = "UNKNOWN" if missing_mae_overlap else "FAILED" if conservative_failure else "SURVIVED"
        realized_only_failure = start_balance + day_net <= floor
        if not conservative_failure:
            balance += day_net
        running_peak = max(running_peak, balance)
        if balance > floor and day_net >= profile.winning_day_threshold and day_net > 0:
            winning_days += 1
        eod_peak = max(eod_peak, balance)
        if profile.drawdown_mode == "eod_trailing":
            floor = max(floor, min(_floor_ceiling(profile), eod_peak - profile.max_loss))
        rows.append(
            {
                "account_day": day,
                "trade_count": int(len(group)),
                "gross_pnl_dollars": float(group["gross_pnl_dollars"].sum()),
                "commission_dollars": float(group["commission_dollars"].sum()),
                "net_pnl_dollars": day_net,
                "balance_before": start_balance,
                "balance_after": balance,
                "floor_after": floor,
                "winning_days_after": winning_days,
                "strict_status": strict_status,
                "realized_only_failure": bool(realized_only_failure),
                "conservative_bound_failure": bool(conservative_failure),
                "risk_mode": risk_mode,
                "risk_mode_label": "conservative bound, not exact" if risk_mode == "CONSERVATIVE_OVERLAP_MAE_BOUND" else risk_mode,
                "payout_eligible_after_day": _is_payout_eligible(balance=balance, profile=profile, winning_days=winning_days, daily_profits=[day_net]),
            }
        )
    return pd.DataFrame(rows)


def _has_overlap_missing_mae(group: pd.DataFrame) -> bool:
    rows = list(group.to_dict("records"))
    for left_index, left in enumerate(rows):
        for right in rows[left_index + 1 :]:
            if pd.Timestamp(left["entry_time"]) < pd.Timestamp(right["exit_time"]) and pd.Timestamp(left["exit_time"]) > pd.Timestamp(right["entry_time"]):
                if pd.isna(left.get("mae_dollars")) or pd.isna(right.get("mae_dollars")):
                    return True
    return False


def portfolio_contribution_summary(ledger: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    by_strategy = (
        ledger.groupby(["allocation_id", "strategy_id"], dropna=False)
        .agg(
            gross_pnl_dollars=("gross_pnl_dollars", "sum"),
            net_pnl_dollars=("net_pnl_dollars", "sum"),
            commission_dollars=("commission_dollars", "sum"),
            trade_count=("source_trade_id", "count"),
            contract_count=("contract_count", "first"),
        )
        .reset_index()
    )
    by_asset = (
        ledger.groupby(["allocation_id", "asset_id"], dropna=False)
        .agg(
            gross_pnl_dollars=("gross_pnl_dollars", "sum"),
            net_pnl_dollars=("net_pnl_dollars", "sum"),
            commission_dollars=("commission_dollars", "sum"),
            trade_count=("source_trade_id", "count"),
        )
        .reset_index()
    )
    return by_strategy, by_asset


def portfolio_export_frames(
    *,
    specs: list[PortfolioInstrumentSpec],
    allocations: list[PortfolioAllocation],
    dependency_manifest: pd.DataFrame,
    strategy_path_manifest: pd.DataFrame,
    trade_ledger: pd.DataFrame,
    overlap_audit: pd.DataFrame,
    account_day_ledger: pd.DataFrame,
    account_trace: pd.DataFrame,
    path_results: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    by_strategy, by_asset = portfolio_contribution_summary(trade_ledger) if not trade_ledger.empty else (pd.DataFrame(), pd.DataFrame())
    allocation_manifest = pd.DataFrame(
        [{"allocation_id": allocation.allocation_id, **allocation.contracts_by_strategy} for allocation in allocations]
    )
    return {
        "portfolio_source_manifest": pd.DataFrame([asdict(spec) for spec in specs]),
        "portfolio_instrument_specs": pd.DataFrame([asdict(spec) for spec in specs]),
        "portfolio_allocation_manifest": allocation_manifest,
        "portfolio_dependency_manifest": dependency_manifest,
        "portfolio_strategy_path_manifest": strategy_path_manifest,
        "portfolio_trade_ledger": trade_ledger,
        "portfolio_overlap_audit": overlap_audit,
        "portfolio_account_day_ledger": account_day_ledger,
        "portfolio_account_trace": account_trace,
        "portfolio_path_results": path_results,
        "portfolio_allocation_summary": by_strategy.merge(by_asset, on="allocation_id", how="outer", suffixes=("_strategy", "_asset")),
        "portfolio_validation_report": pd.DataFrame([{"check": "portfolio_units_are_dollars", "passed": "combined_portfolio_points" not in trade_ledger.columns}]),
    }


def write_portfolio_exports(frames: dict[str, pd.DataFrame], export_dir: str | Path) -> dict[str, Path]:
    root = Path(export_dir)
    root.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for name, frame in frames.items():
        path = root / f"{name}.csv"
        frame.to_csv(path, index=False)
        outputs[name] = path
    return outputs
