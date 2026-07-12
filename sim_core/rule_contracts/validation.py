from __future__ import annotations

from .models import ContractStatus, RuleContract


REQUIRED_MECHANICS = {"account_size", "max_loss", "drawdown_mode", "profit_split"}


def validate_contract(contract: RuleContract) -> None:
    if not contract.id or not contract.firm or not contract.account_name:
        raise ValueError("rule contract requires id, firm, and account name")
    if contract.status is ContractStatus.ENABLED:
        missing = sorted(REQUIRED_MECHANICS - set(contract.mechanics))
        if missing:
            raise ValueError(f"enabled contract {contract.id} missing mechanics: {', '.join(missing)}")
        if not contract.sources:
            raise ValueError(f"enabled contract {contract.id} requires source provenance")
    if contract.status is ContractStatus.SOURCE_GAP and contract.stage != "evaluation":
        # A funded SOURCE_GAP may be represented as an intentionally disabled
        # payout module, but it must not be published as a selectable profile.
        return


def validate_contracts(contracts: list[RuleContract]) -> None:
    identifiers = [contract.id for contract in contracts]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("rule contract ids must be unique")
    for contract in contracts:
        validate_contract(contract)
