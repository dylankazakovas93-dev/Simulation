# Streamlit Community Cloud Deploy

Use this repository and branch in Streamlit Community Cloud:

- Repository: `dylankazakovas93-dev/Simulation`
- Branch: `recovery/prop-lab-local-2026-07-08`
- Main file path: `streamlit_app.py`

The root `streamlit_app.py` delegates to `app/streamlit_app.py`. `requirements.txt`
contains the runtime dependencies Streamlit Cloud installs.

Local smoke command:

```bash
streamlit run streamlit_app.py
```

Forward ledger artifacts are committed under:

- `artifacts/forward_master_path/1rr/`
- `artifacts/forward_master_path/1_5rr/`

Use `forward_strategy_ledger.csv` for the clean two-month trade document.
Use `per_trade_account_ledger.csv` only for Prop Lab account lifecycle traces.
