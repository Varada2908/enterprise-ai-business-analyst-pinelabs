"""KPI card row rendered as one HTML grid (responsive, consistent heights)."""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Literal

import streamlit as st

Tone = Literal["good", "bad", "neutral"]


@dataclass
class KPI:
    label: str
    value: str
    help: str = ""
    delta: str | None = None          # already formatted, e.g. "+0.42 pp"
    direction: Literal["up", "down", "flat", "none"] = "flat"
    tone: Tone = "neutral"            # whether the change is good or bad for the business
    delta_note: str = "last 7d vs prior 7d"


def delta_props(change: float | None, higher_is_better: bool, flat_below: float) -> tuple[str, str]:
    """(direction, tone) for a change; changes smaller than ``flat_below`` count as flat."""
    if change is None or abs(change) < flat_below:
        return "flat", "neutral"
    up = change > 0
    return ("up" if up else "down"), ("good" if up == higher_is_better else "bad")


_ARROWS = {"up": "↑", "down": "↓", "flat": "→", "none": ""}


def render_kpis(kpis: list[KPI]) -> None:
    cards = []
    for k in kpis:
        delta_html = ""
        if k.delta is not None:
            arrow = f'<span aria-hidden="true">{_ARROWS[k.direction]}</span> ' if _ARROWS[k.direction] else ""
            delta_html = (
                f'<div class="kpi-delta"><span class="chip chip-{k.tone}">{arrow}{escape(k.delta)}</span>'
                f'<span class="kpi-note">{escape(k.delta_note)}</span></div>'
            )
        cards.append(
            f'<div class="kpi-card" title="{escape(k.help)}">'
            f'<div class="kpi-label">{escape(k.label)}</div>'
            f'<div class="kpi-value">{escape(k.value)}</div>{delta_html}</div>'
        )
    st.html(f'<div class="kpi-grid">{"".join(cards)}</div>')
