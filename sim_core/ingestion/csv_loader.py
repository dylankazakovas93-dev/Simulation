from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd

from sim_core.instruments import DEFAULT_INSTRUMENT_REGISTRY
from sim_core.models import (
    InstrumentSpec,
    StrategyMetadata,
    Trade,
    TradeValidationError,
    ValidationIssue,
    classify_result,
)

REQUIRED_COLUMNS = {"strategy_id", "instrument", "entry_time", "exit_time"}
OPTIONAL_COLUMNS = {
    "direction",
    "entry_price",
    "exit_price",
    "pnl_points",
    "pnl_dollars",
    "stop_points",
    "target_points",
    "mae_points",
    "mfe_points",
    "result_type",
    "session",
    "dollars_per_point",
    "commission_round_turn",
    "trade_id",
    "source_row_id",
    "contract_symbol",
    "currency",
}
NUMERIC_COLUMNS = {
    "entry_price",
    "exit_price",
    "pnl_points",
    "pnl_dollars",
    "stop_points",
    "target_points",
    "mae_points",
    "mfe_points",
    "dollars_per_point",
    "commission_round_turn",
}


def load_trade_csv(
    path: str | Path,
    *,
    metadata: StrategyMetadata | None = None,
) -> list[Trade]:
    """Load one timestamped strategy ledger CSV into normalized Trade objects."""

    source_path = Path(path)
    frame = pd.read_csv(source_path)
    return normalize_trade_frame(frame, source_path=source_path, metadata=metadata)


def load_canonical_margin_csv(
    path: str | Path,
    *,
    instrument_registry: dict[str, InstrumentSpec] | None = None,
) -> list[Trade]:
    source_path = Path(path)
    frame = pd.read_csv(source_path)
    normalized = normalize_canonical_margin_frame(
        frame,
        source_path=source_path,
        instrument_registry=instrument_registry,
    )
    return normalize_trade_frame(normalized, source_path=source_path)


