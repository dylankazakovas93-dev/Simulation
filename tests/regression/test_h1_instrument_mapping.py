"""HIGH-1 / BLOCKER-5 — declared contract mapping; no silent micro fallback.

Decision (DECISIONS ADR-011): this ledger is micros (NQ->MNQ $2, ES->MES $5).
The file's `dpp` is authoritative and is cross-checked against an explicitly
declared per-strategy/instrument mapping. The engine must NOT silently infer the
contract, and a blank/missing `dpp` must FAIL rather than defaulting to micro.

Finding (HANDOFF Review 002): `normalize_canonical_margin_frame` does
`if dpp is None: dpp = spec.dollars_per_point`, so a blank `dpp` silently adopts
micro economics with no warning.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sim_core.ingestion.csv_loader import load_canonical_margin_csv, normalize_canonical_margin_frame
from sim_core.models import InstrumentSpec, TradeValidationError

CANONICAL = "sample_data/nq_es_margin_sim_master_2025_2026.csv"


def test_blank_dpp_fails_validation():
    """RED: a blank dpp silently defaults to micro today instead of raising (HIGH-1)."""
    frame = pd.read_csv(CANONICAL)
    frame.loc[0, "dpp"] = np.nan
    with pytest.raises(TradeValidationError):
        normalize_canonical_margin_frame(frame)


def test_declared_micro_mapping_passes():
    """GUARD: an explicit NQ->MNQ $2 / ES->MES $5 declaration loads and matches dpp."""
    registry = {
        "NQ": InstrumentSpec("NQ", "MNQ", 2.0, "USD"),
        "ES": InstrumentSpec("ES", "MES", 5.0, "USD"),
    }
    trades = load_canonical_margin_csv(CANONICAL, instrument_registry=registry)
    declared = {(t.instrument, t.contract_symbol, t.dollars_per_point) for t in trades}
    assert ("NQ", "MNQ", 2.0) in declared
    assert ("ES", "MES", 5.0) in declared


def test_dpp_disagreeing_with_declaration_is_rejected():
    """GUARD: a dpp that contradicts the declared mapping fails closed (no silent 10x)."""
    frame = pd.read_csv(CANONICAL)
    frame.loc[0, "dpp"] = 20.0  # full-size value under a declared-micro mapping
    with pytest.raises(TradeValidationError):
        normalize_canonical_margin_frame(frame)
