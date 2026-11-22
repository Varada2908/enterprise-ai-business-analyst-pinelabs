"""Card shell for charts: a bordered container with a title, a subtitle and a 'View SQL' popover."""
from __future__ import annotations

from contextlib import contextmanager
from html import escape
from typing import Iterator, Sequence

import streamlit as st

from services.backend import QueryOutcome


def sql_popover(outcomes: Sequence[QueryOutcome], key: str) -> None:
    with st.popover(":material/code:", help="View the SQL behind this card", type="tertiary", key=f"sql_{key}"):
        st.markdown("**Queries behind this card**")
        st.caption("Fixed SQL · passed the SQL safety validator · run read-only on DuckDB")
        for i, o in enumerate(outcomes, 1):
            if len(outcomes) > 1:
                st.markdown(f"Query {i}")
            st.code(o.sql, language="sql", wrap_lines=True)
            st.caption(f"{len(o.df):,} rows · {o.elapsed_ms:,} ms{' · truncated' if o.truncated else ''}")


@contextmanager
def card(key: str, title: str, subtitle: str = "", sql: Sequence[QueryOutcome] = ()) -> Iterator[None]:
    with st.container(border=True, key=f"card_{key}"):
        head, tools = st.columns([6, 1], vertical_alignment="top")
        with head:
            sub = f'<div class="card-subtitle">{escape(subtitle)}</div>' if subtitle else ""
            st.html(f'<div class="card-title">{escape(title)}</div>{sub}')
        if sql:
            with tools:
                with st.container(horizontal=True, horizontal_alignment="right"):
                    sql_popover(sql, key)
        yield


def empty_state(message: str) -> None:
    st.html(f'<div class="empty-state">{escape(message)}</div>')
