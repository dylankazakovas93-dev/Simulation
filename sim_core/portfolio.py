from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from itertools import product
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd

from sim_core.lifecycle import LifecyclePlan, LifecycleSettings
from sim_core.models import Trade
from sim_core.prop_rules import (
    calculate_payout_decision,
    _floor_ceiling,
    _is_payout_eligible,
    _trade_day,
)

PnlBasis = Literal["points", "dollars"]
MaeMfeConvention = Literal[
    "POSITIVE_MAGNITUDES",
    "SIGNED_MAE_NEGATIVE_MFE_POSITIVE",
    "positive_magnitude",
    "signed_adverse_negative",
    "signed_favorable_positive",
]
DependencyMode = Literal["PAIRED_CALENDAR_BLOCKS", "INDEPENDENT_SOURCE_PATHS"]
OverlapPolicy = Literal["REJECT_SAME_ASSET_OVERLAP", "PRIORITY_KEEP_ONE", "ALLOW_STACKING"]
IntratradeRiskMode = Literal["REALIZED_PNL_ONLY", "CONSERVATIVE_OVERLAP_MAE_BOUND", "EXACT_INTRATRADE"]

CANONICAL_ASSET_BY_CONTRACT = {
    "MNQ": "NQ",
    "NQ": "NQ",
    "MES": "ES",
    "ES": "ES",
    "MGC": "GC",
    "GC": "GC",
}


def portfolio_lifecycle_plan_unsupported_reason(plan: LifecyclePlan) -> str | None:
    if plan.eval_profile is not None:
        return "Portfolio Builder supports funded-only lifecycle plans; eval-to-funded routing is not implemented."
    if plan.funded_profile.drawdown_mode == "intraday_trailing":
        return "Intraday-trailing funded plans require genuine intratrade evidence and are not supported by the portfolio lifecycle engine."
    return None


def is_supported_portfolio_lifecycle_plan(plan: LifecyclePlan) -> bool:
    return portfolio_lifecycle_plan_unsupported_reason(plan) is None


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
    source_contract_count: int = 1
    pnl_basis: PnlBasis = "points"
    pnl_basis_confirmed: bool = False
    mae_mfe_convention: MaeMfeConvention = "positive_magnitude"
    mae_mfe_convention_override: bool = False
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
        if self.source_contract_count <= 0:
            raise ValueError("source_contract_count must be positive")
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
        source_contracts = float(spec.source_contract_count)
        gross = pnl_dollars.fillna(0.0) / source_contracts * contracts
    commission = float(spec.commission_round_turn_per_contract) * contracts
    expected_source_dollars = pnl_points * spec.dollars_per_point_per_contract * float(spec.source_contract_count)
    disagreement_abs = (expected_source_dollars - pnl_dollars).abs()
    disagreement_pct = disagreement_abs / pnl_dollars.abs().replace(0, np.nan)
    out["pnl_points"] = pnl_points
    out["source_pnl_dollars"] = pnl_dollars
    out["expected_source_pnl_dollars"] = expected_source_dollars
    out["pnl_dollar_disagreement_abs"] = disagreement_abs
    out["pnl_dollar_disagreement_pct"] = disagreement_pct
    out["gross_pnl_dollars"] = gross.astype(float)
    out["commission_dollars"] = commission
    out["net_pnl_dollars"] = out["gross_pnl_dollars"] - commission

    stop_points = pd.to_numeric(frame.get("stop_points"), errors="coerce") if "stop_points" in frame else pd.Series(np.nan, index=frame.index)
    target_points = pd.to_numeric(frame.get("target_points"), errors="coerce") if "target_points" in frame else pd.Series(np.nan, index=frame.index)
    mae_points = pd.to_numeric(frame.get("mae_points"), errors="coerce") if "mae_points" in frame else pd.Series(np.nan, index=frame.index)
    mfe_points = pd.to_numeric(frame.get("mfe_points"), errors="coerce") if "mfe_points" in frame else pd.Series(np.nan, index=frame.index)
    adverse_abs, favorable_abs = _normalize_mae_mfe(mae_points, mfe_points, spec)
    out["stop_points"] = stop_points
    out["stop_dollars"] = stop_points.abs() * spec.dollars_per_point_per_contract * contracts
    out["target_points"] = target_points
    out["target_dollars"] = target_points.abs() * spec.dollars_per_point_per_contract * contracts
    out["mae_points"] = mae_points
    out["mfe_points"] = mfe_points
    out["adverse_excursion_points_abs"] = adverse_abs
    out["favorable_excursion_points_abs"] = favorable_abs
    out["mae_dollars_abs"] = adverse_abs * spec.dollars_per_point_per_contract * contracts
    out["mfe_dollars_abs"] = favorable_abs * spec.dollars_per_point_per_contract * contracts
    out["mae_dollars"] = out["mae_dollars_abs"]
    out["mfe_dollars"] = out["mfe_dollars_abs"]
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
            "expected_source_pnl_dollars",
            "pnl_dollar_disagreement_abs",
            "pnl_dollar_disagreement_pct",
            "gross_pnl_dollars",
            "commission_dollars",
            "net_pnl_dollars",
            "stop_points",
            "stop_dollars",
            "target_points",
            "target_dollars",
            "mae_points",
            "adverse_excursion_points_abs",
            "mae_dollars",
            "mae_dollars_abs",
            "mfe_points",
            "favorable_excursion_points_abs",
            "mfe_dollars",
            "mfe_dollars_abs",
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
        expected = pd.to_numeric(frame["pnl_points"], errors="coerce") * spec.dollars_per_point_per_contract * float(spec.source_contract_count)
        actual = pd.to_numeric(frame["pnl_dollars"], errors="coerce")
        disagreement = (expected - actual).abs() > tolerance
        if disagreement.any() and not spec.pnl_basis_confirmed:
            raise ValueError("pnl_points and pnl_dollars conflict; explicitly confirm authoritative P&L basis")


def _canonical_mae_mfe_convention(convention: MaeMfeConvention) -> str:
    mapping = {
        "positive_magnitude": "POSITIVE_MAGNITUDES",
        "signed_adverse_negative": "SIGNED_MAE_NEGATIVE_MFE_POSITIVE",
        "signed_favorable_positive": "SIGNED_MAE_NEGATIVE_MFE_POSITIVE",
    }
    return mapping.get(str(convention), str(convention))


