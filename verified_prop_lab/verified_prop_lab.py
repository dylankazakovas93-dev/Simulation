from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal, Sequence

import numpy as np
import pandas as pd

DrawdownMode = Literal['eod_trailing', 'fixed', 'intraday_trailing']
Stage = Literal['evaluation', 'funded']
MissingExcursionPolicy = Literal['error', 'realized_only']


class PropLabValidationError(ValueError):
    pass


@dataclass(frozen=True)
class LedgerTrade:
    trade_id: str
    session_date: pd.Timestamp
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    pnl_points: float
    raw_stop_points: float | None = None
    stop_points: float | None = None
    mae_points: float | None = None
    mfe_points: float | None = None
    dollars_per_point: float = 2.0
    commission_round_turn: float = 0.0
    slippage_points_round_turn: float = 0.0
    strategy_id: str = 'strategy'
    sample_weight: float = 1.0
    result_type: str | None = None

    def __post_init__(self) -> None:
        if not self.trade_id:
            raise PropLabValidationError('trade_id is required')
        session = pd.Timestamp(self.session_date).normalize().tz_localize(None)
        entry = pd.Timestamp(self.entry_time)
        exit_ = pd.Timestamp(self.exit_time)
        if entry.tzinfo is None or entry.tz is None:
            raise PropLabValidationError(f'{self.trade_id}: entry_time must be timezone-aware')
        if exit_.tzinfo is None or exit_.tz is None:
            raise PropLabValidationError(f'{self.trade_id}: exit_time must be timezone-aware')
        entry = entry.tz_convert('UTC')
        exit_ = exit_.tz_convert('UTC')
        if exit_ < entry:
            raise PropLabValidationError(f'{self.trade_id}: exit precedes entry')
        if self.dollars_per_point <= 0:
            raise PropLabValidationError(f'{self.trade_id}: dollars_per_point must be positive')
        if self.commission_round_turn < 0 or self.slippage_points_round_turn < 0:
            raise PropLabValidationError(f'{self.trade_id}: costs cannot be negative')
        if self.sample_weight <= 0:
            raise PropLabValidationError(f'{self.trade_id}: sample_weight must be positive')
        if self.raw_stop_points is not None and self.raw_stop_points <= 0:
            raise PropLabValidationError(f'{self.trade_id}: raw_stop_points must be positive')
        if self.stop_points is not None and self.stop_points <= 0:
            raise PropLabValidationError(f'{self.trade_id}: stop_points must be positive')
        if self.raw_stop_points is not None and self.stop_points is not None:
            expected_stop = min(float(self.raw_stop_points), 200.0)
            if abs(float(self.stop_points) - expected_stop) > 1e-6:
                raise PropLabValidationError(
                    f'{self.trade_id}: stop_points must equal min(raw_stop_points, 200)'
                )
        if self.mae_points is not None and self.mae_points < 0:
            raise PropLabValidationError(f'{self.trade_id}: mae_points must be non-negative')
        if self.mfe_points is not None and self.mfe_points < 0:
            raise PropLabValidationError(f'{self.trade_id}: mfe_points must be non-negative')
        object.__setattr__(self, 'session_date', session)
        object.__setattr__(self, 'entry_time', entry)
        object.__setattr__(self, 'exit_time', exit_)

    @property
    def source_month(self) -> pd.Period:
        return pd.Period(self.session_date, 'M')

    @property
    def effective_stop_points(self) -> float:
        if self.stop_points is not None:
            return float(self.stop_points)
        if self.raw_stop_points is not None:
            return min(float(self.raw_stop_points), 200.0)
        raise PropLabValidationError(f'{self.trade_id}: stop_points are required for R-normalized scaling')

    @property
    def pnl_r(self) -> float:
        return float(self.pnl_points) / self.effective_stop_points

    @property
    def mae_r(self) -> float:
        if self.mae_points is None:
            raise PropLabValidationError(f'{self.trade_id}: mae_points are required for MAE-R')
        return float(self.mae_points) / self.effective_stop_points

    @property
    def mfe_r(self) -> float:
        if self.mfe_points is None:
            raise PropLabValidationError(f'{self.trade_id}: mfe_points are required for MFE-R')
        return float(self.mfe_points) / self.effective_stop_points

    def scaled_for_point_volatility(self, point_scale: float) -> 'LedgerTrade':
        if point_scale <= 0:
            raise PropLabValidationError('point_scale must be positive')
        if self.raw_stop_points is None:
            raise PropLabValidationError(f'{self.trade_id}: raw_stop_points are required for point-volatility scaling')
        scaled_raw_stop = float(self.raw_stop_points) * float(point_scale)
        scaled_stop = min(scaled_raw_stop, 200.0)
        return replace(
            self,
            raw_stop_points=scaled_raw_stop,
            stop_points=scaled_stop,
            pnl_points=self.pnl_r * scaled_stop,
            mae_points=self.mae_r * scaled_stop,
            mfe_points=self.mfe_r * scaled_stop,
        )

    def shifted_to_session_month(self, target_month: pd.Period) -> 'LedgerTrade':
        target_month = pd.Period(target_month, 'M')
        source_start = self.source_month.start_time
        target_start = target_month.start_time
        day_offset = int((self.session_date - source_start).days)
        target_session = min(target_start + pd.Timedelta(days=day_offset), target_month.end_time.normalize())
        delta = target_session - self.session_date
        return replace(
            self,
            trade_id=f'{self.trade_id}@{target_month}',
            session_date=target_session,
            entry_time=self.entry_time + delta,
            exit_time=self.exit_time + delta,
        )


@dataclass(frozen=True)
class PropRule:
    firm: str
    account_name: str
    starting_balance: float
    max_loss: float
    drawdown_mode: DrawdownMode
    max_contracts: int
    floor_lock_balance: float | None = None
    daily_loss_limit: float | None = None
    daily_loss_hard: bool = False
    min_winning_days: int = 0
    winning_day_threshold: float = 0.0
    consistency_pct: float | None = None
    min_payout: float = 0.0
    payout_reserve: float = 0.0
    payout_profit_fraction: float = 1.0
    profit_split: float = 1.0
    payout_caps: tuple[float | None, ...] = (None,)
    max_payouts: int | None = None
    payout_cadence_days: int = 0

    def __post_init__(self) -> None:
        if self.starting_balance <= 0 or self.max_loss <= 0:
            raise PropLabValidationError('starting_balance and max_loss must be positive')
        if self.max_contracts <= 0:
            raise PropLabValidationError('max_contracts must be positive')
        if not 0 < self.profit_split <= 1:
            raise PropLabValidationError('profit_split must be in (0, 1]')
        if not 0 < self.payout_profit_fraction <= 1:
            raise PropLabValidationError('payout_profit_fraction must be in (0, 1]')
        if self.consistency_pct is not None and not 0 < self.consistency_pct <= 1:
            raise PropLabValidationError('consistency_pct must be in (0, 1]')

    @property
    def starting_floor(self) -> float:
        return self.starting_balance - self.max_loss

    @property
    def lock_balance(self) -> float:
        return self.starting_balance if self.floor_lock_balance is None else self.floor_lock_balance

    def cap_for_payout(self, payout_number: int) -> float | None:
        if not self.payout_caps:
            return None
        index = min(max(payout_number - 1, 0), len(self.payout_caps) - 1)
        return self.payout_caps[index]


@dataclass(frozen=True)
class LifecyclePlan:
    key: str
    funded_rule: PropRule
    evaluation_rule: PropRule | None = None
    evaluation_profit_target: float = 0.0
    evaluation_fee: float = 0.0
    activation_fee: float = 0.0
    replacement_fee: float = 0.0


