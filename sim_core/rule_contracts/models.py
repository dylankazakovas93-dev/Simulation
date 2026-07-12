from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Generic, TypeVar


class LifecycleStage(StrEnum):
    EVALUATION = "evaluation"
    FUNDED = "funded"


class ContractStatus(StrEnum):
    ENABLED = "enabled"
    SOURCE_GAP = "source_gap"
    RETIRED = "retired"
    CONDITIONAL = "conditional"


class DrawdownMode(StrEnum):
    EOD_TRAILING = "eod_trailing"
    INTRADAY_TRAILING = "intraday_trailing"
    FIXED = "fixed"


class DrawdownUpdateTiming(StrEnum):
    END_OF_DAY = "end_of_day"
    INTRATRADE = "intratrade"
    FIXED = "fixed"


class ThresholdComparator(StrEnum):
    GREATER_THAN = ">"
    GREATER_THAN_OR_EQUAL = ">="
    LESS_THAN = "<"
    LESS_THAN_OR_EQUAL = "<="


class DailyLossConsequence(StrEnum):
    NONE = "none"
    SOFT_PAUSE = "soft_pause"
    HARD_FAILURE = "hard_failure"


class InactivityTimeBasis(StrEnum):
    TRADING_DAYS = "trading_days"
    CALENDAR_DAYS = "calendar_days"
    CALENDAR_WEEK = "calendar_week"


class RuleExactness(StrEnum):
    EXACT = "exact"
    NON_RANKABLE = "non_rankable"
    SOURCE_GAP = "source_gap"
    CONDITIONAL = "conditional"


T = TypeVar("T")


@dataclass(frozen=True)
class SourceReference:
    document: str
    sha256: str
    page: str
    article: str
    capture_date: str = "2026-07-12"


@dataclass(frozen=True)
class Sourced(Generic[T]):
    """One normalized value and the exact source that supports it."""

    value: T
    source: SourceReference
    exactness: RuleExactness = RuleExactness.EXACT
    raw_wording: str = ""


@dataclass(frozen=True)
class Identity:
    firm: str
    program: str
    account_size: int
    stage: LifecycleStage
    account_name: str
    source_version: str


@dataclass(frozen=True)
class Economics:
    profit_split: Sourced[float]
    activation_fee: Sourced[float] | None = None
    evaluation_fee: Sourced[float] | None = None


@dataclass(frozen=True)
class Drawdown:
    amount: Sourced[float]
    mode: Sourced[DrawdownMode]
    update_timing: Sourced[DrawdownUpdateTiming]
    locks_at_starting_balance: Sourced[bool]
    breach_comparator: Sourced[ThresholdComparator]


@dataclass(frozen=True)
class DailyLoss:
    amount: Sourced[float] | None
    consequence: Sourced[DailyLossConsequence]
    reset_time: Sourced[str] | None = None
    includes_unrealized: Sourced[bool] | None = None


@dataclass(frozen=True)
class PositionLimits:
    max_micro_contracts: Sourced[int] | None
    scaling_exact: Sourced[bool]


@dataclass(frozen=True)
class Consistency:
    percent: Sourced[float] | None
    comparator: Sourced[ThresholdComparator] | None
    target_extension: Sourced[bool] | None = None


@dataclass(frozen=True)
class Payouts:
    min_payout: Sourced[float] | None
    max_payout: Sourced[float] | None
    payout_fraction: Sourced[float] | None
    winning_days: Sourced[int] | None
    winning_day_threshold: Sourced[float] | None
    sequential_caps: tuple[Sourced[float], ...] = ()
    buffer: Sourced[float] | None = None


@dataclass(frozen=True)
class Inactivity:
    limit: Sourced[int] | None
    basis: Sourced[InactivityTimeBasis] | None


@dataclass(frozen=True)
class Compatibility:
    manual_only: Sourced[bool] | None = None
    automation_allowed: Sourced[bool] | None = None
    news_requires_calendar: Sourced[bool] | None = None
    price_limit_requires_data: Sourced[bool] | None = None


@dataclass(frozen=True)
class Transition:
    evaluation_target: Sourced[float] | None = None
    replacement_supported: Sourced[bool] | None = None
    automatic: Sourced[bool] | None = None


@dataclass(frozen=True)
class RuleContract:
    id: str
    identity: Identity
    status: ContractStatus
    economics: Economics | None
    drawdown: Drawdown | None
    daily_loss: DailyLoss | None
    position_limits: PositionLimits | None
    consistency: Consistency | None
    payouts: Payouts | None
    inactivity: Inactivity | None
    compatibility: Compatibility | None
    transition: Transition | None
    exactness: RuleExactness
    disabled_reason: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def profile_key(self) -> str:
        return f"{self.identity.firm} - {self.identity.account_name}"

    @property
    def rankable(self) -> bool:
        return self.status is ContractStatus.ENABLED and self.exactness is RuleExactness.EXACT
