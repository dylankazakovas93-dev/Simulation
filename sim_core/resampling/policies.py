from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
import warnings

import numpy as np
import pandas as pd

from sim_core.ingestion.csv_loader import sort_trades_chronologically
from sim_core.models import ResampledPath, SampledBlock, StrategyCoverage, Trade


class ResamplingPolicy(ABC):
    name: str

    @abstractmethod
    def sample(
        self,
        trades: Sequence[Trade],
        *,
        seed: int | None = None,
        path_index: int = 0,
        coverage: Sequence[StrategyCoverage] | None = None,
    ) -> ResampledPath:
        raise NotImplementedError


class HistoricalReplay(ResamplingPolicy):
    name = "historical_replay"

    def sample(
        self,
        trades: Sequence[Trade],
        *,
        seed: int | None = None,
        path_index: int = 0,
        coverage: Sequence[StrategyCoverage] | None = None,
    ) -> ResampledPath:
        del seed, coverage
        sorted_trades = sort_trades_chronologically(trades)
        sampled_blocks = [
            SampledBlock(path_index, month, month, self.name) for month in _sorted_source_months(sorted_trades)
        ]
        return ResampledPath(sorted_trades, sampled_blocks)


class SameCalendarMonthBootstrap(ResamplingPolicy):
    """Sample source months with the same calendar month as each target month."""

    name = "same_calendar_month_bootstrap"

    def __init__(self, months: int, *, start_month: str | pd.Period | None = None) -> None:
        if months <= 0:
            raise ValueError("months must be positive")
        self.months = months
        self.start_month = pd.Period(start_month, "M") if start_month else None

    def sample(
        self,
        trades: Sequence[Trade],
        *,
        seed: int | None = None,
        path_index: int = 0,
        coverage: Sequence[StrategyCoverage] | None = None,
    ) -> ResampledPath:
        rng = _rng_for_path(seed, path_index)
        _warn_if_coverage_absent(coverage)
        source_months = _sorted_source_months(trades, coverage=coverage)
        if not source_months:
            raise ValueError("no source months available for requested bootstrap")
        start_month = self.start_month or source_months[0]
        target_months = [start_month + offset for offset in range(self.months)]
        sampled = []
        for target_month in target_months:
            candidates = [month for month in source_months if month.month == target_month.month]
            if not candidates:
                raise ValueError(f"no source months available for calendar month {target_month.month}")
            sampled.append(candidates[int(rng.integers(0, len(candidates)))])
        return _materialize_months(trades, sampled, target_months, self.name, path_index)


class MovingBlockBootstrap(ResamplingPolicy):
    """Sample contiguous blocks of historical months, synchronized across strategies."""

    name = "moving_block_bootstrap"

    def __init__(self, months: int, *, block_length: int, start_month: str | pd.Period | None = None) -> None:
        if months <= 0:
            raise ValueError("months must be positive")
        if block_length <= 0:
            raise ValueError("block_length must be positive")
        self.months = months
        self.block_length = block_length
        self.start_month = pd.Period(start_month, "M") if start_month else None

    def sample(
        self,
        trades: Sequence[Trade],
        *,
        seed: int | None = None,
        path_index: int = 0,
        coverage: Sequence[StrategyCoverage] | None = None,
    ) -> ResampledPath:
        rng = _rng_for_path(seed, path_index)
        _warn_if_coverage_absent(coverage)
        source_months = _sorted_source_months(trades, coverage=coverage)
        if not source_months:
            raise ValueError("no source months available for requested bootstrap")
        runs = _consecutive_runs(source_months)
        # Valid block starts must lie inside a single run of calendar-consecutive
        # months; a gap or dataset boundary never bridges a block.
        valid_starts = [
            (run_index, offset)
            for run_index, run in enumerate(runs)
            for offset in range(len(run) - self.block_length + 1)
        ]
        if not valid_starts:
            raise ValueError(
                f"no run of consecutive verified months is long enough for "
                f"block_length={self.block_length} (longest run="
                f"{max(len(run) for run in runs)})"
            )
        target_start = self.start_month or source_months[0]
        target_months = [target_start + offset for offset in range(self.months)]
        sampled: list[pd.Period] = []
        block_count = 0
        while len(sampled) < self.months:
            run_index, offset = valid_starts[int(rng.integers(0, len(valid_starts)))]
            sampled.extend(runs[run_index][offset : offset + self.block_length])
            block_count += 1
        path = _materialize_months(
            trades, sampled[: self.months], target_months, self.name, path_index
        )
        diagnostics = {
            "policy": self.name,
            "consecutive_runs": len(runs),
            "max_run_length": max(len(run) for run in runs),
            "block_length": self.block_length,
            "blocks_drawn": block_count,
            "restarts_due_to_boundary": max(block_count - 1, 0),
        }
        return ResampledPath(path.trades, path.sampled_blocks, diagnostics)


