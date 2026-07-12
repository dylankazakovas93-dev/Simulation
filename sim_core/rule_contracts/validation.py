from __future__ import annotations

from dataclasses import fields, is_dataclass

from .models import ContractStatus, LifecycleStage, RuleContract, RuleExactness, Sourced


def _sourced_values(value):
    if isinstance(value, Sourced):
        yield value
    elif is_dataclass(value):
        for item in fields(value):
            yield from _sourced_values(getattr(value, item.name))
    elif isinstance(value, tuple):
        for item in value:
            yield from _sourced_values(item)


def validate_contract(contract: RuleContract) -> None:
    if not contract.id or not contract.identity.firm or contract.identity.account_size <= 0:
        raise ValueError("contract identity is incomplete")
    if contract.status is ContractStatus.ENABLED:
        required = (contract.economics, contract.drawdown, contract.daily_loss, contract.position_limits, contract.consistency, contract.payouts, contract.inactivity, contract.compatibility, contract.transition)
        if any(item is None for item in required):
            raise ValueError(f"enabled contract {contract.id} has a missing typed rule group")
        sourced = tuple(_sourced_values(contract))
        if not sourced or any(not item.source.document or not item.source.page for item in sourced):
            raise ValueError(f"enabled contract {contract.id} has missing field provenance")
        if any(item.exactness is RuleExactness.SOURCE_GAP for item in sourced):
            raise ValueError(f"enabled contract {contract.id} contains a SOURCE_GAP field")
        if contract.identity.stage is LifecycleStage.EVALUATION and contract.transition.evaluation_target is None:
            raise ValueError(f"evaluation contract {contract.id} requires a target")
        if contract.drawdown.amount.value <= 0 or not 0 < contract.economics.profit_split.value <= 1:
            raise ValueError(f"enabled contract {contract.id} has impossible economics")
        caps = [cap.value for cap in contract.payouts.sequential_caps]
        if caps and caps != sorted(caps):
            raise ValueError(f"contract {contract.id} has non-monotonic payout caps")
    elif not contract.disabled_reason:
        raise ValueError(f"non-enabled contract {contract.id} requires a reason")


def validate_contracts(contracts: tuple[RuleContract, ...] | list[RuleContract]) -> None:
    ids = [contract.id for contract in contracts]
    if len(ids) != len(set(ids)):
        raise ValueError("rule contract ids must be unique")
    for contract in contracts:
        validate_contract(contract)
