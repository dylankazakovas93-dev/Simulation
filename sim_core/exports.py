from __future__ import annotations

from pathlib import Path

from sim_core.batch import build_result_distribution
from sim_core.metrics.reports import monthly_equity_percentiles, summarize_paths
from sim_core.models import ResultDistribution, Scenario, SimulationResult


def export_simulation_result(
    result: SimulationResult,
    output_dir: str | Path,
    *,
    scenario: Scenario | None = None,
) -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    equity_path = output_path / "equity_path.csv"
    result.to_equity_frame().to_csv(equity_path, index=False)
    exported = {"equity_path": equity_path}
    if scenario is not None:
        metadata_path = output_path / "scenario_metadata.json"
        metadata_path.write_text(scenario.to_json(), encoding="utf-8")
        exported["scenario_metadata"] = metadata_path
    return exported


def export_simulation_batch(
    results: list[SimulationResult],
    output_dir: str | Path,
    *,
    scenario: Scenario | None = None,
    distribution: ResultDistribution | None = None,
) -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    summary_path = output_path / "path_summary.csv"
    monthly_path = output_path / "monthly_percentiles.csv"
    summarize_paths(results).to_csv(summary_path, index=False)
    monthly_equity_percentiles(results).to_csv(monthly_path, index=False)
    exported = {"path_summary": summary_path, "monthly_percentiles": monthly_path}
    if scenario is not None:
        if distribution is None:
            all_trades = [trade for result in results for trade in result.trades]
            distribution = build_result_distribution(scenario, all_trades, results)
        metadata_path = output_path / "result_distribution.json"
        metadata_path.write_text(distribution.to_json(), encoding="utf-8")
        exported["result_distribution"] = metadata_path
    return exported
