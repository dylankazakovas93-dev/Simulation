from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

import numpy as np
import pandas as pd

from sim_core.models import Trade
from sim_core.prop_rules import (
    MonthBlockSampler,
    PropRuleProfile,
    default_prop_rule_profiles,
    _floor_ceiling,
    _gross_cash_available,
    _is_payout_eligible,
    _trade_day,
    _trade_mae_dollars,
    _trade_mfe_dollars,
    _trade_pnl_dollars,
)

LifecycleStage = Literal["evaluation", "funded"]
StartMode = Literal["new_eval", "existing_eval", "funded"]


@dataclass(frozen=True)
class LifecyclePlan:
    firm: str
    account_name: str
    funded_profile: PropRuleProfile
    eval_profile: PropRuleProfile | None = None
    eval_profit_target: float = 0.0
    default_eval_fee: float = 0.0
    default_activation_fee: float = 0.0
    default_reset_fee: float = 0.0
    notes: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        stage = "Eval to funded" if self.eval_profile is not None else "Funded only"
        return f"{self.firm} - {self.account_name} - {stage}"

    @property
    def account_size(self) -> float:
        return self.funded_profile.account_size

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["key"] = self.key
        data["funded_profile"] = self.funded_profile.key
        data["eval_profile"] = self.eval_profile.key if self.eval_profile is not None else None
        return data


@dataclass(frozen=True)
class LifecycleSettings:
    start_mode: StartMode = "new_eval"
    current_balance: float | None = None
    current_floor: float | None = None
    current_winning_days: int = 0
    current_highest_winning_day: float = 0.0
    current_daily_profits: tuple[float, ...] = ()
    payouts_already_taken: int = 0
    prior_fees: float = 0.0
    desired_payout: float = 0.0
    required_cushion: float = 0.0
    allow_rebuys: bool = True
    max_rebuy_capital: float = 0.0
    eval_fee: float = 0.0
    activation_fee: float = 0.0
    reset_fee: float = 0.0
    auto_payout: bool = True


@dataclass(frozen=True)
class LifecyclePathResult:
    plan_key: str
    firm: str
    account_name: str
    contracts: int
    path_id: int
    seed: int
    failed: bool
    terminal_stage: LifecycleStage
    attempts: int
    eval_passes: int
    funded_failures: int
    payouts_taken: int
    first_payout_month: int | None
    first_payout_day: int | None
    first_payout_order: int | None
    first_failure_month: int | None
    first_failure_day: int | None
    first_failure_order: int | None
    total_payouts: float
    total_fees: float
    net_cash: float
    roi_on_fees: float | None
    ending_balance: float
    ending_floor: float
    max_drawdown: float
    target_hit: bool
    cushion_ok_after_payout: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LifecycleEvent:
    path_id: int
    plan_key: str
    event_order: int
    month_index: int
    date: str
    stage: LifecycleStage
    event: str
    amount: float = 0.0
    balance: float = 0.0
    floor: float = 0.0
    attempt: int = 1
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LifecycleMonth:
    path_id: int
    plan_key: str
    firm: str
    account_name: str
    contracts: int
    month_index: int
    month: str
    stage: LifecycleStage
    attempt: int
    starting_balance: float
    ending_balance: float
    pnl: float
    max_drawdown: float
    floor: float
    payouts: float
    fees: float
    cumulative_payouts: float
    cumulative_fees: float
    net_cash: float
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _AccountState:
    stage: LifecycleStage
    profile: PropRuleProfile
    balance: float
    floor: float
    eod_peak: float
    running_peak: float
    minimum_balance: float
    day: pd.Timestamp | None = None
    day_pnl: float = 0.0
    daily_paused: bool = False
    winning_days: int = 0
    daily_profits: list[float] | None = None

    def __post_init__(self) -> None:
        if self.daily_profits is None:
            self.daily_profits = []


def default_lifecycle_plans() -> dict[str, LifecyclePlan]:
    profiles = default_prop_rule_profiles()
    plans: list[LifecyclePlan] = []

    apex_eval_specs = {
        "25K": (25_000, 1_500, 1_000, 500, 40),
        "50K": (50_000, 3_000, 2_000, 1_000, 60),
        "100K": (100_000, 6_000, 3_000, 1_500, 80),
        "150K": (150_000, 9_000, 4_000, 2_000, 120),
    }
    for size_label, (size, target, max_loss, daily_loss, max_contracts) in apex_eval_specs.items():
        funded = profiles[f"Apex Trader Funding - EOD PA {size_label}"]
        eval_profile = PropRuleProfile(
            firm="Apex Trader Funding",
            account_name=f"EOD Eval {size_label}",
            account_size=size,
            max_loss=max_loss,
            drawdown_mode="eod_trailing",
            max_micro_contracts=max_contracts,
            profit_split=0.0,
            min_payout=0.0,
            consistency_pct=None,
            min_winning_days=0,
            daily_loss_limit=daily_loss,
            source="Apex pasted rules, EOD Evaluation table",
            notes=(
                "Evaluation target only; no consistency requirement modeled.",
                "Passing switches the path into the matching EOD PA after activation fee.",
                "Max contracts are stored as micro-equivalent contracts because this simulator sizes MNQ-style micro contracts.",
            ),
        )
        plans.append(
            LifecyclePlan(
                firm="Apex Trader Funding",
                account_name=f"EOD {size_label}",
                funded_profile=funded,
                eval_profile=eval_profile,
                eval_profit_target=target,
                default_eval_fee=0.0,
                default_activation_fee=0.0,
                default_reset_fee=0.0,
                notes=("Apex EOD eval can pass once the profit target is reached.",),
            )
        )

    for profile in profiles.values():
        plans.append(
            LifecyclePlan(
                firm=profile.firm,
                account_name=profile.account_name,
                funded_profile=profile,
                eval_profile=None,
                default_activation_fee=profile.activation_fee,
                notes=("Funded-stage only profile. Use current-funded mode for live-account scenarios.",),
            )
        )

    return {plan.key: plan for plan in plans}


