from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

from sim_core.models import AccountConfig, ResampledPath, StrategyCoverage, Trade
from sim_core.resampling.policies import ResamplingPolicy


@dataclass(frozen=True)
class PathEnsemble:
    paths: list[ResampledPath]


def run_path_ensemble(
    trades: Sequence[Trade],
    policy: ResamplingPolicy,
    *,
    n_paths: int,
    master_seed: int,
    account: AccountConfig | None = None,
    coverage: Sequence[StrategyCoverage] | None = None,
) -> PathEnsemble:
    del account
    paths = [
        policy.sample(trades, seed=master_seed, path_index=path_index, coverage=coverage)
        for path_index in range(n_paths)
    ]
    return PathEnsemble(paths)
