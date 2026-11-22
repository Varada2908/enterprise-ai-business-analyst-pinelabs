"""Page-level building blocks shared by all views."""
from __future__ import annotations

from html import escape

import streamlit as st


def page_header(eyebrow: str, title: str, subtitle: str, badge: str | None = "✓ Read-only · SQL validated") -> None:
    badge_html = f'<span class="trust-badge">{escape(badge)}</span>' if badge else ""
    st.html(f"""
<div class="page-header">
  <div>
    <div class="page-eyebrow">{escape(eyebrow)}</div>
    <div class="page-title">{escape(title)}</div>
    <div class="page-sub">{subtitle}</div>
  </div>
  {badge_html}
</div>""")


def placeholder(title: str, text: str, items: list[str]) -> None:
    lis = "".join(f"<li>{escape(i)}</li>" for i in items)
    st.html(f"""
<div class="placeholder">
  <span class="soon">Coming next</span>
  <h3>{escape(title)}</h3>
  <p>{escape(text)}</p>
  <ul>{lis}</ul>
</div>""")