@dataclass(frozen=True)
class LifecycleSettings:
    start_stage: Stage = 'funded'
    current_balance: float | None = None
    current_floor: float | None = None
    current_winning_days: int = 0
    current_highest_winning_day: float = 0.0
    desired_gross_payout: float = 0.0
    required_post_payout_cushion: float = 0.0
    allow_replacements: bool = False
    max_external_fee_capital: float = 0.0
    auto_payout: bool = True
    missing_excursion_policy: MissingExcursionPolicy = 'error'


@dataclass(frozen=True)
class TradeAuditRow:
    path_id: int
    trade_id: str
    strategy_id: str
    session_date: str
    stage: Stage
    attempt: int
    payout_cycle: int
    contracts: int
    balance_before: float
    floor_before: float
    estimated_low_balance: float
    gross_pnl: float
    commission: float
    slippage: float
    net_pnl: float
    balance_after: float
    floor_after: float
    day_pnl_after: float
    trading_nav_after: float
    trading_peak_after: float
    trading_drawdown_after: float
    breached: bool
    taken: bool
    skip_reason: str | None
    excursion_quality: str


@dataclass(frozen=True)
class LifecycleEvent:
    path_id: int
    session_date: str
    event: str
    stage: Stage
    attempt: int
    amount: float
    balance: float
    floor: float
    note: str = ''


@dataclass(frozen=True)
class DrawdownPeriod:
    path_id: int
    start_trade_id: str
    start_session_date: str
    trough_trade_id: str
    trough_session_date: str
    recovery_trade_id: str | None
    recovery_session_date: str | None
    depth: float
    duration_trades: int
    duration_calendar_days: int | None


@dataclass(frozen=True)
class PathResult:
    path_id: int
    plan_key: str
    contracts: int
    terminal_stage: Stage
    terminal_failed: bool
    attempts: int
    payouts_taken: int
    gross_payouts: float
    cash_payouts_after_split: float
    external_fees: float
    net_external_cash: float
    ending_balance: float
    ending_floor: float
    max_trading_drawdown: float
    first_payout_day: int | None
    first_failure_day: int | None
    trade_rows: tuple[TradeAuditRow, ...]
    events: tuple[LifecycleEvent, ...]
    drawdown_periods: tuple[DrawdownPeriod, ...]
    first_five_qualifying_days_day: int | None = None
    first_payout_eligible_day: int | None = None
    five_days_before_failure: bool = False
    eligible_before_failure: bool = False
    payout_before_failure: bool = False
    failure_before_payout: bool = False
    unresolved_at_horizon: bool = False
    ending_cushion: float = 0.0
    balance_at_five_qualifying_days: float | None = None
    balance_at_payout_eligibility: float | None = None


@dataclass
class _State:
    stage: Stage
    rule: PropRule
    balance: float
    floor: float
    eod_peak: float
    attempt: int
    payout_cycle: int = 1
    winning_days: int = 0
    daily_profits: list[float] = field(default_factory=list)
    cycle_start_balance: float = 0.0
    cycle_net_profit: float = 0.0
    payouts_taken: int = 0


@dataclass
class _DrawdownTracker:
    path_id: int
    peak_nav: float
    peak_trade_id: str = 'START'
    peak_date: pd.Timestamp | None = None
    active_start_trade_id: str | None = None
    active_start_date: pd.Timestamp | None = None
    trough_trade_id: str | None = None
    trough_date: pd.Timestamp | None = None
    trough_depth: float = 0.0
    active_trade_count: int = 0
    max_depth: float = 0.0
    periods: list[DrawdownPeriod] = field(default_factory=list)

    def update(self, trade_id: str, session_date: pd.Timestamp, nav: float) -> float:
        if nav >= self.peak_nav - 1e-9:
            if self.active_start_trade_id is not None:
                self.periods.append(
                    DrawdownPeriod(
                        path_id=self.path_id,
                        start_trade_id=self.active_start_trade_id,
                        start_session_date=str(self.active_start_date.date()),
                        trough_trade_id=self.trough_trade_id or self.active_start_trade_id,
                        trough_session_date=str((self.trough_date or self.active_start_date).date()),
                        recovery_trade_id=trade_id,
                        recovery_session_date=str(session_date.date()),
                        depth=self.trough_depth,
                        duration_trades=self.active_trade_count,
                        duration_calendar_days=int((session_date - self.active_start_date).days),
                    )
                )
            self.peak_nav = max(self.peak_nav, nav)
            self.peak_trade_id = trade_id
            self.peak_date = session_date
            self.active_start_trade_id = None
            self.active_start_date = None
            self.trough_trade_id = None
            self.trough_date = None
            self.trough_depth = 0.0
            self.active_trade_count = 0
            return 0.0
        depth = self.peak_nav - nav
        if self.active_start_trade_id is None:
            self.active_start_trade_id = self.peak_trade_id
            self.active_start_date = self.peak_date or session_date
            self.active_trade_count = 1
        else:
            self.active_trade_count += 1
        if depth >= self.trough_depth:
            self.trough_depth = depth
            self.trough_trade_id = trade_id
            self.trough_date = session_date
        self.max_depth = max(self.max_depth, depth)
        return depth

    def close_unrecovered(self) -> None:
        if self.active_start_trade_id is None:
            return
        self.periods.append(
            DrawdownPeriod(
                path_id=self.path_id,
                start_trade_id=self.active_start_trade_id,
                start_session_date=str(self.active_start_date.date()),
                trough_trade_id=self.trough_trade_id or self.active_start_trade_id,
                trough_session_date=str((self.trough_date or self.active_start_date).date()),
                recovery_trade_id=None,
                recovery_session_date=None,
                depth=self.trough_depth,
                duration_trades=self.active_trade_count,
                duration_calendar_days=None,
            )
        )
        self.active_start_trade_id = None


def _validate_settings(plan: LifecyclePlan, settings: LifecycleSettings, contracts: int) -> None:
    if contracts <= 0:
        raise PropLabValidationError('contracts must be positive')
    active_rule = plan.evaluation_rule if settings.start_stage == 'evaluation' else plan.funded_rule
    if active_rule is None:
        raise PropLabValidationError('evaluation start requested without evaluation rule')
    if contracts > active_rule.max_contracts or contracts > plan.funded_rule.max_contracts:
        raise PropLabValidationError('contract limit exceeded')
    balance = active_rule.starting_balance if settings.current_balance is None else settings.current_balance
    floor = active_rule.starting_floor if settings.current_floor is None else settings.current_floor
    if floor < active_rule.starting_floor - 1e-9:
        raise PropLabValidationError('current_floor cannot be below the contractual starting floor')
    if floor > balance + 1e-9:
        raise PropLabValidationError('current_floor cannot exceed current_balance')


def _trade_cash(trade: LedgerTrade, contracts: int) -> tuple[float, float, float, float]:
    gross = trade.pnl_points * trade.dollars_per_point * contracts
    commission = trade.commission_round_turn * contracts
    slippage = trade.slippage_points_round_turn * trade.dollars_per_point * contracts
    return gross, commission, slippage, gross - commission - slippage


def _adverse_cash(trade: LedgerTrade, contracts: int, policy: MissingExcursionPolicy) -> tuple[float, str]:
    if trade.mae_points is not None:
        return -abs(trade.mae_points) * trade.dollars_per_point * contracts, 'mae'
    if policy == 'error':
        raise PropLabValidationError(
            f'{trade.trade_id}: MAE is required for exact intraday drawdown-barrier simulation'
        )
    _, _, _, net = _trade_cash(trade, contracts)
    return min(0.0, net), 'realized_only_optimistic'


def _eligible_for_payout(state: _State) -> bool:
    rule = state.rule
    available = _gross_payout_available(state)
    if available + 1e-9 < rule.min_payout:
        return False
    cycle_profit = state.cycle_net_profit
    if state.winning_days < rule.min_winning_days:
        return False
    if rule.consistency_pct is None:
        return True
    positive = [value for value in state.daily_profits if value > 0]
    return bool(positive and cycle_profit > 0 and max(positive) <= rule.consistency_pct * cycle_profit + 1e-9)


