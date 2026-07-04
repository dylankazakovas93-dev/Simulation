"""BLOCKER-2 — independent, reproducible per-path RNG streams.

Finding (HANDOFF Review 002): every `sample()` builds `np.random.default_rng(seed)`
and uses `path_index` only as a label, so paths at one seed are identical and the
cross-path distribution is degenerate. There is no ensemble runner that derives
independent per-path randomness.

Target: `run_path_ensemble` spawns one `Generator` per path from
`SeedSequence(master_seed)`; same master seed reproduces the whole ensemble;
different paths differ.
"""
from __future__ import annotations

import pandas as pd

from sim_core.ingestion.csv_loader import normalize_trade_frame
from sim_core.resampling.policies import SameCalendarMonthBootstrap


def _two_year_trades() -> list:
    rows = []
    for year in (2023, 2024):
        for month in (1, 2, 3):
            rows.append(
                {
                    "strategy_id": "s",
                    "instrument": "ES",
                    "entry_time": f"{year}-{month:02d}-05 09:30",
                    "exit_time": f"{year}-{month:02d}-05 10:00",
                    "pnl_dollars": 10 * year + month,
                }
            )
    return normalize_trade_frame(pd.DataFrame(rows), source_timezone="UTC")


def _source_months(path) -> tuple:
    return tuple(block.source_month for block in path.sampled_blocks)


def test_existing_sample_path_index_is_currently_inert():
    """RED (current API): path_index does not reach the RNG, so all paths are identical."""
    trades = _two_year_trades()
    policy = SameCalendarMonthBootstrap(months=3, start_month="2026-01")
    sources = {_source_months(policy.sample(trades, seed=99, path_index=i)) for i in range(8)}
    assert len(sources) > 1, "all paths identical at one seed: path_index is inert (BLOCKER-2)"


def test_run_path_ensemble_gives_independent_streams():
    """RED: ensemble runner not implemented yet (BLOCKER-2)."""
    from sim_core.execution.ensemble import run_path_ensemble  # noqa: PLC0415
    from sim_core.models import AccountConfig

    trades = _two_year_trades()
    dist = run_path_ensemble(
        trades,
        SameCalendarMonthBootstrap(months=3, start_month="2026-01"),
        n_paths=16,
        master_seed=12345,
        account=AccountConfig(initial_equity=100_000),
    )
    assert len(dist.paths) == 16
    assert len({_source_months(p) for p in dist.paths}) > 1


def test_same_master_seed_reproduces_full_ensemble():
    """RED: ensemble runner not implemented yet (BLOCKER-2 / T1)."""
    from sim_core.execution.ensemble import run_path_ensemble  # noqa: PLC0415

    trades = _two_year_trades()
    first = run_path_ensemble(
        trades, SameCalendarMonthBootstrap(months=3, start_month="2026-01"),
        n_paths=16, master_seed=777,
    )
    second = run_path_ensemble(
        trades, SameCalendarMonthBootstrap(months=3, start_month="2026-01"),
        n_paths=16, master_seed=777,
    )
    assert [_source_months(p) for p in first.paths] == [_source_months(p) for p in second.paths]


def test_path_indices_produce_non_identical_valid_paths():
    """RED: ensemble runner not implemented yet (BLOCKER-2)."""
    from sim_core.execution.ensemble import run_path_ensemble  # noqa: PLC0415

    trades = _two_year_trades()
    dist = run_path_ensemble(
        trades, SameCalendarMonthBootstrap(months=3, start_month="2026-01"),
        n_paths=24, master_seed=2024,
    )
    sources = [_source_months(p) for p in dist.paths]
    assert len(sources) == 24
    assert len(set(sources)) > 1
