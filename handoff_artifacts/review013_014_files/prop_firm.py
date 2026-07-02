"""V4 — prop-firm / funded-account engine.

An explicit, event-driven account state machine that consumes a chronological
trade stream and simulates deploying a strategy through a prop-firm evaluation and
funded account. Every rule and cost is **declared** (no firm is hardcoded), and the
headline output is **realized net cash to the trader**, never the notional account
balance.

GOVERNING PRINCIPLE (ADR-019/020, KNOWN_LIMITATIONS):
  * A notional prop-account balance is NOT personal wealth. Only realized net cash
    — trader's split of withdrawn payouts, minus evaluation / activation / reset
    fees — counts.
  * Consistent with the V1/V2 realized-P&L booking, drawdown and daily-loss rules
    are evaluated on **end-of-trade realized balances**, NOT intratrade excursions.
    This UNDERSTATES breach probability (a trade may breach intratrade and recover
    before its close). Stated on every breach/survival number.

Phases: ``evaluation`` -> (pass) ``funded`` -> (max payouts) ``retired``.
Terminal states: ``retired`` (success), ``funded`` (still active at stream end),
``failed_dead`` (breached with no reset left), ``evaluation`` (target never met).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import pandas as pd

from sim_core.batch import hash_trades
from sim_core.models import Trade

# Phase constants -------------------------------------------------------------------
PHASE_EVALUATION = "evaluation"
PHASE_FUNDED = "funded"
PHASE_RETIRED = "retired"
PHASE_FAILED = "failed_dead"

_TRAILING_BASES = {"end_of_trade", "end_of_day"}
# "standard": withdraw when above first_payout_threshold, gated by
# min_days_between_payouts. "daily": at most one payout per calendar day (the
# instant-funding / daily-payout firm model).
_PAYOUT_MODES = {"standard", "daily"}


@dataclass(frozen=True)
class PropFirmRules:
    """Declared prop-firm account rules and economics. No firm defaults are baked in.

    Money amounts are in account currency (USD). ``trailing_drawdown`` is the size
    of the trailing max-drawdown band below the peak balance. ``trailing_lock_at``
    is the balance level at which the drawdown floor stops rising (e.g. the account
    start, the common "trailing locks at breakeven" rule); ``None`` = pure trailing
    that never locks.
    """

    account_size: float
    profit_target: float
    trailing_drawdown: float
    trailing_basis: str = "end_of_trade"
    trailing_lock_at: float | None = None
    daily_loss_limit: float | None = None
    min_trading_days: int = 0
    consistency_pct: float | None = None
    # economics
    evaluation_fee: float = 0.0
    activation_fee: float = 0.0
    reset_fee: float | None = None  # None => a failed evaluation cannot be reset
    # payout
    payout_mode: str = "standard"  # "standard" (threshold + min_days) | "daily"
    profit_split: float = 0.9
    payout_buffer: float = 0.0  # profit above start that must be retained after a payout
    first_payout_threshold: float | None = None  # min funded profit before first payout
    payout_cap: float | None = None
    min_days_between_payouts: int = 0
    max_payouts: int | None = None
    # sizing on the copied stream (fixed contract count for V4)
    contracts_per_trade: int = 1
    label: str = ""

    def __post_init__(self) -> None:
        if self.account_size <= 0:
            raise ValueError("account_size must be positive")
        if self.profit_target <= 0:
            raise ValueError("profit_target must be positive")
        if self.trailing_drawdown <= 0:
            raise ValueError("trailing_drawdown must be positive")
        if self.trailing_basis not in _TRAILING_BASES:
            raise ValueError(f"trailing_basis must be one of {sorted(_TRAILING_BASES)}")
        if self.daily_loss_limit is not None and self.daily_loss_limit <= 0:
            raise ValueError("daily_loss_limit must be positive when set")
        if self.min_trading_days < 0:
            raise ValueError("min_trading_days cannot be negative")
        if self.consistency_pct is not None and not (0 < self.consistency_pct <= 1):
            raise ValueError("consistency_pct must be in (0, 1]")
        for name in ("evaluation_fee", "activation_fee", "payout_buffer"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.reset_fee is not None and self.reset_fee < 0:
            raise ValueError("reset_fee cannot be negative")
        if not (0 < self.profit_split <= 1):
            raise ValueError("profit_split must be in (0, 1]")
        if self.first_payout_threshold is not None and self.first_payout_threshold < 0:
            raise ValueError("first_payout_threshold cannot be negative")
        if self.payout_cap is not None and self.payout_cap <= 0:
            raise ValueError("payout_cap must be positive when set")
        if self.payout_mode not in _PAYOUT_MODES:
            raise ValueError(f"payout_mode must be one of {sorted(_PAYOUT_MODES)}")
        if self.min_days_between_payouts < 0:
            raise ValueError("min_days_between_payouts cannot be negative")
        if self.max_payouts is not None and self.max_payouts <= 0:
            raise ValueError("max_payouts must be positive when set")
        if self.contracts_per_trade <= 0:
            raise ValueError("contracts_per_trade must be positive")

    def drawdown_floor(self, peak: float) -> float:
        """Floor below which the account breaches, given the running peak balance."""

        floor = peak - self.trailing_drawdown
        if self.trailing_lock_at is not None:
            # Floor stops rising once it reaches the lock level (e.g. the account
            # start — the common "trailing locks at breakeven" rule).
            floor = min(floor, self.trailing_lock_at)
        return floor

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_size": self.account_size,
            "profit_target": self.profit_target,
            "trailing_drawdown": self.trailing_drawdown,
            "trailing_basis": self.trailing_basis,
            "trailing_lock_at": self.trailing_lock_at,
            "daily_loss_limit": self.daily_loss_limit,
            "min_trading_days": self.min_trading_days,
            "consistency_pct": self.consistency_pct,
            "evaluation_fee": self.evaluation_fee,
            "activation_fee": self.activation_fee,
            "reset_fee": self.reset_fee,
            "payout_mode": self.payout_mode,
            "profit_split": self.profit_split,
            "payout_buffer": self.payout_buffer,
            "first_payout_threshold": self.first_payout_threshold,
            "payout_cap": self.payout_cap,
            "min_days_between_payouts": self.min_days_between_payouts,
            "max_payouts": self.max_payouts,
            "contracts_per_trade": self.contracts_per_trade,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PropFirmRules":
        return cls(**data)


@dataclass(frozen=True)
class PropPhaseEvent:
    """A recorded state-machine transition or breach."""

    timestamp: pd.Timestamp
    event_type: str  # eval_start | reset | passed | activated | breach | payout | retired
    phase: str
    balance: float
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type,
            "phase": self.phase,
            "balance": self.balance,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class PayoutRecord:
    timestamp: pd.Timestamp
    gross_amount: float  # notional profit withdrawn from the account
    trader_amount: float  # trader's split — the real cash received
    balance_after: float
    payout_index: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "gross_amount": self.gross_amount,
            "trader_amount": self.trader_amount,
            "balance_after": self.balance_after,
            "payout_index": self.payout_index,
        }


@dataclass(frozen=True)
class PropAccountResult:
    rules: PropFirmRules
    terminal_phase: str
    terminal_balance: float
    events: list[PropPhaseEvent]
    payouts: list[PayoutRecord]
    summary: dict[str, Any]
    input_data_hash: str = ""

    @property
    def net_trader_cash(self) -> float:
        return float(self.summary["net_trader_cash"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "rules": self.rules.to_dict(),
            "terminal_phase": self.terminal_phase,
            "terminal_balance": self.terminal_balance,
            "events": [e.to_dict() for e in self.events],
            "payouts": [p.to_dict() for p in self.payouts],
            "summary": self.summary,
            "input_data_hash": self.input_data_hash,
        }


# --------------------------------------------------------------------------- engine


def _ordered_trades(trades: list[Trade]) -> list[Trade]:
    # Realized P&L books at exit; break ties deterministically.
    return sorted(trades, key=lambda t: (t.exit_time, t.entry_time, t.trade_id))


class _AccountMachine:
    """Mutable running state for a single prop account over one trade stream."""

    def __init__(self, rules: PropFirmRules, initial_phase: str = PHASE_EVALUATION) -> None:
        self.rules = rules
        self.phase = initial_phase
        self.balance = rules.account_size
        self.peak = rules.account_size
        self.resets_used = 0
        # An account started directly in the funded phase is funded by assumption.
        self.reached_funded = initial_phase == PHASE_FUNDED
        # per-day realized P&L tracking (keyed by exit date)
        self._day_start_balance: float | None = None
        self._current_day = None
        self.trading_days: set = set()
        self.funded_trading_days: set = set()
        self.funded_day_profit: dict = {}  # exit-date -> realized funded P&L that day
        # economics
        self.fees_paid = rules.evaluation_fee  # buying the evaluation
        self.gross_payouts = 0.0
        self.trader_payouts = 0.0
        self.payouts_count = 0
        self._last_payout_day = None
        self._activation_day = None
        self.activation_timestamp: pd.Timestamp | None = None
        self.breach_timestamp: pd.Timestamp | None = None
        self.first_payout_timestamp: pd.Timestamp | None = None
        self.events: list[PropPhaseEvent] = []
        self.payouts: list[PayoutRecord] = []

    def _record(self, ts: pd.Timestamp, event_type: str, detail: str = "") -> None:
        self.events.append(PropPhaseEvent(ts, event_type, self.phase, self.balance, detail))

    def _reset_daily(self, day, opening_balance: float) -> None:
        self._current_day = day
        self._day_start_balance = opening_balance

    def _floor(self) -> float:
        return self.rules.drawdown_floor(self.peak)

    def start(self, ts: pd.Timestamp) -> None:
        self._record(ts, "eval_start", "evaluation purchased")

    # -- breach / reset ----------------------------------------------------------
    def _breach(self, ts: pd.Timestamp, reason: str) -> bool:
        """Handle a breach. Return True if the account is now terminal (dead)."""

        self._record(ts, "breach", reason)
        if self.phase == PHASE_EVALUATION and self.rules.reset_fee is not None:
            self.fees_paid += self.rules.reset_fee
            self.resets_used += 1
            self.balance = self.rules.account_size
            self.peak = self.rules.account_size
            self.trading_days = set()
            self._current_day = None
            self._day_start_balance = None
            self._record(ts, "reset", f"reset #{self.resets_used}")
            return False
        # funded breach, or evaluation with no reset allowed => dead
        self.phase = PHASE_FAILED
        self.breach_timestamp = ts
        return True

    # -- apply one trade ---------------------------------------------------------
    def apply_trade(self, trade: Trade) -> bool:
        """Apply a single trade at its exit. Return True if the account is terminal."""

        if self.phase in (PHASE_FAILED, PHASE_RETIRED):
            return True

        day = trade.exit_time.date()
        if day != self._current_day:
            self._reset_daily(day, self.balance)

        pnl = self.rules.contracts_per_trade * float(trade.pnl_dollars)
        self.balance += pnl
        if self.balance > self.peak:
            self.peak = self.balance

        if self.phase == PHASE_EVALUATION:
            self.trading_days.add(day)
        else:
            self.funded_trading_days.add(day)
            self.funded_day_profit[day] = self.funded_day_profit.get(day, 0.0) + pnl

        # --- breach checks (realized-only) ---
        if self.balance <= self._floor():
            return self._breach(trade.exit_time, "trailing drawdown breach")
        if self.rules.daily_loss_limit is not None and self._day_start_balance is not None:
            if (self._day_start_balance - self.balance) >= self.rules.daily_loss_limit:
                return self._breach(trade.exit_time, "daily loss limit breach")

        # --- phase progression ---
        if self.phase == PHASE_EVALUATION:
            self._maybe_pass(trade.exit_time)
        if self.phase == PHASE_FUNDED:
            self._maybe_payout(trade.exit_time)

        return self.phase in (PHASE_FAILED, PHASE_RETIRED)

    def _maybe_pass(self, ts: pd.Timestamp) -> None:
        profit = self.balance - self.rules.account_size
        if profit >= self.rules.profit_target and len(self.trading_days) >= self.rules.min_trading_days:
            self._record(ts, "passed", f"profit {profit:.2f} >= target")
            self.fees_paid += self.rules.activation_fee
            self.phase = PHASE_FUNDED
            self.reached_funded = True
            # funded account starts fresh at its own starting balance
            self.balance = self.rules.account_size
            self.peak = self.rules.account_size
            self._activation_day = ts.date()
            self.activation_timestamp = ts
            self._current_day = None
            self._day_start_balance = None
            self.funded_trading_days = set()
            self.funded_day_profit = {}
            self._record(ts, "activated", "funded account activated")

    def _payout_eligible(self, ts: pd.Timestamp) -> bool:
        r = self.rules
        profit = self.balance - r.account_size
        withdrawable = profit - r.payout_buffer
        if withdrawable <= 0:
            return False
        threshold = r.first_payout_threshold if self.payouts_count == 0 else 0.0
        if threshold is not None and profit < threshold:
            return False
        if len(self.funded_trading_days) < r.min_trading_days:
            return False
        if r.payout_mode == "daily":
            # At most one payout per calendar day; no multi-day wait.
            if self._last_payout_day is not None and ts.date() <= self._last_payout_day:
                return False
        else:
            reference_day = self._last_payout_day or self._activation_day
            if reference_day is not None and r.min_days_between_payouts > 0:
                if (ts.date() - reference_day).days < r.min_days_between_payouts:
                    return False
        if r.consistency_pct is not None and self.funded_day_profit:
            total = sum(v for v in self.funded_day_profit.values() if v > 0)
            best = max(self.funded_day_profit.values())
            if total > 0 and best > r.consistency_pct * total:
                return False
        return True

    def _maybe_payout(self, ts: pd.Timestamp) -> None:
        r = self.rules
        if not self._payout_eligible(ts):
            return
        profit = self.balance - r.account_size
        withdrawable = profit - r.payout_buffer
        gross = withdrawable if r.payout_cap is None else min(withdrawable, r.payout_cap)
        if gross <= 0:
            return
        trader_cash = gross * r.profit_split
        self.balance -= gross
        # A withdrawal is NOT a trading loss (charter accounting rule). Move the
        # trailing floor down with the balance (no self-breach) AND lower the
        # current day's opening baseline so the withdrawal is not miscounted as a
        # daily-loss-limit breach for later trades on the same day.
        self.peak -= gross
        if self._day_start_balance is not None:
            self._day_start_balance -= gross
        self.gross_payouts += gross
        self.trader_payouts += trader_cash
        self.payouts_count += 1
        self._last_payout_day = ts.date()
        if self.first_payout_timestamp is None:
            self.first_payout_timestamp = ts
        self.payouts.append(
            PayoutRecord(ts, gross, trader_cash, self.balance, self.payouts_count)
        )
        self._record(ts, "payout", f"gross {gross:.2f}, trader {trader_cash:.2f}")
        if r.max_payouts is not None and self.payouts_count >= r.max_payouts:
            self.phase = PHASE_RETIRED
            self._record(ts, "retired", f"max payouts {r.max_payouts} reached")


def run_prop_account_path(
    trades: list[Trade],
    rules: PropFirmRules,
    *,
    verify_hash: bool = True,
    initial_phase: str = PHASE_EVALUATION,
) -> PropAccountResult:
    """Run a single prop account over one chronological trade stream.

    ``initial_phase`` = ``"funded"`` starts the account already funded (skips the
    evaluation), for "assume I am funded — how often do I blow up over the next N
    months" analyses (see ``funded_window_analysis``).
    """

    if initial_phase not in (PHASE_EVALUATION, PHASE_FUNDED):
        raise ValueError("initial_phase must be 'evaluation' or 'funded'")
    ordered = _ordered_trades(list(trades))
    machine = _AccountMachine(rules, initial_phase=initial_phase)
    stream_start = ordered[0].entry_time if ordered else None
    if stream_start is not None:
        if initial_phase == PHASE_FUNDED:
            machine.activation_timestamp = stream_start
            machine._activation_day = stream_start.date()
            machine._record(stream_start, "activated", "funded (assumed start)")
        else:
            machine.start(stream_start)

    for trade in ordered:
        terminal = machine.apply_trade(trade)
        if terminal:
            break

    def _days_from_start(ts: pd.Timestamp | None) -> float | None:
        if ts is None or stream_start is None:
            return None
        return (ts - stream_start).total_seconds() / 86400.0

    net_cash = machine.trader_payouts - machine.fees_paid
    summary: dict[str, Any] = {
        "terminal_phase": machine.phase,
        "terminal_balance": machine.balance,
        "reached_funded": machine.reached_funded,
        "passed_evaluation": machine.reached_funded and initial_phase == PHASE_EVALUATION,
        "survived": machine.phase in (PHASE_FUNDED, PHASE_RETIRED),
        "blew_up": machine.phase == PHASE_FAILED,
        "resets_used": machine.resets_used,
        "fees_paid": machine.fees_paid,
        "gross_payouts": machine.gross_payouts,
        "trader_payouts": machine.trader_payouts,
        "net_trader_cash": net_cash,
        "payouts_count": machine.payouts_count,
        "first_payout_achieved": machine.payouts_count >= 1,
        "time_to_first_payout_days": _days_from_start(machine.first_payout_timestamp),
        "time_to_pass_days": _days_from_start(machine.activation_timestamp)
        if initial_phase == PHASE_EVALUATION
        else None,
        "time_to_blow_days": _days_from_start(machine.breach_timestamp),
        "eval_trading_days": len(machine.trading_days),
        "funded_trading_days": len(machine.funded_trading_days),
        "realized_only_note": (
            "Drawdown/daily-loss evaluated on end-of-trade realized balances; "
            "intratrade excursions are not modeled and breach probability is a "
            "lower bound."
        ),
        "notional_balance_note": (
            "terminal_balance is a notional account figure, NOT personal wealth; "
            "only net_trader_cash is realized."
        ),
    }
    input_hash = hash_trades(ordered) if verify_hash else ""
    summary["input_data_hash"] = input_hash
    summary["config_hash"] = _rules_hash(rules)

    return PropAccountResult(
        rules=rules,
        terminal_phase=machine.phase,
        terminal_balance=machine.balance,
        events=machine.events,
        payouts=machine.payouts,
        summary=summary,
        input_data_hash=input_hash,
    )


def run_prop_account_portfolio(
    trades: list[Trade],
    rules_list: list[PropFirmRules],
) -> dict[str, Any]:
    """Run several copied prop accounts over the SAME trade stream and aggregate.

    Models copy-trading N funded accounts off one strategy. The combined figure is
    the sum of realized net cash across accounts (correlated by construction: they
    share the identical trade path)."""

    if not rules_list:
        raise ValueError("rules_list must be non-empty")
    results = [run_prop_account_path(trades, rules) for rules in rules_list]
    combined_net = sum(r.net_trader_cash for r in results)
    combined_fees = sum(float(r.summary["fees_paid"]) for r in results)
    combined_trader = sum(float(r.summary["trader_payouts"]) for r in results)
    return {
        "accounts": results,
        "num_accounts": len(results),
        "combined_net_trader_cash": combined_net,
        "combined_fees_paid": combined_fees,
        "combined_trader_payouts": combined_trader,
        "num_survived": sum(1 for r in results if r.summary["survived"]),
        "num_with_payout": sum(1 for r in results if r.summary["first_payout_achieved"]),
        "correlation_note": (
            "Accounts share one identical trade path (fully correlated); this is a "
            "copy-trading model, not independent diversification."
        ),
    }


def summarize_prop_accounts(results: list[PropAccountResult]) -> dict[str, Any]:
    """Aggregate cash economics across many account runs (paths x accounts)."""

    n = len(results)
    if n == 0:
        return {"num_accounts": 0}

    def _mean(key: str) -> float:
        return sum(float(r.summary[key]) for r in results) / n

    net = sorted(r.net_trader_cash for r in results)
    ttp = [
        float(r.summary["time_to_first_payout_days"])
        for r in results
        if r.summary["time_to_first_payout_days"] is not None
    ]

    def _pct(sorted_vals: list[float], q: float) -> float:
        if not sorted_vals:
            return 0.0
        idx = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
        return sorted_vals[idx]

    return {
        "num_accounts": n,
        "prob_reached_funded": sum(1 for r in results if r.summary["reached_funded"]) / n,
        "prob_first_payout": sum(1 for r in results if r.summary["first_payout_achieved"]) / n,
        "prob_survived": sum(1 for r in results if r.summary["survived"]) / n,
        "prob_failed": sum(1 for r in results if r.terminal_phase == PHASE_FAILED) / n,
        "expected_net_trader_cash": _mean("net_trader_cash"),
        "median_net_trader_cash": _pct(net, 0.5),
        "p5_net_trader_cash": _pct(net, 0.05),
        "p95_net_trader_cash": _pct(net, 0.95),
        "prob_net_cash_positive": sum(1 for r in results if r.net_trader_cash > 0) / n,
        "expected_fees_paid": _mean("fees_paid"),
        "expected_gross_payouts": _mean("gross_payouts"),
        "expected_trader_payouts": _mean("trader_payouts"),
        "mean_time_to_first_payout_days": (sum(ttp) / len(ttp)) if ttp else None,
        "expected_resets_used": _mean("resets_used"),
        "notional_balance_note": (
            "Aggregates realized net cash only. Notional account balances are "
            "excluded — they are not personal wealth."
        ),
    }


def _quantile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def _mean_or_none(vals: list[float]) -> float | None:
    return (sum(vals) / len(vals)) if vals else None


def summarize_evaluation_stage(results: list[PropAccountResult]) -> dict[str, Any]:
    """Evaluation-stage stats across many eval accounts (one per resampled path).

    Answers: how often does the evaluation pass, how long does it take, and how
    much does failing cost? Run each account with the default ``initial_phase``
    ("evaluation")."""

    n = len(results)
    if n == 0:
        return {"num_accounts": 0}
    passed = [r for r in results if r.summary["reached_funded"]]
    times = sorted(
        float(r.summary["time_to_pass_days"])
        for r in passed
        if r.summary.get("time_to_pass_days") is not None
    )
    return {
        "num_accounts": n,
        "pass_rate": len(passed) / n,
        "fail_rate": sum(1 for r in results if r.summary["blew_up"]) / n,
        "incomplete_rate": sum(
            1 for r in results if r.terminal_phase == PHASE_EVALUATION
        )
        / n,  # ran out of data still trying
        "mean_days_to_pass": _mean_or_none(times),
        "median_days_to_pass": _quantile(times, 0.5) if times else None,
        "p95_days_to_pass": _quantile(times, 0.95) if times else None,
        "mean_eval_trading_days_to_pass": _mean_or_none(
            [float(r.summary["eval_trading_days"]) for r in passed]
        ),
        "mean_resets_used": sum(float(r.summary["resets_used"]) for r in results) / n,
        "mean_eval_cost_paid": sum(float(r.summary["fees_paid"]) for r in results) / n,
        "note": (
            "Pass/fail measured on realized end-of-trade balances (breach probability "
            "is a lower bound). Time-to-pass is calendar days from the account's first "
            "trade."
        ),
    }


def funded_window_analysis(
    trades: list[Trade],
    rules: PropFirmRules,
    *,
    horizons_months: Sequence[int] = (2, 4, 6, 8, 12),
    num_starts: int | None = 200,
    seed: int = 0,
) -> dict[str, Any]:
    """Blow-up / payout economics for an ALREADY-FUNDED account over fixed windows
    that begin at random historical start dates.

    For each horizon H (months), sample random real trade-start timestamps whose
    window ``[start, start+H)`` fits inside the data, run a funded account over the
    trades in that window, and aggregate blow rate, survival, payout probability,
    and realized net cash. This answers "if I go live funded from a random point,
    how often do I blow the account within N months, and what do I clear?".
    """

    import random

    ordered = _ordered_trades(list(trades))
    if not ordered:
        return {"horizons": {}, "note": "no trades supplied"}
    first_entry = ordered[0].entry_time
    last_exit = max(t.exit_time for t in ordered)
    rng = random.Random(seed)

    horizons: dict[str, Any] = {}
    for months in horizons_months:
        offset = pd.DateOffset(months=int(months))
        candidate_starts = [t.entry_time for t in ordered if t.entry_time + offset <= last_exit]
        if not candidate_starts:
            horizons[str(months)] = {
                "insufficient_data": True,
                "note": f"history shorter than a {months}-month window",
            }
            continue
        if num_starts is not None and num_starts < len(candidate_starts):
            starts = rng.sample(candidate_starts, num_starts)
        else:
            starts = candidate_starts

        window_results: list[PropAccountResult] = []
        trade_counts: list[int] = []
        for start in starts:
            window_end = start + offset
            sub = [t for t in ordered if start <= t.entry_time < window_end]
            if not sub:
                continue
            trade_counts.append(len(sub))
            window_results.append(
                run_prop_account_path(sub, rules, verify_hash=False, initial_phase=PHASE_FUNDED)
            )

        m = len(window_results)
        if m == 0:
            horizons[str(months)] = {"insufficient_data": True, "note": "no non-empty windows"}
            continue
        net = sorted(r.net_trader_cash for r in window_results)
        blow_times = sorted(
            float(r.summary["time_to_blow_days"])
            for r in window_results
            if r.summary["blew_up"] and r.summary["time_to_blow_days"] is not None
        )
        horizons[str(months)] = {
            "num_windows": m,
            "blow_rate": sum(1 for r in window_results if r.summary["blew_up"]) / m,
            "survival_rate": sum(1 for r in window_results if not r.summary["blew_up"]) / m,
            "prob_payout": sum(1 for r in window_results if r.summary["first_payout_achieved"]) / m,
            "expected_num_payouts": sum(float(r.summary["payouts_count"]) for r in window_results) / m,
            "expected_net_trader_cash": sum(net) / m,
            "median_net_trader_cash": _quantile(net, 0.5),
            "p5_net_trader_cash": _quantile(net, 0.05),
            "p95_net_trader_cash": _quantile(net, 0.95),
            "prob_net_cash_positive": sum(1 for r in window_results if r.net_trader_cash > 0) / m,
            "mean_days_to_blow": _mean_or_none(blow_times),
            "mean_trades_per_window": _mean_or_none([float(c) for c in trade_counts]),
        }

    return {
        "horizons": horizons,
        "data_span": {"first_entry": first_entry.isoformat(), "last_exit": last_exit.isoformat()},
        "num_starts_requested": num_starts,
        "input_data_hash": hash_trades(ordered),
        "config_hash": _rules_hash(rules),
        "notes": [
            "Account is assumed already funded at each window start (evaluation skipped).",
            "Blow rate is realized-only (end-of-trade); it is a LOWER bound on true "
            "intratrade breach risk.",
            "Windows begin at real historical trade starts and overlap; they are not "
            "independent samples (blocks of the same history are reused).",
            "Net cash is the trader's realized split minus activation/reset fees; the "
            "notional balance is not personal wealth.",
        ],
    }


def _rules_hash(rules: PropFirmRules) -> str:
    import hashlib
    import json

    payload = json.dumps(rules.to_dict(), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
