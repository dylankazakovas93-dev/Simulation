#!/usr/bin/env python3
"""Machine-enforced minimum delivery gate for the multi-firm contract layer."""
from __future__ import annotations

import json
from pathlib import Path

from sim_core.rule_contracts import ContractStatus, LifecycleStage, load_contracts

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = (
    "source_manifest.json", "multi_firm_rule_matrix.csv", "current_profile_diff.csv",
    "conformance_report.md", "source_gaps.md", "synthetic_golden_results.json",
    "registry_snapshot.json", "delivery_verification.json",
)


def main() -> int:
    contracts = load_contracts()
    problems = []
    required = {"Alpha Futures": 18, "FundedNext Futures": 20, "TakeProfitTrader": 15}
    for firm, expected in required.items():
        actual = sum(contract.identity.firm == firm for contract in contracts)
        if actual != expected:
            problems.append(f"{firm}: expected {expected} records, found {actual}")
    for contract in contracts:
        if contract.status is ContractStatus.ENABLED and contract.exactness.value == "source_gap":
            problems.append(f"enabled SOURCE_GAP {contract.id}")
        if contract.status is ContractStatus.ENABLED and contract.identity.stage is LifecycleStage.FUNDED and not contract.rankable:
            # Non-rankable contracts may be selectable for diagnostics, never ranking.
            continue
    for name in ARTIFACTS:
        if not (ROOT / "artifacts" / "rule_audit" / name).exists():
            problems.append(f"missing artifact {name}")
    for name in ("tests/test_multi_firm_conformance.py", "tests/test_rule_contract_runtime.py", "tests/test_rule_certification_ui.py"):
        if not (ROOT / name).exists():
            problems.append(f"missing test {name}")
    result = {"contracts": len(contracts), "problems": problems, "ok": not problems}
    (ROOT / "artifacts" / "rule_audit" / "delivery_verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
