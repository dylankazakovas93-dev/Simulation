"""Real-ledger integration harness.

Loads the real canonical NQ/ES micro ledger under an EXPLICIT declared contract
mapping (ADR-011), runs V1 smoke checks, and emits a provenance-stamped
integration report. Usage:

    python -m sim_core.integration.real_ledger \
        --csv /path/to/nq_es_margin_sim_master_2025_2026.csv \
        --mapping configs/nq_es_micro_contracts.yaml \
        --output reports/real_ledger_v1/

The command first prints every discovered strategy_id and fails with a useful
message if any strategy lacks a declared mapping.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from sim_core.batch import hash_trades, scenario_hash
from sim_core.diagnostics.coverage import build_coverage_report
from sim_core.execution.replay import run_fixed_contract_simulation
from sim_core.ingestion.csv_loader import load_canonical_margin_csv
from sim_core.metrics.reports import trade_outcome_taxonomy
from sim_core.models import InstrumentSpec, Scenario, StrategyCoverage
from sim_core.resampling.policies import (
    HistoricalReplay,
    MovingBlockBootstrap,
    SameCalendarMonthBootstrap,
    StationaryBlockBootstrap,
)

DEFAULT_SEED = 20260630


class IntegrationError(RuntimeError):
    """Raised when the ledger cannot be integrated (e.g. unmapped strategies)."""


def load_mapping(path: str | Path) -> dict[str, InstrumentSpec]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    specs: dict[str, InstrumentSpec] = {}
    for strategy, cfg in (data.get("strategies") or {}).items():
        specs[strategy] = InstrumentSpec(
            underlying=str(cfg["underlying"]),
            contract_symbol=str(cfg["contract_symbol"]),
            dollars_per_point=float(cfg["dollars_per_point"]),
            currency=str(cfg.get("currency", "USD")),
        )
    return specs


def discover_strategy_ids(csv_path: str | Path) -> list[str]:
    frame = pd.read_csv(csv_path)
    if "strategy" not in frame.columns:
        raise IntegrationError("canonical ledger is missing the 'strategy' column")
    return sorted({str(s).strip() for s in frame["strategy"].dropna() if str(s).strip()})


def _distinct_months(trades) -> list[pd.Period]:
    return sorted({trade.source_month for trade in trades})


def _smoke(policy, trades) -> dict[str, Any]:
    try:
        path = policy.sample(trades, seed=DEFAULT_SEED)
        result = run_fixed_contract_simulation(path)
        return {
            "ok": True,
            "sampled_trades": len(path.trades),
            "sampled_blocks": len(path.sampled_blocks),
            "terminal_equity": result.terminal_equity,
            "diagnostics": path.diagnostics,
        }
    except Exception as exc:  # smoke tests record failures rather than crash the report
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def build_integration_report(
    csv_path: str | Path,
    mapping_path: str | Path,
    *,
    seed: int = DEFAULT_SEED,
    coverage: list[StrategyCoverage] | None = None,
) -> dict[str, Any]:
    discovered = discover_strategy_ids(csv_path)
    specs = load_mapping(mapping_path)
    unmapped = [s for s in discovered if s not in specs]
    if unmapped:
        raise IntegrationError(
            "the following strategy_id(s) have no declared contract mapping in "
            f"{mapping_path}: {', '.join(unmapped)}. Add an explicit entry for each "
            "(ADR-011: underlyings are not silently mapped to contracts)."
        )

    trades = load_canonical_margin_csv(csv_path, contract_specs_by_strategy=specs)

    by_strategy_pnl: dict[str, float] = defaultdict(float)
    by_strategy_count: dict[str, int] = defaultdict(int)
    for trade in trades:
        by_strategy_pnl[trade.strategy_id] += trade.pnl_dollars
        by_strategy_count[trade.strategy_id] += 1

    replay = HistoricalReplay().sample(trades)
    replay_result = run_fixed_contract_simulation(replay)
    exit_times = [point.timestamp for point in replay_result.equity_path]
    chronological = all(a <= b for a, b in zip(exit_times, exit_times[1:]))

    months = _distinct_months(trades)
    smoke_months = max(1, min(3, len(months)))
    start_month = str(months[0]) if months else None

    coverage_report = build_coverage_report(trades, coverage)

    tz_values = {str(trade.entry_time.tz) for trade in trades} | {
        str(trade.exit_time.tz) for trade in trades
    }

    scenario = Scenario(
        scenario_id="real_ledger_v1",
        master_seed=seed,
        number_of_paths=1,
        horizon_months=smoke_months,
        starting_equity=100_000.0,
        selected_strategies=discovered,
        resampling_method="same_calendar_month_bootstrap",
        resampling_params={"months": smoke_months, "start_month": start_month},
        contract_mappings={
            s: {
                "underlying": spec.underlying,
                "contract_symbol": spec.contract_symbol,
                "dollars_per_point": spec.dollars_per_point,
                "currency": spec.currency,
            }
            for s, spec in specs.items()
        },
        input_data_hash=hash_trades(trades),
    )

    smokes: dict[str, Any] = {}
    if start_month is not None:
        smokes["seasonal_bootstrap"] = _smoke(
            SameCalendarMonthBootstrap(months=smoke_months, start_month=start_month), trades
        )
        smokes["moving_block"] = _smoke(
            MovingBlockBootstrap(months=smoke_months, block_length=2, start_month=start_month), trades
        )
        smokes["stationary_block"] = _smoke(
            StationaryBlockBootstrap(
                months=smoke_months, expected_block_length=3.0, start_month=start_month
            ),
            trades,
        )

    return {
        "engine_version": scenario.engine_version,
        "test_seed": seed,
        "row_count": len(trades),
        "strategy_ids": discovered,
        "contract_mapping": scenario.contract_mappings,
        "date_range": {
            "start": str(min(trade.entry_time for trade in trades)) if trades else None,
            "end": str(max(trade.exit_time for trade in trades)) if trades else None,
        },
        "timezone_validation": {
            "all_utc": tz_values == {"UTC"},
            "observed_timezones": sorted(tz_values),
        },
        "coverage_months": coverage_report.to_records(),
        "coverage_strategies": coverage_report.strategies,
        "historical_replay_pnl_by_strategy": dict(by_strategy_pnl),
        "trade_count_by_strategy": dict(by_strategy_count),
        "breakeven_taxonomy": trade_outcome_taxonomy(trades),
        "smoke_tests": smokes,
        "chronological_order_valid": chronological,
        "data_hash": hash_trades(trades),
        "scenario_hash": scenario_hash(scenario),
        "warnings": coverage_report.warnings(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Integrate the real canonical NQ/ES ledger.")
    parser.add_argument("--csv", required=True, help="path to the real canonical ledger CSV")
    parser.add_argument("--mapping", required=True, help="path to the explicit contract mapping YAML")
    parser.add_argument("--output", required=True, help="output directory for the integration report")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)

    discovered = discover_strategy_ids(args.csv)
    print(f"Discovered {len(discovered)} strategy_id(s): {', '.join(discovered) or '(none)'}")

    try:
        report = build_integration_report(args.csv, args.mapping, seed=args.seed)
    except IntegrationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "integration_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")

    print(f"Rows: {report['row_count']}  Strategies: {len(report['strategy_ids'])}")
    print(f"Chronological order valid: {report['chronological_order_valid']}")
    print(f"All timestamps UTC: {report['timezone_validation']['all_utc']}")
    print(f"data_hash: {report['data_hash']}")
    print(f"Report written to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
