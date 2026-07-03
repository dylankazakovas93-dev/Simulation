"""Firm-specific prop presets — Apex, Take Profit Trader, FundedNext, Alpha Futures.

These live OUTSIDE the engine on purpose. ADR-019 keeps `sim_core` firm-neutral (no
firm's numbers are hardcoded in the engine); this module is opinionated *config*
that maps each firm's published rules onto the neutral `PropFirmRules`.

EVERY preset is dated and cited, and each carries a `notes` list stating exactly
what is modeled vs approximated. Prop firms revamp rules and run rotating
discounts, so treat the pricing as "typical common-promo" and RE-VERIFY before
buying. Retrieved 2026-07-02.

Modeling caveats that apply to ALL presets (see KNOWN_LIMITATIONS / ADR-020/023):
  * Breach checks are realized-only (end-of-trade P&L); blow rates are a LOWER bound.
  * The engine has ONE `min_trading_days` gating both "pass" and "payout". Where a
    firm has 0 eval-min-days but N payout-qualifying-days (Apex), we set the payout
    number and note the eval pass time is therefore slightly conservative.
  * "Qualifying/benchmark day" minimum-daily-profit rules are approximated by a plain
    min-trading-days count.
  * Ramped payout caps (first payout smaller than later ones) are modeled with a
    single representative `payout_cap`; see each firm's note.
"""
from __future__ import annotations

from dataclasses import dataclass

from sim_core.prop_firm import PropFirmRules

RETRIEVED = "2026-07-02"


@dataclass(frozen=True)
class PropFirmPreset:
    key: str
    firm: str
    plan: str
    account_size: int
    rules: PropFirmRules
    eval_cost: float          # eval / challenge price (typical common discount)
    activation_cost: float    # one-time activation on passing (0 if none)
    recurring_monthly: bool   # True = eval price recurs monthly until you pass
    payout_cadence: str
    profit_split_note: str
    sources: tuple[str, ...]
    notes: tuple[str, ...]

    @property
    def cost_to_funded(self) -> float:
        """Cash out of pocket to reach a funded account (first month if monthly)."""
        return self.eval_cost + self.activation_cost


# --------------------------------------------------------------------------- APEX
# EOD plan, Apex 4.0 (Mar 2026): one-time eval fee, 50% consistency, EOD trailing
# that locks at the starting balance; EOD accounts carry a Daily Loss Limit.
_APEX_SRC = (
    "https://apextraderfunding.com/help-center/eod-trailing-drawdown-accounts/eod-drawdown-explained/",
    "https://apextraderfunding.com/help-center/eod-trailing-drawdown-accounts/eod-payouts/",
    "https://apextraderfunding.com/help-center/additional-helpful-items/50-consistency-requirement/",
    "https://apextraderfunding.com/coupon-code/",
)
_APEX_NOTES = (
    "VERIFIED from the Apex help center 2026-07 (EOD Evaluations, EOD Payouts, 50% Consistency).",
    "EOD trailing drawdown locks at the starting balance (modeled trailing_lock_at=account_size).",
    "Eval: NO minimum trading days and NO consistency (may pass in one day). Payout: 5 qualifying "
    "days each meeting a minimum daily profit ($250 on 50k, $300 on 100k), plus 50% consistency.",
    "Safety net = drawdown + $100 (50k: $52,100 / 100k: $103,100); only profit above it is withdrawable "
    "(modeled as payout_buffer). Minimum payout $500. Max 6 payouts, then the PA closes.",
    "Payout caps RAMP by payout number (50k: 1500/1500/2000/2500/2500/3000; 100k: 2000/2500/2500/3000/"
    "4000/4000). The tested Python engine takes a single representative cap; the browser Lab models the "
    "full ramp + qualifying-day + max-6 logic.",
    "Split is 100% (Apex pays 100% of approved payouts).",
    "Pricing is still ESTIMATED (90%-off promo) — verify at purchase.",
)

