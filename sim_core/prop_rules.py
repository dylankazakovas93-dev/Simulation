from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any, Literal

import numpy as np
import pandas as pd

from sim_core.models import Trade

DrawdownMode = Literal["eod_trailing", "intraday_trailing", "fixed"]


@dataclass(frozen=True)
class PropRuleProfile:
    firm: str
    account_name: str
    account_size: float
    max_loss: float
    drawdown_mode: DrawdownMode
    max_micro_contracts: int
    profit_split: float
    min_payout: float = 0.0
    max_payout: float | None = None
    payout_cap_schedule: tuple[float, ...] = ()
    payout_count_cap: int | None = None
    payout_profit_fraction: float = 1.0
    payout_reserve: float = 0.0
    payout_cadence: str = ""
    min_winning_days: int = 0
    winning_day_threshold: float = 0.0
    consistency_pct: float | None = None
    daily_loss_limit: float | None = None
    daily_loss_hard: bool = False
    withdrawal_buffer: float = 0.0
    activation_fee: float = 0.0
    source: str = ""
    notes: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.firm} - {self.account_name}"

    @property
    def starting_balance(self) -> float:
        return self.account_size

    @property
    def starting_floor(self) -> float:
        return self.account_size - self.max_loss

    @property
    def payout_profit_required(self) -> float:
        fraction = self.payout_profit_fraction if self.payout_profit_fraction > 0 else 1.0
        min_payout_profit = self.payout_reserve + (
            self.min_payout / fraction if self.min_payout > 0 else 0.0
        )
        return max(self.withdrawal_buffer, min_payout_profit)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["key"] = self.key
        data["starting_floor"] = self.starting_floor
        data["payout_profit_required"] = self.payout_profit_required
        return data


@dataclass(frozen=True)
class ConflictDecision:
    trade_id: str
    strategy_id: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    kept: bool
    reason: str
    conflicting_trade_id: str | None = None
    conflicting_strategy_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["entry_time"] = self.entry_time.isoformat()
        data["exit_time"] = self.exit_time.isoformat()
        return data