def normalize_canonical_margin_frame(
    frame: pd.DataFrame,
    *,
    source_path: Path | None = None,
    instrument_registry: dict[str, InstrumentSpec] | None = None,
) -> pd.DataFrame:
    """Map nq_es_margin_sim_master_2025_2026.csv columns into the V1 schema."""

    registry = instrument_registry or DEFAULT_INSTRUMENT_REGISTRY
    required = {
        "strategy",
        "inst",
        "entry_utc",
        "exit_utc",
        "side",
        "pnl_pts",
        "mae_pts",
        "mfe_pts",
        "exit",
        "dpp",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise TradeValidationError(
            [ValidationIssue(None, column, "required canonical column is missing") for column in missing]
        )

    rows: list[dict[str, object]] = []
    issues: list[ValidationIssue] = []
    for index, row in frame.iterrows():
        row_number = int(index) + 2
        underlying = _required_str(row.get("inst"), "inst", row_number, issues)
        if underlying is None:
            continue
        spec = registry.get(underlying)
        if spec is None:
            issues.append(ValidationIssue(row_number, "inst", "missing instrument registry entry"))
            continue
        if spec.currency != "USD":
            issues.append(ValidationIssue(row_number, "inst", "Version 1 supports USD only"))
            continue

        dpp = _optional_float(row.get("dpp"))
        if dpp is None:
            dpp = spec.dollars_per_point
        elif abs(dpp - spec.dollars_per_point) > 1e-9:
            issues.append(
                ValidationIssue(
                    row_number,
                    "dpp",
                    f"does not match registry value {spec.dollars_per_point} for {underlying}",
                )
            )

        source_row_id = f"{source_path.name if source_path else 'canonical'}:{row_number}"
        metadata = {
            "source_schema": "nq_es_margin_sim_master_2025_2026",
            "window": _optional_str(row.get("window")),
            "mult": _optional_float(row.get("mult")),
            "nq_inside": row.get("nq_inside") if "nq_inside" in frame.columns else None,
            "year": row.get("year") if "year" in frame.columns else None,
            "sess_date": row.get("sess_date") if "sess_date" in frame.columns else None,
        }
        rows.append(
            {
                "strategy_id": _required_str(row.get("strategy"), "strategy", row_number, issues),
                "instrument": spec.underlying,
                "contract_symbol": spec.contract_symbol,
                "entry_time": row.get("entry_utc"),
                "exit_time": row.get("exit_utc"),
                "direction": row.get("side"),
                "pnl_points": row.get("pnl_pts"),
                "mae_points": row.get("mae_pts"),
                "mfe_points": row.get("mfe_pts"),
                "result_type": row.get("exit"),
                "dollars_per_point": dpp,
                "currency": spec.currency,
                "commission_round_turn": spec.commission_round_turn,
                "trade_id": source_row_id,
                "source_row_id": source_row_id,
                "metadata": metadata,
            }
        )

    if issues:
        raise TradeValidationError(issues)
    return pd.DataFrame(rows)


def load_trade_csvs(
    paths: Iterable[str | Path],
    *,
    metadata_by_strategy: dict[str, StrategyMetadata] | None = None,
) -> list[Trade]:
    trades: list[Trade] = []
    metadata_by_strategy = metadata_by_strategy or {}
    for path in paths:
        frame = pd.read_csv(path)
        strategy_ids = set(frame["strategy_id"].dropna().astype(str)) if "strategy_id" in frame else set()
        metadata = metadata_by_strategy.get(next(iter(strategy_ids))) if len(strategy_ids) == 1 else None
        trades.extend(normalize_trade_frame(frame, source_path=Path(path), metadata=metadata))
    return sort_trades_chronologically(trades)


def normalize_trade_frame(
    frame: pd.DataFrame,
    *,
    source_path: Path | None = None,
    metadata: StrategyMetadata | None = None,
) -> list[Trade]:
    issues: list[ValidationIssue] = []
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    for column in missing:
        issues.append(ValidationIssue(None, column, "required column is missing"))

    can_derive_from_points = "pnl_points" in frame.columns and (
        "dollars_per_point" in frame.columns
        or (metadata is not None and metadata.dollars_per_point is not None)
    )
    if "pnl_dollars" not in frame.columns and not can_derive_from_points:
        issues.append(
            ValidationIssue(
                None,
                "pnl_dollars",
                "provide pnl_dollars or both pnl_points and dollars_per_point",
            )
        )
    if issues:
        raise TradeValidationError(issues)

    normalized = frame.copy()
    for column in NUMERIC_COLUMNS & set(normalized.columns):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")

    entry_times = _parse_timestamp_column(normalized["entry_time"], "entry_time", issues)
    exit_times = _parse_timestamp_column(normalized["exit_time"], "exit_time", issues)

    trades: list[Trade] = []
    seen_keys: set[tuple[Any, ...]] = set()
    for index, row in normalized.iterrows():
        row_number = int(index) + 2
        strategy_id = _required_str(row.get("strategy_id"), "strategy_id", row_number, issues)
        instrument = _required_str(row.get("instrument"), "instrument", row_number, issues)
        entry_time = entry_times.iloc[index] if index in entry_times.index else pd.NaT
        exit_time = exit_times.iloc[index] if index in exit_times.index else pd.NaT

        if pd.isna(entry_time):
            issues.append(ValidationIssue(row_number, "entry_time", "timestamp is missing or invalid"))
        if pd.isna(exit_time):
            issues.append(ValidationIssue(row_number, "exit_time", "timestamp is missing or invalid"))
        if pd.notna(entry_time) and pd.notna(exit_time) and exit_time < entry_time:
            issues.append(ValidationIssue(row_number, "exit_time", "exit_time precedes entry_time"))

        dollars_per_point = _optional_float(row.get("dollars_per_point"))
        metadata_matches_row = (
            metadata is not None
            and metadata.strategy_id == strategy_id
            and metadata.instrument == instrument
        )
        if dollars_per_point is None and metadata_matches_row and metadata.dollars_per_point is not None:
            dollars_per_point = metadata.dollars_per_point

        pnl_dollars = _optional_float(row.get("pnl_dollars"))
        pnl_points = _optional_float(row.get("pnl_points"))
        if pnl_dollars is None:
            if pnl_points is None or dollars_per_point is None:
                issues.append(
                    ValidationIssue(
                        row_number,
                        "pnl_dollars",
                        "cannot derive pnl_dollars without pnl_points and dollars_per_point",
                    )
                )
                continue
            pnl_dollars = pnl_points * dollars_per_point

        commission = _optional_float(row.get("commission_round_turn"))
        if commission is None and metadata_matches_row:
            commission = metadata.commission_round_turn
        commission = 0.0 if commission is None else commission
        if commission < 0:
            issues.append(ValidationIssue(row_number, "commission_round_turn", "cannot be negative"))

        if not strategy_id or not instrument or pd.isna(entry_time) or pd.isna(exit_time):
            continue

        duplicate_key = (strategy_id, instrument, entry_time, exit_time, pnl_dollars)
        if duplicate_key in seen_keys:
            issues.append(ValidationIssue(row_number, None, "duplicate trade detected"))
            continue
        seen_keys.add(duplicate_key)

        trade_id = _optional_str(row.get("trade_id"))
        if trade_id is None:
            trade_id = f"{source_path or 'frame'}:{row_number}"
        source_row_id = _optional_str(row.get("source_row_id")) or trade_id
        contract_symbol = _optional_str(row.get("contract_symbol"))
        currency = _optional_str(row.get("currency")) or "USD"
        if currency != "USD":
            issues.append(ValidationIssue(row_number, "currency", "Version 1 supports USD only"))
            continue

        result_type = _normalize_result(row.get("result_type"), pnl_dollars, row_number, issues)
        row_metadata = {"row_number": row_number}
        if "metadata" in normalized.columns and isinstance(row.get("metadata"), dict):
            row_metadata.update(row.get("metadata"))
        trades.append(
            Trade(
                trade_id=trade_id,
                source_row_id=source_row_id,
                strategy_id=strategy_id,
                instrument=instrument,
                contract_symbol=contract_symbol
                or (metadata.contract_symbol if metadata_matches_row else None),
                entry_time=pd.Timestamp(entry_time),
                exit_time=pd.Timestamp(exit_time),
                pnl_dollars=float(pnl_dollars),
                direction=_optional_str(row.get("direction")),
                entry_price=_optional_float(row.get("entry_price")),
                exit_price=_optional_float(row.get("exit_price")),
                pnl_points=pnl_points,
                stop_points=_optional_float(row.get("stop_points")),
                target_points=_optional_float(row.get("target_points")),
                mae_points=_optional_float(row.get("mae_points")),
                mfe_points=_optional_float(row.get("mfe_points")),
                result_type=result_type,
                session=_optional_str(row.get("session")),
                dollars_per_point=dollars_per_point,
                currency=currency,
                commission_round_turn=float(commission),
                source_path=source_path,
                metadata=row_metadata,
            )
        )

    if issues:
        raise TradeValidationError(issues)
    return sort_trades_chronologically(trades)


def sort_trades_chronologically(trades: Iterable[Trade]) -> list[Trade]:
    return sorted(
        trades,
        key=lambda trade: (
            trade.entry_time,
            trade.exit_time,
            trade.strategy_id,
            trade.source_row_id,
        ),
    )


def _parse_timestamp_column(
    values: pd.Series, column: str, issues: list[ValidationIssue]
) -> pd.Series:
    parsed = pd.to_datetime(values, errors="coerce", format="mixed")
    invalid_rows = values.notna() & parsed.isna()
    for index in values[invalid_rows].index:
        issues.append(ValidationIssue(int(index) + 2, column, "unsupported timestamp format"))
    return parsed


def _required_str(
    value: object,
    column: str,
    row_number: int,
    issues: list[ValidationIssue],
) -> str | None:
    text = _optional_str(value)
    if not text:
        issues.append(ValidationIssue(row_number, column, "value is required"))
    return text


def _optional_str(value: object) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _normalize_result(
    value: object,
    pnl_dollars: float,
    row_number: int,
    issues: list[ValidationIssue],
) -> str:
    text = _optional_str(value)
    if text is None:
        return classify_result(float(pnl_dollars))
    lowered = text.lower()
    aliases = {
        "be": "breakeven",
        "break_even": "breakeven",
        "scratch": "breakeven",
        "flat": "breakeven",
        "target": "win",
        "tp": "win",
        "profit": "win",
        "stop": "loss",
        "sl": "loss",
        "loss_exit": "loss",
    }
    lowered = aliases.get(lowered, lowered)
    if lowered not in {"win", "loss", "breakeven"}:
        issues.append(ValidationIssue(row_number, "result_type", "must be win, loss, or breakeven"))
        return classify_result(float(pnl_dollars))
    if lowered == "win" and pnl_dollars <= 0:
        issues.append(ValidationIssue(row_number, "result_type", "win requires positive pnl_dollars"))
    if lowered == "loss" and pnl_dollars >= 0:
        issues.append(ValidationIssue(row_number, "result_type", "loss requires negative pnl_dollars"))
    if lowered == "breakeven" and abs(pnl_dollars) > 1e-9:
        issues.append(ValidationIssue(row_number, "result_type", "breakeven requires zero pnl_dollars"))
    return lowered
