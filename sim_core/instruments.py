from __future__ import annotations

from sim_core.models import InstrumentSpec


DEFAULT_INSTRUMENT_REGISTRY: dict[str, InstrumentSpec] = {
    "NQ": InstrumentSpec(
        underlying="NQ",
        contract_symbol="MNQ",
        dollars_per_point=2.0,
        currency="USD",
    ),
    "ES": InstrumentSpec(
        underlying="ES",
        contract_symbol="MES",
        dollars_per_point=5.0,
        currency="USD",
    ),
}


def get_instrument_spec(
    underlying: str,
    registry: dict[str, InstrumentSpec] | None = None,
) -> InstrumentSpec:
    registry = registry or DEFAULT_INSTRUMENT_REGISTRY
    try:
        return registry[underlying]
    except KeyError as exc:
        raise KeyError(f"no instrument registry entry for {underlying}") from exc