@dataclass(frozen=True)
class PropPathResult:
    profile_key: str
    contracts: int
    failed: bool
    failure_reason: str | None
    ending_balance: float
    minimum_balance: float
    ending_floor: float
    max_drawdown: float
    net_profit: float
    gross_cash_available: float
    payout_after_split: float
    eligible: bool
    first_eligible_day: int | None
    winning_days: int
    daily_guard_pauses: int
    trades_taken: int
    trades_skipped: int
    approximation_warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def default_prop_rule_profiles() -> dict[str, PropRuleProfile]:
    """Built-in profiles transcribed from the user-supplied PDFs.

    These are parameter presets, not a legal/rules guarantee. The simulator
    models payout and drawdown mechanics that are visible from point ledgers;
    news, inactivity, platform, and discretionary rules remain outside scope.
    """

    profiles: list[PropRuleProfile] = []

    alpha_common = {
        "firm": "Alpha Futures",
        "drawdown_mode": "eod_trailing",
        "profit_split": 0.90,
        "min_winning_days": 5,
        "winning_day_threshold": 200.0,
        "payout_profit_fraction": 0.50,
        "payout_cadence": "Up to 4 requests per month after every 5 qualifying winning days.",
        "activation_fee": 149.0,
        "source": "Alpha Rules PDF, account overview + Maximum Loss Limit pages",
        "notes": (
            "MLL trails from end-of-day balance highs and locks at starting balance.",
            "Payout qualification uses up to 50% of profits after five $200 winning days.",
        ),
    }
    for name, size, max_loss, max_micros, min_payout, max_payout in (
        ("Advanced 50K", 50_000, 1_750, 50, 1_000, 15_000),
        ("Advanced 100K", 100_000, 3_500, 100, 1_000, 15_000),
        ("Advanced 150K", 150_000, 5_250, 150, 1_000, 15_000),
        ("Premium 50K", 50_000, 2_000, 40, 500, 4_000),
        ("Premium 100K", 100_000, 3_000, 80, 500, 5_000),
        ("Premium 150K", 150_000, 4_500, 120, 500, 6_000),
    ):
        profiles.append(
            PropRuleProfile(
                account_name=name,
                account_size=size,
                max_loss=max_loss,
                max_micro_contracts=max_micros,
                min_payout=min_payout,
                max_payout=max_payout,
                consistency_pct=None,
                payout_count_cap=None,
                **alpha_common,
            )
        )

    for name, size, max_loss, max_micros, min_payout, max_payout, dll in (
        ("Standard 50K", 50_000, 2_000, 50, 200, 15_000, 1_000),
        ("Standard 100K", 100_000, 4_000, 100, 200, 15_000, 2_000),
        ("Standard 150K", 150_000, 6_000, 150, 200, 15_000, 3_000),
        ("Zero 25K", 25_000, 1_000, 10, 200, 1_000, 500),
        ("Zero 50K", 50_000, 2_000, 30, 200, 1_500, 1_000),
        ("Zero 100K", 100_000, 3_000, 60, 200, 2_500, 2_000),
    ):
        profiles.append(
            PropRuleProfile(
                account_name=name,
                account_size=size,
                max_loss=max_loss,
                max_micro_contracts=max_micros,
                min_payout=min_payout,
                max_payout=max_payout,
                consistency_pct=0.40,
                daily_loss_limit=dll,
                payout_count_cap=None,
                **alpha_common,
            )
        )

    fundednext_source = "FundedNext Rules PDF, Futures Trading Objectives pages"
    fundednext_common = {
        "firm": "FundedNext Futures",
        "drawdown_mode": "eod_trailing",
        "profit_split": 0.80,
        "payout_profit_fraction": 0.80,
        "consistency_pct": 0.40,
        "payout_cadence": "Performance Reward can be requested as early as 3 days after meeting consistency; no benchmark days modeled.",
        "payout_count_cap": None,
        "source": fundednext_source,
        "notes": (
            "MLL trails from highest end-of-day balance, locks at initial balance.",
            "Consistency uses largest single trading day <= 40% of total profit.",
            "Futures reward share shown as 80% standard in the supplied rules.",
        ),
    }
    for name, size, target, max_loss, daily_loss, min_payout, max_payout in (
        ("Rapid 25K", 25_000, 1_500, 1_000, None, 250, 800),
        ("Rapid 50K", 50_000, 3_000, 2_000, None, 250, 1_500),
        ("Rapid 100K", 100_000, 5_000, 2_500, None, 500, 2_500),
        ("Legacy 25K", 25_000, 1_250, 1_000, None, 0, None),
        ("Legacy 50K", 50_000, 3_000, 2_000, None, 0, None),
        ("Legacy 100K", 100_000, 6_000, 3_000, None, 0, None),
        ("Flex 50K", 50_000, 2_500, 1_500, None, 0, None),
        ("Flex 100K", 100_000, 5_000, 2_500, None, 0, None),
        ("Flex 150K", 150_000, 8_000, 4_000, None, 0, None),
        ("Bolt 50K", 50_000, 3_000, 2_000, 1_000, 0, None),
    ):
        cap_note = (
            "Rapid account max reward caps are modeled through the first five requests; cap removal "
            "after five rewards is not dynamically modeled."
            if name.startswith("Rapid")
            else "Performance Reward min/max caps for this challenge variant need source-level confirmation."
        )
        profiles.append(
            PropRuleProfile(
                account_name=name,
                account_size=size,
                max_loss=max_loss,
                max_micro_contracts=999,
                min_payout=min_payout,
                max_payout=max_payout,
                withdrawal_buffer=0.0,
                daily_loss_limit=daily_loss,
                notes=(
                    *fundednext_common["notes"],
                    f"Challenge profit target: ${target:,.0f}.",
                    cap_note,
                ),
                **{k: v for k, v in fundednext_common.items() if k != "notes"},
            )
        )

    tpt_common = {
        "firm": "TakeProfitTrader",
        "drawdown_mode": "intraday_trailing",
        "profit_split": 0.80,
        "payout_profit_fraction": 0.80,
        "payout_cadence": "Withdrawals can start day one after building the max-drawdown buffer.",
        "source": "TPT Rules PDF, PRO Account Rules + Profit Split & Withdrawal Rules",
        "notes": (
            "PRO drawdown is intraday and includes unrealized gains; MAE/MFE columns improve approximation.",
            "Normal withdrawal at 80% starts after reaching the buffer zone equal to max drawdown.",
        ),
    }
    for name, size, max_loss in (
        ("PRO 25K", 25_000, 1_500),
        ("PRO 50K", 50_000, 2_000),
        ("PRO 75K", 75_000, 2_500),
        ("PRO 100K", 100_000, 3_000),
        ("PRO 150K", 150_000, 4_500),
    ):
        profiles.append(
            PropRuleProfile(
                account_name=name,
                account_size=size,
                max_loss=max_loss,
                max_micro_contracts=999,
                withdrawal_buffer=max_loss,
                **tpt_common,
            )
        )

    apex_common = {
        "firm": "Apex Trader Funding",
        "drawdown_mode": "eod_trailing",
        "profit_split": 1.00,
        "min_payout": 500.0,
        "payout_profit_fraction": 1.0,
        "payout_cadence": "Up to weekly after 5 qualifying trading days.",
        "payout_count_cap": 6,
        "consistency_pct": 0.50,
        "min_winning_days": 5,
        "source": "Apex pasted rules, EOD Evaluations + EOD Payouts + 50% Consistency",
        "notes": (
            "EOD threshold is calculated once per day at market close and enforced next session.",
            "Safety net is drawdown limit plus $100; only profit above that reserve is withdrawable.",
            "Payout cap ladder is modeled by sequential approved payout number.",
            "Max contracts are stored as micro-equivalent contracts because this simulator sizes MNQ-style micro contracts.",
            "Apex PA scaling tiers are approximated with the maximum tier cap; current-tier enforcement is not modeled yet.",
        ),
    }
    for name, size, max_loss, daily_loss, max_contracts, min_day_profit, payout_caps in (
        ("EOD PA 25K", 25_000, 1_000, 500, 20, 100, (1_000, 1_000, 1_000, 1_000, 1_000, 1_000)),
        ("EOD PA 50K", 50_000, 2_000, 1_000, 40, 250, (1_500, 1_500, 2_000, 2_500, 2_500, 3_000)),
        ("EOD PA 100K", 100_000, 3_000, 1_500, 60, 300, (2_000, 2_500, 2_500, 3_000, 4_000, 4_000)),
        ("EOD PA 150K", 150_000, 4_000, 2_000, 100, 350, (2_500, 3_000, 3_000, 3_000, 4_000, 5_000)),
    ):
        profiles.append(
            PropRuleProfile(
                account_name=name,
                account_size=size,
                max_loss=max_loss,
                max_micro_contracts=max_contracts,
                max_payout=payout_caps[0],
                payout_cap_schedule=payout_caps,
                payout_reserve=max_loss + 100,
                winning_day_threshold=min_day_profit,
                daily_loss_limit=daily_loss,
                **apex_common,
            )
        )

    return {profile.key: profile for profile in profiles}