def simulate_lifecycle_path(
    trades: list[Trade],
    plan: LifecyclePlan,
    *,
    contracts: int,
    settings: LifecycleSettings,
    path_id: int = 0,
    seed: int = 0,
    dollars_per_point: float = 2.0,
    timezone: str = "America/New_York",
    return_trade_ledger: bool = False,
) -> tuple[LifecyclePathResult, list[LifecycleMonth], list[LifecycleEvent]] | tuple[
    LifecyclePathResult, list[LifecycleMonth], list[LifecycleEvent], list[dict[str, Any]]
]:
    ordered = sorted(trades, key=lambda trade: (trade.exit_time, trade.entry_time, trade.source_row_id))
    if contracts > plan.funded_profile.max_micro_contracts:
        result = LifecyclePathResult(
            plan_key=plan.key,
            firm=plan.firm,
            account_name=plan.account_name,
            contracts=contracts,
            path_id=path_id,
            seed=seed,
            failed=True,
            terminal_stage="funded",
            attempts=0,
            eval_passes=0,
            funded_failures=0,
            payouts_taken=0,
            first_payout_month=None,
            first_payout_day=None,
            first_payout_order=None,
            first_failure_month=None,
            first_failure_day=None,
            first_failure_order=None,
            total_payouts=0.0,
            total_fees=0.0,
            net_cash=0.0,
            roi_on_fees=None,
            ending_balance=plan.funded_profile.starting_balance,
            ending_floor=plan.funded_profile.starting_floor,
            max_drawdown=0.0,
            target_hit=False,
            cushion_ok_after_payout=False,
        )
        if return_trade_ledger:
            return result, [], [], []
        return result, [], []

    months: list[LifecycleMonth] = []
    events: list[LifecycleEvent] = []
    trade_ledger: list[dict[str, Any]] = []
    total_payouts = 0.0
    total_fees = max(0.0, float(settings.prior_fees))
    attempts = 0
    eval_passes = 0
    funded_failures = 0
    payouts_taken = max(0, int(settings.payouts_already_taken))
    first_payout_month: int | None = None
    first_payout_day: int | None = None
    first_payout_order: int | None = None
    first_failure_month: int | None = None
    first_failure_day: int | None = None
    first_failure_order: int | None = None
    event_order = 0
    failed_terminal = False
    terminal_reason = ""
    max_drawdown_seen = 0.0
    cushion_ok_after_payout = False
    first_day = _trade_day(ordered[0].entry_time, timezone) if ordered else None

    def next_event_order() -> int:
        nonlocal event_order
        event_order += 1
        return event_order

    def consistency_ratio() -> float | None:
        if state.stage != "funded":
            return None
        profit = state.balance - state.profile.starting_balance
        positive = [value for value in (state.daily_profits or []) if value > 0]
        if profit <= 0 or not positive:
            return None
        return max(positive) / profit

    def trace_base(*, current_day: pd.Timestamp, record_type: str, sequence_number: int | None) -> dict[str, Any]:
        return {
            "path_id": path_id,
            "plan_key": plan.key,
            "firm": plan.firm,
            "account": plan.account_name,
            "contracts": contracts,
            "sequence_number": sequence_number,
            "record_type": record_type,
            "session_date": str(current_day.date()),
            "stage": state.stage,
            "attempt": max(1, attempts),
            "balance_before": state.balance,
            "floor_before": state.floor,
            "running_peak_before": state.running_peak,
            "eod_peak_before": state.eod_peak,
            "day_pnl_before": state.day_pnl,
            "winning_days_before": state.winning_days,
            "consistency_before": consistency_ratio(),
            "balance_after": state.balance,
            "floor_after": state.floor,
            "running_peak_after": state.running_peak,
            "eod_peak_after": state.eod_peak,
            "day_pnl_after": state.day_pnl,
            "winning_days_after": state.winning_days,
            "consistency_after": consistency_ratio(),
            "payout_eligibility": False,
            "gross_account_debit": 0.0,
            "trader_cash": 0.0,
            "payout_number": payouts_taken,
            "fees": total_fees,
            "total_payouts": total_payouts,
            "total_fees": total_fees,
            "net_cash": total_payouts - total_fees,
            "failure": failed_terminal,
            "failure_reason": terminal_reason,
        }

    def refresh_terminal_trace() -> None:
        if not trade_ledger:
            return
        last = trade_ledger[-1]
        last.update(
            {
                "balance_after": state.balance,
                "floor_after": state.floor,
                "running_peak_after": state.running_peak,
                "eod_peak_after": state.eod_peak,
                "day_pnl_after": state.day_pnl,
                "winning_days_after": state.winning_days,
                "consistency_after": consistency_ratio(),
                "payout_number": payouts_taken,
                "fees": total_fees,
                "total_payouts": total_payouts,
                "total_fees": total_fees,
                "net_cash": total_payouts - total_fees,
                "failure": failed_terminal,
                "failure_reason": terminal_reason,
            }
        )

    def add_fee(amount: float, month_index: int, date: pd.Timestamp, event: str, note: str) -> None:
        nonlocal total_fees
        if amount <= 0:
            return
        total_fees += amount
        events.append(
            LifecycleEvent(
                path_id=path_id,
                plan_key=plan.key,
                event_order=next_event_order(),
                month_index=month_index,
                date=str(date.date()),
                stage=state.stage,
                event=event,
                amount=-amount,
                balance=state.balance,
                floor=state.floor,
                attempt=attempts,
                note=note,
            )
        )

    def reset_state(stage: LifecycleStage) -> _AccountState:
        profile = plan.eval_profile if stage == "evaluation" and plan.eval_profile is not None else plan.funded_profile
        profile = profile or plan.funded_profile
        balance = profile.starting_balance
        floor = profile.starting_floor
        return _AccountState(
            stage=stage,
            profile=profile,
            balance=balance,
            floor=floor,
            eod_peak=max(profile.starting_balance, balance),
            running_peak=balance,
            minimum_balance=balance,
        )

    start_stage: LifecycleStage = "funded"
    if settings.start_mode in {"new_eval", "existing_eval"} and plan.eval_profile is not None:
        start_stage = "evaluation"
    state = reset_state(start_stage)
    if settings.current_balance is not None and settings.start_mode in {"existing_eval", "funded"}:
        state.balance = float(settings.current_balance)
        state.running_peak = max(state.running_peak, state.balance)
        state.eod_peak = max(state.eod_peak, state.balance)
        state.minimum_balance = state.balance
    if settings.current_floor is not None and settings.start_mode in {"existing_eval", "funded"}:
        state.floor = float(settings.current_floor)
    state.winning_days = max(0, int(settings.current_winning_days)) if state.stage == "funded" else 0
    if state.stage == "funded":
        state.daily_profits = [float(value) for value in settings.current_daily_profits]
        if settings.current_highest_winning_day > 0:
            state.daily_profits.append(float(settings.current_highest_winning_day))

    first_trade_date = _trade_day(ordered[0].exit_time, timezone) if ordered else pd.Timestamp("1970-01-01")
    if state.stage == "evaluation":
        attempts = 1
        add_fee(settings.eval_fee, 0, first_trade_date, "eval_fee", "New evaluation attempt")

    month_start_balance = state.balance
    month_start_minimum = state.balance
    month_pnl = 0.0
    month_payouts = 0.0
    month_fees_at_start = total_fees
    active_month_index = 1
    active_month_label = ""
    month_status = "active"

    def finish_day() -> None:
        if state.day is None:
            return
        if state.stage == "funded" and state.day_pnl >= state.profile.winning_day_threshold and state.day_pnl > 0:
            state.winning_days += 1
        if state.stage == "funded":
            state.daily_profits.append(state.day_pnl)
        if state.profile.drawdown_mode == "eod_trailing":
            state.eod_peak = max(state.eod_peak, state.balance)
            state.floor = max(
                state.floor,
                min(_floor_ceiling(state.profile), state.eod_peak - state.profile.max_loss),
            )

    def finish_month() -> None:
        nonlocal month_start_balance, month_start_minimum, month_pnl, month_payouts
        nonlocal month_fees_at_start, month_status
        if not active_month_label:
            return
        month_max_dd = max(0.0, month_start_balance - month_start_minimum)
        months.append(
            LifecycleMonth(
                path_id=path_id,
                plan_key=plan.key,
                firm=plan.firm,
                account_name=plan.account_name,
                contracts=contracts,
                month_index=active_month_index,
                month=active_month_label,
                stage=state.stage,
                attempt=max(1, attempts),
                starting_balance=month_start_balance,
                ending_balance=state.balance,
                pnl=month_pnl,
                max_drawdown=month_max_dd,
                floor=state.floor,
                payouts=month_payouts,
                fees=total_fees - month_fees_at_start,
                cumulative_payouts=total_payouts,
                cumulative_fees=total_fees,
                net_cash=total_payouts - total_fees,
                status=month_status,
            )
        )
        month_start_balance = state.balance
        month_start_minimum = state.balance
        month_pnl = 0.0
        month_payouts = 0.0
        month_fees_at_start = total_fees
        month_status = "active"

    def maybe_take_payout(current_day: pd.Timestamp, month_index: int) -> None:
        nonlocal total_payouts, payouts_taken, first_payout_month, first_payout_day
        nonlocal first_payout_order
        nonlocal month_payouts, cushion_ok_after_payout
        if failed_terminal:
            return
        if state.stage != "funded" or not settings.auto_payout:
            return
        if state.profile.payout_count_cap is not None and payouts_taken >= state.profile.payout_count_cap:
            return
        if not _is_payout_eligible(
            balance=state.balance,
            profile=state.profile,
            winning_days=state.winning_days,
            daily_profits=state.daily_profits,
        ):
            return
        gross_account_debit = _gross_cash_available(
            state.balance,
            state.profile,
            max_payout=_payout_cap_for_request(state.profile, payouts_taken + 1),
        )
        trader_cash_available = gross_account_debit * state.profile.profit_split
        desired = float(settings.desired_payout)
        trader_cash = trader_cash_available if desired <= 0 else min(trader_cash_available, desired)
        payout = trader_cash / state.profile.profit_split if state.profile.profit_split > 0 else trader_cash
        min_balance_after = state.profile.starting_balance + max(
            settings.required_cushion,
            state.profile.payout_reserve,
        )
        if payout <= 0 or trader_cash <= 0 or (desired > 0 and trader_cash < desired):
            return
        if state.balance - payout < min_balance_after:
            return
        state.balance -= payout
        state.running_peak = max(state.running_peak, state.balance)
        total_payouts += trader_cash
        month_payouts += trader_cash
        payouts_taken += 1
        cushion_ok_after_payout = True
        payout_order = next_event_order()
        if first_payout_month is None:
            first_payout_month = month_index
            first_payout_day = int((current_day - first_day).days) if first_day is not None else None
            first_payout_order = payout_order
        state.winning_days = 0
        state.daily_profits = []
        events.append(
            LifecycleEvent(
                path_id=path_id,
                plan_key=plan.key,
                event_order=payout_order,
                month_index=month_index,
                date=str(current_day.date()),
                stage="funded",
                event="payout",
                amount=trader_cash,
                balance=state.balance,
                floor=state.floor,
                attempt=max(1, attempts),
                note=(
                    "Payout taken; consistency state reset; "
                    f"gross_account_debit={payout:.2f}; trader_profit_split={state.profile.profit_split:.2f}"
                ),
            )
        )
        if return_trade_ledger:
            row = trace_base(current_day=current_day, record_type="PAYOUT", sequence_number=None)
            row.update(
                {
                    "candidate": False,
                    "executed": False,
                    "account_taken": False,
                    "threshold_touched": False,
                    "strict_account_result": "PAYOUT",
                    "realized_pnl_only_result": "PAYOUT",
                    "source_row_id": None,
                    "trade_id": None,
                    "entry_time": None,
                    "exit_time": None,
                    "pnl_points": 0.0,
                    "gross_pnl_dollars": 0.0,
                    "mae_points": None,
                    "mfe_points": None,
                    "estimated_intratrade_low": state.balance,
                    "estimated_intratrade_high": state.balance,
                    "balance_before": state.balance + payout,
                    "balance_after": state.balance,
                    "gross_account_debit": payout,
                    "trader_cash": trader_cash,
                    "payout_number": payouts_taken,
                    "payout_event_order": payout_order,
                    "payout_eligibility": True,
                    "total_payouts": total_payouts,
                    "total_fees": total_fees,
                    "net_cash": total_payouts - total_fees,
                }
            )
            trade_ledger.append(row)

    for trade in ordered:
        current_day = _trade_day(trade.exit_time, timezone)
        month_index = int(trade.source_month.ordinal - ordered[0].source_month.ordinal + 1)
        month_label = str(trade.source_month)
        if not active_month_label:
            active_month_index = month_index
            active_month_label = month_label
        elif month_index != active_month_index:
            finish_day()
            maybe_take_payout(state.day or current_day, active_month_index)
            finish_month()
            active_month_index = month_index
            active_month_label = month_label
            month_start_balance = state.balance
            month_start_minimum = state.balance
            state.day = None
            state.day_pnl = 0.0
            state.daily_paused = False

        if state.day is None:
            state.day = current_day
        elif current_day != state.day:
            finish_day()
            maybe_take_payout(state.day, month_index)
            state.day = current_day
            state.day_pnl = 0.0
            state.daily_paused = False

        if failed_terminal:
            month_status = "terminal"
            if return_trade_ledger:
                row = trace_base(current_day=current_day, record_type="TRADE", sequence_number=_metadata_int(trade, "sequence_number"))
                row.update(_trade_trace_identity(trade))
                row.update(
                    {
                        "candidate": True,
                        "executed": False,
                        "account_taken": False,
                        "threshold_touched": False,
                        "strict_account_result": "NOT_TAKEN_AFTER_FAILURE",
                        "realized_pnl_only_result": "NOT_TAKEN_AFTER_FAILURE",
                    }
                )
                trade_ledger.append(row)
            continue
        if state.daily_paused:
            if return_trade_ledger:
                row = trace_base(current_day=current_day, record_type="TRADE", sequence_number=_metadata_int(trade, "sequence_number"))
                row.update(_trade_trace_identity(trade))
                row.update(
                    {
                        "candidate": True,
                        "executed": False,
                        "account_taken": False,
                        "threshold_touched": False,
                        "strict_account_result": "NOT_TAKEN_DAILY_PAUSE",
                        "realized_pnl_only_result": "NOT_TAKEN_DAILY_PAUSE",
                    }
                )
                trade_ledger.append(row)
            continue

        if return_trade_ledger:
            trace_row = trace_base(current_day=current_day, record_type="TRADE", sequence_number=_metadata_int(trade, "sequence_number"))
            trace_row.update(_trade_trace_identity(trade))
            trace_row.update({"candidate": True, "executed": True, "account_taken": True})
        else:
            trace_row = {}
        pnl = _trade_pnl_dollars(trade, contracts, dollars_per_point)
        mae_missing = trade.mae_points is None
        mae = _trade_mae_dollars(trade, contracts, dollars_per_point)
        mfe = _trade_mfe_dollars(trade, contracts, dollars_per_point)
        unknown_excursion = bool(
            mae_missing
            and str(trade.metadata.get("excursion_confidence", "")).upper().startswith("UNKNOWN")
        )
        estimated_low = state.balance + mae
        estimated_high = state.balance + max(0.0, mfe)
        if state.profile.drawdown_mode == "intraday_trailing":
            state.running_peak = max(state.running_peak, state.balance + max(0.0, mfe))
            state.floor = max(
                state.floor,
                min(_floor_ceiling(state.profile), state.running_peak - state.profile.max_loss),
            )

        breached = (not unknown_excursion) and state.balance + mae <= state.floor
        if not breached:
            state.balance += pnl
            state.day_pnl += pnl
            month_pnl += pnl
            state.running_peak = max(state.running_peak, state.balance)
            state.minimum_balance = min(state.minimum_balance, state.balance)
            month_start_minimum = min(month_start_minimum, state.balance)
            breached = state.balance <= state.floor
        else:
            state.minimum_balance = min(state.minimum_balance, state.balance + mae)
            month_start_minimum = min(month_start_minimum, state.balance + mae)

        max_drawdown_seen = max(max_drawdown_seen, state.running_peak - state.minimum_balance)

        if breached:
            failure_order = next_event_order()
            if first_failure_month is None:
                first_failure_month = month_index
                first_failure_day = int((current_day - first_day).days) if first_day is not None else None
                first_failure_order = failure_order
            month_status = f"{state.stage}_failed"
            events.append(
                LifecycleEvent(
                    path_id=path_id,
                    plan_key=plan.key,
                    event_order=failure_order,
                    month_index=month_index,
                    date=str(current_day.date()),
                    stage=state.stage,
                    event=f"{state.stage}_failed",
                    amount=0.0,
                    balance=state.balance,
                    floor=state.floor,
                    attempt=max(1, attempts),
                    note="Drawdown breached",
                )
            )
            if state.stage == "funded":
                funded_failures += 1
            next_attempt_fee = (
                settings.eval_fee
                if state.stage == "funded"
                else settings.reset_fee if settings.reset_fee > 0 else settings.eval_fee
            )
            next_attempt_note = (
                "New evaluation after funded failure"
                if state.stage == "funded"
                else "Evaluation reset after failure"
            )
            can_rebuy = (
                settings.allow_rebuys
                and plan.eval_profile is not None
                and total_fees + next_attempt_fee <= settings.max_rebuy_capital
            )
            if return_trade_ledger:
                trace_row.update(
                    {
                        "gross_pnl_dollars": 0.0,
                        "mae_points": trade.mae_points,
                        "mfe_points": trade.mfe_points,
                        "estimated_intratrade_low": estimated_low,
                        "estimated_intratrade_high": estimated_high,
                        "threshold_touched": True,
                        "strict_account_result": "FAILED",
                        "realized_pnl_only_result": "FAILED",
                        "balance_after": state.balance,
                        "floor_after": state.floor,
                        "running_peak_after": state.running_peak,
                        "eod_peak_after": state.eod_peak,
                        "day_pnl_after": state.day_pnl,
                        "winning_days_after": state.winning_days,
                        "consistency_after": consistency_ratio(),
                        "payout_eligibility": False,
                        "failure": True,
                        "failure_reason": "Drawdown breached",
                    }
                )
                trade_ledger.append(trace_row)
            if can_rebuy:
                state = reset_state("evaluation")
                attempts += 1
                add_fee(next_attempt_fee, month_index, current_day, "eval_fee", next_attempt_note)
                month_start_balance = state.balance
                month_start_minimum = state.balance
                continue
            failed_terminal = True
            terminal_reason = "capital budget exhausted or rebuys disabled"
            refresh_terminal_trace()
            continue

        if return_trade_ledger:
            eligible_after = (
                not failed_terminal
                and _is_payout_eligible(
                    balance=state.balance,
                    profile=state.profile,
                    winning_days=state.winning_days,
                    daily_profits=state.daily_profits,
                )
            )
            trace_row.update(
                {
                    "gross_pnl_dollars": pnl,
                    "mae_points": trade.mae_points,
                    "mfe_points": trade.mfe_points,
                    "estimated_intratrade_low": None if unknown_excursion else estimated_low,
                    "estimated_intratrade_high": estimated_high,
                    "threshold_touched": "UNKNOWN" if unknown_excursion else False,
                    "strict_account_result": "UNKNOWN" if unknown_excursion else "SURVIVED",
                    "realized_pnl_only_result": "SURVIVED",
                    "balance_after": state.balance,
                    "floor_after": state.floor,
                    "running_peak_after": state.running_peak,
                    "eod_peak_after": state.eod_peak,
                    "day_pnl_after": state.day_pnl,
                    "winning_days_after": state.winning_days,
                    "consistency_after": consistency_ratio(),
                    "payout_eligibility": eligible_after,
                    "failure": failed_terminal,
                    "failure_reason": terminal_reason,
                }
            )
            trade_ledger.append(trace_row)

        if state.profile.daily_loss_limit is not None and state.day_pnl <= -abs(state.profile.daily_loss_limit):
            if state.profile.daily_loss_hard:
                failed_terminal = True
                terminal_reason = "daily loss limit breached"
                month_status = f"{state.stage}_failed"
            else:
                state.daily_paused = True

        if state.stage == "evaluation" and state.balance - state.profile.starting_balance >= plan.eval_profit_target:
            eval_passes += 1
            month_status = "eval_passed"
            events.append(
                LifecycleEvent(
                    path_id=path_id,
                    plan_key=plan.key,
                    event_order=next_event_order(),
                    month_index=month_index,
                    date=str(current_day.date()),
                    stage="evaluation",
                    event="eval_passed",
                    amount=state.balance - state.profile.starting_balance,
                    balance=state.balance,
                    floor=state.floor,
                    attempt=attempts,
                    note="Evaluation target reached; switching to funded account",
                )
            )
            state = reset_state("funded")
            add_fee(settings.activation_fee, month_index, current_day, "activation_fee", "Funded activation after eval pass")
            month_start_balance = state.balance
            month_start_minimum = state.balance

    finish_day()
    maybe_take_payout(state.day or first_trade_date, active_month_index)
    finish_month()
    refresh_terminal_trace()

    net_cash = total_payouts - total_fees
    roi = net_cash / total_fees if total_fees > 0 else None
    target_hit = total_payouts >= settings.desired_payout if settings.desired_payout > 0 else total_payouts > 0
    result = LifecyclePathResult(
        plan_key=plan.key,
        firm=plan.firm,
        account_name=plan.account_name,
        contracts=contracts,
        path_id=path_id,
        seed=seed,
        failed=failed_terminal,
        terminal_stage=state.stage,
        attempts=max(1, attempts),
        eval_passes=eval_passes,
        funded_failures=funded_failures,
        payouts_taken=payouts_taken,
        first_payout_month=first_payout_month,
        first_payout_day=first_payout_day,
        first_payout_order=first_payout_order,
        first_failure_month=first_failure_month,
        first_failure_day=first_failure_day,
        first_failure_order=first_failure_order,
        total_payouts=total_payouts,
        total_fees=total_fees,
        net_cash=net_cash,
        roi_on_fees=roi,
        ending_balance=state.balance,
        ending_floor=state.floor,
        max_drawdown=max_drawdown_seen,
        target_hit=target_hit,
        cushion_ok_after_payout=cushion_ok_after_payout,
    )
    if terminal_reason:
        events.append(
            LifecycleEvent(
                path_id=path_id,
                plan_key=plan.key,
                event_order=next_event_order(),
                month_index=active_month_index,
                date=str((state.day or first_trade_date).date()),
                stage=state.stage,
                event="terminal",
                balance=state.balance,
                floor=state.floor,
                attempt=max(1, attempts),
                note=terminal_reason,
            )
        )
    if return_trade_ledger:
        refresh_terminal_trace()
        return result, months, events, trade_ledger
    return result, months, events