def _gross_payout_available(state: _State) -> float:
    rule = state.rule
    withdrawable_profit = max(0.0, state.balance - state.rule.starting_balance - rule.payout_reserve)
    gross = withdrawable_profit * rule.payout_profit_fraction
    cap = rule.cap_for_payout(state.payouts_taken + 1)
    if cap is not None:
        gross = min(gross, cap)
    return gross if gross + 1e-9 >= rule.min_payout else 0.0


def simulate_lifecycle_path(
    trades: Sequence[LedgerTrade],
    plan: LifecyclePlan,
    *,
    contracts: int,
    settings: LifecycleSettings,
    path_id: int = 0,
    clock_start_date: pd.Timestamp | None = None,
) -> PathResult:
    _validate_settings(plan, settings, contracts)
    ordered = sorted(trades, key=lambda t: (t.session_date, t.exit_time, t.entry_time, t.trade_id))
    if not ordered:
        raise PropLabValidationError('at least one trade is required')
    clock_origin = (
        ordered[0].session_date
        if clock_start_date is None
        else pd.Timestamp(clock_start_date).normalize().tz_localize(None)
    )
    if any(trade.session_date < clock_origin for trade in ordered):
        raise PropLabValidationError('simulated trade occurs before the supplied lifecycle clock origin')
    rule = plan.evaluation_rule if settings.start_stage == 'evaluation' else plan.funded_rule
    assert rule is not None
    balance = rule.starting_balance if settings.current_balance is None else float(settings.current_balance)
    floor = rule.starting_floor if settings.current_floor is None else float(settings.current_floor)
    state = _State(
        stage=settings.start_stage,
        rule=rule,
        balance=balance,
        floor=floor,
        eod_peak=max(rule.starting_balance, balance),
        attempt=1,
        winning_days=settings.current_winning_days if settings.start_stage == 'funded' else 0,
        daily_profits=([settings.current_highest_winning_day] if settings.start_stage == 'funded' and settings.current_highest_winning_day > 0 else []),
        cycle_start_balance=rule.starting_balance,
        cycle_net_profit=max(0.0, balance - rule.starting_balance),
    )
    external_fees = plan.evaluation_fee if state.stage == 'evaluation' else 0.0
    gross_payouts = 0.0
    cash_payouts = 0.0
    first_payout_day: int | None = None
    first_failure_day: int | None = None
    first_five_qualifying_days_day: int | None = None
    first_payout_eligible_day: int | None = None
    balance_at_five_qualifying_days: float | None = None
    balance_at_payout_eligibility: float | None = None
    terminal_failed = False
    rows: list[TradeAuditRow] = []
    events: list[LifecycleEvent] = []
    trading_nav = rule.starting_balance
    dd = _DrawdownTracker(path_id=path_id, peak_nav=trading_nav, peak_date=clock_origin)
    current_session: pd.Timestamp | None = None
    day_pnl = 0.0
    session_trades: list[LedgerTrade] = []
    pending_replacement = False
    pending_funded_transition = False
    last_payout_date: pd.Timestamp | None = None

    def reset_account(stage: Stage) -> None:
        nonlocal state, trading_nav, dd, day_pnl
        new_rule = plan.evaluation_rule if stage == 'evaluation' else plan.funded_rule
        assert new_rule is not None
        state = _State(
            stage=stage,
            rule=new_rule,
            balance=new_rule.starting_balance,
            floor=new_rule.starting_floor,
            eod_peak=new_rule.starting_balance,
            attempt=state.attempt + 1 if stage == 'evaluation' else state.attempt,
            cycle_start_balance=new_rule.starting_balance,
        )
        trading_nav = new_rule.starting_balance
        dd.close_unrecovered()
        dd = _DrawdownTracker(path_id=path_id, peak_nav=trading_nav, peak_date=current_session)
        day_pnl = 0.0

    def day_number(session_date: pd.Timestamp) -> int:
        return int((pd.Timestamp(session_date).normalize().tz_localize(None) - clock_origin).days)

    def try_take_payout(session_date: pd.Timestamp) -> None:
        nonlocal gross_payouts, cash_payouts, first_payout_day, first_payout_eligible_day
        nonlocal balance_at_payout_eligibility, last_payout_date
        if state.stage != 'funded' or not settings.auto_payout:
            return
        if state.rule.max_payouts is not None and state.payouts_taken >= state.rule.max_payouts:
            return
        if last_payout_date is not None and (session_date - last_payout_date).days < state.rule.payout_cadence_days:
            return
        if not _eligible_for_payout(state):
            return
        if first_payout_eligible_day is None:
            first_payout_eligible_day = day_number(session_date)
            balance_at_payout_eligibility = state.balance
        available = _gross_payout_available(state)
        desired = settings.desired_gross_payout
        gross = available if desired <= 0 else min(available, desired)
        if desired > 0 and gross + 1e-9 < desired:
            return
        min_after = state.rule.starting_balance + max(
            settings.required_post_payout_cushion,
            state.rule.payout_reserve,
        )
        if gross <= 0 or state.balance - gross < min_after - 1e-9:
            return
        state.balance -= gross
        cash = gross * state.rule.profit_split
        gross_payouts += gross
        cash_payouts += cash
        state.payouts_taken += 1
        state.payout_cycle += 1
        state.winning_days = 0
        state.daily_profits = []
        state.cycle_start_balance = state.balance
        state.cycle_net_profit = 0.0
        last_payout_date = session_date
        if first_payout_day is None:
            first_payout_day = day_number(session_date)
        events.append(LifecycleEvent(path_id, str(session_date.date()), 'payout', state.stage, state.attempt, cash, state.balance, state.floor, f'gross account withdrawal={gross:.2f}'))

    if state.stage == 'funded' and state.winning_days >= state.rule.min_winning_days and state.rule.min_winning_days > 0:
        first_five_qualifying_days_day = 0
        balance_at_five_qualifying_days = state.balance
    if clock_start_date is not None:
        try_take_payout(clock_origin)

    def finish_session(session_date: pd.Timestamp) -> None:
        nonlocal day_pnl, gross_payouts, cash_payouts, first_payout_day, external_fees
        nonlocal terminal_failed, pending_replacement, pending_funded_transition, last_payout_date
        nonlocal first_five_qualifying_days_day, first_payout_eligible_day
        nonlocal balance_at_five_qualifying_days, balance_at_payout_eligibility
        if terminal_failed or pending_replacement:
            return
        if day_pnl > 0:
            state.daily_profits.append(day_pnl)
            if state.stage == 'funded' and day_pnl + 1e-9 >= state.rule.winning_day_threshold:
                state.winning_days += 1
        elif state.stage == 'funded':
            state.daily_profits.append(day_pnl)
        if (
            state.stage == 'funded'
            and state.rule.min_winning_days > 0
            and state.winning_days >= state.rule.min_winning_days
            and first_five_qualifying_days_day is None
        ):
            first_five_qualifying_days_day = day_number(session_date)
            balance_at_five_qualifying_days = state.balance
        if state.rule.drawdown_mode == 'eod_trailing':
            state.eod_peak = max(state.eod_peak, state.balance)
            state.floor = max(
                state.floor,
                min(state.rule.lock_balance, state.eod_peak - state.rule.max_loss),
            )
        if state.stage == 'evaluation' and state.balance - state.rule.starting_balance >= plan.evaluation_profit_target - 1e-9:
            pending_funded_transition = True
            events.append(LifecycleEvent(path_id, str(session_date.date()), 'evaluation_passed', state.stage, state.attempt, 0.0, state.balance, state.floor))
            return
        try_take_payout(session_date)

    index = 0
    while index < len(ordered):
        trade = ordered[index]
        if current_session is None or trade.session_date != current_session:
            if current_session is not None:
                finish_session(current_session)
            current_session = trade.session_date
            day_pnl = 0.0
            if pending_funded_transition:
                external_fees += plan.activation_fee
                prior_attempt = state.attempt
                new_rule = plan.funded_rule
                state = _State(
                    stage='funded',
                    rule=new_rule,
                    balance=new_rule.starting_balance,
                    floor=new_rule.starting_floor,
                    eod_peak=new_rule.starting_balance,
                    attempt=prior_attempt,
                    cycle_start_balance=new_rule.starting_balance,
                )
                trading_nav = new_rule.starting_balance
                dd.close_unrecovered()
                dd = _DrawdownTracker(path_id=path_id, peak_nav=trading_nav, peak_date=current_session)
                pending_funded_transition = False
                events.append(LifecycleEvent(path_id, str(current_session.date()), 'funded_activated', 'funded', state.attempt, -plan.activation_fee, state.balance, state.floor))
            if pending_replacement:
                projected = external_fees + plan.evaluation_fee + plan.replacement_fee
                if (
                    not settings.allow_replacements
                    or plan.evaluation_rule is None
                    or projected > settings.max_external_fee_capital + 1e-9
                ):
                    terminal_failed = True
                    break
                external_fees = projected
                reset_account('evaluation')
                pending_replacement = False
                events.append(LifecycleEvent(path_id, str(current_session.date()), 'replacement_evaluation', 'evaluation', state.attempt, -(plan.evaluation_fee + plan.replacement_fee), state.balance, state.floor))
        if terminal_failed or pending_replacement or pending_funded_transition:
            rows.append(
                TradeAuditRow(path_id, trade.trade_id, trade.strategy_id, str(trade.session_date.date()), state.stage, state.attempt, state.payout_cycle, contracts, state.balance, state.floor, state.balance, 0.0, 0.0, 0.0, 0.0, state.balance, state.floor, day_pnl, trading_nav, dd.peak_nav, max(0.0, dd.peak_nav - trading_nav), False, False, 'account_transition_pending', 'not_evaluated')
            )
            index += 1
            continue
        before = state.balance
        floor_before = state.floor
        gross, commission, slippage, net = _trade_cash(trade, contracts)
        adverse, quality = _adverse_cash(trade, contracts, settings.missing_excursion_policy)
        estimated_low = before + adverse
        breached = estimated_low <= state.floor + 1e-9
        taken = True
        if breached:
            state.balance = min(state.balance, state.floor)
            terminal_failed = True
            pending_replacement = settings.allow_replacements and plan.evaluation_rule is not None
            if pending_replacement:
                terminal_failed = False
            if first_failure_day is None:
                first_failure_day = day_number(trade.session_date)
            events.append(LifecycleEvent(path_id, str(trade.session_date.date()), f'{state.stage}_failed', state.stage, state.attempt, 0.0, state.balance, state.floor, 'drawdown barrier touched or crossed'))
            trading_nav += min(net, adverse)
        else:
            state.balance += net
            day_pnl += net
            state.cycle_net_profit += net
            trading_nav += net
            if state.rule.daily_loss_limit is not None and day_pnl <= -abs(state.rule.daily_loss_limit) + 1e-9:
                if state.rule.daily_loss_hard:
                    terminal_failed = True
                    if first_failure_day is None:
                        first_failure_day = day_number(trade.session_date)
                    events.append(LifecycleEvent(path_id, str(trade.session_date.date()), f'{state.stage}_failed', state.stage, state.attempt, 0.0, state.balance, state.floor, 'daily loss limit breached'))
                # A soft daily guard simply causes later trades that session to be skipped.
        drawdown_after = dd.update(trade.trade_id, trade.session_date, trading_nav)
        rows.append(
            TradeAuditRow(
                path_id, trade.trade_id, trade.strategy_id, str(trade.session_date.date()), state.stage, state.attempt, state.payout_cycle, contracts,
                before, floor_before, estimated_low, gross, commission, slippage, net, state.balance, state.floor, day_pnl,
                trading_nav, dd.peak_nav, drawdown_after, breached, taken, None, quality,
            )
        )
        if terminal_failed or pending_replacement:
            # All later trades in the same session are unavailable to a failed account.
            failed_session = trade.session_date
            index += 1
            while index < len(ordered) and ordered[index].session_date == failed_session:
                skipped = ordered[index]
                rows.append(
                    TradeAuditRow(path_id, skipped.trade_id, skipped.strategy_id, str(skipped.session_date.date()), state.stage, state.attempt, state.payout_cycle, contracts, state.balance, state.floor, state.balance, 0.0, 0.0, 0.0, 0.0, state.balance, state.floor, day_pnl, trading_nav, dd.peak_nav, max(0.0, dd.peak_nav - trading_nav), False, False, 'account_failed_earlier_in_session', 'not_evaluated')
                )
                index += 1
            continue
        if state.rule.daily_loss_limit is not None and not state.rule.daily_loss_hard and day_pnl <= -abs(state.rule.daily_loss_limit) + 1e-9:
            paused_session = trade.session_date
            index += 1
            while index < len(ordered) and ordered[index].session_date == paused_session:
                skipped = ordered[index]
                rows.append(
                    TradeAuditRow(path_id, skipped.trade_id, skipped.strategy_id, str(skipped.session_date.date()), state.stage, state.attempt, state.payout_cycle, contracts, state.balance, state.floor, state.balance, 0.0, 0.0, 0.0, 0.0, state.balance, state.floor, day_pnl, trading_nav, dd.peak_nav, max(0.0, dd.peak_nav - trading_nav), False, False, 'daily_loss_guard', 'not_evaluated')
                )
                index += 1
            continue
        index += 1
    if current_session is not None and not terminal_failed and not pending_replacement:
        finish_session(current_session)
    if pending_replacement:
        terminal_failed = True
    dd.close_unrecovered()
    return PathResult(
        path_id=path_id,
        plan_key=plan.key,
        contracts=contracts,
        terminal_stage=state.stage,
        terminal_failed=terminal_failed,
        attempts=state.attempt,
        payouts_taken=state.payouts_taken,
        gross_payouts=gross_payouts,
        cash_payouts_after_split=cash_payouts,
        external_fees=external_fees,
        net_external_cash=cash_payouts - external_fees,
        ending_balance=state.balance,
        ending_floor=state.floor,
        max_trading_drawdown=dd.max_depth,
        first_payout_day=first_payout_day,
        first_failure_day=first_failure_day,
        trade_rows=tuple(rows),
        events=tuple(events),
        drawdown_periods=tuple(dd.periods),
        first_five_qualifying_days_day=first_five_qualifying_days_day,
        first_payout_eligible_day=first_payout_eligible_day,
        five_days_before_failure=(
            first_five_qualifying_days_day is not None
            and (first_failure_day is None or first_five_qualifying_days_day < first_failure_day)
        ),
        eligible_before_failure=(
            first_payout_eligible_day is not None
            and (first_failure_day is None or first_payout_eligible_day < first_failure_day)
        ),
        payout_before_failure=(
            first_payout_day is not None
            and (first_failure_day is None or first_payout_day < first_failure_day)
        ),
        failure_before_payout=(
            first_failure_day is not None
            and (first_payout_day is None or first_failure_day <= first_payout_day)
        ),
        unresolved_at_horizon=first_payout_day is None and first_failure_day is None,
        ending_cushion=state.balance - state.floor,
        balance_at_five_qualifying_days=balance_at_five_qualifying_days,
        balance_at_payout_eligibility=balance_at_payout_eligibility,
    )


