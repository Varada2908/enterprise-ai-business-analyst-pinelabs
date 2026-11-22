"""Smoke tests: every page renders without exceptions, and the dashboard numbers match the backend.

    cd app
    python -m pytest tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent
for p in (str(APP), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

PAGES = ["views/overview.py", "views/ask_analyst.py", "views/data_quality.py", "views/data_catalog.py"]


@pytest.mark.parametrize("page", PAGES)
def test_page_renders(page, monkeypatch):
    monkeypatch.chdir(APP)
    at = AppTest.from_file(str(APP / "streamlit_app.py"), default_timeout=180)
    at.run()
    assert not at.exception, at.exception
    at.switch_page(page).run()
    assert not at.exception, [e.message for e in at.exception]
    assert not at.error, [e.value for e in at.error]


def test_overview_filters(monkeypatch):
    monkeypatch.chdir(APP)
    at = AppTest.from_file(str(APP / "streamlit_app.py"), default_timeout=180)
    at.run()
    at.session_state["ov_methods"] = ["NET_BANKING"]
    at.session_state["ov_channel"] = "ONLINE"
    at.run()
    assert not at.exception, [e.message for e in at.exception]
    assert not at.error, [e.value for e in at.error]


def test_kpis_match_known_totals():
    """Full-period KPIs equal the figures verified on the dataset (and used by the analyst)."""
    from datetime import date

    from services import metrics

    s = metrics.dataset_summary()
    f = metrics.Filters(s["first_date"], s["last_date"])
    k = metrics.kpis(f).df.iloc[0]
    assert round(k["success_rate"], 4) == 0.9002
    assert round(k["tpv"], 2) == 729_439_708.64
    nb = metrics.by_method(f).df.set_index("method_code").loc["NET_BANKING", "success_rate"]
    assert round(nb, 4) == 0.8407
    assert isinstance(s["first_date"], date)


def test_filters_reject_unknown_values():
    from datetime import date

    from services import metrics

    bad = metrics.Filters(date(2026, 8, 1), date(2026, 9, 1), ("UPI'; DROP TABLE x;--",), ("ONLINE",))
    with pytest.raises(ValueError):
        bad.where()


def test_incidents_include_m0137_android():
    from services import metrics

    df = metrics.incidents().df
    segs = set(df["segment"])
    assert any("M0137" in s and "ANDROID" in s for s in segs)