APEX_50K = PropFirmPreset(
    key="apex_eod_50k", firm="Apex", plan="EOD 50K", account_size=50_000,
    rules=PropFirmRules(
        account_size=50_000, profit_target=3_000, trailing_drawdown=2_000,
        trailing_basis="end_of_day", trailing_lock_at=50_000, daily_loss_limit=1_000,
        min_trading_days=5, consistency_pct=0.5, profit_split=1.0,
        first_payout_threshold=2_100, payout_buffer=2_100, payout_cap=2_000,
        min_days_between_payouts=5, payout_mode="standard",
        evaluation_fee=35.0, activation_fee=0.0, label="Apex EOD 50K",
    ),
    eval_cost=35.0, activation_cost=0.0, recurring_monthly=False,
    payout_cadence="5 qualifying days (>=$250/day); ~weekly; caps ramp $1.5k->$3k; max 6",
    profit_split_note="100%",
    sources=_APEX_SRC, notes=_APEX_NOTES,
)

APEX_100K = PropFirmPreset(
    key="apex_eod_100k", firm="Apex", plan="EOD 100K", account_size=100_000,
    rules=PropFirmRules(
        account_size=100_000, profit_target=6_000, trailing_drawdown=3_000,
        trailing_basis="end_of_day", trailing_lock_at=100_000, daily_loss_limit=1_500,
        min_trading_days=5, consistency_pct=0.5, profit_split=1.0,
        first_payout_threshold=3_100, payout_buffer=3_100, payout_cap=2_500,
        min_days_between_payouts=5, payout_mode="standard",
        evaluation_fee=53.0, activation_fee=0.0, label="Apex EOD 100K",
    ),
    eval_cost=53.0, activation_cost=0.0, recurring_monthly=False,
    payout_cadence="5 qualifying days (>=$300/day); ~weekly; caps ramp $2k->$4k; max 6",
    profit_split_note="100%",
    sources=_APEX_SRC, notes=_APEX_NOTES,
)

# --------------------------------------------------------- TAKE PROFIT TRADER (TPT)
# 1-step Test → PRO → PRO+ (auto after consistency / $10k day). We model the PRO+
# funded economics: EOD drawdown, 90/10, day-one payouts, $2k buffer, no DLL.
_TPT_SRC = (
    "https://takeprofittrader.com/",
    "https://takeprofittraderhelp.zendesk.com/hc/en-us/articles/15172219527581-PRO-Account-Profit-Split-Withdrawal-Rules",
    "https://tradetanto.com/learn/take-profit-trader-rules-what-you-need-to-know",
)
_TPT_NOTES = (
    "Modeled as the PRO+ funded account (90/10, EOD drawdown, no daily loss limit).",
    "Trailing locks at the starting balance once the buffer is built (trailing_lock_at=account_size).",
    "Buffer $2,000 (50k): must reach $52,000 to withdraw → first_payout_threshold/payout_buffer = drawdown.",
    "Day-one payout eligibility → min_trading_days=1; frequent payouts → payout_mode='daily'. Min payout $250.",
    "PRICING IS AN ESTIMATE — verify: Test is a MONTHLY sub until you pass (no activation fee). "
    "50k≈$150/mo (~$90 promo), 100k≈$330/mo (~$200 promo).",
    "100k trailing drawdown assumed $3,000 (proportional) — verify.",
)

TPT_50K = PropFirmPreset(
    key="tpt_50k", firm="Take Profit Trader", plan="PRO+ 50K", account_size=50_000,
    rules=PropFirmRules(
        account_size=50_000, profit_target=3_000, trailing_drawdown=2_000,
        trailing_basis="end_of_day", trailing_lock_at=50_000, daily_loss_limit=None,
        min_trading_days=1, consistency_pct=None, profit_split=0.9,
        first_payout_threshold=2_000, payout_buffer=2_000, payout_cap=None,
        min_days_between_payouts=0, payout_mode="daily",
        evaluation_fee=90.0, activation_fee=0.0, label="TPT PRO+ 50K",
    ),
    eval_cost=90.0, activation_cost=0.0, recurring_monthly=True,
    payout_cadence="Day-one eligibility; frequent (modeled daily); $250 min",
    profit_split_note="PRO 80/20 → PRO+ 90/10 (auto-upgrade)",
    sources=_TPT_SRC, notes=_TPT_NOTES,
)