def resolve_overlapping_trades(
    trades: list[Trade],
    priority: list[str],
) -> tuple[list[Trade], list[ConflictDecision]]:
    """Keep higher-priority trades when open intervals overlap.

    Intervals are half-open: [entry_time, exit_time). A higher priority trade
    can remove a lower-priority trade even if the lower-priority entry occurred
    first, which matches a portfolio construction decision made after seeing
    complete ledgers.
    """

    rank = {strategy_id: index for index, strategy_id in enumerate(priority)}
    fallback_rank = len(rank)
    candidates = sorted(
        trades,
        key=lambda trade: (
            rank.get(trade.strategy_id, fallback_rank),
            trade.entry_time,
            trade.exit_time,
            trade.source_row_id,
        ),
    )
    kept: list[Trade] = []
    decisions: list[ConflictDecision] = []
    for trade in candidates:
        conflict = next((selected for selected in kept if _overlaps(trade, selected)), None)
        if conflict is None:
            kept.append(trade)
            decisions.append(
                ConflictDecision(
                    trade_id=trade.trade_id,
                    strategy_id=trade.strategy_id,
                    entry_time=trade.entry_time,
                    exit_time=trade.exit_time,
                    kept=True,
                    reason="kept",
                )
            )
            continue
        decisions.append(
            ConflictDecision(
                trade_id=trade.trade_id,
                strategy_id=trade.strategy_id,
                entry_time=trade.entry_time,
                exit_time=trade.exit_time,
                kept=False,
                reason="overlaps higher-priority selected trade",
                conflicting_trade_id=conflict.trade_id,
                conflicting_strategy_id=conflict.strategy_id,
            )
        )
    return sorted(kept, key=lambda trade: (trade.entry_time, trade.exit_time, trade.strategy_id)), decisions


