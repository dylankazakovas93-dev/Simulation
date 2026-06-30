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


def build_specs_from_registry(
    strategy_underlyings: dict[str, str],
    registry: dict[str, InstrumentSpec] | None = None,
) -> dict[str, InstrumentSpec]:
    """Explicit convenience tooling: build per-strategy contract specifications.

    The caller must name each strategy *and* its underlying explicitly. Nothing
    is inferred from the ledger itself. This exists so a user can opt in to the
    default registry for known instruments (ADR-011); it is never used as a
    silent loader fallback.
    """

    registry = registry or DEFAULT_INSTRUMENT_REGISTRY
    specs: dict[str, InstrumentSpec] = {}
    for strategy, underlying in strategy_underlyings.items():
        if underlying not in registry:
            raise KeyError(f"no registry entry for underlying {underlying}")
        specs[strategy] = registry[underlying]
    return specs
