from __future__ import annotations

from pathlib import Path

from sim_core.metrics.reports import monthly_equity_percentiles, summarize_paths
from sim_core.models import SimulationResult


def export_simulation_result(result: SimulationResult, output_dir: str | Path) -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    equity_path = output_path / "equity_path.csv"
    result.to_equity_frame().to_csv(equity_path, index=False)
    return {"equity_path": equity_path}


def export_simulation_batch(results: list[SimulationResult], output_dir: str | Path) -> dict[str, Path]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    summary_path = output_path / "path_summary.csv"
    monthly_path = output_path / "monthly_percentiles.csv"
    summarize_paths(results).to_csv(summary_path, index=False)
    monthly_equity_percentiles(results).to_csv(monthly_path, index=False)
    return {"path_summary": summary_path, "monthly_percentiles": monthly_path}
