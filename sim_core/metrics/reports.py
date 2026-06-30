from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from sim_core.models import SimulationResult


def max_drawdown(result: SimulationResult) -> dict[str, float]:
    if not result.equity_path:
        return {"max_drawdown": 0.0, "max_drawdown_pct": 0.0}
    values = np.array([result.account.initial_equity, *[point.equity for point in result.equity_path]])
    running_max = np.maximum.accumulate(values)
    drawdowns = running_max - values
    pct = np.divide(drawdowns, running_max, out=np.zeros_like(drawdowns), where=running_max != 0)
    return {
        "max_drawdown": float(drawdowns.max(initial=0.0)),
        "max_drawdown_pct": float(pct.max(initial=0.0)),
    }


def ruin_probability(results: Sequence[SimulationResult], ruin_threshold: float | None = None) -> float:
    if not results:
        return 0.0
    ruined = 0
    for result in results:
        threshold = result.account.ruin_threshold if ruin_threshold is None else ruin_threshold
        if any(point.equity <= threshold for point in result.equity_path):
            ruined += 1
    return ruined / len(results)


def monthly_equity_percentiles(
    results: Sequence[SimulationResult],
    *,
    percentiles: Sequence[float] = (5, 50, 95),
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for path_index, result in enumerate(results):
        frame = result.to_equity_frame()
        if frame.empty:
            continue
        frame["month"] = frame["timestamp"].dt.to_period("M").astype(str)
        month_end = frame.sort_values("timestamp").groupby("month", as_index=False).tail(1)
        for _, row in month_end.iterrows():
            rows.append(
                {
                    "path_index": path_index,
                    "month": row["month"],
                    "equity": float(row["equity"]),
                }
            )
    if not rows:
        return pd.DataFrame(columns=["month", *[f"p{int(p)}" for p in percentiles]])
    frame = pd.DataFrame(rows)
    grouped = frame.groupby("month")["equity"]
    data = {"month": sorted(frame["month"].unique())}
    for percentile in percentiles:
        data[f"p{int(percentile)}"] = [
            float(np.percentile(grouped.get_group(month), percentile)) for month in data["month"]
        ]
    return pd.DataFrame(data)


def summarize_paths(results: Sequence[SimulationResult]) -> pd.DataFrame:
    rows = []
    for path_index, result in enumerate(results):
        drawdown = max_drawdown(result)
        rows.append(
            {
                "path_index": path_index,
                "terminal_equity": result.terminal_equity,
                "max_drawdown": drawdown["max_drawdown"],
                "max_drawdown_pct": drawdown["max_drawdown_pct"],
                "trade_count": len(result.trades),
            }
        )
    return pd.DataFrame(rows)
