"""Data Catalog: the semantic layer the AI analyst works from - tables, columns, joins, metrics, rules and
access controls - with live column profiles from the database."""
from __future__ import annotations

from html import escape

import pandas as pd
import streamlit as st

from components.cards import card, empty_state
from components.format import indian_int, pct
from components.kpi_cards import KPI, render_kpis
from components.layout import page_header
from services import catalog
from services.backend import QueryError, get_schema

page_header("Data", "Data Catalog",
            "The same schema, metric definitions and rules the AI analyst reads before writing SQL")


def summary_row() -> None:
    s = catalog.summary()
    drift = len(s["warnings"])
    render_kpis([
        KPI("Tables", str(s["tables"]), delta=f"{indian_int(s['rows'])} rows", direction="none", delta_note="in total"),
        KPI("Columns", str(s["columns"]), delta="described", direction="none", delta_note="from the catalog"),
        KPI("Join keys", str(s["join_keys"]), delta="with caveats", direction="none", delta_note="used by the analyst"),
        KPI("Metric definitions", str(s["metrics"]), delta="with SQL", direction="none", delta_note="one meaning each"),
        KPI("Validity rules", str(s["rules"]), delta="R1–R8", direction="none", delta_note="see Data Quality"),
        KPI("Catalog vs database", "In sync" if not drift else f"{drift} issue(s)",
            delta="checked at start-up" if not drift else "drift found", direction="none",
            tone="good" if not drift else "bad", delta_note="types and columns"),
    ])
    if drift:
        with st.expander(f"{drift} catalog / database differences", icon=":material/warning:"):
            for w in s["warnings"]:
                st.markdown(f"- {w}")


# ----------------------------------------------------------------------------- tables
def tables_tab() -> None:
    overview = catalog.tables_overview()
    names = overview["table"].tolist()
    st.session_state.setdefault("cat_table", names[0])
    picked = st.pills("Table", names, key="cat_table", label_visibility="collapsed",
                      format_func=lambda n: f"{n} · {indian_int(overview.set_index('table').loc[n, 'rows'])}")
    table = picked or names[0]
    t = overview.set_index("table").loc[table]

    st.html(f"""
<div class="tbl-head">
  <div><div class="tbl-name">{escape(table)}</div><div class="tbl-desc">{escape(t['description'])}</div></div>
  <div class="tbl-facts">
    <div><span>Grain</span><b>{escape(t['grain'])}</b></div>
    <div><span>Primary key</span><b class="mono">{escape(t['primary_key'])}</b></div>
    <div><span>Rows</span><b>{indian_int(t['rows'])}</b></div>
    <div><span>Columns</span><b>{int(t['columns'])}</b></div>
  </div>
</div>""")

    prof = catalog.profile(table)
    cols = catalog.columns(table).merge(prof.df.rename(columns={"column_name": "column"}), on="column", how="left")
    cols["filled"] = cols["non_null"] / cols["rows"]
    with card("columns", "Columns", "Descriptions from the data catalog · profile measured live on the raw table "
              "(before validity rules)", [prof]):
        st.dataframe(
            cols[["column", "type", "description", "values_or_notes", "filled", "distinct_values", "min_value",
                  "max_value"]],
            hide_index=True, width="stretch", height=36 * (len(cols) + 1) + 4,
            column_config={
                "column": st.column_config.TextColumn("Column", width=150),
                "type": st.column_config.TextColumn("Type", width=110),
                "description": st.column_config.TextColumn("Description", width="large"),
                "values_or_notes": st.column_config.TextColumn("Allowed values / notes", width="medium"),
                "filled": st.column_config.ProgressColumn("Filled", format="percent", min_value=0, max_value=1,
                                                          width=100, help="Share of rows where the column is not NULL"),
                "distinct_values": st.column_config.NumberColumn("Distinct", format="localized", width=80),
                "min_value": st.column_config.TextColumn("Min", width=120),
                "max_value": st.column_config.TextColumn("Max", width=120),
            })

    left, right = st.columns([1, 1], gap="medium")
    with left:
        low_card = cols[(cols["distinct_values"] <= 15) & (cols["distinct_values"] > 1)]["column"].tolist()
        with card("values", "Value distribution", "Most frequent values of a low-cardinality column"):
            if not low_card:
                empty_state("This table has no low-cardinality columns to break down.")
            else:
                col = st.selectbox("Column", low_card, key=f"cat_vals_{table}", label_visibility="collapsed")
                tv = catalog.top_values(table, col)
                st.dataframe(tv.df, hide_index=True, width="stretch",
                             column_config={"value": "Value",
                                            "rows": st.column_config.NumberColumn("Rows", format="localized"),
                                            "share": st.column_config.ProgressColumn("Share", format="percent",
                                                                                     min_value=0, max_value=1)})
                allowed = cols.set_index("column").loc[col, "values_or_notes"]
                if allowed:
                    st.caption(f"Catalog says: {allowed}")
    with right:
        rel = catalog.relationships(table)
        with card("rels", "Joins for this table", "How to join it correctly, with the caveats the analyst follows"):
            if rel.empty:
                empty_state("No join keys defined for this table.")
            for _, r in rel.iterrows():
                st.html(f'<div class="join"><div class="join-top"><span class="mono">{escape(r["join_on"])}</span>'
                        f'<span class="card-tag">{escape(r["cardinality"])}</span></div>'
                        + (f'<div class="join-note">{escape(r["note"])}</div>' if r["note"] else "") + "</div>")

    sample = catalog.sample_rows(table)
    with card("sample", "Sample rows", f"First 10 rows of {table}, unfiltered", [sample]):
        st.dataframe(sample.df, hide_index=True, width="stretch")