def _normalize_mae_mfe(
    mae_points: pd.Series,
    mfe_points: pd.Series,
    spec: PortfolioInstrumentSpec,
) -> tuple[pd.Series, pd.Series]:
    convention = _canonical_mae_mfe_convention(spec.mae_mfe_convention)
    if convention == "POSITIVE_MAGNITUDES":
        invalid = (mae_points.dropna() < 0).any() or (mfe_points.dropna() < 0).any()
        if invalid and not spec.mae_mfe_convention_override:
            raise ValueError("POSITIVE_MAGNITUDES requires MAE >= 0 and MFE >= 0")
        return mae_points.abs(), mfe_points.abs()
    if convention == "SIGNED_MAE_NEGATIVE_MFE_POSITIVE":
        invalid = (mae_points.dropna() > 0).any() or (mfe_points.dropna() < 0).any()
        if invalid and not spec.mae_mfe_convention_override:
            raise ValueError("SIGNED_MAE_NEGATIVE_MFE_POSITIVE requires MAE <= 0 and MFE >= 0")
        return mae_points.abs(), mfe_points.abs()
    raise ValueError(f"unsupported MAE/MFE convention: {spec.mae_mfe_convention}")


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
    forecast_start_date: str = "2026-01-02",
) -> tuple[list[dict[str, pd.DataFrame]], pd.DataFrame]:
    if mode == "EXACT_INTRATRADE":
        raise ValueError("invalid dependency mode")
    rng = np.random.default_rng(seed)
    normalized_dates = {
        strategy_id: set(pd.to_datetime(frame["source_session_date"]).dt.date.astype(str))
        for strategy_id, frame in ledgers_by_strategy.items()
    }
    union_dates = set.union(*normalized_dates.values()) if normalized_dates else set()
    full_common = set.intersection(*normalized_dates.values()) if normalized_dates else set()
    coactive = {
        date for date in union_dates
        if sum(date in dates for dates in normalized_dates.values()) >= 2
    }
    common_months = {pd.Timestamp(date).month for date in full_common}
    coverage_rows = []
    for strategy_id, dates in normalized_dates.items():
        coverage_rows.append(
            {
                "strategy_id": strategy_id,
                "dependency_mode": mode,
                "union_date_count": len(union_dates),
                "full_common_date_count": len(full_common),
                "common_date_count": len(full_common),
                "coactive_date_count": len(coactive),
                "common_month_count": len(common_months),
                "active_date_count": len(dates),
                "ledger_date_count": len(dates),
                "union_coverage_pct": len(dates & union_dates) / len(union_dates) if union_dates else 0.0,
                "coactive_coverage_pct": len(dates & coactive) / len(dates) if dates else 0.0,
                "paired_date_coverage_pct": len(dates & full_common) / len(dates) if dates else 0.0,
                "dependence_label": "VERIFIED_PAIRED_CALENDAR" if mode == "PAIRED_CALENDAR_BLOCKS" and union_dates else "CROSS_STRATEGY_DEPENDENCE_UNVERIFIED",
                "seasonal_month_aware": bool(seasonal_month_aware),
            }
        )
    manifest = pd.DataFrame(coverage_rows)
    if mode == "PAIRED_CALENDAR_BLOCKS" and not union_dates:
        raise ValueError("paired calendar blocks require at least one source date")
    paths = []
    ordered_union = sorted(union_dates)
    for portfolio_path_id in range(int(path_count)):
        path: dict[str, pd.DataFrame] = {}
        count = trades_per_path or (len(ordered_union) if mode == "PAIRED_CALENDAR_BLOCKS" else max(len(frame) for frame in ledgers_by_strategy.values()))
        forecast_dates = _forecast_account_dates(forecast_start_date, count)
        if mode == "PAIRED_CALENDAR_BLOCKS":
            sampled_dates = _sample_dates_for_forecast(ordered_union, forecast_dates, rng, seasonal_month_aware=seasonal_month_aware)
            per_strategy_rows: dict[str, list[pd.DataFrame]] = {strategy_id: [] for strategy_id in ledgers_by_strategy}
            for block_occurrence_id, (source_date, synthetic_date) in enumerate(zip(sampled_dates, forecast_dates, strict=True)):
                for strategy_id, frame in ledgers_by_strategy.items():
                    mask = pd.to_datetime(frame["source_session_date"]).dt.date.astype(str).eq(str(source_date))
                    block = frame[mask].copy()
                    if block.empty:
                        continue
                    block = _shift_block_to_synthetic_date(block, str(source_date), str(synthetic_date))
                    block["portfolio_path_id"] = portfolio_path_id
                    block["strategy_path_id"] = portfolio_path_id
                    block["block_occurrence_id"] = block_occurrence_id
                    per_strategy_rows[strategy_id].append(block)
            for strategy_id in ledgers_by_strategy:
                picked = pd.concat(per_strategy_rows[strategy_id], ignore_index=True) if per_strategy_rows[strategy_id] else ledgers_by_strategy[strategy_id].head(0).copy()
                picked["portfolio_path_id"] = portfolio_path_id
                picked["strategy_path_id"] = portfolio_path_id
                path[strategy_id] = picked
        else:
            for strategy_id, frame in ledgers_by_strategy.items():
                indexes = _sample_frame_indexes_for_forecast(frame, forecast_dates, rng, seasonal_month_aware=seasonal_month_aware)
                picked = frame.iloc[indexes].reset_index(drop=True).copy()
                shifted = []
                for block_occurrence_id, (idx, synthetic_date) in enumerate(zip(indexes, forecast_dates, strict=True)):
                    source_date = str(pd.to_datetime(frame.iloc[int(idx)]["source_session_date"]).date())
                    row = _shift_block_to_synthetic_date(frame.iloc[[int(idx)]].copy(), source_date, str(synthetic_date))
                    row["block_occurrence_id"] = block_occurrence_id
                    shifted.append(row)
                picked = pd.concat(shifted, ignore_index=True) if shifted else picked
                picked["portfolio_path_id"] = portfolio_path_id
                picked["strategy_path_id"] = portfolio_path_id
                path[strategy_id] = picked
        _assert_unique_synthetic_dates(path)
        paths.append(path)
    return paths, manifest


