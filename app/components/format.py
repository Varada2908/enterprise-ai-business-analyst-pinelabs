"""Number formatting for Indian payments data (INR, lakh / crore, percentages)."""
from __future__ import annotations

import math


def _missing(x) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def indian_int(n) -> str:
    """1234567 -> '12,34,567' (Indian digit grouping)."""
    if _missing(n):
        return "–"
    n = int(round(n))
    sign, s = ("-" if n < 0 else ""), str(abs(n))
    if len(s) <= 3:
        return sign + s
    head, tail = s[:-3], s[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return sign + ",".join(groups + [tail])


def inr(x, decimals: int = 2) -> str:
    """Compact rupees: ₹72.94 Cr, ₹4.10 L, ₹1,250."""
    if _missing(x):
        return "–"
    sign, a = ("−" if x < 0 else ""), abs(x)
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:,.{decimals}f} Cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:,.{decimals}f} L"
    return f"{sign}₹{indian_int(a)}"


def pct(x, decimals: int = 2) -> str:
    return "–" if _missing(x) else f"{x * 100:.{decimals}f}%"


def pp(delta, decimals: int = 2) -> str:
    """Change in a rate, in percentage points."""
    if _missing(delta):
        return "–"
    return f"{'+' if delta >= 0 else '−'}{abs(delta) * 100:.{decimals}f} pp"


def rel(delta, decimals: int = 1) -> str:
    """Relative change: 0.052 -> '+5.2%'."""
    if _missing(delta):
        return "–"
    return f"{'+' if delta >= 0 else '−'}{abs(delta) * 100:.{decimals}f}%"


def ms(x) -> str:
    if _missing(x):
        return "–"
    return f"{x / 1000:.2f} s" if x >= 1000 else f"{x:,.0f} ms"


def short_date(d) -> str:
    return "–" if d is None else f"{d.day} {d:%b %Y}"