@dataclass(frozen=True)
class SampledPath:
    path_id: int
    trades: tuple[LedgerTrade, ...]
    manifest: tuple[tuple[str, ...], ...]
    clock_start_date: pd.Timestamp | None = None


@dataclass(frozen=True)
class ForwardScenario:
    point_scale: float
    scenario_label: str


@dataclass(frozen=True)
class ForwardVolumeGridResult:
    results: tuple[PathResult, ...]
    summary: pd.DataFrame
    sampled_trades: pd.DataFrame
    events: pd.DataFrame
    path_results: pd.DataFrame
    forecast_start_date: pd.Timestamp | None = None


def scenario_label_for_scale(point_scale: float) -> str:
    if abs(point_scale - 1.0) <= 1e-12:
        return 'Base'
    return f'{(point_scale - 1.0) * 100:+.0f}%'


def forecast_months(start_date: pd.Timestamp, end_date: pd.Timestamp) -> list[pd.Period]:
    start = pd.Timestamp(start_date).normalize().tz_localize(None)
    end = pd.Timestamp(end_date).normalize().tz_localize(None)
    if end < start:
        raise PropLabValidationError('forecast end date must be on or after start date')
    months = pd.period_range(start.to_period('M'), end.to_period('M'), freq='M')
    return [pd.Period(month, 'M') for month in months]


