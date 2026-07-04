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
    results: list[SimulationResult] | ResultDistribution,
    output_dir: str | Path,
    *,
    scenario: Scenario | None = None,
    distribution: ResultDistribution | None = None,
) -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    if isinstance(results, ResultDistribution):
        distribution = results
        manifest_path = output_path / "run_manifest.json"
        manifest_path.write_text(_manifest_json(distribution), encoding="utf-8")
        return {"run_manifest": manifest_path}

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


def _manifest_json(distribution: ResultDistribution) -> str:
    scenario = distribution.scenario
    import json

    return json.dumps(
        {
            "master_seed": scenario.master_seed,
            "resampling_policy": scenario.resampling_method,
            "policy_params": scenario.resampling_params,
            "data_hash": distribution.data_hash or scenario.input_data_hash,
            "limitations": distribution.known_limitations
            or [
                "V1 books realized P&L at trade exit only.",
                "V1 excludes margin, prop-firm rules, exposure, and optimization.",
            ],
        },
        sort_keys=True,
    )
