"""Rendering of analyst answers: summary, verified findings, hypotheses, caveats, charts and evidence."""
from __future__ import annotations

import numbers
import re
from html import escape
from typing import Callable

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ai_analyst.investigator import EvidenceQuery, Investigation
from services.analyst import MODE_LABELS, Turn, friendly_error, quick_df
from services.backend import decimals_to_float

from .format import indian_int, inr, ms, pct
from .kpi_cards import KPI, render_kpis
from .theme import COLORS, FONT, METHOD_COLORS, PLOTLY_CONFIG, method_label

CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
_RATE = re.compile(r"(rate|share|pct|percent|ratio|conversion|sr)(_|$)|^sr_|_sr$", re.I)
_MONEY = re.compile(r"(tpv|amount|value|revenue|volume_inr|gmv|settle|gap|inr|fee)", re.I)
_TIME = re.compile(r"(week|date|day|month|hour|time|period)", re.I)
_LATENCY = re.compile(r"(_ms$|latency|response_time)", re.I)


def md_safe(text: str) -> str:
    """Model text shown with st.markdown: stop '$' amounts being read as LaTeX."""
    return (text or "").replace("$", r"\$")


# ----------------------------------------------------------------------------- value formatting
def _is_rate(col: str, s: pd.Series) -> bool:
    v = pd.to_numeric(s, errors="coerce").dropna()
    return bool(_RATE.search(col)) and not v.empty and v.between(-1, 1).all()


