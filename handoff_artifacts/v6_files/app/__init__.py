"""V6 — UI layer. The engine lives in ``sim_core``; this package only presents it.

``app.controller`` is pure Python (no Streamlit) and holds the only bridge to the
engine. ``app.streamlit_app`` is a thin view. ``app.disclosures`` is the single
source of the mandatory model-risk caveats shown alongside every output.
"""
