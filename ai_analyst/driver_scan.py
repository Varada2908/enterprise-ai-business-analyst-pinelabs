"""Deterministic driver scan: which segments explain a change in success rate between two periods?

The LLM only chooses the two periods (and optionally a payment method). The SQL comes from a
fixed template, so finding "the one merchant x platform that broke" does not depend on the model
writing a clever query. Two queries are produced:

1. **Segment scan**: success rate before vs after for every segment in each dimension (merchant,
   device platform, merchant x platform, payment method, issuing bank, bank x method, merchant
   category, channel), ranked by impact on the overall success rate:
       impact_pp = share_of_current_attempts * (sr_current - sr_baseline) * 100
   Segments with fewer than ``min_attempts`` terminal attempts in either period are left out.
2. **Failure-reason shift**: each failure_code as % of terminal attempts, before vs after, with the
   merchant x platform contributing most to that code in the current period.

Both queries still go through the normal SQL validator and the sandboxed executor.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from pydantic import BaseModel, Field

from .schema_context import SchemaContext

METHOD_CODES = {"UPI", "CREDIT_CARD", "DEBIT_CARD", "NET_BANKING", "WALLET"}

# dimension name -> SQL expression over the `base` CTE
DIMENSIONS = {
    "merchant": "coalesce(merchant_id, '(none)') || coalesce(' ' || merchant_name, '')",
    "device_platform": "device_platform",
    "merchant x platform": "coalesce(merchant_id, '(none)') || ' / ' || device_platform",
    "payment_method": "method_code",
    "issuing_bank": "coalesce(bank, '(none)')",
    "bank x method": "coalesce(bank, '(none)') || ' / ' || method_code",
    "merchant_category": "coalesce(merchant_category, '(none)')",
    "channel": "channel",
}


class DriverScanRequest(BaseModel):
    baseline_start: str | None = Field(default=None, description="First day of the before period, YYYY-MM-DD; "
                                                                 "leave all four dates null to auto-detect the change date")
    baseline_end: str | None = Field(default=None, description="Last day of the before period, YYYY-MM-DD (inclusive)")
    current_start: str | None = Field(default=None, description="First day of the period being explained, YYYY-MM-DD")
    current_end: str | None = Field(default=None, description="Last day of the period being explained, YYYY-MM-DD (inclusive)")
    method_code: str | None = Field(default=None, description="Optional: restrict to one payment method, e.g. UPI")

    @property
    def auto(self) -> bool:
        """True when no period was given: the change date is detected from the data."""
        return not any((self.baseline_start, self.baseline_end, self.current_start, self.current_end))


class DriverScanError(ValueError):
    pass


@dataclass
class ScanQuery:
    purpose: str
    sql: str


def _iso(value: str, name: str) -> str:
    v = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        raise DriverScanError(f"{name} must be YYYY-MM-DD, got {value!r}")
    try:
        date.fromisoformat(v)
    except ValueError as e:
        raise DriverScanError(f"{name} is not a valid date: {value!r}") from e
    return v


def _method_filter(method_code: str | None) -> str:
    if not method_code:
        return ""
    mc = method_code.strip().upper()
    if mc not in METHOD_CODES:
        raise DriverScanError(f"unknown method_code {method_code!r}; use one of {sorted(METHOD_CODES)}")
    return f"AND method_code = '{mc}'"


# ----------------------------------------------------------------------------- change-date detection
CHANGE_DIMENSIONS = {k: DIMENSIONS[k] for k in
                     ("merchant x platform", "merchant", "bank x method", "issuing_bank", "payment_method",
                      "device_platform", "merchant_category")}


# Calibrated on time-shuffled copies of the data (ai_analyst/tests/stress_change_detection.py): across 40
# null copies the strongest spurious break reached z <= -4.5 in 5% of copies and z <= -5 in none.
DEFAULT_Z_THRESHOLD = -5.0


def _z_sql(s1: str, n1: str, s2: str, n2: str) -> str:
    """Two-proportion z-score of rate 2 vs rate 1, as SQL text."""
    p = f"(({s1}) + ({s2}))::DOUBLE / (({n1}) + ({n2}))"
    return (f"(({s2})::DOUBLE / ({n2}) - ({s1})::DOUBLE / ({n1})) / "
            f"NULLIF(sqrt({p} * (1 - {p}) * (1.0 / ({n1}) + 1.0 / ({n2}))), 0)")


def build_change_point_query(schema: SchemaContext, method_code: str | None = None, lookback_days: int = 28,
                             min_attempts: int = 100, min_after_days: int = 7, top_k: int = 12,
                             z_threshold: float | None = DEFAULT_Z_THRESHOLD) -> ScanQuery:
    """Find, for every segment, the day its success rate broke, and rank segments by how clear the break is.

    For each segment and each candidate day d: before = the ``lookback_days`` days before d,
    after = d to the end of the data (at least ``min_after_days`` days). The segment's change date
    is the d where the drop is statistically clearest (most negative two-proportion z-score), and
    segments are ranked the same way. Ranking by z rather than by impact stops big segments with a
    slow drift from outranking a sharp break in one merchant. Impact on the overall rate is reported too:
        impact_pp = segment_attempts_after / all_attempts_after * (sr_after - sr_before) * 100.

    Guards (see ai_analyst/tests/stress_change_detection.py):
    - multiple comparisons: thousands of segment x day tests are run, so only breaks with
      z <= ``z_threshold`` are returned (calibrated on null data; None disables the filter);
    - low volume: both sides need ``min_attempts`` terminal attempts, and z already discounts small samples;
    - recovered incidents: ``sr_first_7d`` (first week after the break) and ``sr_last_7d`` (last week of
      data) give ``status`` = ongoing / recovered / unclear, so an incident that has ended is not
      reported as a current, diluted drop. Recovered means the last week is no longer clearly below
      "before" (z > -2) or at least 75% of the first-week drop has been regained.
    """
    seg_union = "\n    UNION ALL\n    ".join(
        f"SELECT '{dim}' AS dimension, {expr} AS segment, txn_date, count(*) AS n, count_if(status = 'SUCCESS') AS s "
        f"FROM base GROUP BY 1, 2, 3"
        for dim, expr in CHANGE_DIMENSIONS.items())
    threshold = "" if z_threshold is None else f"WHERE z_score <= {float(z_threshold)}"
    sql = f"""WITH {schema.VALID_TRANSACTIONS_CTE},
