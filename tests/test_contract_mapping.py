"""HIGH-R3-1 / ADR-011 — explicit per-strategy contract mapping is required.

Underlying symbols (NQ/ES) must never silently imply a contract (MNQ/MES). The
canonical loader requires `contract_specs_by_strategy`; unknown strategies,
missing mappings, blank `dpp`, and `dpp` that contradicts the declaration all
fail validation. The default registry is convenience tooling only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sim_core.ingestion.csv_loader import (
    load_canonical_margin_csv,
    normalize_canonical_margin_frame,
)
from sim_core.instruments import DEFAULT_INSTRUMENT_REGISTRY, build_specs_from_registry
from sim_core.models import InstrumentSpec, TradeValidationError

CANONICAL = "sample_data/nq_es_margin_sim_master_2025_2026.csv"


def _micro_specs() -> dict[str, InstrumentSpec]:
    return {
        "nq_open": InstrumentSpec("NQ", "MNQ", 2.0, "USD"),
        "es_open": InstrumentSpec("ES", "MES", 5.0, "USD"),
    }


def test_case1_no_mapping_supplied_fails_with_clear_error():
    frame = pd.read_csv(CANONICAL)
    with pytest.raises(TradeValidationError) as exc:
        normalize_canonical_margin_frame(frame)
    assert "contract_specs_by_strategy" in str(exc.value)


def test_case2_one_strategy_mapping_missing_names_the_strategy():
    frame = pd.read_csv(CANONICAL)
    partial = {"nq_open": InstrumentSpec("NQ", "MNQ", 2.0, "USD")}  # es_open missing
    with pytest.raises(TradeValidationError) as exc:
        normalize_canonical_margin_frame(frame, contract_specs_by_strategy=partial)
    assert "es_open" in str(exc.value)


def test_case3_explicit_micro_mappings_load_successfully():
    trades = load_canonical_margin_csv(CANONICAL, contract_specs_by_strategy=_micro_specs())
    assert len(trades) == 4
    by_strategy = {(t.strategy_id, t.contract_symbol, t.dollars_per_point) for t in trades}
    assert ("nq_open", "MNQ", 2.0) in by_strategy
    assert ("es_open", "MES", 5.0) in by_strategy


def test_case4_full_size_mapping_against_micro_dpp_fails():
    full_size = {
        "nq_open": InstrumentSpec("NQ", "NQ", 20.0, "USD"),
        "es_open": InstrumentSpec("ES", "ES", 50.0, "USD"),
    }
    with pytest.raises(TradeValidationError) as exc:
        load_canonical_margin_csv(CANONICAL, contract_specs_by_strategy=full_size)
    assert "does not match declared" in str(exc.value)


def test_case5_blank_dpp_fails():
    frame = pd.read_csv(CANONICAL)
    frame.loc[0, "dpp"] = np.nan
    with pytest.raises(TradeValidationError) as exc:
        normalize_canonical_margin_frame(frame, contract_specs_by_strategy=_micro_specs())
    assert "dpp" in str(exc.value)


def test_case6_unknown_underlying_for_declared_strategy_fails():
    # Declared underlying does not match the ledger's row underlying for nq_open.
    wrong_underlying = {
        "nq_open": InstrumentSpec("CL", "MCL", 100.0, "USD"),
        "es_open": InstrumentSpec("ES", "MES", 5.0, "USD"),
    }
    with pytest.raises(TradeValidationError) as exc:
        normalize_canonical_margin_frame(
            pd.read_csv(CANONICAL), contract_specs_by_strategy=wrong_underlying
        )
    assert "does not match declared" in str(exc.value)


def test_build_specs_from_registry_is_explicit_and_rejects_unknown_underlying():
    specs = build_specs_from_registry(
        {"nq_open": "NQ", "es_open": "ES"}, DEFAULT_INSTRUMENT_REGISTRY
    )
    assert specs["nq_open"].contract_symbol == "MNQ"
    assert specs["es_open"].contract_symbol == "MES"
    with pytest.raises(KeyError):
        build_specs_from_registry({"x": "ZZ"}, DEFAULT_INSTRUMENT_REGISTRY)
