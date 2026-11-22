"""Data Quality: health of the payments dataset, measured live against the catalog's rules R1-R8."""
from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from components import quality_charts as qc
from components.cards import card, empty_state
from components.format import indian_int, inr, pct
from components.kpi_cards import KPI, render_kpis
from components.layout import page_header
from components.theme import PLOTLY_CONFIG
from services import quality
from services.backend import QueryError, get_schema


def _kpi(label: str, value: str, note: str, tone: str = "neutral", chip: str | None = None, help_: str = "") -> KPI:
    return KPI(label, value, help_, chip, "none", tone, note)


def health_row(h: pd.Series, s: pd.Series) -> None:
    raw, valid = int(h["raw_rows"]), int(h["valid_rows"])
    excluded = raw - valid
    score = h["clean_rows"] / raw
    in_tol = s["within_tolerance"] / s["batches"] if s["batches"] else 1.0
    render_kpis([
        _kpi("Data health score", pct(score, 1), "of raw rows are fully clean",
             "good" if score >= 0.98 else "bad", "rows with no defect",
             "Valid rows (R1–R4) that also have a known merchant and bank (R5) and a consistent failure code (R7), "
             "divided by all raw rows."),
        _kpi("Excluded from metrics", indian_int(excluded), "removed by R1–R3", "bad" if excluded else "good",
             f"{pct(excluded / raw, 2)} of raw"),
        _kpi("Unattributable rows", indian_int(h["unattributable_rows"]), "unknown merchant or bank (R5)",
             "bad" if h["unattributable_rows"] else "good", f"{pct(h['unattributable_rows'] / valid, 2)} of valid"),
        _kpi("FAILED without a code", indian_int(h["failed_without_code"]), "R7 · hides the failure reason",
             "bad" if h["failed_without_code"] else "good",
             f"{pct(h['failed_without_code'] / h['failed_rows'], 1)} of failures" if h["failed_rows"] else None),
        _kpi("Batches off by > tolerance", indian_int(s["out_of_tolerance"]), f"of {indian_int(s['batches'])} (R8)",
             "bad" if s["out_of_tolerance"] else "good", f"{pct(in_tol, 2)} reconcile"),
        _kpi("Net settlement gap", inr(s["net_gap"]), f"{int(s['merchants_affected'])} merchant(s) affected",
             "bad" if s["net_gap"] < 0 else "good", "under-paid" if s["net_gap"] < 0 else "no shortfall"),
    ])


def severity_summary(rules: pd.DataFrame) -> str:
    found = rules[(rules["affected_rows"] > 0) & (rules["severity"] != "info")]
    parts = []
    for sev in ("critical", "high", "medium"):
        n = int((found["severity"] == sev).sum())
        if n:
            parts.append(f"{qc.severity_pill(sev)} <b>{n}</b>")
    clean = int(((rules["affected_rows"] == 0) & (rules["severity"] != "info")).sum())
    tail = f'<span class="muted">· {clean} check(s) passed</span>' if clean else ""
    return f'<div class="sev-summary"><span>Open issues by severity</span>{" ".join(parts)}{tail}</div>'


# ----------------------------------------------------------------------------- rules table + drill-down
def rules_section(rules: pd.DataFrame, count_sql) -> None:
    with card("rules", "Quality rules and checks",
              "Select a row to see example records. Rule text comes from the data catalog; counts are live.",
              [count_sql]):
        view = pd.DataFrame({
            "Rule": rules["rule"], "Check": rules["name"],
            "Severity": [qc.severity_label(s) for s in rules["severity"]],
            "Affected": rules["affected_rows"], "Share": rules["share"], "Measured on": rules["scope"],
            "Metrics affected": rules["affects"],
        })
        event = st.dataframe(
            view, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="dq_rules",
            height=36 * (len(view) + 1) + 4,
            column_config={
                "Rule": st.column_config.TextColumn(width=60),
                "Check": st.column_config.TextColumn(width=240),
                "Severity": st.column_config.TextColumn(width=96),
                "Affected": st.column_config.NumberColumn(format="localized", width=80),
                "Share": st.column_config.ProgressColumn(format="percent", min_value=0,
                                                         max_value=max(0.01, float(rules["share"].max())), width=96),
                "Measured on": st.column_config.TextColumn(width=148),
                "Metrics affected": st.column_config.TextColumn(width="large"),
            },
        )
    picked = event.selection.rows[0] if event and event.selection.rows else None
    row = rules.iloc[picked] if picked is not None else rules.iloc[0]
    drill_down(row, selected=picked is not None)


