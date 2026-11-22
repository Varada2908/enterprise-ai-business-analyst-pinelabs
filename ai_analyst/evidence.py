"""Checks that numbers quoted in a finding actually appear in the query results it cites.

The model writes findings in prose ("Net Banking SR is 84.07%, 11.8% of attempts fail"). This
module pulls the numbers out of that prose and looks for each one in the cited result tables:
as a cell value, as a percentage of a ratio cell (0.8407 -> 84.07%), or as a difference between
two cells in the same column (a "pp" gap). Numbers that cannot be traced are reported, so the
reader can tell a grounded finding from one with figures the data does not show.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

_SCALES = {"k": 1e3, "thousand": 1e3, "lakh": 1e5, "lakhs": 1e5, "l": 1e5, "m": 1e6, "mn": 1e6,
           "million": 1e6, "cr": 1e7, "crore": 1e7, "crores": 1e7, "bn": 1e9, "billion": 1e9}
_STRIP = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b"),   # dates / timestamps
    re.compile(r"\[?\bQ\d+\b\]?"),                                          # query ids
    re.compile(r"\b[A-Za-z]+\d[\w-]*"),                                     # ids like M0137, W7, p95
    re.compile(r"\b(?:19|20)\d{2}\b"),                                      # bare years
    re.compile(r"\b\d+(?:st|nd|rd|th)\b", re.IGNORECASE),                   # ordinals ("95th percentile")
]
# "90.4–90.7%", "90.4%-90.7%", "90.40-90.71 pp": the unit written once applies to both ends, and the
# dash is a range separator, not a minus sign on the second number
_RANGE = re.compile(
    r"(?<![\w.\-])(\d[\d,]*(?:\.\d+)?)(\s*(?:%|pp\b))?(\s*[–—]\s*|-)(\d[\d,]*(?:\.\d+)?)(\s*(?:%|pp\b))",
    re.IGNORECASE,
)
_NUMBER = re.compile(
    r"(?<![\w.])(-?\d[\d,]*(?:\.\d+)?)\s*(%|pp\b|percentage points?|bps\b|"
    r"k\b|thousand\b|lakhs?\b|l\b|mn\b|m\b|million\b|cr\b|crores?\b|bn\b|billion\b)?",
    re.IGNORECASE,
)


@dataclass
class NumberCheck:
    verified: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.verified and not self.unverified:
            return "no-numbers"
        return "verified" if not self.unverified else "partially-verified"


@dataclass
class _Quoted:
    text: str
    value: float
    tol: float
    is_percent: bool     # "%" or "pp": may correspond to a ratio cell x 100
    is_diff: bool        # "pp"/"bps": may be a difference of two cells


def extract_numbers(statement: str) -> list[_Quoted]:
    text = statement.replace("₹", " ").replace("−", "-")
    for pat in _STRIP:
        text = pat.sub(" ", text)
    text = _RANGE.sub(lambda m: f"{m.group(1)}{m.group(2) or m.group(5)} to {m.group(4)}{m.group(5)}", text)
    out = []
    for m in _NUMBER.finditer(text):
        raw, unit = m.group(1), (m.group(2) or "").lower()
        digits = raw.replace(",", "")
        try:
            dec = Decimal(digits)
        except InvalidOperation:
            continue
        value = float(dec)
        decimals = max(0, -dec.as_tuple().exponent)
        tol = 0.5 * 10 ** (-decimals) + 1e-9          # what rounding to the stated precision allows
        is_percent = unit in ("%", "pp", "percentage point", "percentage points")
        is_diff = unit in ("pp", "percentage point", "percentage points", "bps")
        if unit == "bps":
            value, tol, is_percent = value / 100, tol / 100, True
        elif unit in _SCALES:
            value, tol = value * _SCALES[unit], tol * _SCALES[unit]
        elif not is_percent and abs(value) <= 31 and decimals == 0:
            continue                                   # small counts ("top 3", "2 weeks") are not evidence
        out.append(_Quoted(m.group(0).strip(), value, tol, is_percent, is_diff))
    return out


def _numeric_columns(results: list[tuple[list[str], list[tuple]]]) -> list[list[float]]:
    cols = []
    for columns, rows in results:
        for j in range(len(columns)):
            vals = []
            for r in rows:
                v = r[j] if j < len(r) else None
                if isinstance(v, bool) or v is None:
                    continue
                try:
                    vals.append(float(v))
                except (TypeError, ValueError):
                    pass
            if vals:
                cols.append(vals)
    return cols


def _matches(q: _Quoted, cols: list[list[float]]) -> bool:
    candidates = [q.value, -q.value] if q.is_diff else [q.value]
    for col in cols:
        for c in col:
            for v in candidates:
                if abs(c - v) <= q.tol or (q.is_percent and abs(c * 100 - v) <= q.tol):
                    return True
        if q.is_diff or q.is_percent:
            # differences between two cells of the same column (e.g. SR gap between segments)
            if len(col) <= 400:
                for i, a in enumerate(col):
                    for b in col[i + 1:]:
                        d = abs(a - b)
                        if abs(d - abs(q.value)) <= q.tol or abs(d * 100 - abs(q.value)) <= q.tol:
                            return True
    return False


def check_numbers(statement: str, results: list[tuple[list[str], list[tuple]]], question: str = "") -> NumberCheck:
    """``results`` is a list of (columns, rows) for the queries the statement cites. Numbers the user wrote in the
    question (a threshold such as "at least 150 attempts") may be restated; they are not claims about the data."""
    cols = _numeric_columns(results)
    asked = [q.value / 100 if q.is_percent else q.value for q in extract_numbers(question)]
    if asked:
        cols.append(asked)
    check = NumberCheck()
    for q in extract_numbers(statement):
        (check.verified if _matches(q, cols) else check.unverified).append(q.text)
    return check


# ----------------------------------------------------------------------------- answer vs findings
def check_answer_numbers(answer: str, verified_findings: list[str], question: str = "") -> NumberCheck:
    """Numbers in the summary answer must be ones a verified finding states.

    Matching evidence tables is not enough: two queries can measure the same thing over different windows
    (e.g. 88.69% before a detected break vs 89.07% in a 14-day baseline), and a summary that mixes them reads
    as a contradiction of the findings. Each finding's numbers form one column, so the answer may restate a
    finding's figure in another form (88.69% / 0.8869) or the gap between two of its figures (-26.63 pp).
    Numbers the user wrote in the question (a threshold such as "at least 150 attempts") may be repeated.
    """
    cols = []
    for text in [*verified_findings, question]:
        col = [q.value / 100 if q.is_percent else q.value for q in extract_numbers(text)]
        if col:
            cols.append(col)
    check = NumberCheck()
    for q in extract_numbers(answer):
        (check.verified if _matches(q, cols) else check.unverified).append(q.text)
    return check


# A segment can fall sharply while explaining little of the overall change; "driver" wording needs a finding
# that quantifies the segment's share of the overall change, not just its own decline.
_DRIVER_CLAIM = re.compile(
    r"\b(?:primary|main|principal|biggest|largest|dominant|major|key|top|sole|single)\s+"
    r"(?:driver|cause|contributor|reason|source)s?\b"
    r"|\b(?:driven|explained)\s+(?:\w+\s+){0,2}by\b"
    r"|\b(?:explains?|accounts? for|responsible for)\s+(?:\w+\s+){0,3}(?:the\s+)?"
    r"(?:overall|total|whole|entire|most|majority|bulk)\b"
    r"|\b(?:heavily|largely|mostly|almost entirely)\s+concentrated\b",
    re.IGNORECASE,
)
_SHARE_OF_OVERALL = re.compile(
    r"(?:share|%|percent|pp|percentage points?|portion|fraction|impact|contribution|effect)\b[^.;]{0,80}?"
    r"\b(?:of|to|on)\s+(?:the\s+)?(?:overall|total|all|platform-wide|business-wide|company-wide|aggregate)\b"
    r"|\b(?:impact(?:ing|ed|s)?|contribut\w*)\s+(?:\w+\s+){0,3}?(?:the\s+)?(?:overall|total)\b"
    r"|\b(?:overall|total)\b[^.;]{0,60}?\b(?:drop|decline|change|fall)\b[^.;]{0,60}?\b(?:explain|account|contribut|share)",
    re.IGNORECASE,
)
# A cause stated as fact. Observed mechanisms (which failure codes rose) are findings; why they rose is not
# in the data, so it is a hypothesis unless hedged.
_CAUSAL = re.compile(r"\b(?:root[- ]cause[sd]?|caused (?:\w+ ){0,2}by|because of|triggered (?:\w+ ){0,2}by|"
                     r"was caused|is caused|stems? from)\b", re.IGNORECASE)
_HEDGE = re.compile(r"\b(?:likely|possibl[ey]|probabl[ey]|may|might|could|suggests?|consistent with|hypothes\w*|"
                    r"unconfirmed|not (?:yet )?(?:confirmed|proven)|cannot confirm|unproven|would need)\b",
                    re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z(])", text.strip()) if s]


# ... and something that compares it: a rank among segments, a share, or the size of the overall change
_COMPARISON = re.compile(
    r"\b(?:largest|biggest|greatest|highest|most|top|leading|ranked|ranks|more than|out of|share of)\b|%\s+of\b"
    r"|\boverall\s+(?:sr\s+|success rate\s+)?(?:change|drop|decline|fall)\b[^.;]{0,40}?-?\d"
    r"|\boverall\s+(?:sr|success rate)\s+(?:changed|dropped|declined|fell)\b",
    re.IGNORECASE,
)


def has_contribution_share(statement: str) -> bool:
    """A quantified contribution to the overall change: contribution wording plus at least one number."""
    return bool(_SHARE_OF_OVERALL.search(statement)) and bool(extract_numbers(statement))


def supports_driver_claim(statement: str) -> bool:
    """A finding that quantifies a contribution to the overall change and compares it (rank, share, or net change)."""
    return has_contribution_share(statement) and bool(_COMPARISON.search(statement))


_SEGMENT_TOKEN = re.compile(r"\b(?:[A-Z]+\d[A-Z0-9_]*|[A-Z][A-Z0-9_]{2,})\b")     # M0137, ANDROID, NET_BANKING


def _segment_tokens(text: str) -> set[str]:
    return set(_SEGMENT_TOKEN.findall(text)) - {"SR", "TPV", "SQL", "IST", "INR", "API", "THE"}


def unhedged_causal_sentences(text: str, is_measured=None) -> list[str]:
    """Sentences that state a cause as fact. ``is_measured(clause)`` may accept a cause that is itself a verified
    measurement ("caused by a rise in GATEWAY_ERROR from 10 to 142"): that is an observed breakdown, not a claim
    about why. A cause clause without numbers ("because of a faulty release") is never accepted."""
    out = []
    for s in _sentences(text):
        m = _CAUSAL.search(s)
        if not m or _HEDGE.search(s):
            continue
        clause = s[m.end():]
        if is_measured and extract_numbers(clause) and is_measured(clause):
            continue
        out.append(s)
    return out


def answer_claim_issues(answer: str, verified_findings: list[str], question: str = "") -> list[str]:
    """Wording problems in the summary that the findings do not support (returned as reader-facing notes)."""
    issues = []
    for s in _sentences(answer):
        # a hedged sentence ("we hypothesise it was driven by ...") is presented as a hypothesis, not a claim
        driver = None if _HEDGE.search(s) else _DRIVER_CLAIM.search(s)
        if not driver:
            continue
        # the supporting finding must be about the same segment when the sentence names one (M0137, ANDROID, UPI)
        named = _segment_tokens(s)
        if not any(supports_driver_claim(f) and (not named or named & _segment_tokens(f)) for f in verified_findings):
            issues.append(f"The answer calls something a driver (\"{driver.group(0)}\") but no verified finding "
                          "quantifies that segment's share of the overall change and compares it with the other "
                          "segments or the net change; a segment's own decline is not its contribution to the total.")
            break
    measured = lambda clause: not check_answer_numbers(clause, verified_findings, question).unverified  # noqa: E731
    for s in unhedged_causal_sentences(answer, measured):
        issues.append(f"The answer states a cause as fact (\"{s[:140]}\"); the data shows what changed, so the cause "
                      "is a hypothesis unless a finding shows it.")
    return issues