def valid_forecast_sessions(start_date: pd.Timestamp, end_date: pd.Timestamp, month: pd.Period) -> list[pd.Timestamp]:
    start = pd.Timestamp(start_date).normalize().tz_localize(None)
    end = pd.Timestamp(end_date).normalize().tz_localize(None)
    month_start = max(start, month.start_time.normalize())
    month_end = min(end, month.end_time.normalize())
    if month_end < month_start:
        return []
    return [pd.Timestamp(day).normalize() for day in pd.bdate_range(month_start, month_end)]


def validate_contract_values(contract_values: Sequence[int], *, max_contracts: int = 4) -> tuple[int, ...]:
    values = tuple(int(value) for value in contract_values)
    if not values:
        raise PropLabValidationError('select at least one contract size')
    if any(value < 1 or value > max_contracts for value in values):
        raise PropLabValidationError(f'contract sizes for this strategy interface must be between 1 and {max_contracts}')
    return values


class ForwardVolumeScenarioSampler:
    def __init__(
        self,
        trades: Sequence[LedgerTrade],
        *,
        forecast_start: str | pd.Timestamp,
        forecast_end: str | pd.Timestamp,
        monthly_trade_counts: dict[str | pd.Period, int],
    ):
        if not trades:
            raise PropLabValidationError('at least one source trade is required')
        self.trades = tuple(trades)
        self.forecast_start = pd.Timestamp(forecast_start).normalize().tz_localize(None)
        self.forecast_end = pd.Timestamp(forecast_end).normalize().tz_localize(None)
        self.months = forecast_months(self.forecast_start, self.forecast_end)
        self.monthly_trade_counts = {
            str(pd.Period(month, 'M')): int(count)
            for month, count in monthly_trade_counts.items()
        }
        self._validate_counts()

    def _validate_counts(self) -> None:
        for month in self.months:
            key = str(month)
            if key not in self.monthly_trade_counts:
                raise PropLabValidationError(f'missing expected trade count for forecast month {key}')
            count = self.monthly_trade_counts[key]
            if count < 0:
                raise PropLabValidationError(f'expected trade count for {key} cannot be negative')
            sessions = valid_forecast_sessions(self.forecast_start, self.forecast_end, month)
            if count > len(sessions):
                raise PropLabValidationError(
                    f'{key}: requested {count} trades but only {len(sessions)} weekday sessions are available'
                )

    def sample_paths(self, *, paths: int, master_seed: int) -> list[SampledPath]:
        if paths <= 0:
            raise PropLabValidationError('paths must be positive')
        child_seeds = np.random.SeedSequence(master_seed).spawn(paths)
        output: list[SampledPath] = []
        weights = np.asarray([trade.sample_weight for trade in self.trades], dtype=float)
        probabilities = weights / weights.sum()
        for path_id, child in enumerate(child_seeds):
            rng = np.random.default_rng(child)
            sampled: list[LedgerTrade] = []
            manifest: list[tuple[str, str, str]] = []
            sequence_number = 0
            for month in self.months:
                count = self.monthly_trade_counts[str(month)]
                if count == 0:
                    continue
                sessions = valid_forecast_sessions(self.forecast_start, self.forecast_end, month)
                source_indices = rng.choice(len(self.trades), size=count, replace=True, p=probabilities)
                session_indices = rng.choice(len(sessions), size=count, replace=False)
                order = rng.permutation(count)
                for order_position in order:
                    source = self.trades[int(source_indices[order_position])]
                    target_session = sessions[int(session_indices[order_position])]
                    shifted = shift_trade_to_session(source, target_session, sequence_number)
                    sampled.append(shifted)
                    manifest.append((str(month), source.trade_id, str(target_session.date())))
                    sequence_number += 1
            output.append(SampledPath(path_id, tuple(sampled), tuple(manifest), self.forecast_start))
        return output


def shift_trade_to_session(trade: LedgerTrade, target_session: pd.Timestamp, sequence_number: int) -> LedgerTrade:
    target_session = pd.Timestamp(target_session).normalize().tz_localize(None)
    delta = target_session - trade.session_date
    return replace(
        trade,
        trade_id=f'{trade.trade_id}@{target_session.date()}#{sequence_number}',
        session_date=target_session,
        entry_time=trade.entry_time + delta,
        exit_time=trade.exit_time + delta,
    )


class SameCalendarMonthSampler:
    def __init__(self, trades: Sequence[LedgerTrade], *, horizon_months: int, start_month: str | pd.Period):
        if horizon_months <= 0:
            raise PropLabValidationError('horizon_months must be positive')
        self.trades = tuple(trades)
        self.horizon_months = horizon_months
        self.start_month = pd.Period(start_month, 'M')
        self.by_month: dict[pd.Period, list[LedgerTrade]] = {}
        for trade in self.trades:
            self.by_month.setdefault(trade.source_month, []).append(trade)
        self.months = sorted(self.by_month)

    def sample_paths(self, *, paths: int, master_seed: int) -> list[SampledPath]:
        if paths <= 0:
            raise PropLabValidationError('paths must be positive')
        child_seeds = np.random.SeedSequence(master_seed).spawn(paths)
        output: list[SampledPath] = []
        for path_id, child in enumerate(child_seeds):
            rng = np.random.default_rng(child)
            sampled: list[LedgerTrade] = []
            manifest: list[tuple[str, str]] = []
            for offset in range(self.horizon_months):
                target = self.start_month + offset
                candidates = [month for month in self.months if month.month == target.month]
                if not candidates:
                    raise PropLabValidationError(f'no source month for calendar month {target.month}')
                source = candidates[int(rng.integers(0, len(candidates)))]
                manifest.append((str(target), str(source)))
                sampled.extend(trade.shifted_to_session_month(target) for trade in self.by_month[source])
            sampled.sort(key=lambda t: (t.session_date, t.exit_time, t.entry_time, t.trade_id))
            output.append(SampledPath(path_id, tuple(sampled), tuple(manifest)))
        return output