base AS (
    SELECT v.*, b.short_name AS bank, m.merchant_name, m.category AS merchant_category
    FROM valid_txn v
    LEFT JOIN banks b ON v.bank_id = b.bank_id
    LEFT JOIN merchants m ON v.merchant_id = m.merchant_id
    WHERE v.status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED') {_method_filter(method_code)}
),
bounds AS (SELECT min(txn_date) AS d0, max(txn_date) AS d1 FROM base),
daily AS (
    {seg_union}
),
overall AS (SELECT txn_date, count(*) AS n FROM base GROUP BY 1),
overall_after AS (
    SELECT txn_date, sum(n) OVER (ORDER BY txn_date ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING) AS total_after
    FROM overall
),
last7 AS (
    SELECT dimension, segment, sum(n) AS n_last7, sum(s) AS s_last7
    FROM daily CROSS JOIN bounds WHERE txn_date > bounds.d1 - INTERVAL 7 DAYS GROUP BY 1, 2
),
cum AS (
    SELECT dimension, segment, txn_date AS change_date,
           sum(n) OVER wb AS n_before, sum(s) OVER wb AS s_before,
           sum(n) OVER wa AS n_after, sum(s) OVER wa AS s_after,
           sum(n) OVER wf AS n_first7, sum(s) OVER wf AS s_first7
    FROM daily
    WINDOW wb AS (PARTITION BY dimension, segment ORDER BY txn_date
                  RANGE BETWEEN INTERVAL {int(lookback_days)} DAYS PRECEDING AND INTERVAL 1 DAYS PRECEDING),
           wa AS (PARTITION BY dimension, segment ORDER BY txn_date
                  RANGE BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING),
           wf AS (PARTITION BY dimension, segment ORDER BY txn_date
                  RANGE BETWEEN CURRENT ROW AND INTERVAL 6 DAYS FOLLOWING)
),
scored AS (
    SELECT c.dimension, c.segment, c.change_date, c.n_before,
           round(c.s_before::DOUBLE / c.n_before, 4) AS sr_before, c.n_after,
           round(c.s_after::DOUBLE / c.n_after, 4) AS sr_after,
           round(c.s_after::DOUBLE / c.n_after - c.s_before::DOUBLE / c.n_before, 4) AS sr_change,
           round(c.n_after::DOUBLE / oa.total_after
                 * (c.s_after::DOUBLE / c.n_after - c.s_before::DOUBLE / c.n_before) * 100, 3) AS impact_on_overall_sr_pp,
           -- two-proportion z-score: how clearly the rate broke (ranks sharp breaks above slow drift in big segments)
           round({_z_sql('c.s_before', 'c.n_before', 'c.s_after', 'c.n_after')}, 2) AS z_score,
           round(c.s_first7::DOUBLE / c.n_first7, 4) AS sr_first_7d,
           l.n_last7, round(l.s_last7::DOUBLE / l.n_last7, 4) AS sr_last_7d,
           -- recovered = last week no longer clearly below "before", or >= 75% of the first-week drop regained
           CASE WHEN coalesce(l.n_last7, 0) < 30 THEN 'unclear'
                WHEN {_z_sql('c.s_before', 'c.n_before', 'l.s_last7', 'l.n_last7')} > -2 THEN 'recovered'
                WHEN c.s_first7::DOUBLE / c.n_first7 < c.s_before::DOUBLE / c.n_before
                     AND (l.s_last7::DOUBLE / l.n_last7 - c.s_first7::DOUBLE / c.n_first7)
                         >= 0.75 * (c.s_before::DOUBLE / c.n_before - c.s_first7::DOUBLE / c.n_first7) THEN 'recovered'
                ELSE 'ongoing' END AS status,
           bounds.d1 AS data_end
    FROM cum c
    JOIN overall_after oa ON c.change_date = oa.txn_date
    LEFT JOIN last7 l ON c.dimension = l.dimension AND c.segment = l.segment
    CROSS JOIN bounds
    WHERE c.n_before >= {int(min_attempts)} AND c.n_after >= {int(min_attempts)}
      AND c.change_date >= bounds.d0 + INTERVAL 14 DAYS
      AND c.change_date <= bounds.d1 - INTERVAL {int(min_after_days) - 1} DAYS
      AND c.s_after::DOUBLE / c.n_after < c.s_before::DOUBLE / c.n_before
),
best AS (
    SELECT * FROM scored
    QUALIFY row_number() OVER (PARTITION BY dimension, segment ORDER BY z_score ASC, change_date) = 1
)
SELECT * FROM best {threshold} ORDER BY z_score ASC LIMIT {int(top_k)}"""
    label = " (" + method_code.upper() + " only)" if method_code else ""
    return ScanQuery(f"[driver scan] change-date detection: where and when success rate broke{label}", sql)


def periods_from_change(change_date, data_end, method_code: str | None = None,
                        status: str = "ongoing") -> DriverScanRequest:
    """Ongoing break: current = change date .. end of data, baseline = the same number of days before it.
    Recovered break: current = the first 7 days of the incident, baseline = the 7 days before it."""
    cs = date.fromisoformat(str(change_date)[:10])
    ce = date.fromisoformat(str(data_end)[:10])
    if status == "recovered":
        ce = min(ce, cs + timedelta(days=6))
    length = (ce - cs).days + 1
    return DriverScanRequest(baseline_start=str(cs - timedelta(days=length)), baseline_end=str(cs - timedelta(days=1)),
                             current_start=str(cs), current_end=str(ce), method_code=method_code)


# ----------------------------------------------------------------------------- period comparison
def build_driver_scan(schema: SchemaContext, req: DriverScanRequest, min_attempts: int = 100,
                      top_per_dimension: int = 4) -> list[ScanQuery]:
    if req.auto:
        raise DriverScanError("no periods given: run build_change_point_query first and use periods_from_change")
    for name in ("baseline_start", "baseline_end", "current_start", "current_end"):
        if not getattr(req, name):
            raise DriverScanError(f"{name} is missing: give all four dates, or none to auto-detect the change date")
    bs, be = _iso(req.baseline_start, "baseline_start"), _iso(req.baseline_end, "baseline_end")
    cs, ce = _iso(req.current_start, "current_start"), _iso(req.current_end, "current_end")
    if bs > be or cs > ce:
        raise DriverScanError("each period's start must be on or before its end")
    method_filter = _method_filter(req.method_code)

    base = f"""WITH {schema.VALID_TRANSACTIONS_CTE},
