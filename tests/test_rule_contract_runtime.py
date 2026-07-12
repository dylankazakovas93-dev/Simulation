from sim_core.prop_rules import default_prop_rule_profiles
from sim_core.rule_contracts import ContractStatus, LifecycleStage, load_contracts


def test_all_enabled_funded_contracts_build_runtime_profiles():
    profiles = default_prop_rule_profiles()
    enabled = [c for c in load_contracts() if c.status is ContractStatus.ENABLED and c.identity.stage is LifecycleStage.FUNDED]
    assert enabled
    assert all(c.profile_key in profiles for c in enabled)
    assert all(profiles[c.profile_key].source.endswith(c.id) for c in enabled)


def test_disabled_and_conditional_records_are_not_selectable():
    profiles = default_prop_rule_profiles()
    blocked = [c for c in load_contracts() if c.status is not ContractStatus.ENABLED]
    assert blocked
    assert all(c.profile_key not in profiles for c in blocked)
