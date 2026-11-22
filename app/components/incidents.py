"""Detected incidents: success-rate breaks found by the backend's change-date detection."""
from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from .format import indian_int, pct, pp, short_date

DIMENSION_LABELS = {
    "merchant": "Merchant",
    "device_platform": "Device platform",
    "merchant x platform": "Merchant × platform",
    "payment_method": "Payment method",
    "issuing_bank": "Issuing bank",
    "bank x method": "Bank × method",
    "merchant_category": "Merchant category",
    "channel": "Channel",
}

# status -> (css tone, icon glyph, label). Always icon + label, never colour alone.
STATUS_STYLE = {
    "ongoing": ("critical", "●", "Ongoing"),
    "recovered": ("good", "✓", "Recovered"),
    "unclear": ("warning", "?", "Unclear"),
}

ASK_PAGE = "views/ask_analyst.py"


def investigation_question(row: pd.Series) -> str:
    dim = DIMENSION_LABELS.get(row["dimension"], row["dimension"]).lower()
    return (f"Why did the payment success rate drop for {dim} {row['segment']} "
            f"from {short_date(row['change_date'])}? What is driving it and is it still ongoing?")


def _go_investigate(question: str) -> None:
    st.session_state["pending_question"] = question


def _card_html(row: pd.Series) -> str:
    tone, glyph, label = STATUS_STYLE.get(row["status"], STATUS_STYLE["unclear"])
    recovered = row["status"] == "recovered"
    after = row["sr_first_7d"] if recovered else row["sr_after"]
    if recovered:
        foot = (f"Rates: 28 days before → first 7 days after. "
                f"Last 7 days back at {pct(row['sr_last_7d'])}.")
    else:
        foot = (f"{indian_int(row['n_before'])} attempts in the 28 days before · "
                f"{indian_int(row['n_after'])} since")
    return f"""
<div class="incident">
  <div class="incident-top">
    <span class="pill pill-{tone}"><span aria-hidden="true">{glyph}</span> {label}</span>
    <span class="incident-dim">{escape(DIMENSION_LABELS.get(row['dimension'], row['dimension']))}</span>
  </div>
  <div class="incident-seg">{escape(str(row['segment']))}</div>
  <div class="incident-meta">From <b>{short_date(row['change_date'])}</b> · z = {row['z_score']:.1f}</div>
  <div class="incident-rates">
    <span>{pct(row['sr_before'])}</span><span class="arrow">→</span><span class="after">{pct(after)}</span>
    <span class="incident-change">{pp(after - row['sr_before'])}</span>
  </div>
  <div class="incident-foot">{foot}</div>
</div>"""


def render_incidents(df: pd.DataFrame, limit: int = 4) -> None:
    """Show the clearest breaks, ongoing first, then by z-score."""
    if df.empty:
        st.html('<div class="empty-state">No statistically clear success-rate break was found '
                '(threshold z ≤ −5, calibrated on data with no incidents).</div>')
        return
    order = {"ongoing": 0, "unclear": 1, "recovered": 2}
    d = df.assign(_o=df["status"].map(order).fillna(3)).sort_values(["_o", "z_score"]).head(limit)
    for i, (_, row) in enumerate(d.iterrows()):
        with st.container(key=f"incident_{i}"):
            st.html(_card_html(row))
            if st.button("Investigate", icon=":material/manage_search:", key=f"investigate_{i}",
                         type="secondary", width="stretch",
                         on_click=_go_investigate, args=(investigation_question(row),)):
                st.switch_page(ASK_PAGE)
    hidden = len(df) - len(d)
    if hidden > 0:
        st.caption(f"+{hidden} more breaks in overlapping segments (e.g. the same merchant on one platform).")