TPT_100K = PropFirmPreset(
    key="tpt_100k", firm="Take Profit Trader", plan="PRO+ 100K", account_size=100_000,
    rules=PropFirmRules(
        account_size=100_000, profit_target=6_000, trailing_drawdown=3_000,
        trailing_basis="end_of_day", trailing_lock_at=100_000, daily_loss_limit=None,
        min_trading_days=1, consistency_pct=None, profit_split=0.9,
        first_payout_threshold=3_000, payout_buffer=3_000, payout_cap=None,
        min_days_between_payouts=0, payout_mode="daily",
        evaluation_fee=200.0, activation_fee=0.0, label="TPT PRO+ 100K",
    ),
    eval_cost=200.0, activation_cost=0.0, recurring_monthly=True,
    payout_cadence="Day-one eligibility; frequent (modeled daily); $250 min",
    profit_split_note="PRO 80/20 → PRO+ 90/10 (auto-upgrade)",
    sources=_TPT_SRC, notes=_TPT_NOTES,
)

# ------------------------------------------------------------------ FUNDEDNEXT
# Futures Legacy challenge: EOD trailing MLL, 40% consistency (challenge only),
# 5-benchmark-day then 5-day payout cycles, one-time fee, no activation.
_FN_SRC = (
    "https://fundednext.com/futures-challenge-terms",
    "https://fundednext.com/how-it-works",
    "https://tradetanto.com/learn/fundednext-futures-rules-a-complete-breakdown",
)
_FN_NOTES = (
    "Modeled on the Legacy challenge (50k target $3,000 / 100k $5,000; MLL 50k $2,000 / 100k $3,000).",
    "MLL is EOD trailing and never moves down; modeled as end_of_day trailing locking at start.",
    "40% consistency applies to the challenge; the engine applies consistency to the payout gate "
    "(approximation).",
    "Payouts: first after 5 benchmark days ($200), then ~every 5 days; caps $1,500 (50k) / $2,500 (100k) "
    "until 5 payouts, then removed. min_days_between_payouts=5.",
    "Pricing: Legacy/Rapid one-time ~$135 (50k) / ~$225 (100k); no activation fee. Profit split assumed 90%.",
)

FUNDEDNEXT_50K = PropFirmPreset(
    key="fundednext_50k", firm="FundedNext", plan="Legacy 50K", account_size=50_000,
    rules=PropFirmRules(
        account_size=50_000, profit_target=3_000, trailing_drawdown=2_000,
        trailing_basis="end_of_day", trailing_lock_at=50_000, daily_loss_limit=None,
        min_trading_days=5, consistency_pct=0.4, profit_split=0.9,
        first_payout_threshold=1_000, payout_buffer=0.0, payout_cap=1_500,
        min_days_between_payouts=5, payout_mode="standard",
        evaluation_fee=135.0, activation_fee=0.0, label="FundedNext Legacy 50K",
    ),
    eval_cost=135.0, activation_cost=0.0, recurring_monthly=False,
    payout_cadence="5 benchmark days then ~every 5 days; caps until 5 payouts",
    profit_split_note="Up to 90% (assumed)",
    sources=_FN_SRC, notes=_FN_NOTES,
)

FUNDEDNEXT_100K = PropFirmPreset(
    key="fundednext_100k", firm="FundedNext", plan="Legacy 100K", account_size=100_000,
    rules=PropFirmRules(
        account_size=100_000, profit_target=5_000, trailing_drawdown=3_000,
        trailing_basis="end_of_day", trailing_lock_at=100_000, daily_loss_limit=None,
        min_trading_days=5, consistency_pct=0.4, profit_split=0.9,
        first_payout_threshold=1_000, payout_buffer=0.0, payout_cap=2_500,
        min_days_between_payouts=5, payout_mode="standard",
        evaluation_fee=225.0, activation_fee=0.0, label="FundedNext Legacy 100K",
    ),
    eval_cost=225.0, activation_cost=0.0, recurring_monthly=False,
    payout_cadence="5 benchmark days then ~every 5 days; caps until 5 payouts",
    profit_split_note="Up to 90% (assumed)",
    sources=_FN_SRC, notes=_FN_NOTES,
)