base AS (
    SELECT v.*, b.short_name AS bank, m.merchant_name, m.category AS merchant_category,
           CASE WHEN v.txn_date BETWEEN DATE '{cs}' AND DATE '{ce}' THEN 'cur'
                WHEN v.txn_date BETWEEN DATE '{bs}' AND DATE '{be}' THEN 'base' END AS period
    FROM valid_txn v
    LEFT JOIN banks b ON v.bank_id = b.bank_id
    LEFT JOIN merchants m ON v.merchant_id = m.merchant_id
    WHERE v.status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED') {method_filter}
)"""
    seg_union = "\n    UNION ALL\n    ".join(
        f"SELECT '{dim}' AS dimension, {expr} AS segment, period, count(*) AS n, count_if(status = 'SUCCESS') AS s "
        f"FROM base WHERE period IS NOT NULL GROUP BY ALL"
        for dim, expr in DIMENSIONS.items())
    segment_sql = f"""{base},
seg AS (
    {seg_union}
),
piv AS (
    SELECT dimension, segment,
           coalesce(sum(n) FILTER (WHERE period = 'base'), 0) AS n_base,
           coalesce(sum(s) FILTER (WHERE period = 'base'), 0) AS s_base,
           coalesce(sum(n) FILTER (WHERE period = 'cur'), 0) AS n_cur,
           coalesce(sum(s) FILTER (WHERE period = 'cur'), 0) AS s_cur
    FROM seg GROUP BY dimension, segment
),
tot AS (
    SELECT count_if(period = 'cur') AS total_cur,
           (count_if(period = 'cur' AND status = 'SUCCESS')::DOUBLE / nullif(count_if(period = 'cur'), 0)
            - count_if(period = 'base' AND status = 'SUCCESS')::DOUBLE / nullif(count_if(period = 'base'), 0)) * 100
           AS overall_change_pp
    FROM base
),
scored AS (
    SELECT dimension, segment, n_base, round(s_base::DOUBLE / n_base, 4) AS sr_baseline,
           n_cur, round(s_cur::DOUBLE / n_cur, 4) AS sr_current,
           round(s_cur::DOUBLE / n_cur - s_base::DOUBLE / n_base, 4) AS sr_change,
           round(n_cur::DOUBLE / total_cur * (s_cur::DOUBLE / n_cur - s_base::DOUBLE / n_base) * 100, 3) AS impact_on_overall_sr_pp,
           round((s_base::DOUBLE / n_base - s_cur::DOUBLE / n_cur) * n_cur) AS excess_unsuccessful_attempts,
           round(overall_change_pp, 3) AS overall_sr_change_pp,
           -- the segment's rate effect as a share of the overall SR change (mix shifts are not included,
           -- so shares within a dimension need not add up to 1)
           round(CASE WHEN abs(overall_change_pp) >= 0.01 THEN
                 n_cur::DOUBLE / total_cur * (s_cur::DOUBLE / n_cur - s_base::DOUBLE / n_base) * 100 / overall_change_pp
                 END, 4) AS share_of_overall_change
    FROM piv CROSS JOIN tot
    WHERE n_base >= {int(min_attempts)} AND n_cur >= {int(min_attempts)}
)
SELECT * FROM scored
QUALIFY row_number() OVER (PARTITION BY dimension ORDER BY impact_on_overall_sr_pp ASC) <= {int(top_per_dimension)}
ORDER BY impact_on_overall_sr_pp ASC
LIMIT 40"""

    failure_sql = f"""{base},