class StationaryBlockBootstrap(ResamplingPolicy):
    """Stationary monthly bootstrap with geometric block resets."""

    name = "stationary_block_bootstrap"

    def __init__(
        self,
        months: int,
        *,
        expected_block_length: float,
        start_month: str | pd.Period | None = None,
    ) -> None:
        if months <= 0:
            raise ValueError("months must be positive")
        if expected_block_length <= 0:
            raise ValueError("expected_block_length must be positive")
        self.months = months
        self.expected_block_length = expected_block_length
        self.start_month = pd.Period(start_month, "M") if start_month else None

    def sample(
        self,
        trades: Sequence[Trade],
        *,
        seed: int | None = None,
        path_index: int = 0,
        coverage: Sequence[StrategyCoverage] | None = None,
    ) -> ResampledPath:
        rng = _rng_for_path(seed, path_index)
        _warn_if_coverage_absent(coverage)
        source_months = _sorted_source_months(trades, coverage=coverage)
        if not source_months:
            raise ValueError("no source months available for requested bootstrap")
        runs = _consecutive_runs(source_months)
        # Positions are (run_index, offset); advancing only walks within a run so
        # a gap or dataset boundary forces a restart instead of silently jumping
        # across non-consecutive months.
        positions = [
            (run_index, offset) for run_index, run in enumerate(runs) for offset in range(len(run))
        ]
        reset_probability = min(1.0, 1.0 / self.expected_block_length)
        run_index, offset = positions[int(rng.integers(0, len(positions)))]
        sampled: list[pd.Period] = []
        boundary_restarts = 0
        reset_restarts = 0
        for _ in range(self.months):
            if sampled and rng.random() < reset_probability:
                run_index, offset = positions[int(rng.integers(0, len(positions)))]
                reset_restarts += 1
            sampled.append(runs[run_index][offset])
            if offset + 1 < len(runs[run_index]):
                offset += 1
            else:
                run_index, offset = positions[int(rng.integers(0, len(positions)))]
                boundary_restarts += 1
        target_start = self.start_month or source_months[0]
        target_months = [target_start + offset_i for offset_i in range(self.months)]
        path = _materialize_months(trades, sampled, target_months, self.name, path_index)
        diagnostics = {
            "policy": self.name,
            "consecutive_runs": len(runs),
            "reset_probability": reset_probability,
            "restarts_due_to_boundary": boundary_restarts,
            "restarts_due_to_reset": reset_restarts,
        }
        return ResampledPath(path.trades, path.sampled_blocks, diagnostics)


def _sorted_source_months(
    trades: Sequence[Trade],
    *,
    coverage: Sequence[StrategyCoverage] | None = None,
) -> list[pd.Period]:
    months = {trade.source_month for trade in trades}
    if coverage:
        partial_months: set[pd.Period] = set()
        for item in coverage:
            partial_months.update(item.partial_months)
            months.update(item.complete_months())
        months.difference_update(partial_months)
    return sorted(months)


def _warn_if_coverage_absent(coverage: Sequence[StrategyCoverage] | None) -> None:
    """Single source of the coverage-absent warning, shared by every bootstrap."""

    if coverage is None:
        warnings.warn(
            "coverage metadata absent; missing months cannot be distinguished from "
            "unverified flat months",
            RuntimeWarning,
            stacklevel=3,
        )


def _consecutive_runs(months: Sequence[pd.Period]) -> list[list[pd.Period]]:
    """Split sorted unique month Periods into maximal calendar-consecutive runs.

    A gap (``next != prev + 1``) starts a new run, so block bootstraps never
    treat non-consecutive source months as contiguous.
    """

    runs: list[list[pd.Period]] = []
    for month in months:
        if runs and month == runs[-1][-1] + 1:
            runs[-1].append(month)
        else:
            runs.append([month])
    return runs


def _rng_for_path(master_seed: int | None, path_index: int) -> np.random.Generator:
    if path_index < 0:
        raise ValueError("path_index cannot be negative")
    if master_seed is None:
        return np.random.default_rng()
    seed_sequence = np.random.SeedSequence(master_seed)
    return np.random.default_rng(seed_sequence.spawn(path_index + 1)[path_index])


def _materialize_months(
    trades: Sequence[Trade],
    source_months: Sequence[pd.Period],
    target_months: Sequence[pd.Period],
    policy_name: str,
    path_index: int,
) -> ResampledPath:
    trades_by_month: dict[pd.Period, list[Trade]] = {}
    for trade in trades:
        trades_by_month.setdefault(trade.source_month, []).append(trade)

    sampled_trades: list[Trade] = []
    sampled_blocks: list[SampledBlock] = []
    for source_month, target_month in zip(source_months, target_months, strict=True):
        sampled_blocks.append(SampledBlock(path_index, target_month, source_month, policy_name))
        sampled_trades.extend(
            trade.shifted_to_month(target_month) for trade in trades_by_month.get(source_month, [])
        )
    return ResampledPath(sort_trades_chronologically(sampled_trades), sampled_blocks)
