from sim_core.rule_contracts import ContractStatus, load_contracts
from app.streamlit_app import rule_certification_rows


def test_only_exact_enabled_contracts_are_certification_rankable():
    assert all(contract.rankable == (contract.status is ContractStatus.ENABLED and contract.exactness.value == "exact") for contract in load_contracts())


def test_certification_rows_expose_ranking_eligibility():
    rows = rule_certification_rows()
    assert {"contract_id", "strict_ranking_eligible", "missing_required_evidence"} <= set(rows)
    assert not rows.loc[rows["status"] != "enabled", "strict_ranking_eligible"].any()
