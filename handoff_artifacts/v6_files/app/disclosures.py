"""V6 — mandatory model-risk disclosures for the UI.

Single source of truth for the caveats the charter requires to be shown WITH the
numbers, not buried. The Streamlit view must render the relevant list on every
tab that shows the corresponding output. Keeping them here (engine-free) means the
UI can never quietly drop a disclosure and the set is unit-testable.
"""
from __future__ import annotations

# Keyed by UI section. Each string is shown verbatim next to that section's output.
DISCLOSURES: dict[str, list[str]] = {
    "ensemble": [
        "The simulation assumes the historical edge persists — there is no "
        "out-of-sample or strategy-degradation control. This is a strong assumption.",
        "Uploaded logs are presumably surviving strategies; the sim cannot correct "
        "for strategies that died and were never uploaded (survivorship).",
        "Seasonal percentile fans backed by few historical instances (thin support) "
        "or dominated by one year are unreliable; support counts are reported.",
        "Percentiles are computed across the ensemble, not by differencing medians.",
    ],
    "drawdown": [
        "Drawdown is booked on REALIZED P&L at trade exit only. Intratrade excursion "
        "(MAE) is not modeled, so realized-only drawdown UNDERSTATES true "
        "peak-to-trough risk.",
        "Risk-of-ruin and drawdown are defined once and held constant across every "
        "report.",
    ],
    "live_account": [
        "Deposits are not profit and withdrawals are not losses; time-weighted vs "
        "money-weighted returns are reported separately.",
        "Compounded/reinvested equity curves must be read as distributions with "
        "caveats, never as point forecasts.",
    ],
    "margin_exposure": [
        "Margin is an entry-time initial-margin cap only; no intraday "
        "maintenance-call or forced liquidation is modeled.",
        "Exposure is measured over each trade's scheduled entry→exit interval "
        "(realized-only); there is no intratrade mark-to-market.",
    ],
    "prop_firm": [
        "A notional prop-account balance is NOT personal wealth. Only realized net "
        "cash (payouts × split − evaluation/activation/reset fees) counts.",
        "Breach checks (trailing drawdown, daily loss) are realized-only "
        "(end-of-trade), so reported breach probability is a LOWER bound and "
        "survival an UPPER bound.",
        "Payout timing is modeled greedily; copied accounts share one identical "
        "trade path (fully correlated, not diversification).",
    ],
    "optimizer": [
        "The decision output is the Pareto frontier under explicit constraints, not "
        "a single 'best' configuration. The scalarized score is a display aid only.",
        "Optimizing any lone metric (e.g. median terminal equity) is disabled by "
        "default because it exploits model traps (capped equity, realized-only "
        "drawdown, greedy prop payouts).",
    ],
}


def for_section(section: str) -> list[str]:
    """Return the mandatory disclosures for a UI section (raises on unknown key)."""

    if section not in DISCLOSURES:
        raise KeyError(f"no disclosures declared for UI section {section!r}")
    return list(DISCLOSURES[section])


def all_sections() -> list[str]:
    return list(DISCLOSURES.keys())