def _forecast_account_dates(start_date: str, count: int) -> list[str]:
    return [str(day.date()) for day in pd.bdate_range(start=start_date, periods=count)]


def _sample_dates_for_forecast(
    dates: list[str],
    forecast_dates: list[str],
    rng: np.random.Generator,
    *,
    seasonal_month_aware: bool,
) -> np.ndarray:
    if not seasonal_month_aware:
        return rng.choice(dates, size=len(forecast_dates), replace=True)
    by_month: dict[int, list[str]] = {}
    for date in dates:
        by_month.setdefault(pd.Timestamp(date).month, []).append(date)
    sampled = []
    for forecast_date in forecast_dates:
        month = pd.Timestamp(forecast_date).month
        if month not in by_month:
            raise ValueError(f"seasonal sampling has no source block for forecast month {month}")
        sampled.append(rng.choice(by_month[month]))
    return np.array(sampled)


def _sample_frame_indexes_for_forecast(
    frame: pd.DataFrame,
    forecast_dates: list[str],
    rng: np.random.Generator,
    *,
    seasonal_month_aware: bool,
) -> np.ndarray:
    if not seasonal_month_aware:
        return rng.choice(np.arange(len(frame)), size=len(forecast_dates), replace=True)
    dates = pd.to_datetime(frame["source_session_date"], errors="coerce")
    by_month = {
        month: dates[dates.dt.month.eq(month)].index.to_numpy()
        for month in sorted(dates.dt.month.dropna().astype(int).unique())
    }
    if not by_month:
        return rng.choice(np.arange(len(frame)), size=len(forecast_dates), replace=True)
    sampled = []
    for forecast_date in forecast_dates:
        month = pd.Timestamp(forecast_date).month
        if month not in by_month:
            raise ValueError(f"seasonal sampling has no source row for forecast month {month}")
        sampled.append(rng.choice(by_month[month]))
    return np.array(sampled)


def _shift_block_to_synthetic_date(frame: pd.DataFrame, source_date: str, synthetic_date: str) -> pd.DataFrame:
    out = frame.copy()
    source_midnight = pd.Timestamp(source_date)
    synthetic_midnight = pd.Timestamp(synthetic_date)
    offset = synthetic_midnight - source_midnight
    out["original_source_session_date"] = out["source_session_date"].astype(str)
    out["synthetic_account_date"] = synthetic_date
    out["source_session_date"] = synthetic_date
    for column in ["entry_time", "exit_time"]:
        parsed = pd.to_datetime(out[column], errors="coerce")
        out[column] = parsed + offset
    return out


def _assert_unique_synthetic_dates(path: dict[str, pd.DataFrame]) -> None:
    occurrence_dates = {}
    intervals = []
    for frame in path.values():
        if frame.empty:
            continue
        if {"block_occurrence_id", "synthetic_account_date"} <= set(frame.columns):
            for item in frame[["block_occurrence_id", "synthetic_account_date"]].drop_duplicates().itertuples(index=False):
                occurrence_id = int(item.block_occurrence_id)
                synthetic_date = str(item.synthetic_account_date)
                if occurrence_id in occurrence_dates and occurrence_dates[occurrence_id] != synthetic_date:
                    raise ValueError("block occurrence maps to multiple synthetic account dates")
                occurrence_dates[occurrence_id] = synthetic_date
        for row in frame.itertuples(index=False):
            intervals.append((pd.Timestamp(row.entry_time), pd.Timestamp(row.exit_time), str(row.synthetic_account_date)))
    dates = list(occurrence_dates.values())
    if len(dates) != len(set(dates)):
        raise ValueError("duplicate synthetic account dates in portfolio path")
    for left_index, left in enumerate(intervals):
        for right in intervals[left_index + 1:]:
            if left[2] != right[2] and left[0] < right[1] and left[1] > right[0]:
                raise ValueError("portfolio blocks overlap across separate synthetic account dates")


