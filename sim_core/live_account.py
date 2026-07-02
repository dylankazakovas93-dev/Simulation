from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
from typing import Any, Literal

import pandas as pd

from sim_core.ingestion.csv_loader import sort_trades_chronologically
from sim_core.models import ENGINE_VERSION, ResampledPath, Trade, VerificationReport

CashFlowType = Literal["deposit", "withdrawal"]
AccountEventType = Literal["deposit", "trade_entry", "trade_exit", "withdrawal"]


@dataclass(frozen=True)
class LiveAccountConfig:
    starting_equity: float
    scenario_id: str = "live_account"
    master_seed: int | None = None
    path_index: int | None = None
    currency: str = "USD"
    operational_ruin_threshold: float = 0.0
    operational_ruin_comparison: str = "<="
    operational_ruin_policy: str = "classify_and_continue"
    drawdown_thresholds: tuple[float, ...] = (0.2, 0.5, 0.8)
    short_horizon_annualization_days: int = 30

    def __post_init__(self) -> None:
        if self.starting_equity <= 0:
            raise ValueError("starting_equity must be positive")
        if not self.scenario_id:
            raise ValueError("scenario_id is required")
        if self.currency != "USD":
            raise ValueError("Version 2 milestone supports USD accounts only")
        if self.operational_ruin_threshold < 0:
            raise ValueError("operational_ruin_threshold cannot be negative")
        if self.operational_ruin_comparison != "<=":
            raise ValueError("only <= operational ruin comparison is supported")
        if self.operational_ruin_policy not in {
            "classify_and_continue",
            "stop_trading_after_ruin",
        }:
            raise ValueError(
                "operational_ruin_policy must be classify_and_continue or stop_trading_after_ruin"
            )
        if any(threshold <= 0 or threshold >= 1 for threshold in self.drawdown_thresholds):
            raise ValueError("drawdown thresholds must be between 0 and 1")
        if self.short_horizon_annualization_days < 0:
            raise ValueError("short_horizon_annualization_days cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["drawdown_thresholds"] = list(self.drawdown_thresholds)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LiveAccountConfig":
        data = dict(data)
        if "drawdown_thresholds" in data:
            data["drawdown_thresholds"] = tuple(data["drawdown_thresholds"])
        if "halt_trading_after_operational_ruin" in data and "operational_ruin_policy" not in data:
            halt = bool(data.pop("halt_trading_after_operational_ruin"))
            data["operational_ruin_policy"] = (
                "stop_trading_after_ruin" if halt else "classify_and_continue"
            )
        return cls(**data)


@dataclass(frozen=True)
class CashFlow:
    timestamp: pd.Timestamp
    amount: float
    type: CashFlowType
    label: str | None = None
    recurrence_source: str | None = None

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ValueError("cash-flow amount must be positive")
        if self.type not in {"deposit", "withdrawal"}:
            raise ValueError("cash-flow type must be deposit or withdrawal")
        timestamp = _to_utc(self.timestamp)
        object.__setattr__(self, "timestamp", timestamp)

    @property
    def signed_amount(self) -> float:
        return self.amount if self.type == "deposit" else -self.amount

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "amount": self.amount,
            "type": self.type,
            "label": self.label,
            "recurrence_source": self.recurrence_source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CashFlow":
        return cls(timestamp=pd.Timestamp(data["timestamp"]), **{k: v for k, v in data.items() if k != "timestamp"})


@dataclass(frozen=True)
class CashFlowPolicy:
    cash_flows: tuple[CashFlow, ...] = ()

    def __init__(self, cash_flows: list[CashFlow] | tuple[CashFlow, ...] | None = None) -> None:
        object.__setattr__(
            self,
            "cash_flows",
            tuple(sorted(cash_flows or (), key=lambda item: (item.timestamp, item.type, item.label or ""))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"cash_flows": [cash_flow.to_dict() for cash_flow in self.cash_flows]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CashFlowPolicy":
        return cls([CashFlow.from_dict(item) for item in data.get("cash_flows", [])])


@dataclass(frozen=True)
class FixedContractSizing:
    contracts: int
    policy_type: str = "fixed_contract"

    def __post_init__(self) -> None:
        if self.contracts < 0:
            raise ValueError("contracts cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return {"policy_type": self.policy_type, "contracts": self.contracts}


@dataclass(frozen=True)
class FixedDollarRiskSizing:
    risk_dollars: float
    risk_proxy_dollars: float | None = None
    reinvestment_rate: float = 0.0
    contract_cap: int | None = None
    minimum_reserve: float = 0.0
    scale_up_buffer: int = 0
    scale_down_buffer: int = 0
    policy_type: str = "fixed_dollar_risk"

    def __post_init__(self) -> None:
        _validate_risk_policy(
            self.risk_dollars,
            self.reinvestment_rate,
            self.contract_cap,
            self.minimum_reserve,
            self.scale_up_buffer,
            self.scale_down_buffer,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PercentEquitySizing:
    risk_fraction: float
    risk_proxy_dollars: float | None = None
    reinvestment_rate: float = 1.0
    contract_cap: int | None = None
    minimum_reserve: float = 0.0
    scale_up_buffer: int = 0
    scale_down_buffer: int = 0
    policy_type: str = "percent_equity"

    def __post_init__(self) -> None:
        if self.risk_fraction <= 0:
            raise ValueError("risk_fraction must be positive")
        _validate_risk_policy(
            self.risk_fraction,
            self.reinvestment_rate,
            self.contract_cap,
            self.minimum_reserve,
            self.scale_up_buffer,
            self.scale_down_buffer,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


SizingPolicy = FixedContractSizing | FixedDollarRiskSizing | PercentEquitySizing


@dataclass(frozen=True)
class StrategyAllocation:
    strategy_id: str
    sizing_policy: SizingPolicy

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ValueError("strategy_id is required")

    def to_dict(self) -> dict[str, Any]:
        return {"strategy_id": self.strategy_id, "sizing_policy": self.sizing_policy.to_dict()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StrategyAllocation":
        return cls(strategy_id=data["strategy_id"], sizing_policy=sizing_policy_from_dict(data["sizing_policy"]))


@dataclass(frozen=True)
class SizingDecision:
    timestamp: pd.Timestamp
    strategy_id: str
    trade_id: str
    policy_type: str
    contracts: int
    previous_contracts: int
    allocated_risk_dollars: float
    per_contract_trade_risk: float | None
    equity_basis: float
    forced_reduction: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SizingDecision":
        data = dict(data)
        data["timestamp"] = pd.Timestamp(data["timestamp"])
        return cls(**data)


@dataclass(frozen=True)
class AccountState:
    timestamp: pd.Timestamp
    equity: float
    trading_pnl: float
    deposits: float
    withdrawals: float
    peak_equity: float
    current_contracts: dict[str, int]

    @property
    def net_external_contributions(self) -> float:
        return self.deposits - self.withdrawals

    @property
    def current_drawdown(self) -> float:
        return max(0.0, self.peak_equity - self.equity)


@dataclass(frozen=True)
class AccountEvent:
    timestamp: pd.Timestamp
    event_type: AccountEventType
    priority: int
    equity: float
    amount: float = 0.0
    trading_pnl: float = 0.0
    deposits: float = 0.0
    withdrawals: float = 0.0
    strategy_id: str | None = None
    trade_id: str | None = None
    source_row_id: str | None = None
    contracts: int = 0
    label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AccountEvent":
        data = dict(data)
        data["timestamp"] = pd.Timestamp(data["timestamp"])
        return cls(**data)


@dataclass(frozen=True)
class LiveAccountPathResult:
    config: LiveAccountConfig
    allocations: dict[str, StrategyAllocation]
    cash_flow_policy: CashFlowPolicy
    trades: list[Trade]
    events: list[AccountEvent]
    sizing_decisions: list[SizingDecision]
    monthly_reports: list[dict[str, Any]]
    summary: dict[str, Any]
    provenance: dict[str, Any] = field(default_factory=dict)

    @property
    def terminal_equity(self) -> float:
        return float(self.summary["ending_equity"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "config": self.config.to_dict(),
            "allocations": {
                strategy_id: allocation.to_dict() for strategy_id, allocation in self.allocations.items()
            },
            "cash_flow_policy": self.cash_flow_policy.to_dict(),
            "trades": [
                {
                    "trade_id": trade.trade_id,
                    "source_row_id": trade.source_row_id,
                    "strategy_id": trade.strategy_id,
                    "instrument": trade.instrument,
                    "contract_symbol": trade.contract_symbol,
                    "entry_time": trade.entry_time.isoformat(),
                    "exit_time": trade.exit_time.isoformat(),
                    "pnl_dollars": trade.pnl_dollars,
                    "pnl_points": trade.pnl_points,
                    "stop_points": trade.stop_points,
                    "dollars_per_point": trade.dollars_per_point,
                    "commission_round_turn": trade.commission_round_turn,
                    "metadata": trade.metadata,
                }
                for trade in self.trades
            ],
            "events": [event.to_dict() for event in self.events],
            "sizing_decisions": [decision.to_dict() for decision in self.sizing_decisions],
            "monthly_reports": self.monthly_reports,
            "summary": self.summary,
            "provenance": self.provenance,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LiveAccountPathResult":
        data = dict(data)
        trades = [
            Trade(
                trade_id=item["trade_id"],
                source_row_id=item["source_row_id"],
                strategy_id=item["strategy_id"],
                instrument=item["instrument"],
                contract_symbol=item.get("contract_symbol"),
                entry_time=pd.Timestamp(item["entry_time"]),
                exit_time=pd.Timestamp(item["exit_time"]),
                pnl_dollars=float(item["pnl_dollars"]),
                pnl_points=item.get("pnl_points"),
                stop_points=item.get("stop_points"),
                dollars_per_point=item.get("dollars_per_point"),
                commission_round_turn=float(item.get("commission_round_turn", 0.0)),
                metadata=item.get("metadata", {}),
            )
            for item in data["trades"]
        ]
        return cls(
            config=LiveAccountConfig.from_dict(data["config"]),
            allocations={
                strategy_id: StrategyAllocation.from_dict(allocation)
                for strategy_id, allocation in data["allocations"].items()
            },
            cash_flow_policy=CashFlowPolicy.from_dict(data["cash_flow_policy"]),
            trades=trades,
            events=[AccountEvent.from_dict(item) for item in data["events"]],
            sizing_decisions=[SizingDecision.from_dict(item) for item in data["sizing_decisions"]],
            monthly_reports=list(data["monthly_reports"]),
            summary=dict(data["summary"]),
            provenance=dict(data.get("provenance", {})),
        )

    @classmethod
    def from_json(cls, payload: str) -> "LiveAccountPathResult":
        return cls.from_dict(json.loads(payload))


def sizing_policy_from_dict(data: dict[str, Any]) -> SizingPolicy:
    policy_type = data.get("policy_type")
    if policy_type == "fixed_contract":
        return FixedContractSizing(contracts=int(data["contracts"]))
    if policy_type == "fixed_dollar_risk":
        return FixedDollarRiskSizing(
            risk_dollars=float(data["risk_dollars"]),
            risk_proxy_dollars=_optional_float(data.get("risk_proxy_dollars")),
            reinvestment_rate=float(data.get("reinvestment_rate", 0.0)),
            contract_cap=data.get("contract_cap"),
            minimum_reserve=float(data.get("minimum_reserve", 0.0)),
            scale_up_buffer=int(data.get("scale_up_buffer", 0)),
            scale_down_buffer=int(data.get("scale_down_buffer", 0)),
        )
    if policy_type == "percent_equity":
        return PercentEquitySizing(
            risk_fraction=float(data["risk_fraction"]),
            risk_proxy_dollars=_optional_float(data.get("risk_proxy_dollars")),
            reinvestment_rate=float(data.get("reinvestment_rate", 1.0)),
            contract_cap=data.get("contract_cap"),
            minimum_reserve=float(data.get("minimum_reserve", 0.0)),
            scale_up_buffer=int(data.get("scale_up_buffer", 0)),
            scale_down_buffer=int(data.get("scale_down_buffer", 0)),
        )
    raise ValueError(f"unknown sizing policy_type: {policy_type!r}")


def run_live_account_path(
    trades: list[Trade] | ResampledPath,
    *,
    config: LiveAccountConfig,
    allocations: dict[str, StrategyAllocation],
    cash_flow_policy: CashFlowPolicy | None = None,
) -> LiveAccountPathResult:
    """Consume V1 ordered trade events with live-account cash-flow and sizing rules."""

    trade_list = trades.trades if isinstance(trades, ResampledPath) else list(trades)
    cash_flow_policy = cash_flow_policy or CashFlowPolicy()
    _validate_allocations(trade_list, allocations)

    state = _MutableAccountState(config)
    pending_contracts: dict[str, int] = {}
    previous_contracts: dict[str, int] = {}
    events: list[AccountEvent] = []
    sizing_decisions: list[SizingDecision] = []
    ordered_events = _ordered_raw_events(trade_list, cash_flow_policy.cash_flows)

    for raw_event in ordered_events:
        timestamp = raw_event["timestamp"]
        state.advance(timestamp)
        event_type = raw_event["event_type"]
        if (
            config.operational_ruin_policy == "stop_trading_after_ruin"
            and state.operational_ruin_hit
            and event_type in {"trade_entry", "trade_exit"}
        ):
            continue
        if event_type == "deposit":
            cash_flow = raw_event["cash_flow"]
            state.apply_deposit(cash_flow.amount)
            _append_account_event(
                events,
                state,
                config,
                state.event(
                    event_type="deposit",
                    priority=_event_priority("deposit"),
                    amount=cash_flow.amount,
                    label=cash_flow.label,
                ),
            )
        elif event_type == "withdrawal":
            cash_flow = raw_event["cash_flow"]
            state.apply_withdrawal(cash_flow.amount)
            _append_account_event(
                events,
                state,
                config,
                state.event(
                    event_type="withdrawal",
                    priority=_event_priority("withdrawal"),
                    amount=-cash_flow.amount,
                    label=cash_flow.label,
                ),
            )
        elif event_type == "trade_entry":
            trade = raw_event["trade"]
            allocation = allocations[trade.strategy_id]
            previous = previous_contracts.get(trade.strategy_id, 0)
            decision = decide_contracts(
                trade,
                config=config,
                state=state.snapshot(),
                allocation=allocation,
                previous_contracts=previous,
            )
            pending_contracts[trade.trade_id] = decision.contracts
            previous_contracts[trade.strategy_id] = decision.contracts
            state.current_contracts[trade.strategy_id] = decision.contracts
            if decision.forced_reduction:
                state.forced_size_reductions += 1
            sizing_decisions.append(decision)
            _append_account_event(
                events,
                state,
                config,
                state.event(
                    event_type="trade_entry",
                    priority=_event_priority("trade_entry"),
                    strategy_id=trade.strategy_id,
                    trade_id=trade.trade_id,
                    source_row_id=trade.source_row_id,
                    contracts=decision.contracts,
                ),
            )
        elif event_type == "trade_exit":
            trade = raw_event["trade"]
            contracts = pending_contracts.pop(trade.trade_id, None)
            if contracts is None:
                allocation = allocations[trade.strategy_id]
                decision = decide_contracts(
                    trade,
                    config=config,
                    state=state.snapshot(),
                    allocation=allocation,
                    previous_contracts=previous_contracts.get(trade.strategy_id, 0),
                )
                contracts = decision.contracts
                sizing_decisions.append(decision)
            gross = trade.pnl_dollars * contracts
            commission = trade.commission_round_turn * contracts
            net = gross - commission
            state.apply_trading_pnl(net)
            _append_account_event(
                events,
                state,
                config,
                state.event(
                    event_type="trade_exit",
                    priority=_event_priority("trade_exit"),
                    amount=net,
                    strategy_id=trade.strategy_id,
                    trade_id=trade.trade_id,
                    source_row_id=trade.source_row_id,
                    contracts=contracts,
                ),
            )
        else:
            raise ValueError(f"unknown event type: {event_type!r}")

    summary = _build_summary(config, state, events, sizing_decisions)
    monthly = _build_monthly_reports(config, events)
    result = LiveAccountPathResult(
        config=config,
        allocations=allocations,
        cash_flow_policy=cash_flow_policy,
        trades=sort_trades_chronologically(trade_list),
        events=events,
        sizing_decisions=sizing_decisions,
        monthly_reports=monthly,
        summary=summary,
    )
    return LiveAccountPathResult(
        config=result.config,
        allocations=result.allocations,
        cash_flow_policy=result.cash_flow_policy,
        trades=result.trades,
        events=result.events,
        sizing_decisions=result.sizing_decisions,
        monthly_reports=result.monthly_reports,
        summary=result.summary,
        provenance=build_live_account_provenance(result, trade_list, config, cash_flow_policy, allocations),
    )


def decide_contracts(
    trade: Trade,
    *,
    config: LiveAccountConfig,
    state: AccountState,
    allocation: StrategyAllocation,
    previous_contracts: int = 0,
) -> SizingDecision:
    policy = allocation.sizing_policy
    if isinstance(policy, FixedContractSizing):
        return SizingDecision(
            timestamp=trade.entry_time,
            strategy_id=trade.strategy_id,
            trade_id=trade.trade_id,
            policy_type=policy.policy_type,
            contracts=policy.contracts,
            previous_contracts=previous_contracts,
            allocated_risk_dollars=0.0,
            per_contract_trade_risk=None,
            equity_basis=state.equity,
            forced_reduction=policy.contracts < previous_contracts,
        )

    per_contract_risk = _per_contract_risk(trade, policy)
    equity_basis = _sizing_equity_basis(config, state, policy)
    if isinstance(policy, FixedDollarRiskSizing):
        allocated_risk = policy.risk_dollars * equity_basis / config.starting_equity
    else:
        allocated_risk = equity_basis * policy.risk_fraction
    contracts = math.floor(allocated_risk / per_contract_risk) if per_contract_risk > 0 else 0
    contracts = _apply_contract_controls(contracts, previous_contracts, policy)
    return SizingDecision(
        timestamp=trade.entry_time,
        strategy_id=trade.strategy_id,
        trade_id=trade.trade_id,
        policy_type=policy.policy_type,
        contracts=contracts,
        previous_contracts=previous_contracts,
        allocated_risk_dollars=allocated_risk,
        per_contract_trade_risk=per_contract_risk,
        equity_basis=equity_basis,
        forced_reduction=contracts < previous_contracts,
    )


def summarize_live_account_paths(results: list[LiveAccountPathResult]) -> dict[str, float]:
    if not results:
        return {
            "path_count": 0.0,
            "probability_forced_size_reduction": 0.0,
            "probability_operational_ruin": 0.0,
            "probability_zero_equity_ruin": 0.0,
        }
    path_count = len(results)
    summary: dict[str, float] = {
        "path_count": float(path_count),
        "probability_forced_size_reduction": sum(
            1 for result in results if result.summary["forced_size_reductions"] > 0
        )
        / path_count,
        "probability_operational_ruin": sum(
            1 for result in results if result.summary["operational_ruin_hit"]
        )
        / path_count,
        "probability_zero_equity_ruin": sum(
            1 for result in results if result.summary["zero_equity_ruin"]
        )
        / path_count,
    }
    thresholds = sorted({threshold for result in results for threshold in result.config.drawdown_thresholds})
    for threshold in thresholds:
        key = f"probability_drawdown_{int(threshold * 100)}pct"
        summary[key] = sum(
            1
            for result in results
            if result.summary["trading_drawdown_thresholds_reached"].get(str(threshold))
        ) / path_count
    return summary


def build_live_account_provenance(
    result: LiveAccountPathResult,
    trade_events: list[Trade],
    account_config: LiveAccountConfig,
    cash_flow_policy: CashFlowPolicy,
    sizing_policies: dict[str, StrategyAllocation],
) -> dict[str, Any]:
    hashes = {
        "trade_input_hash": live_account_trade_input_hash(trade_events),
        "live_account_config_hash": _hash_payload(account_config.to_dict()),
        "cash_flow_schedule_hash": _hash_payload(cash_flow_policy.to_dict()),
        "sizing_policy_hash": _hash_payload(_allocations_to_dict(sizing_policies)),
        "contract_specification_hash": contract_specification_hash(trade_events),
        "ruin_configuration_hash": _hash_payload(_ruin_config_to_dict(account_config)),
        "reinvestment_configuration_hash": _hash_payload(
            _reinvestment_config_to_dict(sizing_policies)
        ),
    }
    return {
        **hashes,
        "engine_version": ENGINE_VERSION,
        "scenario_id": account_config.scenario_id,
        "master_seed": account_config.master_seed,
        "path_index": account_config.path_index,
        "result_hash": live_account_result_hash(result),
        "hash_algorithm": "sha256",
        "deterministic_hash_fields": sorted(hashes),
    }


def verify_live_account_result_provenance(
    result: LiveAccountPathResult,
    trade_events: list[Trade],
    account_config: LiveAccountConfig,
    cash_flows: CashFlowPolicy | list[CashFlow] | tuple[CashFlow, ...],
    sizing_policies: dict[str, StrategyAllocation],
) -> VerificationReport:
    cash_flow_policy = cash_flows if isinstance(cash_flows, CashFlowPolicy) else CashFlowPolicy(cash_flows)
    provenance = result.provenance or {}
    checks = {
        "trade_input_hash": provenance.get("trade_input_hash")
        == live_account_trade_input_hash(trade_events),
        "live_account_config_hash": provenance.get("live_account_config_hash")
        == _hash_payload(account_config.to_dict()),
        "cash_flow_schedule_hash": provenance.get("cash_flow_schedule_hash")
        == _hash_payload(cash_flow_policy.to_dict()),
        "sizing_policy_hash": provenance.get("sizing_policy_hash")
        == _hash_payload(_allocations_to_dict(sizing_policies)),
        "contract_specification_hash": provenance.get("contract_specification_hash")
        == contract_specification_hash(trade_events),
        "ruin_configuration_hash": provenance.get("ruin_configuration_hash")
        == _hash_payload(_ruin_config_to_dict(account_config)),
        "reinvestment_configuration_hash": provenance.get("reinvestment_configuration_hash")
        == _hash_payload(_reinvestment_config_to_dict(sizing_policies)),
        "engine_version": provenance.get("engine_version") == ENGINE_VERSION,
        "scenario_id": provenance.get("scenario_id") == account_config.scenario_id,
        "master_seed": provenance.get("master_seed") == account_config.master_seed,
        "path_index": provenance.get("path_index") == account_config.path_index,
        "result_hash": provenance.get("result_hash") == live_account_result_hash(result),
    }
    return VerificationReport(
        ok=all(checks.values()),
        checks=checks,
        details={
            "declared": provenance,
            "computed": {
                "trade_input_hash": live_account_trade_input_hash(trade_events),
                "live_account_config_hash": _hash_payload(account_config.to_dict()),
                "cash_flow_schedule_hash": _hash_payload(cash_flow_policy.to_dict()),
                "sizing_policy_hash": _hash_payload(_allocations_to_dict(sizing_policies)),
                "contract_specification_hash": contract_specification_hash(trade_events),
                "ruin_configuration_hash": _hash_payload(_ruin_config_to_dict(account_config)),
                "reinvestment_configuration_hash": _hash_payload(
                    _reinvestment_config_to_dict(sizing_policies)
                ),
                "engine_version": ENGINE_VERSION,
                "scenario_id": account_config.scenario_id,
                "master_seed": account_config.master_seed,
                "path_index": account_config.path_index,
                "result_hash": live_account_result_hash(result),
            },
        },
    )


def live_account_trade_input_hash(trades: list[Trade]) -> str:
    return _hash_payload(
        [
            {
                "trade_id": trade.trade_id,
                "source_row_id": trade.source_row_id,
                "strategy_id": trade.strategy_id,
                "instrument": trade.instrument,
                "contract_symbol": trade.contract_symbol,
                "entry_time": trade.entry_time.isoformat(),
                "exit_time": trade.exit_time.isoformat(),
                "pnl_dollars": trade.pnl_dollars,
                "pnl_points": trade.pnl_points,
                "stop_points": trade.stop_points,
                "dollars_per_point": trade.dollars_per_point,
                "commission_round_turn": trade.commission_round_turn,
            }
            for trade in sorted(trades, key=lambda item: item.source_row_id)
        ]
    )


def contract_specification_hash(trades: list[Trade]) -> str:
    specs = sorted(
        {
            (
                trade.strategy_id,
                trade.instrument,
                trade.contract_symbol or "",
                float(trade.dollars_per_point or 0.0),
            )
            for trade in trades
        }
    )
    return _hash_payload(specs)


def live_account_result_hash(result: LiveAccountPathResult) -> str:
    payload = result.to_dict()
    payload.pop("provenance", None)
    return _hash_payload(payload)


def _ordered_raw_events(trades: list[Trade], cash_flows: tuple[CashFlow, ...]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for cash_flow in cash_flows:
        events.append(
            {
                "timestamp": cash_flow.timestamp,
                "event_type": cash_flow.type,
                "priority": _event_priority(cash_flow.type),
                "cash_flow": cash_flow,
                "strategy_id": "",
                "source_row_id": cash_flow.label or "",
            }
        )
    for trade in trades:
        events.append(
            {
                "timestamp": trade.entry_time,
                "event_type": "trade_entry",
                "priority": _event_priority("trade_entry"),
                "trade": trade,
                "strategy_id": trade.strategy_id,
                "source_row_id": trade.source_row_id,
            }
        )
        events.append(
            {
                "timestamp": trade.exit_time,
                "event_type": "trade_exit",
                "priority": _event_priority("trade_exit"),
                "trade": trade,
                "strategy_id": trade.strategy_id,
                "source_row_id": trade.source_row_id,
            }
        )
    return sorted(
        events,
        key=lambda event: (
            event["timestamp"],
            event["priority"],
            event["strategy_id"],
            event["source_row_id"],
        ),
    )


def _event_priority(event_type: str) -> int:
    priorities = {
        "deposit": 0,
        "trade_exit": 1,
        "withdrawal": 2,
        "trade_entry": 3,
    }
    return priorities[event_type]


def _per_contract_risk(trade: Trade, policy: FixedDollarRiskSizing | PercentEquitySizing) -> float:
    metadata = trade.metadata or {}
    for key in ("stop_loss_dollars", "stop_dollars", "explicit_stop_loss_dollars"):
        value = metadata.get(key)
        if value is not None:
            risk = abs(float(value))
            if risk > 0:
                return risk
    if trade.stop_points is not None and trade.dollars_per_point is not None:
        risk = abs(float(trade.stop_points) * float(trade.dollars_per_point))
        if risk > 0:
            return risk
    if policy.risk_proxy_dollars is not None and policy.risk_proxy_dollars > 0:
        return float(policy.risk_proxy_dollars)
    raise ValueError(
        f"trade {trade.trade_id} for strategy {trade.strategy_id} has no declared per-contract risk"
    )


def _sizing_equity_basis(
    config: LiveAccountConfig,
    state: AccountState,
    policy: FixedDollarRiskSizing | PercentEquitySizing,
) -> float:
    external_capital = config.starting_equity + state.net_external_contributions
    if state.trading_pnl >= 0:
        profit_component = state.trading_pnl * policy.reinvestment_rate
    else:
        profit_component = state.trading_pnl
    return max(0.0, external_capital + profit_component - policy.minimum_reserve)


def _apply_contract_controls(
    raw_contracts: int,
    previous_contracts: int,
    policy: FixedDollarRiskSizing | PercentEquitySizing,
) -> int:
    contracts = max(0, raw_contracts)
    if policy.contract_cap is not None:
        contracts = min(contracts, policy.contract_cap)
    if previous_contracts <= 0:
        return contracts
    if contracts > previous_contracts and contracts < previous_contracts + policy.scale_up_buffer:
        return previous_contracts
    if contracts < previous_contracts and contracts > previous_contracts - policy.scale_down_buffer:
        return previous_contracts
    return contracts


def _build_summary(
    config: LiveAccountConfig,
    state: "_MutableAccountState",
    events: list[AccountEvent],
    sizing_decisions: list[SizingDecision],
) -> dict[str, Any]:
    account_drawdown = _drawdown_metrics(
        config,
        events,
        value_at_event=lambda event: event.equity,
    )
    trading_drawdown = _drawdown_metrics(
        config,
        events,
        value_at_event=lambda event: config.starting_equity + event.trading_pnl,
    )
    returns = _return_metrics(config, events)
    min_contract = min((decision.contracts for decision in sizing_decisions), default=0)
    return {
        "starting_equity": config.starting_equity,
        "ending_equity": state.equity,
        "trading_pnl": state.trading_pnl,
        "deposits": state.deposits,
        "withdrawals": state.withdrawals,
        "net_external_contributions": state.deposits - state.withdrawals,
        "simple_return_on_total_contributions": returns["simple_return_on_total_contributions"],
        "period_twr": returns["period_twr"],
        "annualized_twr": returns["annualized_twr"],
        "period_money_weighted_return": returns["period_money_weighted_return"],
        "annualized_xirr": returns["annualized_xirr"],
        "annualized_xirr_status": returns["annualized_xirr_status"],
        "annualized_xirr_unavailable_reason": returns["annualized_xirr_unavailable_reason"],
        "measurement_start": returns["measurement_start"],
        "measurement_end": returns["measurement_end"],
        "measurement_period_days": returns["measurement_period_days"],
        "annualization_applied": returns["annualization_applied"],
        "annualization_warning": returns["annualization_warning"],
        # Backward-compatible aliases are kept but are not the canonical names.
        "time_weighted_return": returns["period_twr"],
        "money_weighted_return": returns["annualized_xirr"],
        "trading_return_before_cash_flows": state.trading_pnl / config.starting_equity,
        "account_peak_equity": account_drawdown["peak_equity"],
        "account_current_drawdown_dollars": account_drawdown["current_drawdown_dollars"],
        "account_max_drawdown_dollars": account_drawdown["max_drawdown_dollars"],
        "account_max_drawdown_percent": account_drawdown["max_drawdown_percent"],
        "account_drawdown_duration": account_drawdown["drawdown_duration_seconds"],
        "account_drawdown_duration_seconds": account_drawdown["drawdown_duration_seconds"],
        "account_recovery_duration_seconds": account_drawdown["recovery_duration_seconds"],
        "flow_neutral_peak_equity": trading_drawdown["peak_equity"],
        "current_trading_drawdown_dollars": trading_drawdown["current_drawdown_dollars"],
        "trading_current_drawdown_dollars": trading_drawdown["current_drawdown_dollars"],
        "trading_max_drawdown_dollars": trading_drawdown["max_drawdown_dollars"],
        "trading_max_drawdown_percent": trading_drawdown["max_drawdown_percent"],
        "trading_drawdown_duration": trading_drawdown["drawdown_duration_seconds"],
        "trading_recovery_duration": trading_drawdown["recovery_duration_seconds"],
        "max_trading_drawdown_dollars": trading_drawdown["max_drawdown_dollars"],
        "max_trading_drawdown_percent": trading_drawdown["max_drawdown_percent"],
        "trading_drawdown_duration_seconds": trading_drawdown["drawdown_duration_seconds"],
        "trading_recovery_duration_seconds": trading_drawdown["recovery_duration_seconds"],
        "trading_drawdown_thresholds_reached": trading_drawdown["thresholds_reached"],
        # Default risk aliases now point to flow-neutral trading drawdown.
        "peak_equity": trading_drawdown["peak_equity"],
        "current_drawdown": trading_drawdown["current_drawdown_dollars"],
        "max_drawdown": trading_drawdown["max_drawdown_dollars"],
        "max_drawdown_pct": trading_drawdown["max_drawdown_percent"],
        "drawdown_duration_seconds": trading_drawdown["drawdown_duration_seconds"],
        "recovery_duration_seconds": trading_drawdown["recovery_duration_seconds"],
        "drawdown_thresholds_reached": trading_drawdown["thresholds_reached"],
        "forced_size_reductions": state.forced_size_reductions,
        "time_spent_below_prior_peak_size_seconds": trading_drawdown["drawdown_duration_seconds"],
        "minimum_contract_size_reached": min_contract,
        "operational_ruin": state.operational_ruin_hit,
        "operational_ruin_hit": state.operational_ruin_hit,
        "operational_ruin_first_timestamp": (
            state.operational_ruin_first_timestamp.isoformat()
            if state.operational_ruin_first_timestamp is not None
            else None
        ),
        "operational_ruin_min_equity": state.operational_ruin_min_equity,
        "operational_ruin_event_index": state.operational_ruin_event_index,
        "operational_ruin_trade_id": state.operational_ruin_trade_id,
        "operational_ruin_trigger_event_id": state.operational_ruin_trigger_event_id,
        "operational_ruin_threshold": config.operational_ruin_threshold,
        "operational_ruin_comparison": config.operational_ruin_comparison,
        "operational_ruin_policy": config.operational_ruin_policy,
        "halt_trading_after_operational_ruin": (
            config.operational_ruin_policy == "stop_trading_after_ruin"
        ),
        "zero_equity_ruin": state.equity <= 0,
    }


def _build_monthly_reports(config: LiveAccountConfig, events: list[AccountEvent]) -> list[dict[str, Any]]:
    if not events:
        return []
    rows: list[dict[str, Any]] = []
    months = sorted({_timestamp_to_month(event.timestamp) for event in events})
    for month in months:
        month_events = [event for event in events if _timestamp_to_month(event.timestamp) == month]
        ending_equity = month_events[-1].equity
        trading_pnl = sum(event.amount for event in month_events if event.event_type == "trade_exit")
        deposits = sum(event.amount for event in month_events if event.event_type == "deposit")
        withdrawals = -sum(event.amount for event in month_events if event.event_type == "withdrawal")
        rows.append(
            {
                "month": str(month),
                "trading_pnl": trading_pnl,
                "deposits": deposits,
                "withdrawals": withdrawals,
                "net_external_contributions": deposits - withdrawals,
                "ending_equity": ending_equity,
                "simple_return_on_total_contributions": (
                    (ending_equity - config.starting_equity - deposits + withdrawals)
                    / max(config.starting_equity + deposits, 1e-12)
                ),
            }
        )
    return rows


def _return_metrics(config: LiveAccountConfig, events: list[AccountEvent]) -> dict[str, float]:
    starting_equity = config.starting_equity
    measurement_start = events[0].timestamp if events else None
    measurement_end = events[-1].timestamp if events else None
    period_days = (
        _year_fraction(measurement_start, measurement_end) * 365.25
        if measurement_start is not None and measurement_end is not None
        else 0.0
    )
    ending_equity = events[-1].equity if events else starting_equity
    deposits = sum(event.amount for event in events if event.event_type == "deposit")
    trading_pnl = sum(event.amount for event in events if event.event_type == "trade_exit")
    simple = trading_pnl / max(starting_equity + deposits, 1e-12)
    twr = _time_weighted_return(starting_equity, events)
    period_mwr = _period_money_weighted_return(starting_equity, ending_equity, events, twr)
    xirr = _annualized_xirr(starting_equity, ending_equity, events)
    minimum_days = config.short_horizon_annualization_days
    annualized_twr = _annualize_period_return(twr, period_days) if period_days >= minimum_days else None
    annualization_warning = None
    if period_days < minimum_days and events:
        annualization_warning = (
            f"annualized_xirr is based on a short {period_days:.2f}-day measurement period "
            f"below the configured {minimum_days}-day warning threshold and can be extreme"
        )
    return {
        "simple_return_on_total_contributions": simple,
        "period_twr": twr,
        "annualized_twr": annualized_twr,
        "period_money_weighted_return": period_mwr,
        "annualized_xirr": xirr["value"],
        "annualized_xirr_status": xirr["status"],
        "annualized_xirr_unavailable_reason": xirr["reason"],
        "measurement_start": measurement_start.isoformat() if measurement_start is not None else None,
        "measurement_end": measurement_end.isoformat() if measurement_end is not None else None,
        "measurement_period_days": period_days,
        "annualization_applied": xirr["status"] == "ok",
        "annualization_warning": annualization_warning,
    }


def _time_weighted_return(starting_equity: float, events: list[AccountEvent]) -> float:
    factor = 1.0
    subperiod_start_value = starting_equity
    last_equity = starting_equity
    for event in events:
        if event.event_type in {"deposit", "withdrawal"}:
            if subperiod_start_value != 0:
                factor *= 1 + ((last_equity - subperiod_start_value) / subperiod_start_value)
            subperiod_start_value = event.equity
        last_equity = event.equity
    if subperiod_start_value != 0:
        factor *= 1 + ((last_equity - subperiod_start_value) / subperiod_start_value)
    return factor - 1


def _period_money_weighted_return(
    starting_equity: float,
    ending_equity: float,
    events: list[AccountEvent],
    period_twr: float,
) -> float | None:
    external_flows = [event for event in events if event.event_type in {"deposit", "withdrawal"}]
    if not external_flows:
        return period_twr
    invested = starting_equity + sum(event.amount for event in events if event.event_type == "deposit")
    withdrawals = -sum(event.amount for event in events if event.event_type == "withdrawal")
    return (ending_equity - starting_equity - invested + starting_equity + withdrawals) / max(
        invested,
        1e-12,
    )


def _annualized_xirr(
    starting_equity: float,
    ending_equity: float,
    events: list[AccountEvent],
) -> dict[str, Any]:
    if not events:
        return {"status": "unavailable", "value": None, "reason": "no_events"}
    start = events[0].timestamp
    cash_flows: list[tuple[float, float]] = [(0.0, -starting_equity)]
    for event in events:
        if event.event_type == "deposit":
            cash_flows.append((_year_fraction(start, event.timestamp), -event.amount))
        elif event.event_type == "withdrawal":
            cash_flows.append((_year_fraction(start, event.timestamp), -event.amount))
    cash_flows.append((_year_fraction(start, events[-1].timestamp), ending_equity))
    has_positive = any(amount > 0 for _, amount in cash_flows)
    has_negative = any(amount < 0 for _, amount in cash_flows)
    if not has_positive or not has_negative:
        return {"status": "unavailable", "value": None, "reason": "missing_cash_flow_sign"}
    signs = [1 if amount > 0 else -1 for _, amount in cash_flows if abs(amount) > 1e-12]
    sign_changes = sum(1 for left, right in zip(signs, signs[1:]) if left != right)
    if sign_changes > 1:
        return {"status": "unavailable", "value": None, "reason": "non_unique_xirr_sign_pattern"}

    def npv(rate: float) -> float:
        return sum(amount / ((1 + rate) ** years) for years, amount in cash_flows)

    low = -0.999999
    high = 10.0
    while npv(high) > 0 and high < 1_000_000:
        high *= 2
    if npv(high) > 0:
        return {"status": "unavailable", "value": None, "reason": "xirr_bracket_not_found"}
    for _ in range(120):
        mid = (low + high) / 2
        if npv(mid) > 0:
            low = mid
        else:
            high = mid
    return {"status": "ok", "value": (low + high) / 2, "reason": None}


def _drawdown_metrics(
    config: LiveAccountConfig,
    events: list[AccountEvent],
    *,
    value_at_event,
) -> dict[str, Any]:
    if not events:
        return {
            "peak_equity": config.starting_equity,
            "current_drawdown_dollars": 0.0,
            "max_drawdown_dollars": 0.0,
            "max_drawdown_percent": 0.0,
            "drawdown_duration_seconds": 0.0,
            "recovery_duration_seconds": 0.0,
            "thresholds_reached": {str(threshold): False for threshold in config.drawdown_thresholds},
        }
    peak = config.starting_equity
    peak_time = events[0].timestamp
    drawdown_start: pd.Timestamp | None = None
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    max_drawdown_duration = 0.0
    max_recovery_duration = 0.0
    thresholds = {str(threshold): False for threshold in config.drawdown_thresholds}
    for event in events:
        value = value_at_event(event)
        if value >= peak:
            if drawdown_start is not None:
                max_recovery_duration = max(
                    max_recovery_duration,
                    (event.timestamp - drawdown_start).total_seconds(),
                )
            peak = value
            peak_time = event.timestamp
            drawdown_start = None
            continue
        drawdown = peak - value
        pct = drawdown / peak if peak else 0.0
        if drawdown_start is None:
            drawdown_start = peak_time
        duration = (event.timestamp - drawdown_start).total_seconds()
        max_drawdown = max(max_drawdown, drawdown)
        max_drawdown_pct = max(max_drawdown_pct, pct)
        max_drawdown_duration = max(max_drawdown_duration, duration)
        for threshold in config.drawdown_thresholds:
            if pct >= threshold:
                thresholds[str(threshold)] = True
    current_drawdown = max(0.0, peak - value_at_event(events[-1]))
    return {
        "peak_equity": peak,
        "current_drawdown_dollars": current_drawdown,
        "max_drawdown_dollars": max_drawdown,
        "max_drawdown_percent": max_drawdown_pct,
        "drawdown_duration_seconds": max_drawdown_duration,
        "recovery_duration_seconds": max_recovery_duration,
        "thresholds_reached": thresholds,
    }


class _MutableAccountState:
    def __init__(self, config: LiveAccountConfig) -> None:
        self.timestamp = pd.Timestamp("1970-01-01T00:00:00Z")
        self.equity = config.starting_equity
        self.trading_pnl = 0.0
        self.deposits = 0.0
        self.withdrawals = 0.0
        self.peak_equity = config.starting_equity
        self.current_contracts: dict[str, int] = {}
        self.forced_size_reductions = 0
        initial_ruin = self.equity <= config.operational_ruin_threshold
        self.operational_ruin_hit = initial_ruin
        self.operational_ruin_first_timestamp: pd.Timestamp | None = None
        self.operational_ruin_min_equity = config.starting_equity
        self.operational_ruin_event_index: int | None = None
        self.operational_ruin_trade_id: str | None = None
        self.operational_ruin_trigger_event_id: str | None = (
            "initial_equity" if initial_ruin else None
        )

    def advance(self, timestamp: pd.Timestamp) -> None:
        self.timestamp = timestamp

    def apply_deposit(self, amount: float) -> None:
        self.equity += amount
        self.deposits += amount
        self.peak_equity = max(self.peak_equity, self.equity)

    def apply_withdrawal(self, amount: float) -> None:
        self.equity -= amount
        self.withdrawals += amount

    def apply_trading_pnl(self, amount: float) -> None:
        self.equity += amount
        self.trading_pnl += amount
        self.peak_equity = max(self.peak_equity, self.equity)

    def observe_operational_ruin(
        self,
        config: LiveAccountConfig,
        event: AccountEvent,
        event_index: int,
    ) -> None:
        self.operational_ruin_min_equity = min(self.operational_ruin_min_equity, self.equity)
        if self.equity <= config.operational_ruin_threshold and not self.operational_ruin_hit:
            self.operational_ruin_hit = True
            self.operational_ruin_first_timestamp = event.timestamp
            self.operational_ruin_event_index = event_index
            self.operational_ruin_trade_id = event.trade_id
            self.operational_ruin_trigger_event_id = (
                event.trade_id
                or event.source_row_id
                or event.label
                or f"{event.event_type}:{event_index}"
            )

    def snapshot(self) -> AccountState:
        return AccountState(
            timestamp=self.timestamp,
            equity=self.equity,
            trading_pnl=self.trading_pnl,
            deposits=self.deposits,
            withdrawals=self.withdrawals,
            peak_equity=self.peak_equity,
            current_contracts=dict(self.current_contracts),
        )

    def event(
        self,
        *,
        event_type: AccountEventType,
        priority: int,
        amount: float = 0.0,
        strategy_id: str | None = None,
        trade_id: str | None = None,
        source_row_id: str | None = None,
        contracts: int = 0,
        label: str | None = None,
    ) -> AccountEvent:
        return AccountEvent(
            timestamp=self.timestamp,
            event_type=event_type,
            priority=priority,
            equity=self.equity,
            amount=amount,
            trading_pnl=self.trading_pnl,
            deposits=self.deposits,
            withdrawals=self.withdrawals,
            strategy_id=strategy_id,
            trade_id=trade_id,
            source_row_id=source_row_id,
            contracts=contracts,
            label=label,
        )


def _validate_allocations(trades: list[Trade], allocations: dict[str, StrategyAllocation]) -> None:
    missing = sorted({trade.strategy_id for trade in trades if trade.strategy_id not in allocations})
    if missing:
        raise ValueError(f"missing sizing allocation(s): {', '.join(missing)}")


def _append_account_event(
    events: list[AccountEvent],
    state: _MutableAccountState,
    config: LiveAccountConfig,
    event: AccountEvent,
) -> None:
    event_index = len(events)
    state.observe_operational_ruin(config, event, event_index)
    events.append(event)


def _validate_risk_policy(
    value: float,
    reinvestment_rate: float,
    contract_cap: int | None,
    minimum_reserve: float,
    scale_up_buffer: int,
    scale_down_buffer: int,
) -> None:
    if value <= 0:
        raise ValueError("risk value must be positive")
    if not 0 <= reinvestment_rate <= 1:
        raise ValueError("reinvestment_rate must be between 0 and 1")
    if contract_cap is not None and contract_cap < 0:
        raise ValueError("contract_cap cannot be negative")
    if minimum_reserve < 0:
        raise ValueError("minimum_reserve cannot be negative")
    if scale_up_buffer < 0 or scale_down_buffer < 0:
        raise ValueError("scale buffers cannot be negative")


def _to_utc(value: object) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None or timestamp.tz is None:
        raise ValueError("live-account timestamps must be timezone-aware")
    return timestamp.tz_convert("UTC")


def _timestamp_to_month(value: object) -> pd.Period:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None and timestamp.tz is not None:
        timestamp = timestamp.tz_convert("UTC")
    return pd.Period(f"{timestamp.year}-{timestamp.month:02d}", "M")


def _year_fraction(start: pd.Timestamp, end: pd.Timestamp) -> float:
    return max(0.0, (end - start).total_seconds() / (365.25 * 24 * 60 * 60))


def _annualize_period_return(period_return: float | None, period_days: float) -> float | None:
    if period_return is None or period_days <= 0:
        return None
    if period_return <= -1:
        return -1.0
    return (1 + period_return) ** (365.25 / period_days) - 1


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _allocations_to_dict(allocations: dict[str, StrategyAllocation]) -> dict[str, Any]:
    return {
        strategy_id: allocation.to_dict()
        for strategy_id, allocation in sorted(allocations.items(), key=lambda item: item[0])
    }


def _ruin_config_to_dict(config: LiveAccountConfig) -> dict[str, Any]:
    return {
        "operational_ruin_threshold": config.operational_ruin_threshold,
        "operational_ruin_comparison": config.operational_ruin_comparison,
        "operational_ruin_policy": config.operational_ruin_policy,
    }


def _reinvestment_config_to_dict(allocations: dict[str, StrategyAllocation]) -> dict[str, Any]:
    configs: dict[str, Any] = {}
    for strategy_id, allocation in sorted(allocations.items(), key=lambda item: item[0]):
        policy = allocation.sizing_policy
        configs[strategy_id] = {
            "policy_type": policy.policy_type,
            "reinvestment_rate": getattr(policy, "reinvestment_rate", None),
            "contract_cap": getattr(policy, "contract_cap", None),
            "minimum_reserve": getattr(policy, "minimum_reserve", None),
            "scale_up_buffer": getattr(policy, "scale_up_buffer", None),
            "scale_down_buffer": getattr(policy, "scale_down_buffer", None),
        }
    return configs


def _hash_payload(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(payload: Any) -> str:
    return json.dumps(_canonicalize(payload), sort_keys=True, separators=(",", ":"))


def _canonicalize(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, pd.Period):
        return str(value)
    if isinstance(value, tuple):
        return [_canonicalize(item) for item in value]
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _canonicalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    return value