def simulate_prop_account(
    trades: list[Trade],
    profile: PropRuleProfile,
    *,
    contracts: int,
    dollars_per_point: float = 2.0,
    starting_balance: float | None = None,
    starting_floor: float | None = None,
    timezone: str = "America/New_York",
) -> PropPathResult:
    if contracts < 0:
        raise ValueError("contracts cannot be negative")
    if contracts > profile.max_micro_contracts:
        return PropPathResult(
            profile_key=profile.key,
            contracts=contracts,
            failed=True,
            failure_reason="contract limit exceeded",
            ending_balance=starting_balance or profile.starting_balance,
            minimum_balance=starting_balance or profile.starting_balance,
            ending_floor=starting_floor or profile.starting_floor,
            max_drawdown=0.0,
            net_profit=0.0,
            gross_cash_available=0.0,
            payout_after_split=0.0,
            eligible=False,
            first_eligible_day=None,
            winning_days=0,
            daily_guard_pauses=0,
            trades_taken=0,
            trades_skipped=len(trades),
        )

    ordered = sorted(trades, key=lambda trade: (trade.exit_time, trade.entry_time, trade.source_row_id))
    balance = float(starting_balance if starting_balance is not None else profile.starting_balance)
    floor = float(starting_floor if starting_floor is not None else profile.starting_floor)
    floor = max(floor, profile.starting_floor)
    eod_peak = max(profile.starting_balance, balance)
    running_peak = balance
    minimum_balance = balance
    failed = False
    failure_reason: str | None = None
    day = None
    day_pnl = 0.0
    daily_paused = False
    daily_guard_pauses = 0
    winning_days = 0
    daily_profits: list[float] = []
    first_eligible_day: int | None = None
    first_trade_day = _trade_day(ordered[0].entry_time, timezone) if ordered else None
    trades_taken = 0
    trades_skipped = 0
    warnings: set[str] = set()
    if profile.drawdown_mode == "intraday_trailing" and not any(
        trade.mae_points is not None or trade.mfe_points is not None for trade in ordered
    ):
        warnings.add("intraday trailing drawdown approximated with realized P&L only")

    def finish_day() -> None:
        nonlocal day_pnl, winning_days, floor, eod_peak, first_eligible_day
        if day is None:
            return
        if day_pnl >= profile.winning_day_threshold and day_pnl > 0:
            winning_days += 1
        daily_profits.append(day_pnl)
        if profile.drawdown_mode == "eod_trailing":
            eod_peak = max(eod_peak, balance)
            floor = max(floor, min(profile.starting_balance, eod_peak - profile.max_loss))
        if first_eligible_day is None and _is_payout_eligible(
            balance=balance,
            profile=profile,
            winning_days=winning_days,
            daily_profits=daily_profits,
        ):
            first_eligible_day = (
                int((day - first_trade_day).days) if first_trade_day is not None else 0
            )

    for trade in ordered:
        current_day = _trade_day(trade.exit_time, timezone)
        if day is None:
            day = current_day
        elif current_day != day:
            finish_day()
            day = current_day
            day_pnl = 0.0
            daily_paused = False

        if failed:
            trades_skipped += 1
            continue
        if daily_paused:
            trades_skipped += 1
            continue

        pnl = _trade_pnl_dollars(trade, contracts, dollars_per_point)
        mae = _trade_mae_dollars(trade, contracts, dollars_per_point)
        mfe = _trade_mfe_dollars(trade, contracts, dollars_per_point)

        if profile.drawdown_mode == "intraday_trailing":
            running_peak = max(running_peak, balance + max(0.0, mfe))
            floor = max(floor, min(profile.starting_balance, running_peak - profile.max_loss))

        if balance + mae <= floor:
            failed = True
            failure_reason = "maximum loss limit breached by estimated adverse excursion"
            minimum_balance = min(minimum_balance, balance + mae)
            trades_skipped += 1
            continue

        balance += pnl
        day_pnl += pnl
        trades_taken += 1
        running_peak = max(running_peak, balance)
        minimum_balance = min(minimum_balance, balance)

        if balance <= floor:
            failed = True
            failure_reason = "maximum loss limit breached"
            continue

        if profile.daily_loss_limit is not None and day_pnl <= -abs(profile.daily_loss_limit):
            daily_guard_pauses += 1
            if profile.daily_loss_hard:
                failed = True
                failure_reason = "daily loss limit breached"
            else:
                daily_paused = True

    finish_day()
    net_profit = balance - profile.starting_balance
    eligible = _is_payout_eligible(
        balance=balance,
        profile=profile,
        winning_days=winning_days,
        daily_profits=daily_profits,
    )
    gross_cash_available = _gross_cash_available(balance, profile) if eligible else 0.0
    payout_after_split = gross_cash_available * profile.profit_split
    return PropPathResult(
        profile_key=profile.key,
        contracts=contracts,
        failed=failed,
        failure_reason=failure_reason,
        ending_balance=balance,
        minimum_balance=minimum_balance,
        ending_floor=floor,
        max_drawdown=max(0.0, running_peak - minimum_balance),
        net_profit=net_profit,
        gross_cash_available=gross_cash_available,
        payout_after_split=payout_after_split,
        eligible=eligible,
        first_eligible_day=first_eligible_day,
        winning_days=winning_days,
        daily_guard_pauses=daily_guard_pauses,
        trades_taken=trades_taken,
        trades_skipped=trades_skipped,
        approximation_warnings=tuple(sorted(warnings)),
    )