def drill_down(row: pd.Series, selected: bool) -> None:
    check = next(c for c in quality.checks() if c.id == row["check_id"])
    sample = quality.sample(check)
    hint = "" if selected else " · showing the first rule; select a row above to change"
    with card("drill", f"Example records · {check.rule} · {check.name}",
              f"Up to 25 matching records from the database{hint}", [sample]):
        st.html(f'<div class="rule-box">{qc.severity_pill(check.severity)}'
                f'<span class="qchip">{escape(check.rule)}</span>'
                f'<span class="rule-text">{escape(quality.rule_text(check.rule))}</span></div>'
                f'<div class="rule-meta"><b>{indian_int(row["affected_rows"])}</b> of {indian_int(row["base"])} '
                f'{escape(row["scope"])} ({pct(row["share"], 2)})<br><b>How the analyst handles it:</b> {escape(check.handling)}'
                f'<br><b>Metrics affected:</b> {escape(check.affects)}</div>')
        if sample.df.empty:
            empty_state("No records break this check. ✓")
        else:
            st.dataframe(sample.df, hide_index=True, width="stretch", height=min(400, 36 * (len(sample.df) + 1) + 4),
                         key=f"sample_{check.id}")
            if check.id == "dup_rows":
                st.caption("Rows with the same transaction_id: R1 keeps the one with the latest updated_at "
                           "('kept') and drops the others.")


# ----------------------------------------------------------------------------- settlement reconciliation
def settlement_section(s: pd.Series, tol: float) -> None:
    st.html('<div class="section-head"><div class="sh-title">Settlement reconciliation (R8)</div>'
            f'<div class="sh-sub">Each (merchant, day) batch in settlements is compared with the net amount expected '
            f'from that day\'s valid SUCCESS transactions: amount − MDR fee − 18% GST, using the catalog formulas. '
            f'Gaps within ±₹{tol:,.2f} are rounding.</div></div>')
    render_kpis([
        _kpi("Merchant-days reconciled", indian_int(s["merchant_days"]), f"{indian_int(s['batches'])} settlement batches"),
        _kpi("Within tolerance", indian_int(s["within_tolerance"]), f"±₹{tol:,.2f} per batch", "good",
             pct(s["within_tolerance"] / s["merchant_days"], 2) if s["merchant_days"] else None),
        _kpi("Under-paid batches", indian_int(s["under_paid"]), "merchant received less than expected",
             "bad" if s["under_paid"] else "good", inr(s["under_paid_amount"]) if s["under_paid"] else "none"),
        _kpi("Over-paid batches", indian_int(s["over_paid"]), "merchant received more than expected",
             "bad" if s["over_paid"] else "good"),
        _kpi("Missing / orphan batches", indian_int(s["missing_batches"] + s["orphan_batches"]),
             "days with sales but no batch, or batches with no sales",
             "bad" if s["missing_batches"] + s["orphan_batches"] else "good"),
        _kpi("Aged pending batches", indian_int(s["aged_pending_batches"]), "PENDING ≥ 2 days past settlement date",
             "bad" if s["aged_pending_batches"] else "good"),
    ])
    st.space("small")

    daily = quality.settlement_daily_gap()
    merchants = quality.settlement_by_merchant()
    left, right = st.columns([3, 2], gap="medium")
    with left:
        with card("gap", "Daily settlement gap beyond tolerance", "Below zero = merchants were paid less than expected",
                  [daily]):
            if (daily.df["batches_out_of_tolerance"] > 0).any():
                st.plotly_chart(qc.settlement_gap_chart(daily.df, tol), config=PLOTLY_CONFIG, theme=None, key="ch_gap")
            else:
                empty_state("Every batch reconciles within tolerance.")
    with right:
        with card("gap_merchants", "Merchants with discrepancies", "Select a merchant to see its batches",
                  [merchants]):
            if merchants.df.empty:
                empty_state("No merchant has a batch outside tolerance.")
                return
            m = merchants.df.rename(columns={"merchant_id": "Merchant", "merchant_name": "Name",
                                             "batches_out_of_tolerance": "Batches off", "merchant_days": "Days",
                                             "total_gap": "Total gap (₹)", "first_gap_date": "First gap",
                                             "last_gap_date": "Last gap"})
            ev = st.dataframe(m, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                              key="dq_merchants",
                              column_config={"Total gap (₹)": st.column_config.NumberColumn(format="localized"),
                                             "First gap": st.column_config.DateColumn(format="D MMM"),
                                             "Last gap": st.column_config.DateColumn(format="D MMM")})
            pick = ev.selection.rows[0] if ev and ev.selection.rows else 0
            merchant_id = merchants.df.iloc[pick]["merchant_id"]
            r = merchants.df.iloc[pick]
            st.html(f'<div class="rule-meta"><b>{escape(str(r["merchant_name"]))}</b> was paid '
                    f'<b>{inr(abs(r["total_gap"]))}</b> {"less" if r["total_gap"] < 0 else "more"} than expected over '
                    f'{int(r["batches_out_of_tolerance"])} batches, from {r["first_gap_date"]:%d %b} to '
                    f'{r["last_gap_date"]:%d %b %Y}.</div>')

    batches = quality.settlement_batches(merchant_id)
    with card("batches", f"Batches for {merchant_id}", "Discrepant days first · expected values recomputed from "
              "transactions", [batches]):
        b = batches.df.copy()
        money = {"gap": "Gap (₹)", "net_amount": "Net paid (₹)", "expected_net": "Expected net (₹)",
                 "gross_amount": "Gross (₹)", "expected_gross": "Expected gross (₹)"}
        cfg = {c: st.column_config.NumberColumn(label, format="localized") for c, label in money.items()}
        cfg.update({"transaction_date": st.column_config.DateColumn("Sales date", format="D MMM YYYY"),
                    "outcome": "Outcome", "transaction_count": "Txns in batch", "expected_count": "Expected txns",
                    "batch_status": "Batch status", "settlement_id": "Settlement ID",
                    "settlement_date": st.column_config.DateColumn("Settlement date", format="D MMM YYYY")})
        st.dataframe(b, hide_index=True, width="stretch", height=min(400, 36 * (len(b) + 1) + 4), column_config=cfg)

    status = quality.batch_status()
    with card("batch_status", "Settlement batch status", "Aged pending uses the catalog definition "
              "(PENDING with settlement date ≥ 2 days before the extract)", [status]):
        st.dataframe(status.df, hide_index=True, width="stretch",
                     column_config={"status": "Status", "status_reason": "Reason",
                                    "net_amount": st.column_config.NumberColumn("Net amount (₹)", format="localized"),
                                    "batches": st.column_config.NumberColumn("Batches", format="localized"),
                                    "aged_pending": st.column_config.NumberColumn("Aged pending", format="localized")})


