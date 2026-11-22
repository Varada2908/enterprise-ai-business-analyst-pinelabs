"""Sidebar shared by all pages: data snapshot, AI settings and the safety note."""
from __future__ import annotations

import streamlit as st

from services import metrics
from services.backend import MODEL_OPTIONS, QueryError, api_key_configured, default_model

from .format import indian_int, short_date


def _init_state() -> None:
    st.session_state.setdefault("model", default_model() if default_model() in MODEL_OPTIONS
                                else next(iter(MODEL_OPTIONS)))
    st.session_state.setdefault("max_rounds", 4)


def render_sidebar() -> None:
    _init_state()
    with st.sidebar:

        try:
            s = metrics.dataset_summary()
            st.html(f"""
<div class="side-section">
  <div class="side-label">Data snapshot</div>
  <div class="side-row"><span>Period</span><b>{short_date(s['first_date'])} – {short_date(s['last_date'])}</b></div>
  <div class="side-row"><span>Valid transactions</span><b>{indian_int(s['valid_rows'])}</b></div>
  <div class="side-row"><span>Raw rows</span><b>{indian_int(s['raw_rows'])}</b></div>
  <div class="side-row"><span>Merchants</span><b>{indian_int(s['merchants'])}</b></div>
</div>""")
        except QueryError as e:
            st.error(f"Could not read the dataset: {e}")

        st.html('<div class="side-label side-label-gap">AI analyst</div>')
        st.selectbox("Model", options=list(MODEL_OPTIONS), format_func=MODEL_OPTIONS.get, key="model",
                     help="Used by Ask the Analyst. Lite is faster and has a larger free-tier quota.")
        st.slider("Max investigation rounds", 1, 6, key="max_rounds",
                  help="How many rounds of queries a deep investigation may run.")
        if api_key_configured():
            st.badge("Gemini API key configured", icon=":material/check_circle:", color="green")
        else:
            st.badge("Gemini API key missing", icon=":material/error:", color="red",
                     help="Add GEMINI_API_KEY to ai_analyst/.env to enable Ask the Analyst. "
                          "Dashboards work without it.")

        st.html("""
<div class="trust">
  <div class="trust-title">Read-only &amp; validated</div>
  Every query is checked by the SQL safety validator and runs on a read-only, sandboxed DuckDB connection.
</div>""")
