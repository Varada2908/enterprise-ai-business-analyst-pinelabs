"""Data Catalog tests: catalog facts match the database, profiles are computed live, and the SQL guard
only validates (never executes).

    cd app
    python -m pytest tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1]
for p in (str(APP), str(APP.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from ai_analyst.schema_context import DEFAULT_DB_PATH  # noqa: E402
from services import catalog  # noqa: E402


@pytest.fixture(scope="module")
def db():
    con = duckdb.connect(str(DEFAULT_DB_PATH), read_only=True, config={"enable_external_access": False})
    yield con
    con.close()


def test_summary_matches_database(db):
    s = catalog.summary()
    names = catalog.table_names()
    assert s["tables"] == len(names) == 6
    cols = db.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'main' "
                      f"AND table_name IN ({', '.join(repr(n) for n in names)})").fetchone()[0]
    assert s["columns"] == cols
    assert s["rows"] == sum(db.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0] for n in names)
    assert s["warnings"] == []


def test_profile_is_computed_live(db):
    prof = catalog.profile("transactions").df.set_index("column_name")
    nn, distinct = db.execute("SELECT count(merchant_id), count(DISTINCT status) FROM transactions").fetchone()
    assert prof.loc["merchant_id", "non_null"] == nn
    assert prof.loc["status", "distinct_values"] == distinct
    assert list(prof.index) == [c for c in catalog.columns("transactions")["column"]]


def test_top_values_and_samples(db):
    tv = catalog.top_values("transactions", "status").df
    assert tv["rows"].iloc[0] == db.execute(
        "SELECT count(*) FROM transactions GROUP BY status ORDER BY 1 DESC LIMIT 1").fetchone()[0]
    assert abs(tv["share"].sum() - 1) < 0.01 or len(tv) == 8
    assert len(catalog.sample_rows("banks").df) == 10


def test_unknown_names_are_rejected():
    with pytest.raises(ValueError):
        catalog.profile("transactions; DROP TABLE x")
    with pytest.raises(ValueError):
        catalog.top_values("transactions", 'status" FROM banks --')


def test_join_on_is_displayed_correctly():
    rel = catalog.relationships("settlements")
    joins = " | ".join(rel["join_on"])
    assert "CAST(transactions.created_at AS DATE)" in joins
    assert "transactions.CAST(" not in joins


def test_sql_guard_only_validates(db):
    assert not catalog.check_sql("DELETE FROM transactions").ok
    assert not catalog.check_sql("DROP TABLE merchants").ok
    assert not catalog.check_sql("SELECT * FROM read_csv('x.csv')").ok
    assert not catalog.check_sql("SELECT * FROM secret_table").ok
    assert catalog.check_sql("SELECT count(*) FROM merchants").ok
    # validating "DROP TABLE" did nothing to the data
    assert db.execute("SELECT count(*) FROM merchants").fetchone()[0] == catalog.summary_table_rows("merchants")


def test_er_diagram_has_every_table_and_join():
    dot = catalog.er_diagram_dot()
    for t in catalog.table_names():
        assert f'"{t}"' in dot
    assert dot.count("->") == catalog.summary()["join_keys"]


def test_page_tabs_and_interactions(monkeypatch):
    monkeypatch.chdir(APP)
    at = AppTest.from_file(str(APP / "streamlit_app.py"), default_timeout=180)
    at.run()
    at.switch_page("views/data_catalog.py").run()
    assert not at.exception, [e.message for e in at.exception]
    assert len(at.tabs) == 4

    at.session_state["cat_table"] = "settlements"
    at.text_input(key="cat_search").input("bank").run()
    assert not at.exception, [e.message for e in at.exception]
    html = " ".join(str(getattr(h, "proto", "")) for h in at.get("html"))
    assert "column(s) matching" in html and "settlements" in html

    at.text_area(key="guard_sql").input("DELETE FROM transactions")
    next(b for b in at.button if b.label == "Check").click().run()
    assert not at.exception, [e.message for e in at.exception]
    html = " ".join(str(getattr(h, "proto", "")) for h in at.get("html"))
    assert "Blocked" in html