def run_common_path_grid(
    sampled_paths: Sequence[SampledPath],
    plans: Sequence[LifecyclePlan],
    contract_values: Sequence[int],
    settings_by_plan: dict[str, LifecycleSettings],
) -> tuple[list[PathResult], pd.DataFrame]:
    results: list[PathResult] = []
    for sampled in sampled_paths:
        for plan in plans:
            for contracts in contract_values:
                results.append(
                    simulate_lifecycle_path(
                        sampled.trades,
                        plan,
                        contracts=contracts,
                        settings=settings_by_plan[plan.key],
                        path_id=sampled.path_id,
                    )
                )
    rows: list[dict[str, float | int | str]] = []
    frame = pd.DataFrame([
        {
            'path_id': result.path_id,
            'plan': result.plan_key,
            'contracts': result.contracts,
            'failed': result.terminal_failed,
            'payouts': result.cash_payouts_after_split,
            'fees': result.external_fees,
            'net_cash': result.net_external_cash,
            'max_drawdown': result.max_trading_drawdown,
            'first_payout_day': result.first_payout_day,
        }
        for result in results
    ])
    for (plan, contracts), group in frame.groupby(['plan', 'contracts'], sort=False):
        net = group['net_cash'].astype(float).to_numpy()
        payout = group['payouts'].astype(float).to_numpy()
        first = group['first_payout_day'].dropna().astype(float).to_numpy()
        rows.append({
            'plan': plan,
            'contracts': int(contracts),
            'paths': int(len(group)),
            'failure_rate': float(group['failed'].mean()),
            'any_payout_rate': float((group['payouts'] > 0).mean()),
            'mean_net_cash': float(net.mean()),
            'variance_net_cash': float(net.var(ddof=1)) if len(net) > 1 else 0.0,
            'std_net_cash': float(net.std(ddof=1)) if len(net) > 1 else 0.0,
            'p05_net_cash': float(np.percentile(net, 5)),
            'p50_net_cash': float(np.percentile(net, 50)),
            'p95_net_cash': float(np.percentile(net, 95)),
            'mean_payout': float(payout.mean()),
            'p50_first_payout_day': float(np.percentile(first, 50)) if len(first) else np.nan,
            'p95_max_drawdown': float(np.percentile(group['max_drawdown'], 95)),
        })
    return results, pd.DataFrame(rows)


def _percentile_or_nan(values: Sequence[float], percentile: float) -> float:
    cleaned = np.asarray([float(value) for value in values if pd.notna(value)], dtype=float)
    if cleaned.size == 0:
        return float('nan')
    return float(np.percentile(cleaned, percentile))


def _rate(values: pd.Series) -> float:
    return float(values.astype(bool).mean()) if len(values) else 0.0


def run_forward_volume_grid(
    sampled_paths: Sequence[SampledPath],
    plan: LifecyclePlan,
    *,
    contract_values: Sequence[int],
    scenarios: Sequence[ForwardScenario | float],
    settings: LifecycleSettings,
    time_threshold_days: Sequence[int] = (10, 20, 30, 45, 60),
    max_contracts: int = 4,
) -> ForwardVolumeGridResult:
    contracts_to_run = validate_contract_values(contract_values, max_contracts=max_contracts)
    if not sampled_paths:
        raise PropLabValidationError('at least one sampled path is required')
    clock_start_dates = {path.clock_start_date for path in sampled_paths if path.clock_start_date is not None}
    if len(clock_start_dates) > 1:
        raise PropLabValidationError('sampled paths contain multiple lifecycle clock origins')
    forecast_start_date = next(iter(clock_start_dates)) if clock_start_dates else None
    normalized_scenarios: tuple[ForwardScenario, ...] = tuple(
        scenario
        if isinstance(scenario, ForwardScenario)
        else ForwardScenario(float(scenario), scenario_label_for_scale(float(scenario)))
        for scenario in scenarios
    )
    if not normalized_scenarios:
        raise PropLabValidationError('select at least one point-volatility scenario')
    thresholds = tuple(sorted({int(value) for value in time_threshold_days if int(value) >= 0}))

    results: list[PathResult] = []
    result_rows: list[dict[str, Any]] = []
    sampled_trade_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []

    for scenario in normalized_scenarios:
        if scenario.point_scale <= 0:
            raise PropLabValidationError('point-volatility scenarios must be positive')
        for sampled in sampled_paths:
            scaled_trades = tuple(
                trade.scaled_for_point_volatility(scenario.point_scale)
                for trade in sampled.trades
            )
            for trade in scaled_trades:
                sampled_trade_rows.append(
                    {
                        'path_id': sampled.path_id,
                        'point_scale': scenario.point_scale,
                        'scenario_label': scenario.scenario_label,
                        'clock_start_date': str(sampled.clock_start_date.date()) if sampled.clock_start_date is not None else None,
                        'trade_id': trade.trade_id,
                        'source_trade_id': trade.trade_id.split('@', 1)[0],
                        'session_date': str(trade.session_date.date()),
                        'entry_time': trade.entry_time.isoformat(),
                        'exit_time': trade.exit_time.isoformat(),
                        'strategy_id': trade.strategy_id,
                        'raw_stop_points': trade.raw_stop_points,
                        'stop_points': trade.stop_points,
                        'pnl_points': trade.pnl_points,
                        'mae_points': trade.mae_points,
                        'mfe_points': trade.mfe_points,
                        'pnl_r': trade.pnl_r,
                        'mae_r': trade.mae_r,
                        'mfe_r': trade.mfe_r,
                    }
                )
            for contracts in contracts_to_run:
                result = simulate_lifecycle_path(
                    scaled_trades,
                    plan,
                    contracts=contracts,
                    settings=settings,
                    path_id=sampled.path_id,
                    clock_start_date=sampled.clock_start_date,
                )
                results.append(result)
                row = {
                    'path_id': result.path_id,
                    'plan': result.plan_key,
                    'contracts': result.contracts,
                    'point_scale': scenario.point_scale,
                    'scenario_label': scenario.scenario_label,
                    'clock_start_date': str(sampled.clock_start_date.date()) if sampled.clock_start_date is not None else None,
                    'five_days_before_failure': result.five_days_before_failure,
                    'eligible_before_failure': result.eligible_before_failure,
                    'payout_before_failure': result.payout_before_failure,
                    'failure_before_payout': result.failure_before_payout,
                    'unresolved_at_horizon': result.unresolved_at_horizon,
                    'any_payout': result.payouts_taken > 0,
                    'payouts_taken': result.payouts_taken,
                    'cash_payouts_after_split': result.cash_payouts_after_split,
                    'gross_payouts': result.gross_payouts,
                    'external_fees': result.external_fees,
                    'net_external_cash': result.net_external_cash,
                    'ending_balance': result.ending_balance,
                    'ending_floor': result.ending_floor,
                    'ending_cushion': result.ending_cushion,
                    'max_trading_drawdown': result.max_trading_drawdown,
                    'first_five_qualifying_days_day': result.first_five_qualifying_days_day,
                    'first_payout_eligible_day': result.first_payout_eligible_day,
                    'first_payout_day': result.first_payout_day,
                    'first_failure_day': result.first_failure_day,
                    'balance_at_five_qualifying_days': result.balance_at_five_qualifying_days,
                    'balance_at_payout_eligibility': result.balance_at_payout_eligibility,
                }
                result_rows.append(row)
                for event in result.events:
                    event_rows.append(
                        {
                            'path_id': result.path_id,
                            'plan': result.plan_key,
                            'contracts': result.contracts,
                            'point_scale': scenario.point_scale,
                            'scenario_label': scenario.scenario_label,
                            'clock_start_date': str(sampled.clock_start_date.date()) if sampled.clock_start_date is not None else None,
                            'session_date': event.session_date,
                            'event': event.event,
                            'stage': event.stage,
                            'attempt': event.attempt,
                            'amount': event.amount,
                            'balance': event.balance,
                            'floor': event.floor,
                            'note': event.note,
                        }
                    )

    frame = pd.DataFrame(result_rows)
    summary_rows: list[dict[str, Any]] = []
    group_cols = ['plan', 'point_scale', 'scenario_label', 'contracts']
    for (plan_key, point_scale, label, contracts), group in frame.groupby(group_cols, sort=False):
        payout_days = group.loc[group['payout_before_failure'], 'first_payout_day'].dropna().astype(float)
        eligibility_days = group.loc[group['eligible_before_failure'], 'first_payout_eligible_day'].dropna().astype(float)
        five_days = group.loc[group['five_days_before_failure'], 'first_five_qualifying_days_day'].dropna().astype(float)
        net = group['net_external_cash'].astype(float).to_numpy()
        payout_cash = group['cash_payouts_after_split'].astype(float).to_numpy()
        gross_payout = group['gross_payouts'].astype(float).to_numpy()
        drawdown = group['max_trading_drawdown'].astype(float).to_numpy()
        cushion = group['ending_cushion'].astype(float).to_numpy()
        row = {
            'plan': plan_key,
            'point_scale': float(point_scale),
            'scenario_label': label,
            'contracts': int(contracts),
            'paths': int(len(group)),
            'five_days_before_failure_rate': _rate(group['five_days_before_failure']),
            'eligible_before_failure_rate': _rate(group['eligible_before_failure']),
            'payout_before_failure_rate': _rate(group['payout_before_failure']),
            'failure_before_payout_rate': _rate(group['failure_before_payout']),
            'unresolved_rate': _rate(group['unresolved_at_horizon']),
            'any_payout_rate': _rate(group['any_payout']),
            'avg_payout_count': float(group['payouts_taken'].astype(float).mean()),
            'avg_payout_cash': float(payout_cash.mean()),
            'p50_payout_cash': _percentile_or_nan(payout_cash, 50),
            'avg_gross_payout': float(gross_payout.mean()),
            'mean_net_cash': float(net.mean()),
            'p05_net_cash': _percentile_or_nan(net, 5),
            'p50_net_cash': _percentile_or_nan(net, 50),
            'p95_net_cash': _percentile_or_nan(net, 95),
            'p50_first_five_qualifying_days': _percentile_or_nan(five_days, 50),
            'p50_first_eligible_day': _percentile_or_nan(eligibility_days, 50),
            'p05_first_payout_day_conditional': _percentile_or_nan(payout_days, 5),
            'p50_first_payout_day_conditional': _percentile_or_nan(payout_days, 50),
            'p95_first_payout_day_conditional': _percentile_or_nan(payout_days, 95),
            'p90_first_payout_day_conditional': _percentile_or_nan(payout_days, 90),
            'p95_max_drawdown': _percentile_or_nan(drawdown, 95),
            'p05_ending_cushion': _percentile_or_nan(cushion, 5),
            'p50_ending_cushion': _percentile_or_nan(cushion, 50),
            'p95_ending_cushion': _percentile_or_nan(cushion, 95),
        }
        for threshold in thresholds:
            row[f'payout_before_failure_by_day_{threshold}'] = float(
                ((group['payout_before_failure']) & (group['first_payout_day'].fillna(np.inf) <= threshold)).mean()
            )
            row[f'failure_before_payout_by_day_{threshold}'] = float(
                ((group['failure_before_payout']) & (group['first_failure_day'].fillna(np.inf) <= threshold)).mean()
            )
            row[f'unresolved_by_day_{threshold}'] = float(
                (
                    (group['first_payout_day'].fillna(np.inf) > threshold)
                    & (group['first_failure_day'].fillna(np.inf) > threshold)
                ).mean()
            )
        summary_rows.append(row)

    return ForwardVolumeGridResult(
        results=tuple(results),
        summary=pd.DataFrame(summary_rows),
        sampled_trades=pd.DataFrame(sampled_trade_rows),
        events=pd.DataFrame(event_rows),
        path_results=frame,
        forecast_start_date=forecast_start_date,
    )