def fmt_value(col: str, v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "–"
    if isinstance(v, numbers.Real) and not isinstance(v, bool):
        if _RATE.search(col) and -1 <= v <= 1:
            return pct(v)
        if _LATENCY.search(col):
            return ms(v)
        if _MONEY.search(col):
            return inr(v)
        if float(v).is_integer():
            return indian_int(v)
        return f"{v:,.4g}" if abs(v) < 1 else f"{v:,.2f}"
    return str(v)


def column_config(df: pd.DataFrame) -> dict:
    cfg = {}
    for c in df.columns:
        if pd.api.types.is_numeric_dtype(df[c]):
            if _is_rate(c, df[c]):
                cfg[c] = st.column_config.NumberColumn(c, format="percent")
            elif _MONEY.search(c):
                cfg[c] = st.column_config.NumberColumn(c, format="₹ %.2f")
            else:
                cfg[c] = st.column_config.NumberColumn(c, format="localized")
    return cfg


# ----------------------------------------------------------------------------- automatic charts
def _time_col(df: pd.DataFrame) -> str | None:
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            return c
    for c in df.columns:
        if _TIME.search(c) and not pd.api.types.is_numeric_dtype(df[c]):
            parsed = pd.to_datetime(df[c], errors="coerce")
            if parsed.notna().mean() > 0.9:
                return c
    return None


def _pick_measure(df: pd.DataFrame, exclude: set[str]) -> str | None:
    nums = [c for c in df.columns if c not in exclude and pd.api.types.is_numeric_dtype(df[c])
            and not c.lower().endswith("_id")]
    if not nums:
        return None
    for pattern in (_RATE, _MONEY):
        for c in nums:
            if pattern.search(c):
                return c
    return nums[-1]


def _colors_for(values) -> list[str]:
    vals = list(values)
    if all(v in METHOD_COLORS for v in vals):
        return [METHOD_COLORS[v] for v in vals]
    return [CATEGORICAL[i % len(CATEGORICAL)] for i in range(len(vals))]


def auto_chart(df: pd.DataFrame) -> go.Figure | None:
    """A line chart for time series, a bar chart for a ranking; None when a table reads better."""
    df = decimals_to_float(df.copy())
    if len(df) < 2:
        return None
    t = _time_col(df)
    cats = [c for c in df.columns if c != t and not pd.api.types.is_numeric_dtype(df[c])]
    if t:
        y = _pick_measure(df, {t})
        if y is None or len(df) < 3:
            return None
        d = df.assign(**{t: pd.to_datetime(df[t])}).sort_values(t)
        fig = go.Figure()
        series = cats[0] if len(cats) == 1 and 2 <= d[cats[0]].nunique() <= 6 else None
        groups = list(d.groupby(series, sort=True)) if series else [(None, d)]
        colors = _colors_for([g for g, _ in groups]) if series else [COLORS["brand"]]
        for (name, part), color in zip(groups, colors):
            label = method_label(name) if name in METHOD_COLORS else (str(name) if name is not None else y)
            fig.add_trace(go.Scatter(
                x=part[t], y=part[y], mode="lines+markers", name=label,
                line=dict(color=color, width=2), marker=dict(size=8, color=color, line=dict(color="#fff", width=1.5)),
                hovertemplate=f"<b>{escape(label)}</b><br>%{{x|%d %b %Y}}<br>{escape(y)}: %{{y:{'.2%' if _is_rate(y, d[y]) else ',.2f'}}}<extra></extra>",
            ))
        if _is_rate(y, d[y]):
            fig.update_yaxes(tickformat=".1%")
        fig.update_layout(template="fintech", height=320, showlegend=bool(series),
                          margin=dict(l=8, r=16, t=36 if series else 12, b=8),
                          yaxis_title=y, hovermode="x unified" if series else "closest")
        return fig

    if len(cats) >= 1 and len(df) <= 25:
        cat = cats[0]
        y = _pick_measure(df, set())
        if y is None or df[cat].nunique() != len(df):
            return None
        d = df.sort_values(y, ascending=True)
        rate = _is_rate(y, d[y])
        labels = [method_label(v) if v in METHOD_COLORS else str(v) for v in d[cat]]
        colors = _colors_for(d[cat]) if all(v in METHOD_COLORS for v in d[cat]) else COLORS["brand"]
        lo, hi = d[y].min(), d[y].max()
        hover = f"<b>%{{y}}</b><br>{escape(y)}: %{{x:{'.2%' if rate else ',.2f'}}}<extra></extra>"
        text = [fmt_value(y, v) for v in d[y]]
        textfont = dict(size=11, color=COLORS["text2"], family=FONT)
        if rate:
            # rates are compared, not summed: a dot plot can zoom in honestly (a bar must start at zero)
            fig = go.Figure(go.Scatter(
                x=d[y], y=labels, mode="markers+text", text=text, textposition="middle right", textfont=textfont,
                marker=dict(size=13, color=colors, line=dict(color="#fff", width=2)), cliponaxis=False,
                hovertemplate=hover))
            pad = max((hi - lo) * 0.15, 0.005)
            fig.update_xaxes(range=[lo - pad, hi + pad * 2.5])
        else:
            fig = go.Figure(go.Bar(
                x=d[y], y=labels, orientation="h", marker=dict(color=colors, cornerradius=4),
                text=text, textposition="outside", cliponaxis=False, textfont=textfont, hovertemplate=hover))
            fig.update_xaxes(range=[min(0, lo * 1.1), hi * 1.25 if hi > 0 else 1])
        if rate:
            fig.update_xaxes(tickformat=".0%")
        fig.update_yaxes(showgrid=False, tickfont=dict(size=12, color=COLORS["ink"]))
        fig.update_xaxes(showgrid=True, title=y)
        fig.update_layout(template="fintech", height=max(200, 32 * len(d) + 70), showlegend=False, bargap=0.35)
        return fig
    return None


def headline(df: pd.DataFrame) -> str | None:
    """One-sentence summary computed from the result rows (no model involved)."""
    df = decimals_to_float(df.copy())
    if len(df) < 2:
        return None
    t = _time_col(df)
    if t:
        y = _pick_measure(df, {t})
        cats = [c for c in df.columns if c != t and not pd.api.types.is_numeric_dtype(df[c])]
        if y is None or cats:
            return None
        d = df.assign(**{t: pd.to_datetime(df[t])}).sort_values(t)
        first, last, low = d.iloc[0], d.iloc[-1], d.loc[d[y].idxmin()]
        name = y.replace("_", " ")
        return (f"{name.capitalize()} went from {fmt_value(y, first[y])} ({first[t]:%d %b}) to "
                f"{fmt_value(y, last[y])} ({last[t]:%d %b}); the lowest point was {fmt_value(y, low[y])} "
                f"({low[t]:%d %b}).")
    cats = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])]
    y = _pick_measure(df, set())
    if not cats or y is None or df[cats[0]].nunique() != len(df):
        return None
    d = df.sort_values(y, ascending=False)
    lbl = lambda v: method_label(v) if v in METHOD_COLORS else str(v)  # noqa: E731
    top, bottom = d.iloc[0], d.iloc[-1]
    name = y.replace("_", " ")
    return (f"Highest {name}: {lbl(top[cats[0]])} ({fmt_value(y, top[y])}); lowest: {lbl(bottom[cats[0]])} "
            f"({fmt_value(y, bottom[y])}), across {len(d)} rows.")


