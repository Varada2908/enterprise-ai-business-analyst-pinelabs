"""Plotly figures for the dashboard.

Mark rules: one y-axis per chart (two measures -> stacked subplots, never a dual axis), 2px lines with
8px markers, bars no thicker than ~24px with labels at the tip, hairline grid, legend for 2+ series,
text in ink colours (never the series colour), and a hover tooltip on every mark.
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .format import indian_int, pct
from .theme import COLORS, FONT, METHOD_COLORS, method_label


def _base(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(template="fintech", height=height, hovermode="closest")
    return fig


# ----------------------------------------------------------------------------- weekly trend
def weekly_trend(df: pd.DataFrame) -> go.Figure:
    """Success rate (line, top) and terminal attempts (columns, bottom) on a shared week axis."""
    d = df.copy()
    d["partial"] = d["days"] < 7
    d["week_label"] = d["week_start"].map(lambda w: f"Week of {w.day} {w:%b %Y}")
    d["note"] = d.apply(lambda r: f" · partial week ({int(r['days'])} days)" if r["partial"] else "", axis=1)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.64, 0.36], vertical_spacing=0.07)
    fig.add_trace(go.Scatter(
        x=d["week_start"], y=d["success_rate"], mode="lines+markers", name="Success rate",
        line=dict(color=COLORS["brand"], width=2), marker=dict(size=8, color=COLORS["brand"],
                                                               line=dict(color="#FFFFFF", width=2)),
        customdata=d[["week_label", "note"]],
        hovertemplate="<b>%{customdata[0]}</b>%{customdata[1]}<br>Success rate %{y:.2%}<extra></extra>",
    ), row=1, col=1)
    fig.add_trace(go.Bar(
        x=d["week_start"], y=d["terminal_attempts"], name="Terminal attempts",
        marker=dict(color=[COLORS["bar_partial"] if p else COLORS["bar_neutral"] for p in d["partial"]],
                    cornerradius=4),
        customdata=d[["week_label", "note"]],
        hovertemplate="<b>%{customdata[0]}</b>%{customdata[1]}<br>Terminal attempts %{y:,}<extra></extra>",
    ), row=2, col=1)

    lo, hi = d["success_rate"].min(), d["success_rate"].max()
    pad = max((hi - lo) * 0.25, 0.01)
    fig.update_yaxes(tickformat=".0%", range=[lo - pad, min(1.0, hi + pad)], nticks=5, row=1, col=1)
    fig.update_yaxes(tickformat="~s", nticks=3, row=2, col=1)
    fig.update_xaxes(tickformat="%d %b", row=2, col=1)
    fig.update_layout(showlegend=False, bargap=0.45, margin=dict(l=8, r=16, t=22, b=8))
    fig.add_annotation(text="Success rate", xref="paper", yref="paper", x=0, y=1.0, xanchor="left", yanchor="bottom",
                       showarrow=False, font=dict(size=11, color=COLORS["muted"]))
    fig.add_annotation(text="Terminal attempts", xref="paper", yref="paper", x=0, y=0.34, xanchor="left",
                       yanchor="bottom", showarrow=False, font=dict(size=11, color=COLORS["muted"]))
    return _base(fig, 360)


# ----------------------------------------------------------------------------- success rate by method
def method_success(df: pd.DataFrame, overall: float | None) -> go.Figure:
    """Dot plot: success rate per payment method, with the overall rate as a reference line."""
    d = df.sort_values("success_rate").copy()
    d["label"] = d["method_code"].map(method_label)
    fig = go.Figure(go.Scatter(
        x=d["success_rate"], y=d["label"], mode="markers+text",
        marker=dict(size=14, color=[METHOD_COLORS.get(m, COLORS["muted"]) for m in d["method_code"]],
                    line=dict(color="#FFFFFF", width=2)),
        text=[pct(v) for v in d["success_rate"]], textposition="middle right",
        textfont=dict(size=12, color=COLORS["ink"], family=FONT),
        customdata=d[["terminal_attempts", "failure_rate", "cancellation_rate"]],
        hovertemplate=("<b>%{y}</b><br>Success rate %{x:.2%}<br>Terminal attempts %{customdata[0]:,}"
                       "<br>Failure rate %{customdata[1]:.2%}<br>Cancellation rate %{customdata[2]:.2%}<extra></extra>"),
        cliponaxis=False,
    ))
    lo, hi = d["success_rate"].min(), d["success_rate"].max()
    pad = max((hi - lo) * 0.18, 0.01)
    fig.update_xaxes(tickformat=".0%", range=[lo - pad, hi + pad * 2.2], showgrid=True, nticks=6)
    fig.update_yaxes(showgrid=False, tickfont=dict(size=12, color=COLORS["ink"]))
    if overall is not None:
        fig.add_vline(x=overall, line=dict(color=COLORS["muted"], width=1, dash="dot"))
        fig.add_annotation(x=overall, y=1.0, yref="paper", yanchor="bottom", showarrow=False,
                           text=f"Overall {pct(overall)}", font=dict(size=11, color=COLORS["muted"]))
    fig.update_layout(showlegend=False, margin=dict(l=8, r=16, t=24, b=8))
    return _base(fig, 300)


# ----------------------------------------------------------------------------- payment mix
def payment_mix(df: pd.DataFrame, method_order: tuple[str, ...]) -> go.Figure:
    """100% stacked weekly columns: share of terminal attempts by payment method."""
    fig = go.Figure()
    for m in method_order:
        part = df[df["method_code"] == m]
        if part.empty:
            continue
        fig.add_trace(go.Bar(
            x=part["week_start"], y=part["share"], name=method_label(m),
            marker=dict(color=METHOD_COLORS.get(m, COLORS["muted"]), line=dict(color="#FFFFFF", width=1.5)),
            customdata=part[["terminal_attempts"]],
            hovertemplate=f"<b>{method_label(m)}</b> · week of %{{x|%d %b}}<br>Share %{{y:.1%}}"
                          f"<br>Terminal attempts %{{customdata[0]:,}}<extra></extra>",
        ))
    fig.update_layout(barmode="stack", bargap=0.3, legend=dict(traceorder="normal"),
                      margin=dict(l=8, r=16, t=36, b=8))
    fig.update_yaxes(tickformat=".0%", range=[0, 1], nticks=5)
    fig.update_xaxes(tickformat="%d %b")
    return _base(fig, 300)


# ----------------------------------------------------------------------------- failure reasons
def failure_reasons(df: pd.DataFrame, top_n: int = 10) -> go.Figure:
    """Horizontal bars, single hue: the most common coded failure reasons."""
    d = df[df["failure_code"] != "(missing)"].head(top_n).iloc[::-1].copy()
    d["tick"] = d["failure_code"].str.replace("_", " ").str.title()
    d["text"] = [f"{indian_int(n)} · {pct(s, 1)}" for n, s in zip(d["failed_attempts"], d["share_of_coded_failures"])]
    fig = go.Figure(go.Bar(
        x=d["failed_attempts"], y=d["tick"], orientation="h",
        marker=dict(color=COLORS["brand"], cornerradius=4),
        text=d["text"], textposition="outside", textfont=dict(size=11, color=COLORS["text2"]), cliponaxis=False,
        customdata=d[["description", "failure_category", "responsible_party", "share_of_coded_failures"]],
        hovertemplate=("<b>%{y}</b><br>%{customdata[0]}<br>Category %{customdata[1]} · responsible: %{customdata[2]}"
                       "<br>Failed attempts %{x:,} (%{customdata[3]:.1%} of coded failures)<extra></extra>"),
    ))
    fig.update_xaxes(showgrid=True, tickformat="~s", range=[0, d["failed_attempts"].max() * 1.32] if len(d) else None)
    fig.update_yaxes(showgrid=False, tickfont=dict(size=12, color=COLORS["ink"]))
    # reserve room for the longest label: automargin measures before the web font loads and clipped it
    left = 12 + 7 * int(d["tick"].str.len().max()) if len(d) else 8
    fig.update_layout(showlegend=False, bargap=0.38, margin=dict(l=left, r=24, t=8, b=8))
    return _base(fig, max(220, 34 * len(d) + 40))

