"""Fixed SQL for the dashboard pages.

Every query starts from the backend's standard valid_txn CTE (dedup + validity rules R1-R4) and
uses the metric expressions from the data catalog via backend.metric_sql, so the dashboard and the
AI analyst compute numbers the same way. Filter values are whitelisted or typed (dates), never
free text, and every query still passes the SQL validator before it runs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from ai_analyst.driver_scan import METHOD_CODES, build_change_point_query

from .backend import QueryOutcome, get_schema, metric_sql, run_query

CHANNELS = ("ONLINE", "POS")
METHOD_ORDER = ("UPI", "CREDIT_CARD", "DEBIT_CARD", "NET_BANKING", "WALLET")


@dataclass(frozen=True)
class Filters:
    start: date
    end: date
    methods: tuple[str, ...] = METHOD_ORDER
    channels: tuple[str, ...] = CHANNELS

    def where(self) -> str:
        methods = [m for m in self.methods if m in METHOD_CODES]
        channels = [c for c in self.channels if c in CHANNELS]
        if not methods or not channels:
            raise ValueError("select at least one payment method and one channel")
        quoted = lambda xs: ", ".join(f"'{x}'" for x in xs)  # noqa: E731 - values are whitelisted above
        return (f"txn_date BETWEEN DATE '{self.start.isoformat()}' AND DATE '{self.end.isoformat()}' "
                f"AND method_code IN ({quoted(methods)}) AND channel IN ({quoted(channels)})")

    def window(self, start: date, end: date) -> "Filters":
        return Filters(start, end, self.methods, self.channels)

    @property
    def single_method(self) -> str | None:
        return self.methods[0] if len(self.methods) == 1 else None


def _cte(f: Filters) -> str:
    return f"WITH {get_schema().VALID_TRANSACTIONS_CTE},\nf AS (SELECT * FROM valid_txn WHERE {f.where()})"


def _m(name: str) -> str:
    return metric_sql(name)


def _p95() -> str:
    p50 = _m("response_time_p50 / p95")
    p95 = p50.replace(", 0.5)", ", 0.95)")
    assert p95 != p50, "catalog latency metric changed shape"
    return p95


# ----------------------------------------------------------------------------- dataset facts
def dataset_summary() -> pd.Series:
    sql = f"""WITH {get_schema().VALID_TRANSACTIONS_CTE}
SELECT (SELECT count(*) FROM transactions) AS raw_rows,
       count(*) AS valid_rows,
       min(txn_date) AS first_date,
       max(txn_date) AS last_date,
       max(created_at) AS data_as_of,
       (SELECT count(*) FROM merchants) AS merchants
FROM valid_txn"""
    return run_query(sql).df.iloc[0]


# ----------------------------------------------------------------------------- overview
def kpis(f: Filters) -> QueryOutcome:
    sql = f"""{_cte(f)},
orders AS (
    SELECT order_id, bool_or(status = 'SUCCESS') AS order_succeeded, bool_and(status <> 'PENDING') AS resolved
    FROM f GROUP BY order_id
)
SELECT {_m('terminal_attempts')} AS terminal_attempts,
       {_m('success_rate')} AS success_rate,
       {_m('tpv')} AS tpv,
       {_m('success_count')} AS success_count,
       {_m('failure_rate')} AS failure_rate,
       {_m('user_cancellation_rate')} AS cancellation_rate,
       {_m('average_ticket_size')} AS avg_ticket,
       {_m('response_time_p50 / p95')} AS latency_p50_ms,
       {_p95()} AS latency_p95_ms,
       (SELECT avg(order_succeeded::INT) FROM orders WHERE resolved) AS order_conversion_rate
FROM f"""
    return run_query(sql)


def weekly_trend(f: Filters) -> QueryOutcome:
    sql = f"""{_cte(f)}
SELECT date_trunc('week', created_at)::DATE AS week_start,
       count(DISTINCT txn_date) AS days,
       {_m('terminal_attempts')} AS terminal_attempts,
       {_m('success_rate')} AS success_rate,
       {_m('failure_rate')} AS failure_rate,
       {_m('tpv')} AS tpv
FROM f GROUP BY 1 ORDER BY 1"""
    return run_query(sql)


def by_method(f: Filters) -> QueryOutcome:
    sql = f"""{_cte(f)}
SELECT method_code,
       {_m('terminal_attempts')} AS terminal_attempts,
       {_m('terminal_attempts')}::DOUBLE / sum({_m('terminal_attempts')}) OVER () AS attempt_share,
       {_m('success_rate')} AS success_rate,
       {_m('failure_rate')} AS failure_rate,
       {_m('user_cancellation_rate')} AS cancellation_rate,
       {_m('tpv')} AS tpv,
       {_p95()} AS latency_p95_ms
FROM f GROUP BY 1 ORDER BY success_rate DESC"""
    return run_query(sql)


def payment_mix(f: Filters) -> QueryOutcome:
    sql = f"""{_cte(f)},
w AS (SELECT date_trunc('week', created_at)::DATE AS week_start, method_code, {_m('terminal_attempts')} AS terminal_attempts
      FROM f GROUP BY 1, 2)
SELECT week_start, method_code, terminal_attempts,
       terminal_attempts::DOUBLE / sum(terminal_attempts) OVER (PARTITION BY week_start) AS share
FROM w ORDER BY week_start, method_code"""
    return run_query(sql)


def failure_reasons(f: Filters) -> QueryOutcome:
    sql = f"""{_cte(f)}
SELECT coalesce(x.failure_code, '(missing)') AS failure_code,
       coalesce(fc.description, 'FAILED row without a failure_code') AS description,
       coalesce(fc.failure_category, 'UNKNOWN') AS failure_category,
       coalesce(fc.responsible_party, 'UNKNOWN') AS responsible_party,
       count(*) AS failed_attempts,
       CASE WHEN x.failure_code IS NULL THEN NULL
            ELSE count(*)::DOUBLE / sum(count(*)) FILTER (WHERE x.failure_code IS NOT NULL) OVER () END AS share_of_coded_failures
FROM f x LEFT JOIN failure_codes fc ON x.failure_code = fc.failure_code
WHERE x.status = 'FAILED'
GROUP BY 1, 2, 3, 4, x.failure_code
ORDER BY failed_attempts DESC"""
    return run_query(sql)


def incidents(method_code: str | None = None) -> QueryOutcome:
    """Statistically clear success-rate breaks over the whole history (backend driver scan)."""
    q = build_change_point_query(get_schema(), method_code=method_code)
    return run_query(q.sql)


def last_week_windows(f: Filters) -> tuple[Filters, Filters] | None:
    """Last 7 days of the selected range and the 7 days before it (for KPI deltas)."""
    if (f.end - f.start).days < 13:
        return None
    cur = f.window(f.end - timedelta(days=6), f.end)
    prev = f.window(f.end - timedelta(days=13), f.end - timedelta(days=7))
    return cur, prev
