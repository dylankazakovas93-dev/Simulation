# Simulation

Portfolio and prop-firm strategy simulation laboratory.

Version 1 is a validated simulation core. The repo now also includes a first
Streamlit control surface for prop-account convexity analysis. It supports:

- Loading one or more timestamped strategy CSV files.
- Validating and normalizing required trade fields.
- Preserving chronological trade ordering.
- Synchronized monthly/block resampling across strategies.
- Fixed-contract portfolio replay with commissions.
- Equity paths, drawdown, ruin, and monthly percentile reports.
- Deterministic random seeds.
- CSV export helpers for simulation outputs.
- Uploading 12-month point ledgers in Streamlit.
- Dropping overlapping open trades by user-defined strategy priority.
- Ranking prop-account profiles and micro-contract counts by payout, failure,
  and convexity metrics.

## Quick Start

```bash
python3 -m pytest
```

Run the Streamlit app locally:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[app]'
.venv/bin/python -m streamlit run app/streamlit_app.py
```

The app expects one or more CSV ledgers. Common column aliases are accepted:

- entry: `entry_time`, `entry_utc`, `entry_ts`, `entry`, `touched_at`
- exit: `exit_time`, `exit_utc`, `exit_ts`, `exit`
- PnL points: `pnl_points`, `pnl_pts`, `points`, `pnl`
- excursions: `mae_points`/`mae_pts`, `mfe_points`/`mfe_pts`

Raw point ledgers are treated as one micro contract by default, with the app
defaulting to MNQ at `$2/point/contract`.

Minimal Python example:

```python
from sim_core import (
    AccountConfig,
    FixedContractPortfolio,
    SameCalendarMonthBootstrap,
    load_trade_csvs,
    monthly_equity_percentiles,
    run_fixed_contract_simulation,
)

trades = load_trade_csvs(["sample_data/es_strategy.csv", "sample_data/nq_strategy.csv"])
path = SameCalendarMonthBootstrap(months=6, start_month="2025-01").sample(trades, seed=42)
result = run_fixed_contract_simulation(
    path,
    account=AccountConfig(initial_equity=100_000),
    portfolio=FixedContractPortfolio(strategy_contracts={"es_morning": 1, "nq_open": 1}),
)
print(result.terminal_equity)
print(monthly_equity_percentiles([result]))
```

## Version 1 CSV Schema

Required:

- `strategy_id`
- `instrument`
- `entry_time`
- `exit_time`
- `pnl_dollars`, or both `pnl_points` and `dollars_per_point`

Optional:

- `trade_id`
- `direction`
- `entry_price`
- `exit_price`
- `stop_points`
- `target_points`
- `mae_points`
- `mfe_points`
- `result_type` (`win`, `loss`, `breakeven`)
- `session`
- `commission_round_turn`

Timestamps are parsed by pandas with mixed-format support. Duplicate trades with the same
strategy, instrument, entry, exit, and PnL are rejected during ingestion.

## Repository Layout

```text
sim_core/
    ingestion/      CSV validation and normalization
    resampling/     Historical replay and synchronized bootstrap policies
    execution/      Fixed-contract event replay
    metrics/        Drawdown, ruin, and percentile reports
    prop_rules.py   Prop-account rules, overlap filtering, payout ranking
tests/              Pytest regression suite
configs/            Example scenario configuration
sample_data/        Small ledgers for manual smoke tests
reports/            Intended output location for generated reports
app/                Streamlit app entrypoint
```

## Deployment

The simplest deployment path is Streamlit Community Cloud or another service
that can run a Python app from GitHub. Point it at:

```text
app/streamlit_app.py
```

Install command:

```bash
pip install -e '.[app]'
```

For private/local hardware, run the local command above and keep the Streamlit
process open. The app is intentionally file-upload based, so ledgers do not need
to live in the GitHub repository.