def run_prop_ensemble(
    trades: list[Trade],
    profile: PropRuleProfile,
    *,
    contract_values: list[int],
    paths: int = 250,
    horizon_months: int = 12,
    seed: int = 12345,
    dollars_per_point: float = 2.0,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    rng = np.random.default_rng(seed)
    sampler = MonthBlockSampler(trades, horizon_months)
    for contracts in contract_values:
        path_results: list[PropPathResult] = []
        for _ in range(paths):
            sampled = sampler.sample(rng)
            path_results.append(
                simulate_prop_account(
                    sampled,
                    profile,
                    contracts=contracts,
                    dollars_per_point=dollars_per_point,
                )
            )
        rows.append(_summarize_contract_results(profile, contracts, path_results))
    return pd.DataFrame(rows).sort_values(["convexity_score", "p50_cash"], ascending=[False, False])


class MonthBlockSampler:
    def __init__(self, trades: list[Trade], horizon_months: int) -> None:
        self.horizon_months = horizon_months
        self.by_month: dict[pd.Period, list[Trade]] = {}
        for trade in trades:
            self.by_month.setdefault(trade.source_month, []).append(trade)
        self.months = sorted(self.by_month)
        self.start_month = min((trade.source_month for trade in trades), default=None)
        self.shifted_blocks: dict[tuple[pd.Period, int], list[Trade]] = {}

    def sample(self, rng: np.random.Generator) -> list[Trade]:
        if self.horizon_months <= 0 or not self.months or self.start_month is None:
            return []
        sampled: list[Trade] = []
        for offset in range(self.horizon_months):
            source_month = self.months[int(rng.integers(0, len(self.months)))]
            sampled.extend(self._shifted_block(source_month, offset))
        return sorted(sampled, key=lambda trade: (trade.entry_time, trade.exit_time, trade.strategy_id))

    def _shifted_block(self, source_month: pd.Period, offset: int) -> list[Trade]:
        key = (source_month, offset)
        if key not in self.shifted_blocks:
            target_month = self.start_month + offset
            self.shifted_blocks[key] = [
                trade.shifted_to_month(target_month) for trade in self.by_month[source_month]
            ]
        return self.shifted_blocks[key]


def sample_month_blocks(
    trades: list[Trade],
    *,
    horizon_months: int,
    rng: np.random.Generator,
) -> list[Trade]:
    if horizon_months <= 0 or not trades:
        return []
    by_month: dict[pd.Period, list[Trade]] = {}
    for trade in trades:
        by_month.setdefault(trade.source_month, []).append(trade)
    months = sorted(by_month)
    if not months:
        return []
    start_month = min(trade.source_month for trade in trades)
    sampled: list[Trade] = []
    for offset in range(horizon_months):
        source_month = months[int(rng.integers(0, len(months)))]
        target_month = start_month + offset
        sampled.extend(trade.shifted_to_month(target_month) for trade in by_month[source_month])
    return sorted(sampled, key=lambda trade: (trade.entry_time, trade.exit_time, trade.strategy_id))


def _summarize_contract_results(
    profile: PropRuleProfile,
    contracts: int,
    results: list[PropPathResult],
) -> dict[str, Any]:
    cash = np.array([result.payout_after_split for result in results], dtype=float)
    net = np.array([result.net_profit for result in results], dtype=float)
    fail = np.array([result.failed for result in results], dtype=bool)
    eligible = np.array([result.eligible for result in results], dtype=bool)
    first_days = np.array(
        [np.nan if result.first_eligible_day is None else result.first_eligible_day for result in results],
        dtype=float,
    )
    p50_cash = float(np.percentile(cash, 50)) if len(cash) else 0.0
    p05_cash = float(np.percentile(cash, 5)) if len(cash) else 0.0
    p95_cash = float(np.percentile(cash, 95)) if len(cash) else 0.0
    fail_rate = float(fail.mean()) if len(fail) else 0.0
    eligible_rate = float(eligible.mean()) if len(eligible) else 0.0
    downside = abs(float(np.percentile(net, 5))) if len(net) else 0.0
    convexity = (p50_cash + 0.35 * max(0.0, p95_cash - p50_cash)) / (1.0 + downside)
    convexity *= max(0.0, 1.0 - fail_rate)
    return {
        "profile": profile.key,
        "contracts": contracts,
        "paths": len(results),
        "fail_rate": fail_rate,
        "eligible_rate": eligible_rate,
        "p05_cash": p05_cash,
        "p50_cash": p50_cash,
        "p95_cash": p95_cash,
        "mean_cash": float(cash.mean()) if len(cash) else 0.0,
        "p05_net_profit": float(np.percentile(net, 5)) if len(net) else 0.0,
        "p50_net_profit": float(np.percentile(net, 50)) if len(net) else 0.0,
        "p95_net_profit": float(np.percentile(net, 95)) if len(net) else 0.0,
        "p50_days_to_eligible": _nanpercentile(first_days, 50),
        "p95_days_to_eligible": _nanpercentile(first_days, 95),
        "convexity_score": convexity,
    }


def _is_payout_eligible(
    *,
    balance: float,
    profile: PropRuleProfile,
    winning_days: int,
    daily_profits: list[float],
) -> bool:
    net_profit = balance - profile.starting_balance
    if net_profit < profile.payout_profit_required:
        return False
    if winning_days < profile.min_winning_days:
        return False
    if profile.consistency_pct is None:
        return True
    positive_days = [profit for profit in daily_profits if profit > 0]
    if not positive_days or net_profit <= 0:
        return False
    return max(positive_days) <= profile.consistency_pct * net_profit


def _gross_cash_available(
    balance: float,
    profile: PropRuleProfile,
    *,
    max_payout: float | None = None,
) -> float:
    profit = max(0.0, balance - profile.starting_balance)
    gross = max(0.0, profit - profile.payout_reserve) * profile.payout_profit_fraction
    cap = profile.max_payout if max_payout is None else max_payout
    if cap is not None:
        gross = min(gross, cap)
    if gross < profile.min_payout:
        return 0.0
    return gross


def _trade_pnl_dollars(trade: Trade, contracts: int, default_dpp: float) -> float:
    if trade.pnl_points is not None:
        dpp = trade.dollars_per_point or default_dpp
        return float(trade.pnl_points) * dpp * contracts - trade.commission_round_turn * contracts
    return float(trade.pnl_dollars) * contracts


def _trade_mae_dollars(trade: Trade, contracts: int, default_dpp: float) -> float:
    if trade.mae_points is None:
        return 0.0
    dpp = trade.dollars_per_point or default_dpp
    return -abs(float(trade.mae_points)) * dpp * contracts


def _trade_mfe_dollars(trade: Trade, contracts: int, default_dpp: float) -> float:
    if trade.mfe_points is None:
        return max(0.0, _trade_pnl_dollars(trade, contracts, default_dpp))
    dpp = trade.dollars_per_point or default_dpp
    return abs(float(trade.mfe_points)) * dpp * contracts


def _overlaps(left: Trade, right: Trade) -> bool:
    return left.entry_time < right.exit_time and left.exit_time > right.entry_time


def _trade_day(timestamp: pd.Timestamp, timezone: str) -> pd.Timestamp:
    return _cached_trade_day(timestamp.value, str(timestamp.tz), timezone)


@lru_cache(maxsize=200_000)
def _cached_trade_day(timestamp_ns: int, source_timezone: str, target_timezone: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(timestamp_ns, tz=source_timezone)
    return timestamp.tz_convert(target_timezone).normalize().tz_localize(None)


def _nanpercentile(values: np.ndarray, percentile: float) -> float | None:
    finite = values[np.isfinite(values)]
    if not len(finite):
        return None
    return float(np.percentile(finite, percentile))
