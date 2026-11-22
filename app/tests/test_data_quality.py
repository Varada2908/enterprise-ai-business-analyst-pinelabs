"""Data Quality page tests: the page renders, and its numbers are computed from the data, not typed in.

Each headline count is recomputed independently (plain DuckDB / pandas on a separate read-only connection)
and compared with what the page's service returns.

    cd app
    python -m pytest tests -q
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import duckdb
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1]
for p in (str(APP), str(APP.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

from ai_analyst.schema_context import DEFAULT_DB_PATH  # noqa: E402
from services import quality  # noqa: E402

VALID = ("SUCCESS", "FAILED", "USER_CANCELLED", "PENDING")


@pytest.fixture(scope="module")
def db():
    con = duckdb.connect(str(DEFAULT_DB_PATH), read_only=True, config={"enable_external_access": False})
    yield con
    con.close()


@pytest.fixture(scope="module")
def raw(db):
    return db.execute("SELECT * FROM transactions").df()


@pytest.fixture(scope="module")
def latest(raw):
    return raw.sort_values("updated_at", ascending=False).drop_duplicates("transaction_id")


@pytest.fixture(scope="module")
def valid(latest, db):
    pm = db.execute("SELECT payment_method_id, method_code FROM payment_methods").df()
    v = latest[latest["status"].isin(VALID)]
    v = v[(v["amount"].astype(float) > 0) & (v["amount"].astype(float) <= 1_000_000)]
    return v.merge(pm, on="payment_method_id")


def test_counts_match_independent_recomputation(raw, latest, valid, db):
    c = quality.check_counts().df.set_index("check_id")["affected_rows"]
    merchants = set(db.execute("SELECT merchant_id FROM merchants").df()["merchant_id"])
    banks = set(db.execute("SELECT bank_id FROM banks").df()["bank_id"])
    assert c["dup_rows"] == len(raw) - raw["transaction_id"].nunique()
    assert c["bad_status"] == (~latest["status"].isin(VALID)).sum()
    in_status = latest[latest["status"].isin(VALID)]
    amt = in_status["amount"].astype(float)
    assert c["bad_amount"] == (amt.isna() | (amt <= 0) | (amt > 1_000_000)).sum()
    assert c["merchant_null"] == valid["merchant_id"].isna().sum()
    assert c["merchant_unknown"] == (valid["merchant_id"].notna() & ~valid["merchant_id"].isin(merchants)).sum()
    non_wallet = valid["method_code"] != "WALLET"
    assert c["bank_null"] == (valid["bank_id"].isna() & non_wallet).sum()
    assert c["bank_unknown"] == (valid["bank_id"].notna() & ~valid["bank_id"].isin(banks)).sum()
    assert c["failed_no_code"] == ((valid["status"] == "FAILED") & valid["failure_code"].isna()).sum()


def test_funnel_ends_at_the_backends_valid_transactions(valid):
    f = quality.exclusion_funnel().df["rows_remaining"].tolist()
    assert f == sorted(f, reverse=True)
    assert f[-1] == len(valid) == quality.health_summary().df.iloc[0]["valid_rows"]


def test_settlement_gap_matches_independent_recomputation(valid, db):
    """Recompute expected net settlement in pandas with the catalog formulas and compare with the page."""
    m = db.execute("SELECT merchant_id, mdr_discount_pct FROM merchants").df()
    pm = db.execute("SELECT payment_method_id, mdr_rate_pct FROM payment_methods").df()
    s = db.execute("SELECT merchant_id, transaction_date, net_amount FROM settlements").df()
    t = valid[valid["status"] == "SUCCESS"].merge(m, on="merchant_id").merge(pm, on="payment_method_id")
    from decimal import ROUND_HALF_UP, Decimal

    def r2(x):
        return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    t["fee"] = [r2(Decimal(a) * Decimal(r) * (100 - int(d)) * Decimal("0.0001"))
                for a, r, d in zip(t["amount"], t["mdr_rate_pct"], t["mdr_discount_pct"])]
    t["tax"] = [r2(f * Decimal("0.18")) for f in t["fee"]]
    t["net"] = [Decimal(a) - f - x for a, f, x in zip(t["amount"], t["fee"], t["tax"])]
    t["transaction_date"] = pd.to_datetime(t["created_at"]).dt.date
    exp = t.groupby(["merchant_id", "transaction_date"])["net"].sum().reset_index()
    s["transaction_date"] = pd.to_datetime(s["transaction_date"]).dt.date
    j = s.merge(exp, on=["merchant_id", "transaction_date"], how="inner")
    j["gap"] = [Decimal(str(n)) - e for n, e in zip(j["net_amount"], j["net"])]
    off = j[[abs(g) > Decimal("1.00") for g in j["gap"]]]

    summary = quality.settlement_summary().df.iloc[0]
    assert summary["out_of_tolerance"] == len(off)
    assert round(float(summary["net_gap"]), 2) == round(float(sum(off["gap"], Decimal(0))), 2)
    top = quality.settlement_by_merchant().df
    if len(off):
        worst = off.groupby("merchant_id")["gap"].sum().map(float).abs().idxmax()
        assert top.iloc[0]["merchant_id"] == worst


def test_tolerance_comes_from_catalog_rule_r8():
    assert "1.00" in quality.rule_text("R8") and quality.settlement_tolerance() == 1.0


def test_samples_only_contain_records_that_break_the_rule():
    by_id = {c.id: c for c in quality.checks()}
    s = quality.sample(by_id["bad_status"]).df
    assert len(s) and not s["status"].isin(VALID).any()
    d = quality.sample(by_id["dup_rows"]).df
    assert (d.groupby("transaction_id").size() > 1).all()
    assert (d.groupby("transaction_id")["r1_outcome"].apply(lambda x: (x == "kept").sum()) == 1).all()
    f = quality.sample(by_id["failed_no_code"]).df
    assert (f["status"] == "FAILED").all() and f["failure_code"].isna().all()
    g = quality.sample(by_id["settle_gap"]).df
    assert (g["gap"].abs() > quality.settlement_tolerance()).all()


def test_rules_table_covers_catalog_rules_and_has_no_hardcoded_counts():
    rules = quality.rules_table()
    catalog_ids = {r["id"] for r in quality.get_schema().validity_rules}
    assert {"R1", "R2", "R3", "R5", "R6", "R7", "R8"} <= set(rules["rule"]) <= catalog_ids
    # no data count is typed into the page or its queries
    known = quality.check_counts().df["affected_rows"].tolist() + [quality.settlement_summary().df.iloc[0]["batches"]]
    source = (APP / "services" / "quality.py").read_text(encoding="utf-8") + \
        (APP / "views" / "data_quality.py").read_text(encoding="utf-8")
    numbers_in_source = {int(n.replace(",", "")) for n in re.findall(r"\b\d[\d,]{2,}\b", source)}
    assert not {int(k) for k in known if k >= 100} & numbers_in_source


def test_page_renders(monkeypatch):
    monkeypatch.chdir(APP)
    at = AppTest.from_file(str(APP / "streamlit_app.py"), default_timeout=180)
    at.run()
    at.switch_page("views/data_quality.py").run()
    assert not at.exception, [e.message for e in at.exception]
    assert not at.error, [e.value for e in at.error]
    assert len(at.dataframe) >= 4        # rules, drill-down sample, merchants, batches, status