def single_row_tiles(df: pd.DataFrame) -> bool:
    """A one-row result reads best as KPI tiles."""
    df = decimals_to_float(df.copy())
    if len(df) != 1 or len(df.columns) > 6:
        return False
    row = df.iloc[0]
    render_kpis([KPI(c.replace("_", " ").capitalize(), fmt_value(c, row[c])) for c in df.columns])
    return True


def show_result(df: pd.DataFrame, key: str, chart: bool = True) -> None:
    if df.empty:
        st.html('<div class="empty-state">The query ran but returned no rows.</div>')
        return
    if single_row_tiles(df):
        return
    fig = auto_chart(df) if chart else None
    if fig is not None:
        tab_chart, tab_table = st.tabs([":material/bar_chart: Chart", ":material/table: Table"])
        with tab_chart:
            st.plotly_chart(fig, config=PLOTLY_CONFIG, theme=None, key=f"fig_{key}")
        with tab_table:
            st.dataframe(df, hide_index=True, width="stretch", column_config=column_config(df))
    else:
        st.dataframe(df, hide_index=True, width="stretch", column_config=column_config(df),
                     height=min(400, 36 * (len(df) + 1) + 4))


# ----------------------------------------------------------------------------- pieces
def _meta(turn: Turn, extra: list[str]) -> None:
    kind = "Deep investigation" if turn.kind == "deep" else "Quick answer"
    bits = [f'<span class="tag tag-{turn.kind}">{kind}</span>', escape(turn.route_reason),
            escape(turn.model), f"{turn.elapsed_s:.0f} s", *map(escape, extra)]
    st.html(f'<div class="meta">{" · ".join(bits)}</div>')


def _chips(ids: list[str]) -> str:
    return "".join(f'<span class="qchip">{escape(i)}</span>' for i in ids)


def _check_badge(status: str) -> str:
    return {
        "verified": '<span class="vbadge v-good">✓ Numbers verified</span>',
        "partially-verified": '<span class="vbadge v-warn">! Some numbers not found in results</span>',
        "no-numbers": '<span class="vbadge v-neutral">No numbers to check</span>',
    }.get(status, "")


def _answer_check(inv: Investigation) -> str:
    """Result of the backend's answer check (numbers vs verified findings, driver / cause wording)."""
    issues = getattr(inv, "answer_issues", [])
    revised = getattr(inv, "answer_revised", False)
    if issues:
        badge = '<span class="vbadge v-warn">! Answer check: not fully supported by the findings</span>'
    elif getattr(inv, "answer_numbers_verified", []):
        badge = '<span class="vbadge v-good">✓ Numbers match the verified findings</span>'
    else:
        badge = ""
    if revised:
        badge += '<span class="vbadge v-neutral">Rewritten after the answer check</span>'
    if not badge:
        return ""
    return (f'<div class="f-foot">{badge}</div>'
            + "".join(f'<div class="f-unv">{escape(i)}</div>' for i in issues))


def render_error(turn: Turn) -> None:
    title, advice = friendly_error(turn.error)
    st.html(f'<div class="answer-error"><div class="ae-title">{escape(title)}</div>'
            f'<div class="ae-body">{escape(advice)}</div></div>')


def _query_block(q: EvidenceQuery, key: str) -> None:
    status = {"ok": "✓ ran", "rejected": "✕ rejected by validator", "error": "✕ DuckDB error"}.get(q.status, q.status)
    rows = f"{q.row_count:,} rows{' (truncated)' if q.truncated else ''} · {q.elapsed_ms:,} ms" if q.ok else ""
    st.html(f'<div class="qhead"><span class="qchip">{escape(q.id)}</span><b>{escape(q.purpose)}</b>'
            f'<span class="qstatus q-{q.status}">{status}</span><span class="qrows">{rows}</span></div>')
    st.code(q.sql, language="sql", wrap_lines=True)
    if q.ok:
        df = decimals_to_float(pd.DataFrame(q.rows, columns=q.columns))
        st.dataframe(df, hide_index=True, width="stretch", column_config=column_config(df),
                     height=min(320, 36 * (len(df) + 1) + 4), key=f"ev_{key}_{q.id}")
    elif q.error:
        st.caption(f"Error: {q.error}")