def canonical_asset_suggestion(contract_symbol: str, asset_id: str) -> dict[str, str | bool]:
    symbol = str(contract_symbol or "").strip().upper()
    entered = str(asset_id or "").strip().upper()
    suggested = CANONICAL_ASSET_BY_CONTRACT.get(symbol, entered)
    warning = bool(suggested and entered and suggested != entered)
    return {
        "contract_symbol": symbol,
        "entered_asset_id": entered,
        "suggested_asset_id": suggested,
        "warning": warning,
        "message": f"{symbol} normally maps to underlying asset {suggested}; entered asset is {entered}."
        if warning
        else "",
    }


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
    if ledger.empty:
        return ledger.copy(), pd.DataFrame()
    priority = priority or sorted(ledger["strategy_id"].dropna().unique().tolist())
    rank = {strategy_id: index for index, strategy_id in enumerate(priority)}
    working = ledger.reset_index(drop=True).copy()
    working["_row_id"] = working.index
    decisions = []
    keep_ids: set[int] = set()
    cluster_id = 0
    for asset_id, asset_rows in working.groupby("asset_id", sort=False):
        ordered = asset_rows.sort_values(["entry_time", "exit_time", "strategy_id", "_row_id"])
        current: list[dict[str, Any]] = []
        current_end: pd.Timestamp | None = None

        def flush_cluster(rows: list[dict[str, Any]]) -> None:
            nonlocal cluster_id
            if not rows:
                return
            overlap_exists = len(rows) > 1
            cid = pd.NA
            if overlap_exists:
                cluster_id += 1
                cid = cluster_id
            if not overlap_exists:
                winners = {int(rows[0]["_row_id"])}
                reason_by_row = {int(rows[0]["_row_id"]): "no overlap"}
                decision_by_row = {int(rows[0]["_row_id"]): "KEEP"}
            elif policy == "REJECT_SAME_ASSET_OVERLAP":
                selected: list[dict[str, Any]] = []
                reject_conflict: dict[int, dict[str, Any]] = {}
                for item in sorted(rows, key=lambda row: (pd.Timestamp(row["entry_time"]), pd.Timestamp(row["exit_time"]), str(row["strategy_id"]))):
                    conflict = next((kept for kept in selected if _rows_overlap(item, kept)), None)
                    if conflict is None:
                        selected.append(item)
                    else:
                        reject_conflict[int(item["_row_id"])] = conflict
                winners = {int(item["_row_id"]) for item in selected}
                reason_by_row = {}
                for item in rows:
                    row_id = int(item["_row_id"])
                    if row_id in winners:
                        reason_by_row[row_id] = "first non-overlapping interval kept"
                    else:
                        conflict = reject_conflict[row_id]
                        reason_by_row[row_id] = f"same asset overlap rejected by {conflict['strategy_id']}"
                decision_by_row = {int(item["_row_id"]): "KEEP" if int(item["_row_id"]) in winners else "DROP" for item in rows}
            elif policy == "PRIORITY_KEEP_ONE":
                selected = []
                reject_conflict = {}
                for item in sorted(
                    rows,
                    key=lambda row: (
                        rank.get(str(row["strategy_id"]), len(rank)),
                        pd.Timestamp(row["entry_time"]),
                        pd.Timestamp(row["exit_time"]),
                        str(row["strategy_id"]),
                    ),
                ):
                    conflict = next((kept for kept in selected if _rows_overlap(item, kept)), None)
                    if conflict is None:
                        selected.append(item)
                    else:
                        reject_conflict[int(item["_row_id"])] = conflict
                winners = {int(item["_row_id"]) for item in selected}
                reason_by_row = {}
                for item in rows:
                    row_id = int(item["_row_id"])
                    if row_id in winners:
                        reason_by_row[row_id] = "priority winner"
                    else:
                        conflict = reject_conflict[row_id]
                        reason_by_row[row_id] = f"priority kept {conflict['strategy_id']}"
                decision_by_row = {int(item["_row_id"]): "KEEP" if int(item["_row_id"]) in winners else "DROP" for item in rows}
            else:
                winners = {int(item["_row_id"]) for item in rows}
                reason_by_row = {int(item["_row_id"]): "same asset stacking allowed" for item in rows}
                decision_by_row = {int(item["_row_id"]): "STACK" for item in rows}
            keep_ids.update(winners)
            rows_for_exposure = [dict(item) for item in rows]
            for item in rows:
                decisions.append(
                    {
                        "portfolio_path_id": item.get("portfolio_path_id"),
                        "allocation_id": item.get("allocation_id"),
                        "overlap_cluster_id": cid,
                        "strategies": "|".join(sorted({str(member["strategy_id"]) for member in rows})),
                        "asset_ids": str(asset_id),
                        "contract_symbols": "|".join(sorted({str(member["contract_symbol"]) for member in rows})),
                        "entry_time": item["entry_time"],
                        "exit_time": item["exit_time"],
                        "overlap_type": "same_asset" if overlap_exists else "none",
                        "selected_overlap_policy": policy,
                        "decision": decision_by_row[int(item["_row_id"])],
                        "priority_reason": reason_by_row[int(item["_row_id"])],
                        "gross_asset_exposure": _gross_asset_exposure(rows_for_exposure) if overlap_exists and policy == "ALLOW_STACKING" else pd.NA,
                        "net_asset_exposure": _net_asset_exposure(rows_for_exposure) if overlap_exists and policy == "ALLOW_STACKING" else pd.NA,
                    }
                )

        for row in ordered.to_dict("records"):
            row_end = pd.Timestamp(row["exit_time"])
            row_entry = pd.Timestamp(row["entry_time"])
            if current and current_end is not None and row_entry >= current_end:
                flush_cluster(current)
                current = []
                current_end = None
            current.append(row)
            current_end = row_end if current_end is None else max(current_end, row_end)
        flush_cluster(current)

    kept = working[working["_row_id"].isin(keep_ids)].drop(columns=["_row_id"]).sort_values(["exit_time", "entry_time", "strategy_id"]).reset_index(drop=True)
    audit = pd.DataFrame(decisions).sort_values(["entry_time", "exit_time", "strategies"]).reset_index(drop=True)
    cross_asset_audit = _cross_asset_overlap_audit(kept, policy, cluster_id)
    if not cross_asset_audit.empty:
        audit = pd.concat([audit, cross_asset_audit], ignore_index=True, sort=False)
    return kept, audit


def _cross_asset_overlap_audit(kept: pd.DataFrame, policy: OverlapPolicy, starting_cluster_id: int) -> pd.DataFrame:
    rows = kept.sort_values(["entry_time", "exit_time", "strategy_id"]).to_dict("records")
    decisions = []
    cluster_id = starting_cluster_id
    for left_index, left in enumerate(rows):
        for right in rows[left_index + 1:]:
            if str(left["asset_id"]) == str(right["asset_id"]):
                continue
            if pd.Timestamp(left["entry_time"]) < pd.Timestamp(right["exit_time"]) and pd.Timestamp(left["exit_time"]) > pd.Timestamp(right["entry_time"]):
                cluster_id += 1
                members = [left, right]
                for item in members:
                    decisions.append(
                        {
                            "portfolio_path_id": item.get("portfolio_path_id"),
                            "allocation_id": item.get("allocation_id"),
                            "overlap_cluster_id": cluster_id,
                            "strategies": "|".join(sorted({str(member["strategy_id"]) for member in members})),
                            "asset_ids": "|".join(sorted({str(member["asset_id"]) for member in members})),
                            "contract_symbols": "|".join(sorted({str(member["contract_symbol"]) for member in members})),
                            "entry_time": item["entry_time"],
                            "exit_time": item["exit_time"],
                            "overlap_type": "cross_asset",
                            "selected_overlap_policy": policy,
                            "decision": "KEEP",
                            "priority_reason": "cross asset overlap retained",
                            "gross_asset_exposure": pd.NA,
                            "net_asset_exposure": pd.NA,
                        }
                    )
    return pd.DataFrame(decisions)