def run_lifecycle_grid(
    trades: list[Trade],
    plans: list[LifecyclePlan],
    *,
    contract_values: list[int],
    paths: int,
    horizon_months: int,
    seed: int,
    dollars_per_point: float,
    settings_by_plan: dict[str, LifecycleSettings],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    sampler = MonthBlockSampler(trades, horizon_months)
    results: list[LifecyclePathResult] = []
    months: list[LifecycleMonth] = []
    events: list[LifecycleEvent] = []
    path_id = 0
    for plan in plans:
        settings = settings_by_plan[plan.key]
        for contracts in contract_values:
            for path_number in range(paths):
                sampled = sampler.sample(rng)
                result, path_months, path_events = simulate_lifecycle_path(
                    sampled,
                    plan,
                    contracts=contracts,
                    settings=settings,
                    path_id=path_id,
                    seed=seed + path_number,
                    dollars_per_point=dollars_per_point,
                )
                results.append(result)
                months.extend(path_months)
                events.extend(path_events)
                path_id += 1
    return (
        summarize_lifecycle_results(results),
        pd.DataFrame([month.to_dict() for month in months]),
        pd.DataFrame([event.to_dict() for event in events]),
    )


def _metadata_int(trade: Trade, key: str) -> int | None:
    value = trade.metadata.get(key)
    if value is None or pd.isna(value):
        return None
    return int(value)


def _trade_trace_identity(trade: Trade) -> dict[str, Any]:
    return {
        "source_row_id": trade.source_row_id,
        "trade_id": trade.trade_id,
        "entry_time": trade.entry_time.isoformat(),
        "exit_time": trade.exit_time.isoformat(),
        "pnl_points": trade.pnl_points,
        "result_type": trade.result_type,
        "mae_points": trade.mae_points,
        "mfe_points": trade.mfe_points,
        "status": trade.metadata.get("status"),
        "source_trade_packet_id": trade.metadata.get("source_trade_packet_id"),
        "evidence_status": trade.metadata.get("evidence_status"),
        "excursion_confidence": trade.metadata.get("excursion_confidence"),
        "strict_barrier_status": trade.metadata.get("strict_barrier_status"),
        "candidate_flag": trade.metadata.get("candidate", True),
        "executed_flag": trade.metadata.get("was_executed", True),
    }


def summarize_lifecycle_results(results: list[LifecyclePathResult]) -> pd.DataFrame:
    if not results:
        return pd.DataFrame()
    frame = pd.DataFrame([result.to_dict() for result in results])
    rows: list[dict[str, Any]] = []
    for (plan_key, contracts), group in frame.groupby(["plan_key", "contracts"], sort=False):
        first_months = group["first_payout_month"].dropna().astype(float)
        first_days = group["first_payout_day"].dropna().astype(float)
        fees = group["total_fees"].astype(float)
        net = group["net_cash"].astype(float)
        payouts = group["total_payouts"].astype(float)
        payout_counts = group["payouts_taken"].astype(float)
        failed = group["failed"].astype(bool)
        target = group["target_hit"].astype(bool)
        has_payout = group["first_payout_month"].notna()
        has_failure = group["first_failure_month"].notna()
        first_payout_order = group["first_payout_order"].astype(float)
        first_failure_order = group["first_failure_order"].astype(float)
        paid_before_first_blow = has_payout & (
            ~has_failure | (first_payout_order < first_failure_order)
        )
        first_blow_before_payout = has_failure & (
            ~has_payout | (first_failure_order < first_payout_order)
        )
        payout_after_rebuy = has_payout & has_failure & (first_failure_order < first_payout_order)
        blew_before_payout = ~has_payout & (has_failure | failed)
        no_resolution = ~(paid_before_first_blow | payout_after_rebuy | blew_before_payout)
        paid_before_count = int(paid_before_first_blow.sum())
        blew_before_count = int(blew_before_payout.sum())
        paid_after_rebuy_count = int(payout_after_rebuy.sum())
        no_resolution_count = int(no_resolution.sum())
        row = {
            "plan": plan_key,
            "firm": group["firm"].iloc[0],
            "account": group["account_name"].iloc[0],
            "contracts": contracts,
            "paths": len(group),
            "paid_before_first_blow_count": paid_before_count,
            "blew_before_payout_count": blew_before_count,
            "paid_after_rebuy_count": paid_after_rebuy_count,
            "no_resolution_count": no_resolution_count,
            "paid_before_first_blow_rate": float(paid_before_first_blow.mean()),
            "first_blow_before_payout_rate": float(first_blow_before_payout.mean()),
            "blew_before_payout_rate": float(blew_before_payout.mean()),
            "payout_after_rebuy_rate": float(payout_after_rebuy.mean()),
            "no_resolution_rate": float(no_resolution.mean()),
            "capital_exhausted_rate": float((failed & ~has_payout).mean()),
            "any_payout_rate": float(has_payout.mean()),
            "target_before_first_fail_rate": float((target & paid_before_first_blow).mean()),
            "p05_net_cash": float(np.percentile(net, 5)),
            "p50_net_cash": float(np.percentile(net, 50)),
            "p95_net_cash": float(np.percentile(net, 95)),
            "mean_net_cash": float(net.mean()),
            "avg_net_cash": float(net.mean()),
            "p50_payouts": float(np.percentile(payouts, 50)),
            "avg_withdrawal": float(payouts.mean()),
            "p50_withdrawal": float(np.percentile(payouts, 50)),
            "p95_withdrawal": float(np.percentile(payouts, 95)),
            "avg_payout_count": float(payout_counts.mean()),
            "p50_payout_count": float(np.percentile(payout_counts, 50)),
            "mean_fees": float(fees.mean()),
            "avg_fees": float(fees.mean()),
            "p50_fees": float(np.percentile(fees, 50)),
            "p50_month_to_first_payout": _nanpercentile(first_months.to_numpy(), 50),
            "p50_days_to_first_payout": _nanpercentile(first_days.to_numpy(), 50),
            "p95_max_drawdown": float(np.percentile(group["max_drawdown"].astype(float), 95)),
            "avg_attempts": float(group["attempts"].astype(float).mean()),
        }
        row["current_account_paid_first_rate"] = row["paid_before_first_blow_rate"]
        row["current_account_blew_first_rate"] = row["first_blow_before_payout_rate"]
        row["paid_before_first_blow_paths"] = f"{paid_before_count} / {len(group)}"
        payout_month = row["p50_month_to_first_payout"]
        row["survival_score"] = 100.0 * row["paid_before_first_blow_rate"] * max(
            0.0, 1.0 - row["blew_before_payout_rate"]
        )
        row["speed_score"] = (
            100.0 * row["paid_before_first_blow_rate"] / max(1.0, float(payout_month))
            if payout_month is not None
            else 0.0
        )
        rows.append(row)
    summary = pd.DataFrame(rows)
    score_frames: list[pd.DataFrame] = []
    for _plan, group in summary.groupby("plan", sort=False):
        group = group.sort_values("contracts").copy()
        mean_cash = group["mean_net_cash"].astype(float)
        cash_min = float(mean_cash.min())
        cash_max = float(mean_cash.max())
        if cash_max > cash_min:
            group["ev_score"] = ((mean_cash - cash_min) / (cash_max - cash_min) * 100.0).clip(0.0, 100.0)
        else:
            group["ev_score"] = 100.0 if cash_max > 0 else 0.0
        convexity_raw: list[float] = []
        previous: pd.Series | None = None
        for row in group.itertuples(index=False):
            if previous is None:
                convexity_raw.append(0.0)
            else:
                incremental_ev = float(row.mean_net_cash) - float(previous["mean_net_cash"])
                extra_blow = max(0.0, float(row.blew_before_payout_rate) - float(previous["blew_before_payout_rate"]))
                extra_fees = max(0.0, float(row.mean_fees) - float(previous["mean_fees"]))
                convexity_raw.append(max(0.0, incremental_ev) / (1.0 + extra_fees + 1000.0 * extra_blow))
            previous = pd.Series(row._asdict())
        convexity = np.asarray(convexity_raw, dtype=float)
        convexity_max = float(convexity.max()) if len(convexity) else 0.0
        group["convexity_score"] = (
            convexity / convexity_max * 100.0 if convexity_max > 0 else np.zeros(len(group))
        )
        group["composite_score"] = (
            0.40 * group["survival_score"]
            + 0.30 * group["ev_score"]
            + 0.15 * group["speed_score"]
            + 0.15 * group["convexity_score"]
        )
        group["risk_adjusted_roi_score"] = group["composite_score"]
        score_frames.append(group)
    return pd.concat(score_frames, ignore_index=True).sort_values(
        ["composite_score", "paid_before_first_blow_rate", "mean_net_cash"],
        ascending=[False, False, False],
    )


def summarize_monthly_paths(monthly: pd.DataFrame) -> pd.DataFrame:
    if monthly.empty:
        return monthly
    grouped = monthly.groupby(["plan_key", "contracts", "month_index"], sort=True)
    rows: list[dict[str, Any]] = []
    for (plan_key, contracts, month_index), group in grouped:
        status = group["status"].astype(str)
        terminal = status.str.contains("failed|terminal")
        active = ~terminal
        active_pnl = group.loc[active, "pnl"].astype(float)
        rows.append(
            {
                "plan": plan_key,
                "contracts": contracts,
                "month_index": month_index,
                "paths": group["path_id"].nunique(),
                "p05_pnl": float(np.percentile(group["pnl"], 5)),
                "p50_pnl": float(np.percentile(group["pnl"], 50)),
                "p95_pnl": float(np.percentile(group["pnl"], 95)),
                "active_paths": int(active.sum()),
                "active_path_rate": float(active.mean()),
                "terminal_path_rate": float(terminal.mean()),
                "p05_active_pnl": _nanpercentile(active_pnl.to_numpy(), 5),
                "p50_active_pnl": _nanpercentile(active_pnl.to_numpy(), 50),
                "p95_active_pnl": _nanpercentile(active_pnl.to_numpy(), 95),
                "p50_net_cash": float(np.percentile(group["net_cash"], 50)),
                "p95_drawdown": float(np.percentile(group["max_drawdown"], 95)),
                "fail_month_rate": float(status.str.contains("failed").mean()),
                "payout_month_rate": float((group["payouts"] > 0).mean()),
            }
        )
    return pd.DataFrame(rows)


def _nanpercentile(values: np.ndarray, percentile: float) -> float | None:
    finite = values[np.isfinite(values)]
    if not len(finite):
        return None
    return float(np.percentile(finite, percentile))


def _payout_cap_for_request(profile: PropRuleProfile, payout_number: int) -> float | None:
    if not profile.payout_cap_schedule:
        return profile.max_payout
    index = max(0, min(int(payout_number) - 1, len(profile.payout_cap_schedule) - 1))
    return float(profile.payout_cap_schedule[index])


def plan_with_costs(plan: LifecyclePlan, *, eval_fee: float, activation_fee: float, reset_fee: float) -> LifecyclePlan:
    return replace(
        plan,
        default_eval_fee=eval_fee,
        default_activation_fee=activation_fee,
        default_reset_fee=reset_fee,
    )
