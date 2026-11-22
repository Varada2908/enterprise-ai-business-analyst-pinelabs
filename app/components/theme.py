"""Design tokens, the Plotly chart template and global CSS.

Colour rules (validated with the dataviz palette checker):
- payment methods keep one fixed colour everywhere, so a filter never repaints a method;
- status colours (good / warning / serious / critical) are reserved for state and always ship with
  an icon + label, never colour alone;
- single-series charts use the brand ink, not a categorical hue.
"""
from __future__ import annotations

from pathlib import Path

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

ASSETS = Path(__file__).resolve().parents[1] / "assets"

COLORS = {
    "brand": "#0F766E",
    "brand_soft": "#CCFBF1",
    "ink": "#0F172A",
    "text2": "#475569",
    "muted": "#64748B",
    "border": "#E2E8F0",
    "grid": "#EEF1F5",
    "surface": "#FFFFFF",
    "page": "#F5F7FA",
    "bar_neutral": "#CBD5E1",
    "bar_partial": "#E2E8F0",
}

METHOD_COLORS = {
    "UPI": "#2a78d6",
    "CREDIT_CARD": "#eb6834",
    "DEBIT_CARD": "#1baf7a",
    "NET_BANKING": "#eda100",
    "WALLET": "#e87ba4",
}
METHOD_LABELS = {
    "UPI": "UPI",
    "CREDIT_CARD": "Credit card",
    "DEBIT_CARD": "Debit card",
    "NET_BANKING": "Net banking",
    "WALLET": "Wallet",
}

STATUS = {
    "good": "#0ca30c",
    "warning": "#fab219",
    "serious": "#ec835a",
    "critical": "#d03b3b",
}

FONT = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"


def method_label(code: str) -> str:
    return METHOD_LABELS.get(code, code.replace("_", " ").title())


def _register_template() -> None:
    axis = dict(
        showgrid=True, gridcolor=COLORS["grid"], gridwidth=1, zeroline=False,
        linecolor=COLORS["border"], showline=False, ticks="",
        tickfont=dict(size=11, color=COLORS["muted"]), title=dict(font=dict(size=11, color=COLORS["muted"])),
        automargin=True,
    )
    pio.templates["fintech"] = go.layout.Template(
        layout=dict(
            font=dict(family=FONT, size=12, color=COLORS["text2"]),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            colorway=list(METHOD_COLORS.values()),
            margin=dict(l=8, r=16, t=8, b=8),
            xaxis={**axis, "showgrid": False},
            yaxis=axis,
            hoverlabel=dict(bgcolor="#FFFFFF", bordercolor=COLORS["border"],
                            font=dict(family=FONT, size=12, color=COLORS["ink"])),
            legend=dict(orientation="h", x=0, xanchor="left", y=1.02, yanchor="bottom",
                        font=dict(size=11, color=COLORS["text2"]), bgcolor="rgba(0,0,0,0)",
                        itemclick="toggleothers", title=dict(text="")),
            bargap=0.35,
        )
    )


_register_template()

PLOTLY_CONFIG = {"displayModeBar": False, "responsive": True}


def inject_css() -> None:
    """Load assets/styles.css once per page run."""
    css = (ASSETS / "styles.css").read_text(encoding="utf-8")
    st.html(f"<style>{css}</style>")