# --------------------------------------------------------------- ALPHA FUTURES
# Premium plan: one-step, EOD trailing MLL (starts 4% below, locks at start),
# NO consistency on Premium Qualified, 90/10, fast (48h) payouts up to 4x/month.
_ALPHA_SRC = (
    "https://alpha-futures.com/premium-offer",
    "https://help.alpha-futures.com/en/articles/9491999-maximum-loss-limit-mll",
    "https://help.alpha-futures.com/en/articles/9492051-payout-policy",
    "https://tradetanto.com/learn/alpha-futures-rules-every-plan-rule-and-limit",
)
_ALPHA_NOTES = (
    "Premium plan: EOD trailing MLL starts 4% below start (50k→$48,000) and locks at the starting "
    "balance once reached (trailing_lock_at=account_size).",
    "No consistency rule on Premium Qualified (consistency_pct=None).",
    "Payouts: after 5 winning days ($200+), up to 4×/month, processed ≤48h; $500 min. Modeled payout_mode='daily' "
    "with min_trading_days=5; the 4×/month cap is not separately enforced.",
    "Withdrawal caps 50k: $2,000/$2,250/$2,500/$3,000/$4,000; 100k: $2,500→$5,000. Single representative cap used.",
    "Pricing: monthly sub ~$159 (50k) / ~$239 (100k) list; 25–40% off codes → ~$95 / ~$179. Recurring until pass.",
    "100k profit target assumed $6,000 and MLL $4,000 (4%) — verify.",
)

ALPHA_50K = PropFirmPreset(
    key="alpha_50k", firm="Alpha Futures", plan="Premium 50K", account_size=50_000,
    rules=PropFirmRules(
        account_size=50_000, profit_target=3_000, trailing_drawdown=2_000,
        trailing_basis="end_of_day", trailing_lock_at=50_000, daily_loss_limit=None,
        min_trading_days=5, consistency_pct=None, profit_split=0.9,
        first_payout_threshold=500, payout_buffer=0.0, payout_cap=2_000,
        min_days_between_payouts=0, payout_mode="daily",
        evaluation_fee=95.0, activation_fee=0.0, label="Alpha Premium 50K",
    ),
    eval_cost=95.0, activation_cost=0.0, recurring_monthly=True,
    payout_cadence="After 5 winning days; up to 4×/month; ≤48h; $500 min",
    profit_split_note="90/10",
    sources=_ALPHA_SRC, notes=_ALPHA_NOTES,
)

ALPHA_100K = PropFirmPreset(
    key="alpha_100k", firm="Alpha Futures", plan="Premium 100K", account_size=100_000,
    rules=PropFirmRules(
        account_size=100_000, profit_target=6_000, trailing_drawdown=4_000,
        trailing_basis="end_of_day", trailing_lock_at=100_000, daily_loss_limit=None,
        min_trading_days=5, consistency_pct=None, profit_split=0.9,
        first_payout_threshold=500, payout_buffer=0.0, payout_cap=2_500,
        min_days_between_payouts=0, payout_mode="daily",
        evaluation_fee=179.0, activation_fee=0.0, label="Alpha Premium 100K",
    ),
    eval_cost=179.0, activation_cost=0.0, recurring_monthly=True,
    payout_cadence="After 5 winning days; up to 4×/month; ≤48h; $500 min",
    profit_split_note="90/10",
    sources=_ALPHA_SRC, notes=_ALPHA_NOTES,
)


ALL_PRESETS: list[PropFirmPreset] = [
    APEX_50K, APEX_100K,
    TPT_50K, TPT_100K,
    FUNDEDNEXT_50K, FUNDEDNEXT_100K,
    ALPHA_50K, ALPHA_100K,
]


def presets_for_size(account_size: int) -> list[PropFirmPreset]:
    return [p for p in ALL_PRESETS if p.account_size == account_size]


def preset_by_key(key: str) -> PropFirmPreset:
    for p in ALL_PRESETS:
        if p.key == key:
            return p
    raise KeyError(f"unknown preset {key!r}")
