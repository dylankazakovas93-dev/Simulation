from __future__ import annotations

from sim_core.prop_rules import PropRuleProfile

from .models import ContractStatus, LifecycleStage, RuleContract


def runtime_profile(contract: RuleContract) -> PropRuleProfile:
    """Lossless adapter for enabled funded contracts used by the generic engine."""
    if contract.status is not ContractStatus.ENABLED or contract.identity.stage is not LifecycleStage.FUNDED:
        raise ValueError(f"contract {contract.id} is not a selectable funded profile")
    if not all((contract.economics, contract.drawdown, contract.daily_loss, contract.position_limits, contract.consistency, contract.payouts)):
        raise ValueError(f"contract {contract.id} cannot be represented by the runtime profile")
    payouts = contract.payouts
    return PropRuleProfile(
        firm=contract.identity.firm,
        account_name=contract.identity.account_name,
        account_size=float(contract.identity.account_size),
        max_loss=float(contract.drawdown.amount.value),
        drawdown_mode=contract.drawdown.mode.value.value,
        # Missing source position limits make the contract non-rankable; this
        # operational ceiling is deliberately not a claimed firm rule.
        max_micro_contracts=(contract.position_limits.max_micro_contracts.value if contract.position_limits.max_micro_contracts else 1_000_000),
        profit_split=float(contract.economics.profit_split.value),
        min_payout=float(payouts.min_payout.value) if payouts.min_payout else 0.0,
        max_payout=float(payouts.max_payout.value) if payouts.max_payout else None,
        payout_cap_schedule=tuple(item.value for item in payouts.sequential_caps),
        payout_profit_fraction=float(payouts.payout_fraction.value) if payouts.payout_fraction else 1.0,
        withdrawal_buffer=float(payouts.buffer.value) if payouts.buffer else 0.0,
        min_winning_days=int(payouts.winning_days.value) if payouts.winning_days else 0,
        winning_day_threshold=float(payouts.winning_day_threshold.value) if payouts.winning_day_threshold else 0.0,
        consistency_pct=float(contract.consistency.percent.value) if contract.consistency.percent else None,
        daily_loss_limit=float(contract.daily_loss.amount.value) if contract.daily_loss.amount else None,
        daily_loss_hard=contract.daily_loss.consequence.value.value == "hard_failure",
        activation_fee=float(contract.economics.activation_fee.value) if contract.economics.activation_fee else 0.0,
        source=f"rule contract {contract.id}",
        notes=(f"exactness={contract.exactness.value}",),
    )


def runtime_profiles(contracts: tuple[RuleContract, ...]) -> dict[str, PropRuleProfile]:
    profiles = {}
    for contract in contracts:
        if contract.status is ContractStatus.ENABLED and contract.identity.stage is LifecycleStage.FUNDED:
            profile = runtime_profile(contract)
            profiles[profile.key] = profile
    return profiles