fc AS (
    SELECT coalesce(failure_code, '(missing)') AS failure_code,
           count_if(period = 'base' AND status = 'FAILED') AS failed_base,
           count_if(period = 'cur' AND status = 'FAILED') AS failed_cur
    FROM base WHERE period IS NOT NULL GROUP BY 1
),
tot AS (SELECT count_if(period = 'base') AS n_base, count_if(period = 'cur') AS n_cur FROM base),
seg_fail AS (
    SELECT coalesce(failure_code, '(missing)') AS failure_code,
           coalesce(merchant_id, '(none)') || ' / ' || device_platform AS segment, count(*) AS n
    FROM base WHERE period = 'cur' AND status = 'FAILED' GROUP BY 1, 2
),
top_seg AS (
    SELECT * FROM seg_fail
    QUALIFY row_number() OVER (PARTITION BY failure_code ORDER BY n DESC, segment) = 1
)
SELECT fc.failure_code, failed_base, round(failed_base::DOUBLE / n_base * 100, 3) AS pct_of_attempts_base,
       failed_cur, round(failed_cur::DOUBLE / n_cur * 100, 3) AS pct_of_attempts_cur,
       round((failed_cur::DOUBLE / n_cur - failed_base::DOUBLE / n_base) * 100, 3) AS change_pp,
       top_seg.segment AS top_merchant_platform_cur, top_seg.n AS top_segment_failed_cur
FROM fc CROSS JOIN tot LEFT JOIN top_seg ON fc.failure_code = top_seg.failure_code
ORDER BY change_pp DESC"""

    label = f"{bs}..{be} vs {cs}..{ce}" + (f", {req.method_code.upper()} only" if req.method_code else "")
    return [ScanQuery(f"[driver scan] segments ranked by impact on SR change ({label})", segment_sql),
            ScanQuery(f"[driver scan] failure-reason shift ({label})", failure_sql)]
