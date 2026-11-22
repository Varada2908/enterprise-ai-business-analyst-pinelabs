"""Charts and small HTML pieces for the Data Quality page.

Severity is a state, so it uses the reserved status colours and always ships with an icon and a label.
"""
from __future__ import annotations

from html import escape

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .format import indian_int, inr, pct
from .theme import COLORS, FONT, STATUS

SEVERITY = {   # severity -> (colour, icon, label)
    "critical": (STATUS["critical"], "⬤", "Critical"),
    "high": (STATUS["serious"], "▲", "High"),
    "medium": (STATUS["warning"], "◆", "Medium"),
    "info": ("#94A3B8", "○", "Expected"),
}


def severity_label(sev: str) -> str:
    _, icon, label = SEVERITY[sev]
    return f"{icon} {label}"


def severity_pill(sev: str) -> str:
    return f'<span class="sev sev-{sev}">{escape(severity_label(sev))}</span>'


# ----------------------------------------------------------------------------- cleaning pipeline (R1-R4)
def pipeline_html(funnel: pd.DataFrame) -> str:
    """Raw rows -> rows removed by each rule -> valid transactions, as a step strip."""
    n = funnel["rows_remaining"].tolist()
    raw, valid = n[0], n[-1]
    steps = [("R1", "Duplicate IDs", n[0] - n[1]), ("R2", "Invalid status", n[1] - n[2]),
             ("R3", "Invalid amount", n[2] - n[3])]
    other = n[3] - n[4]       # e.g. rows whose payment method is unknown (dropped by the backend's join)
    if other:
        steps.append(("—", "Other exclusions", other))
    parts = [f'<div class="pl-node pl-raw"><div class="pl-k">Raw rows</div><div class="pl-v">{indian_int(raw)}</div></div>']
    for rid, name, removed in steps:
        parts.append('<div class="pl-arrow">→</div>')
        parts.append(f'<div class="pl-node pl-step"><div class="pl-k"><span class="qchip">{rid}</span> {escape(name)}</div>'
                     f'<div class="pl-v pl-minus">−{indian_int(removed)}</div>'
                     f'<div class="pl-s">{pct(removed / raw, 2)} of raw</div></div>')
    parts.append('<div class="pl-arrow">→</div>')
    parts.append(f'<div class="pl-node pl-valid"><div class="pl-k">Valid transactions (R4)</div>'
                 f'<div class="pl-v">{indian_int(valid)}</div><div class="pl-s">{pct(valid / raw, 2)} of raw kept</div></div>')
    return f'<div class="pipeline">{"".join(parts)}</div>'


# ----------------------------------------------------------------------------- affected rows per check
def checks_bar(df: pd.DataFrame) -> go.Figure:
    """Horizontal bars of affected rows per check, coloured by severity (expected / info checks left out)."""
    d = df[df["severity"] != "info"].sort_values("affected_rows").copy()
    colors = [SEVERITY[s][0] for s in d["severity"]]
    labels = [f"{indian_int(r)} · {pct(p, 2)}" if r else "0 · none found" for r, p in zip(d["affected_rows"], d["share"])]
    ticks = [f"{r} · {n}" for r, n in zip(d["rule"], d["name"])]
    fig = go.Figure(go.Bar(
        x=d["affected_rows"], y=ticks, orientation="h",
        marker=dict(color=colors, cornerradius=4), text=labels, textposition="outside", cliponaxis=False,
        textfont=dict(size=11, color=COLORS["text2"], family=FONT),
        customdata=d[["severity_text", "scope"]],
        hovertemplate="<b>%{y}</b><br>%{x:,} rows (%{customdata[1]})<br>Severity: %{customdata[0]}<extra></extra>",
    ))
    fig.update_xaxes(showgrid=True, tickformat="~s", range=[0, max(d["affected_rows"].max(), 1) * 1.35])
    fig.update_yaxes(showgrid=False, tickfont=dict(size=12, color=COLORS["ink"]))
    fig.update_layout(template="fintech", height=max(240, 34 * len(d) + 40), showlegend=False, bargap=0.38,
                      # room for the longest label: automargin measures before the web font loads and clipped it
                      margin=dict(l=12 + 7 * max((len(t) for t in ticks), default=0), r=24, t=8, b=8))
    return fig


