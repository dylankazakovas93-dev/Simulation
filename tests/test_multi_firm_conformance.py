from collections import Counter

from sim_core.rule_contracts import ContractStatus, LifecycleStage, load_contracts


def test_required_program_stage_inventory_is_complete():
    contracts = load_contracts()
    assert len(contracts) == 53
    counts = Counter((c.identity.firm, c.identity.stage, c.status) for c in contracts)
    assert counts[("Alpha Futures", LifecycleStage.EVALUATION, ContractStatus.ENABLED)] == 9
    assert counts[("Alpha Futures", LifecycleStage.FUNDED, ContractStatus.ENABLED)] == 9
    assert counts[("FundedNext Futures", LifecycleStage.EVALUATION, ContractStatus.ENABLED)] == 10
    assert counts[("FundedNext Futures", LifecycleStage.FUNDED, ContractStatus.ENABLED)] == 10


def test_enabled_contract_fields_are_source_attributed():
    for contract in load_contracts():
        if contract.status is ContractStatus.ENABLED:
            assert contract.economics.profit_split.source.page
            assert contract.drawdown.amount.source.document
