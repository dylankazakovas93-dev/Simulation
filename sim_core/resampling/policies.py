from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

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
        rng = np.random.default_rng(seed)
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
        rng = np.random.default_rng(seed)
        source_months = _sorted_source_months(trades, coverage=coverage)
        if not source_months:
            raise ValueError("no source months available for requested bootstrap")
        target_start = self.start_month or source_months[0]
        target_months = [target_start + offset for offset in range(self.months)]
        sampled: list[pd.Period] = []
        while len(sampled) < self.months:
            max_start = len(source_months) - self.block_length
            if max_start < 0:
                raise ValueError("not enough complete source months for requested block_length")
            start = int(rng.integers(0, max_start + 1))
            sampled.extend(source_months[start : start + self.block_length])
        return _materialize_months(
            trades, sampled[: self.months], target_months, self.name, path_index
        )


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
        rng = np.random.default_rng(seed)
        source_months = _sorted_source_months(trades, coverage=coverage)
        if not source_months:
            raise ValueError("no source months available for requested bootstrap")
        reset_probability = min(1.0, 1.0 / self.expected_block_length)
        source_index = int(rng.integers(0, len(source_months)))
        sampled = []
        for _ in range(self.months):
            if sampled and rng.random() < reset_probability:
                source_index = int(rng.integers(0, len(source_months)))
            sampled.append(source_months[source_index])
            source_index += 1
            if source_index >= len(source_months):
                source_index = int(rng.integers(0, len(source_months)))
        target_start = self.start_month or source_months[0]
        target_months = [target_start + offset for offset in range(self.months)]
        return _materialize_months(trades, sampled, target_months, self.name, path_index)


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
