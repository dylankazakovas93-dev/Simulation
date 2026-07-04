from __future__ import annotations

from typing import Any

__all__ = [
    "IntegrationError",
    "build_integration_report",
    "discover_strategy_ids",
    "load_mapping",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from sim_core.integration import real_ledger

        return getattr(real_ledger, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