def column_search() -> None:
    q = st.text_input("Search columns", key="cat_search", placeholder="Search all tables, e.g. 'bank', 'amount', "
                      "'settlement'…", label_visibility="collapsed", icon=":material/search:")
    if not q:
        return
    allc = catalog.all_columns()
    mask = pd.Series(False, index=allc.index)
    for field in ("column", "description", "values_or_notes", "table"):
        mask |= allc[field].str.contains(q, case=False, regex=False, na=False)
    hits = allc[mask]
    with card("search", f"{len(hits)} column(s) matching “{q}”", "Across all tables"):
        if hits.empty:
            empty_state("No column matches. Try a shorter word.")
        else:
            st.dataframe(hits, hide_index=True, width="stretch", height=min(400, 36 * (len(hits) + 1) + 4))


# ----------------------------------------------------------------------------- relationships
def relationships_tab() -> None:
    with card("erd", "Entity relationships", "Primary keys in teal · arrows point from the many side to the one side"):
        st.graphviz_chart(catalog.er_diagram_dot(), width="stretch")
    rel = catalog.relationships()
    with card("joins", "Join keys", "Exactly what the analyst is told about each join"):
        st.dataframe(rel, hide_index=True, width="stretch", height=36 * (len(rel) + 1) + 4,
                     column_config={"from": "From", "to": "To",
                                    "join_on": st.column_config.TextColumn("Join on", width="large"),
                                    "cardinality": "Cardinality",
                                    "note": st.column_config.TextColumn("Caveat", width="large")})


# ----------------------------------------------------------------------------- metrics
def metrics_tab() -> None:
    schema = get_schema()
    q = st.text_input("Filter metrics", key="cat_metric_q", placeholder="Filter by name or definition, e.g. "
                      "'success', 'settlement', 'fee'…", label_visibility="collapsed", icon=":material/filter_list:")
    metrics = [m for m in schema.metrics
               if not q or q.lower() in (m["name"] + " " + m.get("definition", "")).lower()]
    st.caption(f"{len(metrics)} of {len(schema.metrics)} metrics · every transaction metric is computed on valid "
               "transactions (the valid_txn CTE below)")
    if not metrics:
        empty_state("No metric matches this filter.")
    cols = st.columns(2, gap="medium")
    for i, m in enumerate(metrics):
        with cols[i % 2]:
            with st.container(border=True, key=f"card_metric_{i}"):
                st.html(f'<div class="metric-name mono">{escape(m["name"])}</div>'
                        f'<div class="metric-def">{escape(m.get("definition", ""))}</div>')
                st.code(m["sql"], language="sql", wrap_lines=True)
                if m.get("notes"):
                    st.html(f'<div class="metric-note">{escape(str(m["notes"]))}</div>')
    with card("cte", "Standard cleaning step: valid_txn", "Implements rules R1–R4. Dashboards and the AI analyst "
              "start every transaction query from it"):
        st.code("WITH " + schema.VALID_TRANSACTIONS_CTE, language="sql")


