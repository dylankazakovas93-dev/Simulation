from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
from typing import Any

import yaml

from .models import ContractStatus, RuleContract, SourceReference
from .validation import validate_contracts


@lru_cache(maxsize=1)
def load_contracts() -> tuple[RuleContract, ...]:
    raw: dict[str, Any] = yaml.safe_load(
        files("sim_core.rule_contracts").joinpath("contracts.yaml").read_text()
    )
    contracts = tuple(
        RuleContract(
            id=item["id"],
            firm=item["firm"],
            program=item["program"],
            stage=item["stage"],
            account_name=item["account_name"],
            status=ContractStatus(item["status"]),
            mechanics=dict(item.get("mechanics", {})),
            sources=tuple(SourceReference(**source) for source in item.get("sources", [])),
            notes=tuple(item.get("notes", [])),
        )
        for item in raw["contracts"]
    )
    validate_contracts(list(contracts))
    return contracts


def enabled_contracts() -> tuple[RuleContract, ...]:
    return tuple(contract for contract in load_contracts() if contract.status is ContractStatus.ENABLED)


def contract_for_profile(profile_key: str) -> RuleContract | None:
    return next((contract for contract in load_contracts() if contract.profile_key == profile_key), None)
