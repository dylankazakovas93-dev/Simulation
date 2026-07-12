from sim_core.rule_contracts import ContractStatus, RuleExactness, load_contracts


def test_registry_is_typed_complete_and_unique():
    contracts = load_contracts()
    assert len(contracts) == 53
    assert len({contract.id for contract in contracts}) == len(contracts)
    assert all(not hasattr(contract, "mechanics") for contract in contracts)


def test_enabled_records_have_field_level_provenance_and_no_source_gap():
    enabled = [contract for contract in load_contracts() if contract.status is ContractStatus.ENABLED]
    assert enabled
    for contract in enabled:
        assert contract.economics.profit_split.source.document
        assert contract.drawdown.amount.source.page
        assert contract.exactness is not RuleExactness.SOURCE_GAP


def test_disabled_records_have_exact_reasons():
    disabled = [contract for contract in load_contracts() if contract.status is not ContractStatus.ENABLED]
    assert disabled
    assert all(contract.disabled_reason for contract in disabled)