# ----------------------------------------------------------------------------- rules & access
def rules_tab() -> None:
    schema = get_schema()
    left, right = st.columns([1, 1], gap="medium")
    with left:
        with card("vrules", "Validity rules (R1–R8)", "From the data catalog · live counts on the Data Quality page"):
            for r in schema.validity_rules:
                st.html(f'<div class="vrule"><span class="qchip">{escape(r["id"])}</span>'
                        f'<span>{escape(r["rule"])}</span></div>')
            if st.button("Open Data Quality", icon=":material/fact_check:", type="tertiary"):
                st.switch_page("views/data_quality.py")
        with card("conv", "Conventions", "Units, time zone and defaults"):
            for k, v in schema.conventions.items():
                st.html(f'<div class="vrule"><b class="conv-k">{escape(k.replace("_", " ").capitalize())}</b>'
                        f'<span>{escape(str(v))}</span></div>')
        with card("sqlrules", "Rules for writing SQL", "Given to the AI analyst with every question"):
            st.html("<ul class='plain-list'>" + "".join(f"<li>{escape(r)}</li>" for r in schema.key_rules) + "</ul>")
    with right:
        p = catalog.access_policy()
        chips = lambda xs: "".join(f'<span class="qchip">{escape(x)}</span> ' for x in xs)  # noqa: E731
        with card("access", "Access and safety", "Enforced on every query, from dashboards and from the AI analyst"):
            st.html(f"""
<div class="access">
  <div class="acc-row"><div class="acc-k">Connection</div><div>Read-only DuckDB connection with external access disabled</div></div>
  <div class="acc-row"><div class="acc-k">Statements</div><div>A single <b>SELECT</b> (WITH allowed); everything else is rejected before it runs</div></div>
  <div class="acc-row"><div class="acc-k">Allowed tables</div><div>{chips(p['allowed_tables'])}</div></div>
  <div class="acc-row"><div class="acc-k">Blocked operations</div><div>{chips(p['forbidden_operations'])}</div></div>
  <div class="acc-row"><div class="acc-k">Blocked functions</div><div>{chips(p['forbidden_functions'])}</div></div>
  <div class="acc-row"><div class="acc-k">Limits</div><div>{p['timeout_s']:.0f} s timeout · up to {indian_int(p['max_rows'])} rows returned</div></div>
  <div class="acc-row"><div class="acc-k">Evaluation data</div><div>Not exposed: only the database and the catalog are read</div></div>
</div>""")
        with card("guard", "Try the SQL guard", "Paste any SQL to see whether it would be allowed. "
                  "It is only validated, never executed."):
            with st.form("guard_form", border=False):
                sql = st.text_area("SQL", key="guard_sql", height=110, label_visibility="collapsed",
                                   placeholder="e.g. DELETE FROM transactions   or   SELECT * FROM read_csv('secrets.csv')")
                go = st.form_submit_button("Check", icon=":material/policy:", type="primary")
            if go and sql.strip():
                r = catalog.check_sql(sql)
                if r.ok:
                    st.html('<div class="guard ok"><b>✓ Allowed</b> · read-only SELECT on allowed tables</div>')
                else:
                    items = "".join(f"<li>{escape(e)}</li>" for e in r.errors)
                    st.html(f'<div class="guard blocked"><b>✕ Blocked</b><ul>{items}</ul></div>')
            elif go:
                st.caption("Paste a query first.")


def render() -> None:
    summary_row()
    st.space("small")
    tabs = st.tabs([":material/table: Tables", ":material/hub: Relationships", ":material/functions: Metrics",
                    ":material/shield: Rules & access"])
    with tabs[0]:
        column_search()
        tables_tab()
    with tabs[1]:
        relationships_tab()
    with tabs[2]:
        metrics_tab()
    with tabs[3]:
        rules_tab()


try:
    render()
except QueryError as e:
    st.error(f"A catalog query failed: {e}", icon=":material/error:")
