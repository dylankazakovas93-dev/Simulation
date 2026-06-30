from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from sim_core.ingestion.csv_loader import (
    load_canonical_margin_csv,
    normalize_canonical_margin_frame,
)
from sim_core.instruments import DEFAULT_INSTRUMENT_REGISTRY
from sim_core.models import InstrumentSpec, TradeValidationError


FIXTURE = Path("sample_data/nq_es_margin_sim_master_2025_2026.csv")


def test_canonical_margin_fixture_maps_source_columns_and_preserves_metadata():
    trades = load_canonical_margin_csv(FIXTURE)

    assert len(trades) == 4
    assert trades[0].strategy_id == "nq_open"
    assert trades[0].instrument == "NQ"
    assert trades[0].contract_symbol == "MNQ"
    assert trades[0].dollars_per_point == 2.0
    assert trades[0].pnl_dollars == 20.0
    assert trades[0].source_row_id == "nq_es_margin_sim_master_2025_2026.csv:2"
    assert trades[0].metadata["mult"] == 3.0
    assert trades[0].metadata["window"] == "09:30-10:00"


def test_canonical_mult_is_metadata_not_position_sizing():
    normalized = normalize_canonical_margin_frame(pd.read_csv(FIXTURE))

    assert "mult" not in normalized.columns
    assert normalized.iloc[0]["metadata"]["mult"] == 3.0


def test_registry_explicitly_maps_underlying_to_micro_contract():
    assert DEFAULT_INSTRUMENT_REGISTRY["NQ"].contract_symbol == "MNQ"
    assert DEFAULT_INSTRUMENT_REGISTRY["ES"].contract_symbol == "MES"


def test_mixed_currency_registry_is_rejected():
    registry = {
        "NQ": InstrumentSpec(
            underlying="NQ",
            contract_symbol="MNQ",
            dollars_per_point=2.0,
            currency="USD",
        )
    }
    with pytest.raises(ValueError):
        registry["ES"] = InstrumentSpec(
            underlying="ES",
            contract_symbol="MES",
            dollars_per_point=5.0,
            currency="EUR",
        )


def test_canonical_dpp_must_match_explicit_registry():
    frame = pd.read_csv(FIXTURE)
    frame.loc[0, "dpp"] = 20.0

    with pytest.raises(TradeValidationError):
        normalize_canonical_margin_frame(frame)