def enrich_summary_comparison(summary: pd.DataFrame) -> pd.DataFrame:
    if summary.empty:
        return summary.copy()
    group_columns = ['plan', 'scenario_label']
    if 'source_pool_id' in summary.columns:
        group_columns.insert(1, 'source_pool_id')
    rows = summary.sort_values([*group_columns, 'contracts']).copy()
    for column in [
        'ev_uplift_vs_smaller',
        'extra_failure_vs_smaller',
        'payout_probability_uplift_vs_smaller',
        'median_time_change_vs_smaller',
    ]:
        rows[column] = np.nan
    for _, group in rows.groupby(group_columns, sort=False, dropna=False):
        ordered = group.sort_values('contracts')
        previous_index = None
        for index, row in ordered.iterrows():
            if previous_index is not None:
                prev = rows.loc[previous_index]
                rows.loc[index, 'ev_uplift_vs_smaller'] = row['mean_net_cash'] - prev['mean_net_cash']
                rows.loc[index, 'extra_failure_vs_smaller'] = row['failure_before_payout_rate'] - prev['failure_before_payout_rate']
                rows.loc[index, 'payout_probability_uplift_vs_smaller'] = row['payout_before_failure_rate'] - prev['payout_before_failure_rate']
                rows.loc[index, 'median_time_change_vs_smaller'] = (
                    row['p50_first_payout_day_conditional'] - prev['p50_first_payout_day_conditional']
                )
            previous_index = index
    return rows


def _append_decision_label(rows: pd.DataFrame, indexes: Sequence[Any], label: str) -> None:
    for index in indexes:
        existing = rows.loc[index, 'decision_label']
        rows.loc[index, 'decision_label'] = f'{existing}, {label}'.strip(', ')


def _payout_time_for_pareto(frame: pd.DataFrame) -> pd.Series:
    return frame['p50_first_payout_day_conditional'].fillna(np.inf)


def pareto_frontier_indexes(frame: pd.DataFrame) -> list[Any]:
    if frame.empty:
        return []
    rows = frame.copy()
    rows['_pareto_time'] = _payout_time_for_pareto(rows)
    frontier: list[Any] = []
    for index, row in rows.iterrows():
        dominated = rows[
            (rows['payout_before_failure_rate'] >= row['payout_before_failure_rate'])
            & (rows['mean_net_cash'] >= row['mean_net_cash'])
            & (rows['failure_before_payout_rate'] <= row['failure_before_payout_rate'])
            & (rows['_pareto_time'] <= row['_pareto_time'])
            & (
                (rows['payout_before_failure_rate'] > row['payout_before_failure_rate'])
                | (rows['mean_net_cash'] > row['mean_net_cash'])
                | (rows['failure_before_payout_rate'] < row['failure_before_payout_rate'])
                | (rows['_pareto_time'] < row['_pareto_time'])
            )
        ]
        if dominated.empty:
            frontier.append(index)
    return frontier


def label_summary_decisions(
    summary: pd.DataFrame,
    *,
    max_failure_rate: float,
    fastest_target_day: int,
) -> pd.DataFrame:
    rows = enrich_summary_comparison(summary)
    if rows.empty:
        return rows
    rows = rows.copy()
    rows['risk_gate'] = rows['failure_before_payout_rate'] <= float(max_failure_rate)
    rows['decision_label'] = ''
    rows['convex_qualified'] = rows['risk_gate']
    rows['convex_note'] = ''
    payout_by_day = f'payout_before_failure_by_day_{int(fastest_target_day)}'
    failure_by_day = f'failure_before_payout_by_day_{int(fastest_target_day)}'
    if payout_by_day not in rows.columns or failure_by_day not in rows.columns:
        raise PropLabValidationError(f'fastest target day {fastest_target_day} is not in the first-passage thresholds')

    group_columns = ['plan', 'scenario_label']
    if 'source_pool_id' in rows.columns:
        group_columns.insert(1, 'source_pool_id')

    for _, group in rows.groupby(group_columns, sort=False, dropna=False):
        survival = group.sort_values(
            ['payout_before_failure_rate', 'failure_before_payout_rate', 'mean_net_cash'],
            ascending=[False, True, False],
        ).head(1).index
        fastest = group.sort_values(
            [payout_by_day, failure_by_day, 'payout_before_failure_rate'],
            ascending=[False, True, False],
        ).head(1).index
        maximum_ev = group.sort_values('mean_net_cash', ascending=False).head(1).index
        qualified = group[group['risk_gate']]
        convex = qualified.sort_values(
            ['mean_net_cash', 'payout_before_failure_rate', 'p50_first_payout_day_conditional'],
            ascending=[False, False, True],
            na_position='last',
        ).head(1).index

        _append_decision_label(rows, survival, 'Survival')
        _append_decision_label(rows, fastest, f'Fastest D{int(fastest_target_day)}')
        _append_decision_label(rows, maximum_ev, 'Maximum EV')
        if len(convex):
            _append_decision_label(rows, convex, 'Convex')
        else:
            rows.loc[group.index, 'convex_note'] = 'No Convex configuration qualifies under the risk gate'

        _append_decision_label(rows, pareto_frontier_indexes(group), 'Pareto frontier')

    rows.loc[~rows['risk_gate'], 'decision_label'] = rows.loc[~rows['risk_gate'], 'decision_label'].replace('', 'Above risk gate')
    return rows


