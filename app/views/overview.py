"""Overview: payments health at a glance (fixed SQL through the backend's safe executor)."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from components import charts
from components.cards import card, empty_state
from components.format import indian_int, inr, ms, pct, pp, rel, short_date
from components.incidents import render_incidents
from components.layout import page_header
from components.kpi_cards import KPI, delta_props, render_kpis
from components.theme import PLOTLY_CONFIG, method_label
from services import metrics
from services.backend import QueryError

ALL = "All channels"
CHANNEL_LABELS = {ALL: "All", "ONLINE": "Online", "POS": "POS"}


# ----------------------------------------------------------------------------- filters
def _defaults(first: date, last: date) -> dict:
    return {"ov_dates": (first, last), "ov_methods": list(metrics.METHOD_ORDER), "ov_channel": ALL}


def _reset(first: date, last: date) -> None:
    for k, v in _defaults(first, last).items():
        st.session_state[k] = v


def filter_bar(first: date, last: date) -> metrics.Filters | None:
    for k, v in _defaults(first, last).items():
        st.session_state.setdefault(k, v)
    with st.container(border=True, key="filters"):
        c1, c2, c3, c4 = st.columns([2.75, 4.25, 1.9, 0.32], gap="small", vertical_alignment="bottom")
        with c1:
            dates = st.date_input("Date range", min_value=first, max_value=last, key="ov_dates", format="DD/MM/YYYY")
        with c2:
            methods = st.pills("Payment method", options=list(metrics.METHOD_ORDER), selection_mode="multi",
                               format_func=method_label, key="ov_methods")
        with c3:
            channel = st.segmented_control("Channel", options=list(CHANNEL_LABELS), format_func=CHANNEL_LABELS.get,
                                           key="ov_channel")
        with c4:
            st.button(":material/restart_alt:", help="Reset filters", on_click=_reset, args=(first, last),
                      type="tertiary", width="stretch")

    if not isinstance(dates, (tuple, list)) or len(dates) != 2:
        st.info("Pick an end date to complete the date range.", icon=":material/date_range:")
        return None
    if not methods:
        st.info("Select at least one payment method.", icon=":material/filter_alt:")
        return None
    channels = metrics.CHANNELS if channel in (None, ALL) else (channel,)
    ordered = tuple(m for m in metrics.METHOD_ORDER if m in methods)
    return metrics.Filters(dates[0], dates[1], ordered, channels)


# ----------------------------------------------------------------------------- KPI row
def kpi_row(f: metrics.Filters) -> tuple[pd.Series, metrics.QueryOutcome]:
    k_out = metrics.kpis(f)
    k = k_out.df.iloc[0]
    cur = prev = None
    windows = metrics.last_week_windows(f)
    if windows:
        cur = metrics.kpis(windows[0]).df.iloc[0]
        prev = metrics.kpis(windows[1]).df.iloc[0]

    def diff(col: str, relative: bool = False):
        if cur is None or pd.isna(cur[col]) or pd.isna(prev[col]) or (relative and not prev[col]):
            return None
        return (cur[col] / prev[col] - 1) if relative else (cur[col] - prev[col])

    def kpi(label, value, col, higher_is_better, relative, flat, help_):
        change = diff(col, relative)
        direction, tone = delta_props(change, higher_is_better, flat)
        delta = None if change is None else (rel(change) if relative else pp(change))
        return KPI(label, value, help_, delta, direction, tone)

    render_kpis([
        kpi("Success rate", pct(k["success_rate"]), "success_rate", True, False, 0.0005,
            "SUCCESS / (SUCCESS + FAILED + USER_CANCELLED) on valid transactions; PENDING excluded."),
        kpi("TPV", inr(k["tpv"]), "tpv", True, True, 0.005,
            "Sum of amount over SUCCESS transactions (INR)."),
        kpi("Terminal attempts", indian_int(k["terminal_attempts"]), "terminal_attempts", True, True, 0.005,
            "Attempts with a final status: SUCCESS, FAILED or USER_CANCELLED."),
        kpi("Order conversion", pct(k["order_conversion_rate"]), "order_conversion_rate", True, False, 0.0005,
            "Share of orders (all attempts incl. retries) that ended with a SUCCESS, among fully resolved orders."),
        kpi("Failure rate", pct(k["failure_rate"]), "failure_rate", False, False, 0.0005,
            "FAILED / terminal attempts. User cancellations are counted separately."),
        kpi("p95 response time", ms(k["latency_p95_ms"]), "latency_p95_ms", False, True, 0.01,
            "95th percentile response time of SUCCESS and FAILED attempts."),
    ])
    return k, k_out


# ----------------------------------------------------------------------------- page
def header(summary: pd.Series) -> None:
    as_of = pd.Timestamp(summary["data_as_of"])
    page_header("Analytics", "Payments overview",
                "Valid transactions only (deduplicated, valid status and amount) · "
                f"data as of {as_of.day} {as_of:%b %Y, %H:%M} IST")


def scorecard(df: pd.DataFrame) -> None:
    t = df.copy()
    t.insert(0, "Method", t.pop("method_code").map(method_label))
    t["tpv_cr"] = t.pop("tpv") / 1e7
    st.dataframe(
        t[["Method", "attempt_share", "success_rate", "failure_rate", "cancellation_rate", "tpv_cr", "latency_p95_ms"]],
        hide_index=True, width="stretch", height=36 * (len(t) + 1) + 4,
        column_config={
            # fixed widths so all seven columns fit a half-width card without horizontal scrolling
            "Method": st.column_config.TextColumn("Method", width=88, pinned=True),
            "attempt_share": st.column_config.NumberColumn("Share", format="percent", width=58),
            "success_rate": st.column_config.NumberColumn("Success", format="percent", width=64),
            "failure_rate": st.column_config.NumberColumn("Failed", format="percent", width=56),
            "cancellation_rate": st.column_config.NumberColumn("Cancel", format="percent", width=58),
            "tpv_cr": st.column_config.NumberColumn("TPV ₹Cr", format="%.1f", width=62),
            "latency_p95_ms": st.column_config.NumberColumn("p95 ms", format="%d", width=58),
        },
    )


def render() -> None:
    summary = metrics.dataset_summary()
    first, last = pd.Timestamp(summary["first_date"]).date(), pd.Timestamp(summary["last_date"]).date()
    header(summary)
    f = filter_bar(first, last)
    if f is None:
        return

    with st.spinner("Running validated queries…"):
        k, k_out = kpi_row(f)
        trend = metrics.weekly_trend(f)
        methods = metrics.by_method(f)
        mix = metrics.payment_mix(f)
        reasons = metrics.failure_reasons(f)
        incidents = metrics.incidents(f.single_method)

    if not k["terminal_attempts"]:
        empty_state("No transactions match these filters.")
        return

    st.space("small")
    left, right = st.columns([2, 1], gap="medium")
    with left:
        with card("trend", "Weekly success rate and volume",
                  "Monday–Sunday weeks · lighter columns are partial weeks", [trend]):
            st.plotly_chart(charts.weekly_trend(trend.df), config=PLOTLY_CONFIG, theme=None, key="ch_trend")
    with right:
        scope = f" · {method_label(f.single_method)} only" if f.single_method else ""
        with card("incidents", "Detected incidents",
                  f"Success-rate breaks over the full history{scope}, found by change-date detection", [incidents]):
            with st.container(height=372, border=False, key="incident_list"):
                render_incidents(incidents.df, limit=8)

    c1, c2 = st.columns(2, gap="medium")
    with c1:
        with card("methods", "Success rate by payment method", "Dotted line = overall success rate", [methods]):
            if len(methods.df) > 1:
                st.plotly_chart(charts.method_success(methods.df, k["success_rate"]), config=PLOTLY_CONFIG,
                                theme=None, key="ch_methods")
            else:
                empty_state("Select two or more methods to compare them.")
    with c2:
        with card("mix", "Payment mix", "Share of terminal attempts by method, per week", [mix]):
            st.plotly_chart(charts.payment_mix(mix.df, f.methods), config=PLOTLY_CONFIG, theme=None, key="ch_mix")

    c3, c4 = st.columns(2, gap="medium")
    with c3:
        missing = reasons.df.loc[reasons.df["failure_code"] == "(missing)", "failed_attempts"].sum()
        with card("reasons", "Top failure reasons",
                  "FAILED attempts by code · % of coded failures", [reasons]):
            if reasons.df.empty:
                empty_state("No failed attempts in this selection.")
            else:
                st.plotly_chart(charts.failure_reasons(reasons.df), config=PLOTLY_CONFIG, theme=None, key="ch_reasons")
                if missing:
                    st.caption(f"{indian_int(missing)} FAILED attempts have no failure code and are not shown "
                               "(a known data-quality issue; see Data Quality).")
    with c4:
        with card("scorecard", "Method scorecard", "Rates use terminal attempts as the denominator",
                  [methods, k_out]):
            scorecard(methods.df)

    st.caption(f"Range {short_date(f.start)} – {short_date(f.end)} · KPI change chips compare the last 7 days of "
               "the range with the 7 days before.")


try:
    render()
except QueryError as e:
    st.error(f"A dashboard query failed: {e}", icon=":material/error:")