def _rows_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return pd.Timestamp(left["entry_time"]) < pd.Timestamp(right["exit_time"]) and pd.Timestamp(left["exit_time"]) > pd.Timestamp(right["entry_time"])


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
    unsupported = portfolio_lifecycle_plan_unsupported_reason(plan)
    if unsupported is not None:
        raise ValueError(f"unsupported portfolio lifecycle plan: {unsupported}")
    return _simulate_portfolio_state(ledger, plan, settings, risk_mode=risk_mode, timezone=timezone, path_id=path_id)


def build_portfolio_account_day_ledger(
    ledger: pd.DataFrame,
    plan: LifecyclePlan,
    settings: LifecycleSettings,
    *,
    risk_mode: IntratradeRiskMode,
    timezone: str = "America/New_York",
) -> pd.DataFrame:
    if risk_mode == "EXACT_INTRATRADE":
        raise ValueError("EXACT_INTRATRADE requires timestamped intratrade equity evidence")
    unsupported = portfolio_lifecycle_plan_unsupported_reason(plan)
    if unsupported is not None:
        raise ValueError(f"unsupported portfolio lifecycle plan: {unsupported}")
    _summary, account_day, _trace = _simulate_portfolio_state(ledger, plan, settings, risk_mode=risk_mode, timezone=timezone, path_id=0)
    return account_day


