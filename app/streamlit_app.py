"""AI Business Analyst - Streamlit entry point.

    cd app
    streamlit run streamlit_app.py

Sets up the page, theme, navigation and sidebar; each page lives in views/.
"""
from __future__ import annotations

import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent                      # project root: makes the ai_analyst backend importable
for p in (str(APP_DIR), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import streamlit as st  # noqa: E402

from components.sidebar import render_sidebar  # noqa: E402
from components.theme import inject_css  # noqa: E402

st.set_page_config(
    page_title="AI Business Analyst",
    page_icon=":material/monitoring:",
    layout="wide",
    initial_sidebar_state="expanded",
)
inject_css()
st.logo(str(APP_DIR / "assets" / "logo.svg"), size="large")

pages = {
    "Analytics": [
        st.Page("views/overview.py", title="Overview", icon=":material/space_dashboard:", default=True),
        st.Page("views/ask_analyst.py", title="Ask the Analyst", icon=":material/forum:"),
    ],
    "Data": [
        st.Page("views/data_quality.py", title="Data Quality", icon=":material/fact_check:"),
        st.Page("views/data_catalog.py", title="Data Catalog", icon=":material/menu_book:"),
    ],
}
navigation = st.navigation(pages, position="sidebar")
render_sidebar()
navigation.run()