# ----------------------------------------------------------------------------- page
def render() -> None:
    page_header("Data", "Data Quality",
                "Checks run live on the dataset with fixed, read-only SQL · rules R1–R8 from the data catalog")
    with st.spinner("Running data-quality checks…"):
        health = quality.health_summary()
        settle = quality.settlement_summary()
        counts = quality.check_counts()
        funnel = quality.exclusion_funnel()
        values = quality.invalid_values()
        impact = quality.metric_impact()
        unattributed = quality.unattributed_impact()
        weekly = quality.weekly_issues()
        rules = quality.rules_table()
    h, s, tol = health.df.iloc[0], settle.df.iloc[0], quality.settlement_tolerance()

    st.html(severity_summary(rules))
    health_row(h, s)
    st.space("small")

    with card("pipeline", "From raw rows to valid transactions (R1–R4)",
              "Every analyst metric is computed on valid transactions only", [funnel, values]):
        st.html(qc.pipeline_html(funnel.df))
        v = values.df
        if not v.empty:
            chips = "".join(f'<span class="valchip"><span class="qchip">{escape(r["rule"].split()[0])}</span> '
                            f'{escape(r["value"])} <b>{indian_int(r["rows"])}</b></span>' for _, r in v.iterrows())
            st.html(f'<div class="valchips"><span class="muted">What the excluded values look like:</span>{chips}</div>')

    left, right = st.columns([3, 2], gap="medium")
    with left:
        with card("checks", "Affected rows by check", "Coloured by severity · label = count · share of the rows or batches checked "
                  "· expected cases (R6) not shown", [counts]):
            st.plotly_chart(qc.checks_bar(rules.assign(severity_text=[qc.severity_label(x) for x in rules["severity"]])),
                            config=PLOTLY_CONFIG, theme=None, key="ch_checks")
            st.html(qc_legend())
    with right:
        with card("impact", "Impact on the analyst's metrics",
                  "Catalog metrics on the raw table vs on valid transactions", [impact, unattributed]):
            st.html(qc.impact_rows(impact.df))
            u = unattributed.df.set_index("dimension")
            notes = [f"<li><b>{pct(u.loc[d, 'attempt_share'], 2)}</b> of valid attempts and "
                     f"<b>{inr(u.loc[d, 'tpv'])}</b> TPV ({pct(u.loc[d, 'tpv_share'], 2)}) can't be attributed to "
                     f"{'a merchant' if d == 'Merchant' else 'an issuing bank'} (R5). They count in company totals "
                     f"but not in {'merchant' if d == 'Merchant' else 'bank'} rankings.</li>" for d in u.index]
            if h["failed_rows"]:
                notes.append(f"<li><b>{pct(h['failed_without_code'] / h['failed_rows'], 1)}</b> of failures have no "
                             "failure code (R7), so failure-reason shares are computed on coded failures only.</li>")
            st.html(f'<ul class="impact-notes">{"".join(notes)}</ul>')

    rules_section(rules, counts)

    with card("weekly", "Issue rate by week", "Rows per 10,000 raw rows in that week · dotted line = period "
              "average · normalising removes volume swings", [weekly]):
        st.plotly_chart(qc.weekly_small_multiples(weekly.df), config=PLOTLY_CONFIG, theme=None, key="ch_weekly")

    st.space("small")
    settlement_section(s, tol)

    with st.expander("Catalog rules R1–R8 (as written in the data catalog)", icon=":material/menu_book:"):
        for r in get_schema().validity_rules:
            st.markdown(f"**{r['id']}** — {r['rule']}")


def qc_legend() -> str:
    return ('<div class="sev-legend">' + "".join(qc.severity_pill(s) for s in ("critical", "high", "medium")) +
            '<span class="muted">Critical: money mismatch · High: would distort headline metrics if not cleaned · '
            'Medium: limits breakdowns</span></div>')


try:
    render()
except QueryError as e:
    st.error(f"A data-quality query failed: {e}", icon=":material/error:")