# ----------------------------------------------------------------------------- quick answer
def render_quick(turn: Turn, key: str) -> None:
    r = turn.quick
    repairs = sum(1 for a in r.attempts if a.stage != "ok")
    _meta(turn, [f"{r.row_count:,} rows", *([f"{repairs} auto-repair(s)"] if repairs else [])])
    if not r.success:
        render_error(turn)
        if r.sql:
            with st.expander("SQL that was tried", icon=":material/code:"):
                for i, a in enumerate(r.attempts, 1):
                    st.markdown(f"Attempt {i} · {a.stage}" + (f" — {a.error}" if a.error else ""))
                    st.code(a.sql, language="sql", wrap_lines=True)
        return
    df = quick_df(r)
    head = headline(df)
    body = (f'<div class="ac-body">{escape(head)}</div>' if head else "") + \
        f'<div class="ac-how"><b>How it was answered:</b> {escape(r.explanation)}</div>'
    st.html(f'<div class="answer-card"><div class="ac-label">Answer</div>{body}</div>')
    show_result(df, key)
    if r.truncated:
        st.caption(f"Showing the first {r.row_count:,} rows; the result was truncated.")
    if r.assumptions:
        _caveats("Assumptions", r.assumptions)
    with st.expander("SQL and execution details", icon=":material/code:"):
        st.caption("Generated by the model · passed the SQL safety validator · run read-only on DuckDB")
        st.code(r.sql, language="sql", wrap_lines=True)
        if len(r.attempts) > 1:
            st.markdown("**Earlier attempts (repaired automatically)**")
            for i, a in enumerate(r.attempts[:-1], 1):
                st.markdown(f"Attempt {i} · failed at {a.stage}: {a.error}")
                st.code(a.sql, language="sql", wrap_lines=True)
        st.caption(f"Tokens: {r.input_tokens:,} in / {r.output_tokens:,} out")


def _caveats(title: str, items: list[str]) -> None:
    lis = "".join(f"<li>{escape(c)}</li>" for c in items)
    st.html(f'<div class="caveats"><div class="cv-title">{escape(title)}</div><ul>{lis}</ul></div>')


# ----------------------------------------------------------------------------- deep investigation
def _key_chart(inv: Investigation, key: str) -> None:
    """Chart the first evidence query behind a finding that has a sensible chart."""
    seen: list[str] = []
    for f in inv.findings:
        seen += [e for e in f.evidence if e not in seen]
    for qid in seen:
        q = inv.query(qid)
        if q is None or not q.ok or q.row_count < 2:
            continue
        fig = auto_chart(pd.DataFrame(q.rows, columns=q.columns))
        if fig is not None:
            st.html(f'<div class="section-title">Key evidence <span class="qchip">{escape(q.id)}</span> '
                    f'<span class="muted">{escape(q.purpose)}</span></div>')
            st.plotly_chart(fig, config=PLOTLY_CONFIG, theme=None, key=f"kc_{key}")
            return