# ----------------------------------------------------------------------------- weekly issue rates
def weekly_small_multiples(df: pd.DataFrame) -> go.Figure:
    """One small panel per issue: rows per 10,000 raw rows each week (normalised for volume)."""
    issues = sorted(df["issue"].unique())
    cols = 3
    rows = (len(issues) + cols - 1) // cols
    fig = make_subplots(rows=rows, cols=cols, subplot_titles=issues, shared_xaxes=True,
                        horizontal_spacing=0.07, vertical_spacing=0.2)
    for i, issue in enumerate(issues):
        part = df[df["issue"] == issue].sort_values("week_start")
        r, c = i // cols + 1, i % cols + 1
        avg = part["rows"].sum() * 10000.0 / part["raw_rows"].sum()
        fig.add_trace(go.Bar(
            x=part["week_start"], y=part["per_10k_rows"], marker=dict(color=COLORS["brand"], cornerradius=3),
            customdata=part[["rows", "raw_rows"]], name=issue,
            hovertemplate=(f"<b>{escape(issue)}</b><br>Week of %{{x|%d %b}}<br>%{{y:.1f}} per 10k rows"
                           "<br>%{customdata[0]:,} of %{customdata[1]:,} rows<extra></extra>"),
        ), row=r, col=c)
        fig.add_hline(y=avg, line=dict(color=COLORS["muted"], width=1, dash="dot"), row=r, col=c)
    fig.update_xaxes(tickformat="%d %b", nticks=4)
    fig.update_yaxes(nticks=4, rangemode="tozero")
    fig.update_annotations(font=dict(size=12, color=COLORS["ink"], family=FONT))
    fig.update_layout(template="fintech", height=200 * rows + 40, showlegend=False, bargap=0.3,
                      margin=dict(l=8, r=8, t=28, b=8))
    return fig


# ----------------------------------------------------------------------------- settlement gap
def settlement_gap_chart(df: pd.DataFrame, tolerance: float) -> go.Figure:
    """Daily net settlement gap beyond tolerance: under-payments below zero, over-payments above."""
    d = df.sort_values("transaction_date")
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=d["transaction_date"], y=d["under_paid"], name="Under-paid (merchant received less)",
        marker=dict(color=STATUS["critical"], cornerradius=3), customdata=d[["batches_out_of_tolerance"]],
        hovertemplate="%{x|%d %b %Y}<br>Under-paid ₹%{y:,.2f}<br>%{customdata[0]} batch(es) beyond tolerance<extra></extra>",
    ))
    if (d["over_paid"] != 0).any():
        fig.add_trace(go.Bar(
            x=d["transaction_date"], y=d["over_paid"], name="Over-paid",
            marker=dict(color="#2a78d6", cornerradius=3),
            hovertemplate="%{x|%d %b %Y}<br>Over-paid ₹%{y:,.2f}<extra></extra>"))
    fig.add_hline(y=0, line=dict(color=COLORS["border"], width=1))
    gap_days = d[d["batches_out_of_tolerance"] > 0]
    if not gap_days.empty:
        first = gap_days["transaction_date"].min()
        fig.add_vline(x=pd.Timestamp(first), line=dict(color=COLORS["muted"], width=1, dash="dot"))
        fig.add_annotation(x=pd.Timestamp(first), y=1, yref="paper", xanchor="left", yanchor="top", showarrow=False,
                           text=f" Gaps start {pd.Timestamp(first):%d %b}", font=dict(size=11, color=COLORS["muted"]))
    fig.update_yaxes(tickprefix="₹", tickformat="~s", title=f"Gap beyond ±₹{tolerance:,.2f} tolerance")
    fig.update_xaxes(tickformat="%d %b")
    fig.update_layout(template="fintech", height=300, barmode="relative", bargap=0.25,
                      showlegend=True, margin=dict(l=8, r=16, t=36, b=8))
    return fig


def impact_rows(impact: pd.DataFrame) -> str:
    """Raw-table vs valid-transaction metrics as comparison rows."""
    raw = impact.set_index("basis").loc["raw"]
    val = impact.set_index("basis").loc["valid"]
    rows = []
    for label, col, fmt in (("Terminal attempts", "terminal_attempts", indian_int),
                            ("Success rate", "success_rate", lambda v: pct(v, 2)),
                            ("TPV", "tpv", inr)):
        r, v = raw[col], val[col]
        if col == "success_rate":
            diff = f"{(r - v) * 100:+.2f} pp"
        else:
            diff = f"{(r / v - 1) * 100:+.1f}%" if v else "–"
        rows.append(f'<div class="imp-row"><div class="imp-k">{label}</div>'
                    f'<div class="imp-raw">{fmt(r)}</div><div class="imp-val">{fmt(v)}</div>'
                    f'<div class="imp-diff">{diff}</div></div>')
    head = ('<div class="imp-row imp-head"><div class="imp-k">Metric</div><div class="imp-raw">Raw table</div>'
            '<div class="imp-val">Valid (R1–R4)</div><div class="imp-diff">Raw vs valid</div></div>')
    return f'<div class="impact">{head}{"".join(rows)}</div>'