def first_passage_threshold_frame(summary: pd.DataFrame, *, contracts: int, scenario_label: str) -> pd.DataFrame:
    rows = summary[
        (summary['contracts'] == int(contracts))
        & (summary['scenario_label'] == scenario_label)
    ]
    if rows.empty:
        return pd.DataFrame(columns=['day', 'payout_before_failure', 'failure_before_payout', 'unresolved'])
    row = rows.iloc[0]
    days = sorted(
        int(column.rsplit('_', 1)[-1])
        for column in summary.columns
        if column.startswith('payout_before_failure_by_day_')
    )
    output = []
    for day in days:
        payout = float(row[f'payout_before_failure_by_day_{day}'])
        failure = float(row[f'failure_before_payout_by_day_{day}'])
        unresolved = float(row[f'unresolved_by_day_{day}'])
        total = payout + failure + unresolved
        if abs(total - 1.0) > 1e-9:
            raise PropLabValidationError(f'first-passage probabilities for day {day} sum to {total:.12f}, not 1')
        output.append(
            {
                'day': day,
                'payout_before_failure': payout,
                'failure_before_payout': failure,
                'unresolved': unresolved,
            }
        )
    return pd.DataFrame(output)

ENTRY_ALIASES = ('entry_time', 'entry_utc', 'entry_ts', 'entry', 'touched_at', 'open_time')
EXIT_ALIASES = ('exit_time', 'exit_utc', 'exit_ts', 'exit', 'closed_at', 'close_time')
PNL_ALIASES = ('pnl_points', 'pnl_pts', 'points', 'pnl', 'net_pts', 'pnl_raw')
RAW_STOP_ALIASES = ('raw_stop_points', 'raw_stop_pts', 'raw_cap_points', 'uncapped_stop_points', 'uncapped_cap_points', 'planned_raw_stop_points')
STOP_ALIASES = ('stop_points', 'stop_pts', 'sl_points', 'sl_pts', 'cap')
MAE_ALIASES = ('mae_points', 'mae_pts', 'mae')
MFE_ALIASES = ('mfe_points', 'mfe_pts', 'mfe')
SESSION_ALIASES = ('session_date', 'sess_date', 'trading_day')
DPP_ALIASES = ('dollars_per_point', 'dpp')
COMMISSION_ALIASES = ('commission_round_turn', 'commission_rt', 'commission')
SLIPPAGE_ALIASES = ('slippage_points_round_turn', 'slippage_points', 'slippage_pts', 'slippage')
SAMPLE_WEIGHT_ALIASES = ('sample_weight', 'weight', 'bootstrap_weight')
RESULT_TYPE_ALIASES = ('result_type', 'outcome', 'trade_result')


def _normalized_columns(frame: pd.DataFrame) -> dict[str, str]:
    return {
        str(column).strip().lower().replace(' ', '_').replace('-', '_'): str(column)
        for column in frame.columns
    }


def _first_column(columns: dict[str, str], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        if alias in columns:
            return columns[alias]
    return None


def load_ledger_frame(
    frame: pd.DataFrame,
    *,
    strategy_id: str,
    default_dollars_per_point: float,
    default_commission_round_turn: float = 0.0,
    default_slippage_points_round_turn: float = 0.0,
    require_forward_schema: bool = False,
) -> list[LedgerTrade]:
    columns = _normalized_columns(frame)
    entry_col = _first_column(columns, ENTRY_ALIASES)
    exit_col = _first_column(columns, EXIT_ALIASES)
    pnl_col = _first_column(columns, PNL_ALIASES)
    session_col = _first_column(columns, SESSION_ALIASES)
    if entry_col is None or exit_col is None or pnl_col is None or session_col is None:
        raise PropLabValidationError(
            'ledger requires explicit session_date, entry_time, exit_time, and point PnL columns'
        )
    raw_stop_col = _first_column(columns, RAW_STOP_ALIASES)
    stop_col = _first_column(columns, STOP_ALIASES)
    mae_col = _first_column(columns, MAE_ALIASES)
    mfe_col = _first_column(columns, MFE_ALIASES)
    dpp_col = _first_column(columns, DPP_ALIASES)
    commission_col = _first_column(columns, COMMISSION_ALIASES)
    slippage_col = _first_column(columns, SLIPPAGE_ALIASES)
    sample_weight_col = _first_column(columns, SAMPLE_WEIGHT_ALIASES)
    result_type_col = _first_column(columns, RESULT_TYPE_ALIASES)
    id_col = columns.get('trade_id') or columns.get('source_row_id') or columns.get('level_id') or columns.get('id')
    strategy_col = columns.get('strategy_id') or columns.get('strategy')
    if require_forward_schema and (raw_stop_col is None or stop_col is None or mae_col is None or mfe_col is None):
        raise PropLabValidationError(
            'forward simulator requires raw_stop_points, stop_points, mae_points, and mfe_points columns'
        )
    trades: list[LedgerTrade] = []
    seen: set[str] = set()
    for position, (_, row) in enumerate(frame.iterrows(), start=2):
        trade_id = str(row[id_col]).strip() if id_col is not None else f'{strategy_id}:{position}'
        row_strategy = str(row[strategy_col]).strip() if strategy_col is not None else strategy_id
        unique_id = f'{row_strategy}:{trade_id}:{position}'
        if unique_id in seen:
            raise PropLabValidationError(f'duplicate trade identity: {unique_id}')
        seen.add(unique_id)
        dpp = float(row[dpp_col]) if dpp_col is not None and pd.notna(row[dpp_col]) else float(default_dollars_per_point)
        commission = (
            float(row[commission_col])
            if commission_col is not None and pd.notna(row[commission_col])
            else float(default_commission_round_turn)
        )
        slippage = (
            float(row[slippage_col])
            if slippage_col is not None and pd.notna(row[slippage_col])
            else float(default_slippage_points_round_turn)
        )
        sample_weight = (
            float(row[sample_weight_col])
            if sample_weight_col is not None and pd.notna(row[sample_weight_col])
            else 1.0
        )
        result_type = (
            str(row[result_type_col]).strip()
            if result_type_col is not None and pd.notna(row[result_type_col])
            else None
        )
        trades.append(
            LedgerTrade(
                trade_id=unique_id,
                strategy_id=row_strategy,
                session_date=pd.Timestamp(row[session_col]),
                entry_time=pd.Timestamp(row[entry_col]),
                exit_time=pd.Timestamp(row[exit_col]),
                pnl_points=float(row[pnl_col]),
                raw_stop_points=(float(row[raw_stop_col]) if raw_stop_col is not None and pd.notna(row[raw_stop_col]) else None),
                stop_points=(float(row[stop_col]) if stop_col is not None and pd.notna(row[stop_col]) else None),
                mae_points=(float(row[mae_col]) if mae_col is not None and pd.notna(row[mae_col]) else None),
                mfe_points=(float(row[mfe_col]) if mfe_col is not None and pd.notna(row[mfe_col]) else None),
                dollars_per_point=dpp,
                commission_round_turn=commission,
                slippage_points_round_turn=slippage,
                sample_weight=sample_weight,
                result_type=result_type,
            )
        )
    trades.sort(key=lambda trade: (trade.session_date, trade.exit_time, trade.entry_time, trade.trade_id))
    return trades
