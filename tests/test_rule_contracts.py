from sim_core.prop_rules import default_prop_rule_profiles
from sim_core.rule_contracts import ContractStatus, contract_for_profile, enabled_contracts, load_contracts


def test_enabled_contracts_are_validated_and_source_backed():
    contracts = load_contracts()

    assert {contract.id for contract in enabled_contracts()} >= {
        "alpha_advanced_50k",
        "fundednext_rapid_50k",
        "tpt_pro_50k",
    }
    assert all(contract.sources for contract in enabled_contracts())
    assert all(contract.status is ContractStatus.ENABLED for contract in enabled_contracts())


def test_disabled_tpt_test_cannot_become_a_selectable_profile():
    contract = contract_for_profile("TakeProfitTrader - Test 50K")

    assert contract is not None
    assert contract.status is ContractStatus.SOURCE_GAP
    assert contract.profile_key not in default_prop_rule_profiles()


def test_contract_and_existing_profile_cannot_drift_on_core_mechanics():
    profiles = default_prop_rule_profiles()

    for contract in enabled_contracts():
        profile = profiles.get(contract.profile_key)
        if profile is None:
            continue
        for field in ("account_size", "max_loss", "drawdown_mode", "profit_split"):
            assert getattr(profile, field) == contract.mechanics[field]
        assert f"rule contract {contract.id}" in profile.source
