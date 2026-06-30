from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any
import warnings

import pandas as pd

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
    source_timezone: str | None = None,
) -> list[Trade]:
    """Load one timestamped strategy ledger CSV into normalized Trade objects."""

    source_path = Path(path)
    frame = pd.read_csv(source_path)
    return normalize_trade_frame(
        frame,
        source_path=source_path,
        metadata=metadata,
        source_timezone=source_timezone,
    )


def load_canonical_margin_csv(
    path: str | Path,
    *,
    contract_specs_by_strategy: dict[str, InstrumentSpec] | None = None,
) -> list[Trade]:
    source_path = Path(path)
    frame = pd.read_csv(source_path)
    normalized = normalize_canonical_margin_frame(
        frame,
        source_path=source_path,
        contract_specs_by_strategy=contract_specs_by_strategy,
    )
    return normalize_trade_frame(normalized, source_path=source_path, source_timezone="UTC")


def normalize_canonical_margin_frame(
    frame: pd.DataFrame,
    *,
    source_path: Path | None = None,
    contract_specs_by_strategy: dict[str, InstrumentSpec] | None = None,
) -> pd.DataFrame:
    """Map nq_es_margin_sim_master_2025_2026.csv columns into the V1 schema.

    Explicit per-strategy contract specifications are REQUIRED (ADR-011). The
    underlying symbol (e.g. ``NQ``/``ES``) never silently implies a contract
    (``MNQ``/``MES``); every ``strategy_id`` present in the ledger must have a
    declared spec. The default instrument registry may be used only as explicit,
    user-selected convenience tooling (see
    ``sim_core.instruments.build_specs_from_registry``), never as an unannounced
    loader fallback. Unknown strategies, missing mappings, and blank ``dpp`` all
    fail validation.
    """

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

    if contract_specs_by_strategy is None:
        raise TradeValidationError(
            [
                ValidationIssue(
                    None,
                    "contract_specs_by_strategy",
                    "explicit per-strategy contract specifications are required for canonical "
                    "margin-ledger loading; underlying symbols are not silently mapped to contracts",
                )
            ]
        )

    rows: list[dict[str, object]] = []
    issues: list[ValidationIssue] = []

    # Every strategy present in the ledger must have an explicit declared spec.
    if "strategy" in frame.columns:
        ledger_strategies = sorted(
            {text for text in (_optional_str(value) for value in frame["strategy"]) if text}
        )
        for strategy in ledger_strategies:
            if strategy not in contract_specs_by_strategy:
                issues.append(
                    ValidationIssue(
                        None,
                        "strategy",
                        f"no declared contract specification for strategy '{strategy}'",
                    )
                )

    for index, row in frame.iterrows():
        row_number = int(index) + 2
        strategy_id = _required_str(row.get("strategy"), "strategy", row_number, issues)
        underlying = _required_str(row.get("inst"), "inst", row_number, issues)
        if underlying is None or strategy_id is None:
            continue
        spec = contract_specs_by_strategy.get(strategy_id)
        if spec is None:
            # Already reported at the aggregate level above; skip the row.
            continue
        if spec.underlying != underlying:
            issues.append(
                ValidationIssue(
                    row_number,
                    "inst",
                    f"row underlying {underlying} does not match declared "
                    f"{spec.underlying} for strategy '{strategy_id}'",
                )
            )
            continue
        if spec.currency != "USD":
            issues.append(ValidationIssue(row_number, "inst", "Version 1 supports USD only"))
            continue

        dpp = _optional_float(row.get("dpp"))
        if dpp is None:
            issues.append(
                ValidationIssue(
                    row_number,
                    "dpp",
                    "dpp is required and must not be blank; no silent fallback to a contract default",
                )
            )
            continue
        elif abs(dpp - spec.dollars_per_point) > 1e-9:
            issues.append(
                ValidationIssue(
                    row_number,
                    "dpp",
                    f"ledger dpp {dpp} does not match declared {spec.dollars_per_point} "
                    f"for strategy '{strategy_id}'",
                )
            )

        source_row_id = f"{source_path.name if source_path else 'canonical'}:{row_number}"
        metadata = {
            "source_schema": "nq_es_margin_sim_master_2025_2026",
            "window": _optional_str(row.get("window")),
            "mult": _optional_float(row.get("mult")),
            "exit_reason": _optional_str(row.get("exit")),
            "nq_inside": row.get("nq_inside") if "nq_inside" in frame.columns else None,
            "year": row.get("year") if "year" in frame.columns else None,
            "sess_date": row.get("sess_date") if "sess_date" in frame.columns else None,
        }
        rows.append(
            {
                "strategy_id": strategy_id,
                "instrument": spec.underlying,
                "contract_symbol": spec.contract_symbol,
                "entry_time": row.get("entry_utc"),
                "exit_time": row.get("exit_utc"),
                "direction": row.get("side"),
                "pnl_points": row.get("pnl_pts"),
                "mae_points": row.get("mae_pts"),
                "mfe_points": row.get("mfe_pts"),
                "result_type": None,
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
    source_timezone: str | None = None,
    dst_resolution: str | None = None,
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

    entry_times = _parse_timestamp_column(
        normalized["entry_time"], "entry_time", issues,
        source_timezone=source_timezone, dst_resolution=dst_resolution,
    )
    exit_times = _parse_timestamp_column(
        normalized["exit_time"], "exit_time", issues,
        source_timezone=source_timezone, dst_resolution=dst_resolution,
    )

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

        explicit_identity = _optional_str(row.get("source_row_id")) or _optional_str(row.get("trade_id"))
        duplicate_key = (
            ("source_identity", explicit_identity)
            if explicit_identity is not None
            else ("semantic", strategy_id, instrument, entry_time, exit_time, pnl_dollars)
        )
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
    values: pd.Series,
    column: str,
    issues: list[ValidationIssue],
    *,
    source_timezone: str | None,
    dst_resolution: str | None = None,
) -> pd.Series:
    parsed_values: list[pd.Timestamp] = []
    for index, value in values.items():
        row_number = int(index) + 2
        if value is None or pd.isna(value):
            parsed_values.append(pd.NaT)
            continue
        try:
            timestamp = pd.Timestamp(value)
        except Exception:
            issues.append(ValidationIssue(row_number, column, "unsupported timestamp format"))
            parsed_values.append(pd.NaT)
            continue
        if timestamp.tzinfo is None or timestamp.tz is None:
            if source_timezone is None:
                issues.append(
                    ValidationIssue(
                        row_number,
                        column,
                        "naive timestamp with no timezone declaration; pass source_timezone explicitly",
                    )
                )
                parsed_values.append(pd.NaT)
                continue
            warnings.warn(
                f"{column}: localized naive timestamp to declared source_timezone={source_timezone}",
                RuntimeWarning,
                stacklevel=2,
            )
            try:
                timestamp = _localize_naive(timestamp, source_timezone, dst_resolution)
            except Exception as exc:
                issues.append(
                    ValidationIssue(
                        row_number,
                        column,
                        f"ambiguous or nonexistent local time for {source_timezone} "
                        f"({type(exc).__name__}); supply an explicit dst_resolution policy",
                    )
                )
                parsed_values.append(pd.NaT)
                continue
        parsed_values.append(timestamp.tz_convert("UTC"))
    return pd.Series(parsed_values, index=values.index)


def _localize_naive(
    timestamp: pd.Timestamp, source_timezone: str, dst_resolution: str | None
) -> pd.Timestamp:
    """Localize a naive timestamp, failing clearly on DST gaps/overlaps.

    With ``dst_resolution=None`` pandas raises on nonexistent (spring-forward gap)
    or ambiguous (fall-back overlap) local times, which we surface as a clear
    validation error. An explicit policy resolves them:
      * ``"shift_forward"`` / ``"shift_backward"`` / ``"NaT"`` -> nonexistent times
      * ``"earliest"`` / ``"latest"`` -> ambiguous times
    """

    if dst_resolution is None:
        return timestamp.tz_localize(source_timezone)
    ambiguous: object = "raise"
    nonexistent: str = "raise"
    if dst_resolution == "earliest":
        ambiguous = True
    elif dst_resolution == "latest":
        ambiguous = False
    elif dst_resolution in {"shift_forward", "shift_backward", "NaT"}:
        nonexistent = dst_resolution
    else:
        raise ValueError(f"unsupported dst_resolution policy: {dst_resolution!r}")
    return timestamp.tz_localize(source_timezone, ambiguous=ambiguous, nonexistent=nonexistent)


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