def render_deep(turn: Turn, key: str) -> None:
    inv = turn.deep
    ok_q = sum(1 for q in inv.queries if q.ok)
    _meta(turn, [f"{inv.rounds} round(s)", f"{ok_q}/{len(inv.queries)} queries ran"])
    if not inv.success:
        render_error(turn)
        if inv.queries:
            _evidence(inv, key)
        return

    st.html(f'<div class="answer-card"><div class="ac-label">Answer</div>'
            f'<div class="ac-body">{escape(inv.answer)}</div>{_answer_check(inv)}</div>')

    if inv.findings:
        cards = []
        for f in inv.findings:
            notes = getattr(f, "claim_notes", [])
            unverified = (f'<div class="f-unv">Not found in results: {escape(", ".join(f.numbers_unverified))}</div>'
                          if f.numbers_unverified else "")
            unverified += "".join(f'<div class="f-unv">{escape(n)}</div>' for n in notes)
            claim = '<span class="vbadge v-warn">! Causal wording</span>' if notes else ""
            cards.append(f'<div class="finding"><div class="f-stmt">{escape(f.statement)}</div>'
                         f'<div class="f-foot">{_check_badge(f.check_status)}{claim}<span class="f-ev">Evidence '
                         f'{_chips(f.evidence)}</span></div>{unverified}</div>')
        st.html(f'<div class="section-title">Verified findings <span class="count">{len(inv.findings)}</span>'
                f'<span class="muted">Facts shown directly by the cited query results</span></div>'
                f'<div class="findings">{"".join(cards)}</div>')

    _key_chart(inv, key)

    if inv.hypotheses:
        cards = []
        for h in inv.hypotheses:
            reason = ("it states a cause the cited results do not show" if h.supporting_evidence
                      else "it did not cite a query that ran")
            demoted = (f'<div class="h-demoted">Moved from findings: {reason}.</div>'
                       if h.demoted_from_finding else "")
            support = f'<span class="f-ev">Consistent with {_chips(h.supporting_evidence)}</span>' \
                if h.supporting_evidence else ""
            cards.append(
                f'<div class="hypothesis"><div class="h-tag">Hypothesis · not proven</div>'
                f'<div class="f-stmt">{escape(h.statement)}</div>'
                + (f'<div class="h-row"><b>Why plausible:</b> {escape(h.rationale)}</div>' if h.rationale else "")
                + (f'<div class="h-row"><b>How to verify:</b> {escape(h.how_to_verify)}</div>' if h.how_to_verify else "")
                + f'{demoted}<div class="f-foot">{support}</div></div>')
        st.html(f'<div class="section-title">Hypotheses <span class="count">{len(inv.hypotheses)}</span>'
                f'<span class="muted">Possible explanations the data does not prove</span></div>'
                f'<div class="findings">{"".join(cards)}</div>')

    if inv.caveats:
        _caveats("Caveats", inv.caveats)
    _evidence(inv, key)


def _evidence(inv: Investigation, key: str) -> None:
    with st.expander(f"SQL evidence · {len(inv.queries)} queries", icon=":material/database:"):
        st.caption("Every query was planned by the model, checked by the SQL safety validator and run read-only. "
                   "Numbers in findings are matched against these results.")
        cited = {e for f in inv.findings for e in f.evidence}
        ordered = sorted(inv.queries, key=lambda q: (q.id not in cited, q.round, q.id))
        for q in ordered:
            _query_block(q, key)
        if inv.round_notes:
            st.markdown("**Analyst notes per round**")
            for i, n in enumerate(inv.round_notes, 1):
                st.caption(f"Round {i}: {n}")
        st.caption(f"Tokens: {inv.input_tokens:,} in / {inv.output_tokens:,} out")


# ----------------------------------------------------------------------------- whole turn
def render_turn(turn: Turn, key: str, is_last: bool, ask: Callable[[str, str], None]) -> None:
    with st.chat_message("user", avatar=":material/person:"):
        st.markdown(md_safe(turn.question))
    with st.chat_message("assistant", avatar=":material/insights:"):
        if turn.kind == "quick":
            render_quick(turn, key)
        else:
            render_deep(turn, key)
        if not is_last:
            return
        with st.container(horizontal=True, key=f"actions_{key}", gap="small"):
            if not turn.success:
                st.button("Retry", icon=":material/refresh:", key=f"retry_{key}",
                          on_click=ask, args=(turn.question, turn.kind))
            if turn.kind == "quick":
                st.button("Investigate deeper", icon=":material/manage_search:", key=f"deeper_{key}",
                          on_click=ask, args=(turn.question, "deep"))
        if turn.follow_ups:
            st.html('<div class="section-title small">Suggested follow-ups</div>')
            with st.container(horizontal=True, key=f"fu_{key}", gap="small"):
                for i, q in enumerate(turn.follow_ups):
                    st.button(q, key=f"fu_{key}_{i}", on_click=ask, args=(q, "auto"), type="secondary")


__all__ = ["render_turn", "auto_chart", "show_result", "MODE_LABELS"]