def _simulate_portfolio_state(
    ledger: pd.DataFrame,
    plan: LifecyclePlan,
    settings: LifecycleSettings,
    *,
    risk_mode: IntratradeRiskMode,
    timezone: str,
    path_id: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    profile = plan.funded_profile
    balance = float(settings.current_balance if settings.current_balance is not None else profile.starting_balance)
    floor = float(settings.current_floor if settings.current_floor is not None else profile.starting_floor)
    eod_peak = max(profile.starting_balance, balance)
    running_peak = balance
    winning_days = int(settings.current_winning_days)
    daily_profits = list(float(value) for value in settings.current_daily_profits)
    if settings.current_highest_winning_day > 0 and (
        not daily_profits or float(settings.current_highest_winning_day) > max(daily_profits)
    ):
        daily_profits.append(float(settings.current_highest_winning_day))
    total_payouts = 0.0
    total_fees = max(0.0, float(settings.prior_fees))
    payouts_taken = max(0, int(settings.payouts_already_taken))
    first_payout_day: int | None = None
    first_payout_order: int | None = None
    cushion_ok_after_payout = False
    failed = False
    failure_reason: str | None = None
    first_failure_day: int | None = None
    first_failure_order: int | None = None
    event_order = 0
    max_drawdown = 0.0
    trace_rows = []
    rows = []
    if ledger.empty:
        summary = _portfolio_summary_row(
            plan,
            settings,
            path_id=path_id,
            failed=False,
            payouts_taken=payouts_taken,
            total_payouts=total_payouts,
            total_fees=total_fees,
            ending_balance=balance,
            ending_floor=floor,
            max_drawdown=0.0,
            first_failure_day=None,
            first_failure_order=None,
            first_payout_day=None,
            first_payout_order=None,
            cushion_ok_after_payout=False,
            account_day=pd.DataFrame(),
        )
        return pd.DataFrame([summary]), pd.DataFrame(), pd.DataFrame()

    work = ledger.sort_values(["exit_time", "entry_time", "strategy_id"]).reset_index(drop=True).copy()
    work["account_day"] = work["exit_time"].map(lambda value: _trade_day(pd.Timestamp(value), timezone).date().isoformat())
    clusters = _risk_clusters(work) if risk_mode == "CONSERVATIVE_OVERLAP_MAE_BOUND" else []
    evaluated_clusters: set[int] = set()

    def next_event() -> int:
        nonlocal event_order
        event_order += 1
        return event_order

    def fail_now(day_number: int, reason: str, when: pd.Timestamp) -> None:
        nonlocal failed, failure_reason, first_failure_day, first_failure_order
        if failed:
            return
        failed = True
        failure_reason = reason
        order = next_event()
        first_failure_day = day_number
        first_failure_order = order
        trace_rows.append(
            {
                "path_id": path_id,
                "plan_key": plan.key,
                "firm": plan.firm,
                "account": plan.account_name,
                "record_type": "failure",
                "event_order": order,
                "session_date": str(_trade_day(when, timezone).date()),
                "entry_time": pd.NaT,
                "exit_time": when,
                "balance_before": balance,
                "pnl_dollars": 0.0,
                "balance_after": balance,
                "floor": floor,
                "failure": True,
                "failure_reason": reason,
                "gross_account_debit": 0.0,
                "trader_cash": 0.0,
                "total_payouts": total_payouts,
                "total_fees": total_fees,
                "net_cash": total_payouts - total_fees,
            }
        )

    def evaluate_due_clusters(exit_time: pd.Timestamp, day_number: int) -> None:
        nonlocal max_drawdown
        if risk_mode != "CONSERVATIVE_OVERLAP_MAE_BOUND" or failed:
            return
        for cluster in clusters:
            if cluster["cluster_id"] in evaluated_clusters or cluster["start_time"] > exit_time:
                continue
            evaluated_clusters.add(int(cluster["cluster_id"]))
            bound = float(cluster["max_concurrent_mae"])
            max_drawdown = max(max_drawdown, bound)
            if bool(cluster["missing_mae"]):
                continue
            if balance - bound <= floor:
                fail_now(day_number, "conservative_overlap_mae_bound", pd.Timestamp(cluster["start_time"]))
                return

    for day_number, (day, group) in enumerate(work.groupby("account_day", sort=True), start=1):
        if failed:
            for _, trade in group.sort_values(["exit_time", "entry_time", "strategy_id"]).iterrows():
                trace_rows.append(
                    _portfolio_trace_trade_row(
                        path_id,
                        plan,
                        trade,
                        balance,
                        balance,
                        floor,
                        next_event(),
                        skipped=True,
                        total_payouts=total_payouts,
                        total_fees=total_fees,
                    )
                )
            continue
        start_balance = balance
        day_net = 0.0
        day_gross = 0.0
        day_commission = 0.0
        executed_count = 0
        conservative_failure = False
        realized_only_failure = False
        daily_paused = False
        day_missing_mae = _day_has_missing_mae(group, risk_mode)
        missing_mae_count = _missing_mae_trade_count(group)
        exact_mae_count = int(len(group) - missing_mae_count)
        for _, trade in group.sort_values(["exit_time", "entry_time", "strategy_id"]).iterrows():
            exit_time = pd.Timestamp(trade["exit_time"])
            evaluate_due_clusters(exit_time, day_number)
            before = balance
            if failed:
                trace_rows.append(
                    _portfolio_trace_trade_row(
                        path_id,
                        plan,
                        trade,
                        before,
                        balance,
                        floor,
                        next_event(),
                        skipped=True,
                        total_payouts=total_payouts,
                        total_fees=total_fees,
                    )
                )
                continue
            if daily_paused:
                trace_rows.append(
                    _portfolio_trace_trade_row(
                        path_id,
                        plan,
                        trade,
                        before,
                        balance,
                        floor,
                        next_event(),
                        skipped=True,
                        reason="daily_loss_pause",
                        total_payouts=total_payouts,
                        total_fees=total_fees,
                    )
                )
                continue
            pnl = float(trade["net_pnl_dollars"])
            balance += pnl
            day_net += pnl
            day_gross += float(trade.get("gross_pnl_dollars", pnl))
            day_commission += float(trade.get("commission_dollars", 0.0))
            executed_count += 1
            running_peak = max(running_peak, balance)
            max_drawdown = max(max_drawdown, running_peak - balance)
            order = next_event()
            trace_rows.append(
                _portfolio_trace_trade_row(
                    path_id,
                    plan,
                    trade,
                    before,
                    balance,
                    floor,
                    order,
                    skipped=False,
                    total_payouts=total_payouts,
                    total_fees=total_fees,
                )
            )
            if balance <= floor:
                realized_only_failure = True
                fail_now(day_number, "realized_exit_floor_breach", exit_time)
                continue
            if profile.daily_loss_limit is not None and day_net <= -abs(profile.daily_loss_limit):
                if profile.daily_loss_hard:
                    realized_only_failure = True
                    fail_now(day_number, "daily_loss_limit_breach", exit_time)
                else:
                    daily_paused = True
        conservative_failure = failed and first_failure_day == day_number and failure_reason == "conservative_overlap_mae_bound"
        running_peak = max(running_peak, balance)
        if failed and executed_count == 0 and not conservative_failure:
            continue
        if not failed and balance > floor and day_net >= profile.winning_day_threshold and day_net > 0:
            winning_days += 1
        if not failed and profile.drawdown_mode == "eod_trailing":
            eod_peak = max(eod_peak, balance)
            floor = max(floor, min(_floor_ceiling(profile), eod_peak - profile.max_loss))
        payout_eligible = False
        payout_gross_debit = 0.0
        payout_trader_cash = 0.0
        payout_order: int | None = None
        if not failed:
            daily_profits.append(day_net)
            payout_decision = calculate_payout_decision(
                balance=balance,
                profile=profile,
                winning_days=winning_days,
                daily_profits=daily_profits,
                payouts_taken=payouts_taken,
                desired_payout=settings.desired_payout,
                required_cushion=settings.required_cushion,
                auto_payout=settings.auto_payout,
            )
            payout_eligible = payout_decision.eligible
            if payout_decision.taken:
                payout_gross_debit = payout_decision.gross_account_debit
                payout_trader_cash = payout_decision.trader_cash
                before_payout = balance
                balance = float(payout_decision.balance_after)
                payouts_taken += 1
                total_payouts += payout_trader_cash
                winning_days = 0
                daily_profits = []
                cushion_ok_after_payout = True
                payout_order = next_event()
                if first_payout_day is None:
                    first_payout_day = day_number
                    first_payout_order = payout_order
                trace_rows.append(
                    {
                        "path_id": path_id,
                        "plan_key": plan.key,
                        "firm": plan.firm,
                        "account": plan.account_name,
                        "record_type": "payout",
                        "event_order": payout_order,
                        "payout_event_order": payout_order,
                        "session_date": day,
                        "entry_time": pd.NaT,
                        "exit_time": pd.NaT,
                        "balance_before": before_payout,
                        "pnl_dollars": 0.0,
                        "gross_account_debit": payout_gross_debit,
                        "trader_cash": payout_trader_cash,
                        "payout_number": payouts_taken,
                        "balance_after": balance,
                        "floor": floor,
                        "failure": False,
                        "failure_reason": "",
                        "total_payouts": total_payouts,
                        "total_fees": total_fees,
                        "net_cash": total_payouts - total_fees,
                        "note": (
                            "Payout taken; consistency state reset; "
                            f"gross_account_debit={payout_gross_debit:.2f}; trader_profit_split={profile.profit_split:.2f}"
                        ),
                    }
                )
        known_failure = bool(conservative_failure or realized_only_failure)
        if known_failure:
            strict_status = "FAILED"
        elif risk_mode == "REALIZED_PNL_ONLY":
            strict_status = "UNKNOWN"
        elif risk_mode == "CONSERVATIVE_OVERLAP_MAE_BOUND":
            strict_status = "UNKNOWN" if day_missing_mae else "SURVIVED"
        else:
            strict_status = "UNKNOWN"
        realized_only_status = "FAILED" if realized_only_failure else "SURVIVED"
        if risk_mode != "CONSERVATIVE_OVERLAP_MAE_BOUND":
            conservative_bound_status = "UNKNOWN"
        elif conservative_failure:
            conservative_bound_status = "FAILED"
        elif day_missing_mae:
            conservative_bound_status = "UNKNOWN"
        else:
            conservative_bound_status = "SURVIVED"
        evidence_coverage = exact_mae_count / len(group) if len(group) else 1.0
        rows.append(
            {
                "account_day": day,
                "trade_count": int(executed_count),
                "gross_pnl_dollars": day_gross,
                "commission_dollars": day_commission,
                "net_pnl_dollars": day_net,
                "balance_before": start_balance,
                "balance_after": balance,
                "floor_after": floor,
                "winning_days_after": winning_days,
                "strict_status": strict_status,
                "realized_only_status": realized_only_status,
                "conservative_bound_status": conservative_bound_status,
                "evidence_coverage": evidence_coverage,
                "missing_mae_trade_count": missing_mae_count,
                "exact_mae_trade_count": exact_mae_count,
                "realized_only_failure": bool(realized_only_failure),
                "conservative_bound_failure": bool(conservative_failure),
                "risk_mode": risk_mode,
                "risk_mode_label": "conservative bound, not exact" if risk_mode == "CONSERVATIVE_OVERLAP_MAE_BOUND" else risk_mode,
                "payout_eligible_after_day": bool(payout_eligible),
                "payout_gross_account_debit": payout_gross_debit,
                "payout_trader_cash": payout_trader_cash,
                "payout_event_order": payout_order,
            }
        )
    account_day = pd.DataFrame(rows)
    summary = _portfolio_summary_row(
        plan,
        settings,
        path_id=path_id,
        failed=failed,
        payouts_taken=payouts_taken,
        total_payouts=total_payouts,
        total_fees=total_fees,
        ending_balance=balance,
        ending_floor=floor,
        max_drawdown=max_drawdown,
        first_failure_day=first_failure_day,
        first_failure_order=first_failure_order,
        first_payout_day=first_payout_day,
        first_payout_order=first_payout_order,
        cushion_ok_after_payout=cushion_ok_after_payout,
        account_day=account_day,
    )
    return pd.DataFrame([summary]), account_day, pd.DataFrame(trace_rows)


def _portfolio_trace_trade_row(
    path_id: int,
    plan: LifecyclePlan,
    trade: pd.Series,
    before: float,
    after: float,
    floor: float,
    event_order: int,
    *,
    skipped: bool,
    reason: str = "",
    total_payouts: float,
    total_fees: float,
) -> dict[str, Any]:
    return {
        "path_id": path_id,
        "plan_key": plan.key,
        "firm": plan.firm,
        "account": plan.account_name,
        "record_type": "trade_skipped_after_failure" if skipped else "trade",
        "event_order": event_order,
        "sequence_number": event_order,
        "strategy_id": trade.get("strategy_id"),
        "source_trade_id": trade.get("source_trade_id"),
        "session_date": trade.get("account_day", trade.get("source_session_date")),
        "entry_time": trade.get("entry_time"),
        "exit_time": trade.get("exit_time"),
        "balance_before": before,
        "pnl_dollars": 0.0 if skipped else float(trade.get("net_pnl_dollars", 0.0)),
        "balance_after": after,
        "floor": floor,
        "failure": False,
        "failure_reason": reason,
        "trader_cash": 0.0,
        "gross_account_debit": 0.0,
        "total_payouts": total_payouts,
        "total_fees": total_fees,
        "net_cash": total_payouts - total_fees,
    }


def _portfolio_summary_row(
    plan: LifecyclePlan,
    settings: LifecycleSettings,
    *,
    path_id: int,
    failed: bool,
    payouts_taken: int,
    total_payouts: float,
    total_fees: float,
    ending_balance: float,
    ending_floor: float,
    max_drawdown: float,
    first_failure_day: int | None,
    first_failure_order: int | None,
    first_payout_day: int | None,
    first_payout_order: int | None,
    cushion_ok_after_payout: bool,
    account_day: pd.DataFrame,
) -> dict[str, Any]:
    net_cash = total_payouts - total_fees
    return {
        "plan_key": plan.key,
        "firm": plan.firm,
        "account_name": plan.account_name,
        "contracts": 1,
        "path_id": path_id,
        "seed": path_id,
        "failed": bool(failed),
        "terminal_stage": "funded",
        "attempts": 0,
        "eval_passes": 0,
        "funded_failures": int(bool(failed)),
        "payouts_taken": payouts_taken,
        "first_payout_month": None if first_payout_day is None else 1,
        "first_payout_day": first_payout_day,
        "first_payout_order": first_payout_order,
        "first_failure_month": None if first_failure_day is None else 1,
        "first_failure_day": first_failure_day,
        "first_failure_order": first_failure_order,
        "total_payouts": total_payouts,
        "total_fees": total_fees,
        "net_cash": net_cash,
        "roi_on_fees": net_cash / total_fees if total_fees > 0 else None,
        "ending_balance": ending_balance,
        "ending_floor": ending_floor,
        "max_drawdown": max_drawdown,
        "target_hit": False,
        "cushion_ok_after_payout": bool(cushion_ok_after_payout),
        "strict_known_failure_rate": float((account_day["strict_status"] == "FAILED").mean()) if not account_day.empty else 0.0,
        "strict_unknown_rate": float((account_day["strict_status"] == "UNKNOWN").mean()) if not account_day.empty else 0.0,
        "realized_only_failure_rate": float((account_day["realized_only_failure"].astype(bool)).mean()) if not account_day.empty else 0.0,
        "conservative_bound_failure_rate": float((account_day["conservative_bound_failure"].astype(bool)).mean()) if not account_day.empty else 0.0,
        "desired_payout": float(settings.desired_payout),
    }


def _has_overlap_missing_mae(group: pd.DataFrame) -> bool:
    rows = list(group.to_dict("records"))
    for left_index, left in enumerate(rows):
        for right in rows[left_index + 1 :]:
            if pd.Timestamp(left["entry_time"]) < pd.Timestamp(right["exit_time"]) and pd.Timestamp(left["exit_time"]) > pd.Timestamp(right["entry_time"]):
                if pd.isna(left.get("mae_dollars")) or pd.isna(right.get("mae_dollars")):
                    return True
    return False


def _day_has_missing_mae(group: pd.DataFrame, risk_mode: IntratradeRiskMode) -> bool:
    if "mae_dollars_abs" not in group:
        return True
    return bool(group["mae_dollars_abs"].isna().any())


def _missing_mae_trade_count(group: pd.DataFrame) -> int:
    if "mae_dollars_abs" not in group:
        return int(len(group))
    return int(group["mae_dollars_abs"].isna().sum())


def _risk_clusters(ledger: pd.DataFrame) -> list[dict[str, Any]]:
    rows = ledger.sort_values(["entry_time", "exit_time", "strategy_id"]).reset_index(drop=True).to_dict("records")
    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_end: pd.Timestamp | None = None
    for row in rows:
        entry = pd.Timestamp(row["entry_time"])
        exit_ = pd.Timestamp(row["exit_time"])
        if current and current_end is not None and entry >= current_end:
            clusters.append(current)
            current = []
            current_end = None
        current.append(row)
        current_end = exit_ if current_end is None else max(current_end, exit_)
    if current:
        clusters.append(current)

    output = []
    for index, cluster in enumerate(clusters, start=1):
        output.append(
            {
                "cluster_id": index,
                "start_time": min(pd.Timestamp(row["entry_time"]) for row in cluster),
                "end_time": max(pd.Timestamp(row["exit_time"]) for row in cluster),
                "missing_mae": any(pd.isna(row.get("mae_dollars_abs", row.get("mae_dollars"))) for row in cluster),
                "max_concurrent_mae": _max_concurrent_mae(cluster),
            }
        )
    return output


def _max_concurrent_mae(cluster: list[dict[str, Any]]) -> float:
    times = sorted({pd.Timestamp(row["entry_time"]) for row in cluster} | {pd.Timestamp(row["exit_time"]) for row in cluster})
    max_bound = 0.0
    for time in times:
        active = [
            row
            for row in cluster
            if pd.Timestamp(row["entry_time"]) <= time < pd.Timestamp(row["exit_time"])
        ]
        bound = sum(float(row.get("mae_dollars_abs", row.get("mae_dollars", 0.0)) or 0.0) for row in active)
        max_bound = max(max_bound, bound)
    return max_bound


def portfolio_contribution_summary(ledger: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    by_strategy = (
        ledger.groupby(["portfolio_path_id", "allocation_id", "strategy_id"], dropna=False)
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
        ledger.groupby(["portfolio_path_id", "allocation_id", "asset_id"], dropna=False)
        .agg(
            gross_pnl_dollars=("gross_pnl_dollars", "sum"),
            net_pnl_dollars=("net_pnl_dollars", "sum"),
            commission_dollars=("commission_dollars", "sum"),
            trade_count=("source_trade_id", "count"),
        )
        .reset_index()
    )
    return by_strategy, by_asset


def portfolio_allocation_monte_carlo_summary(
    path_results: pd.DataFrame,
    by_strategy: pd.DataFrame,
    allocation_manifest: pd.DataFrame,
) -> pd.DataFrame:
    if path_results.empty:
        return pd.DataFrame()
    rows = []

    def numeric_column(frame: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
        if column not in frame:
            return pd.Series(default, index=frame.index, dtype=float)
        return pd.to_numeric(frame[column], errors="coerce").fillna(default)

    for allocation_id, group in path_results.groupby("allocation_id", dropna=False):
        paid = pd.to_numeric(group.get("total_payouts", 0), errors="coerce").fillna(0) > 0
        failed = group.get("failed", pd.Series(False, index=group.index)).astype(bool)
        first_payout_order = pd.to_numeric(group.get("first_payout_order"), errors="coerce")
        first_failure_order = pd.to_numeric(group.get("first_failure_order"), errors="coerce")
        payout_before_failure = first_payout_order.notna() & (first_failure_order.isna() | (first_payout_order < first_failure_order))
        failure_before_payout = first_failure_order.notna() & (first_payout_order.isna() | (first_failure_order < first_payout_order))
        net_cash = pd.to_numeric(group.get("net_cash", 0), errors="coerce").fillna(0)
        ending_balance = pd.to_numeric(group.get("ending_balance", 0), errors="coerce").fillna(0)
        paid_days = pd.to_numeric(group.loc[paid, "first_payout_day"], errors="coerce") if "first_payout_day" in group else pd.Series(dtype=float)
        row = {
            "allocation_id": allocation_id,
            "path_count": int(group["portfolio_path_id"].nunique() if "portfolio_path_id" in group else len(group)),
            "median_ending_balance": float(ending_balance.median()),
            "mean_ending_balance": float(ending_balance.mean()),
            "median_net_cash": float(net_cash.median()),
            "p10_net_cash": float(net_cash.quantile(0.10)),
            "p90_net_cash": float(net_cash.quantile(0.90)),
            "payout_before_failure_rate": float(payout_before_failure.mean()),
            "failure_before_first_payout_rate": float(failure_before_payout.mean()),
            "first_payout_rate": float(paid.mean()),
            "median_time_to_first_payout_among_paid_paths": None if paid_days.dropna().empty else float(paid_days.median()),
            "terminal_failure_rate": float(failed.mean()),
            "survival_rate": float((~failed).mean()),
            "strict_known_failure_rate": float(numeric_column(group, "strict_known_failure_rate").mean()),
            "strict_unknown_evidence_rate": float(numeric_column(group, "strict_unknown_rate").mean()),
            "realized_only_failure_rate": float(numeric_column(group, "realized_only_failure_rate").mean()),
            "conservative_bound_failure_rate": float(numeric_column(group, "conservative_bound_failure_rate").mean()),
        }
        strategy_rows = by_strategy[by_strategy["allocation_id"].eq(allocation_id)] if not by_strategy.empty else pd.DataFrame()
        for strategy_id, strat_group in strategy_rows.groupby("strategy_id", dropna=False):
            row[f"median_gross_pnl_strategy_{strategy_id}"] = float(pd.to_numeric(strat_group["gross_pnl_dollars"], errors="coerce").median())
            row[f"median_net_pnl_strategy_{strategy_id}"] = float(pd.to_numeric(strat_group["net_pnl_dollars"], errors="coerce").median())
        manifest_rows = allocation_manifest[allocation_manifest["allocation_id"].eq(allocation_id)]
        if not manifest_rows.empty:
            for column, value in manifest_rows.iloc[0].items():
                if column != "allocation_id":
                    row[f"contracts_{column}"] = value
        rows.append(row)
    return pd.DataFrame(rows).sort_values("allocation_id").reset_index(drop=True)


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
    allocation_summary = portfolio_allocation_monte_carlo_summary(path_results, by_strategy, allocation_manifest)
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
        "portfolio_per_path_strategy_contribution": by_strategy,
        "portfolio_per_path_asset_contribution": by_asset,
        "portfolio_allocation_summary": allocation_summary,
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
