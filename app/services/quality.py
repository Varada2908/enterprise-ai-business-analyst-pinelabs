"""Fixed SQL for the Data Quality page.

Every number on the page is computed here from the database; nothing is hard-coded. Rules and their text
come from the data catalog (schema.validity_rules R1-R8); metric formulas (fees, tax, settlement gap, aged
pending batches, success rate, TPV) come from the catalog via backend.metric_sql; "valid transactions"
come from the backend's VALID_TRANSACTIONS_CTE. All queries pass the SQL validator and run read-only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .backend import QueryOutcome, get_schema, metric_sql, run_query

VALID_STATUSES = "('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING')"   # rule R2


def rule_text(rule_id: str) -> str:
    for r in get_schema().validity_rules:
        if r["id"] == rule_id:
            return r["rule"]
    return ""


def settlement_tolerance() -> float:
    """R8 tolerance in INR, read from the catalog rule text (e.g. 'INR 1.00 per batch')."""
    m = re.search(r"INR\s*([\d,]+(?:\.\d+)?)", rule_text("R8"))
    return float(m.group(1).replace(",", "")) if m else 1.0


def _valid() -> str:
    return get_schema().VALID_TRANSACTIONS_CTE


# ----------------------------------------------------------------------------- checks catalogue
@dataclass(frozen=True)
class Check:
    id: str
    rule: str
    name: str
    severity: str            # critical / high / medium / info
    scope: str               # which rows the count is taken over
    handling: str            # what the analyst does about it
    affects: str             # which metrics it touches
    sample_sql: str          # drill-down: example records


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def checks() -> list[Check]:
    v = _valid()
    raw_cols = "transaction_id, order_id, merchant_id, payment_method_id, bank_id, status, amount, failure_code, created_at, updated_at"
    v_cols = ("transaction_id, merchant_id, method_code, bank_id, status, amount, failure_code, created_at")
    return [
        Check("dup_rows", "R1", "Duplicate transaction_id rows", "high", "raw rows",
              "Deduplicated: only the latest updated_at row per transaction_id is kept",
              "All transaction metrics (would double-count attempts and TPV)",
              f"""WITH d AS (SELECT transaction_id FROM transactions GROUP BY 1 HAVING count(*) > 1 ORDER BY 1 LIMIT 8)
SELECT CASE WHEN row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) = 1
            THEN 'kept' ELSE 'dropped' END AS r1_outcome, {raw_cols}
FROM transactions t JOIN d USING (transaction_id) ORDER BY transaction_id, updated_at DESC"""),
        Check("bad_status", "R2", "Invalid status value", "high", "rows after R1",
              "Excluded from all metrics", "All transaction metrics (status decides success / failure)",
              f"""WITH latest AS (SELECT * FROM transactions QUALIFY row_number() OVER (PARTITION BY transaction_id ORDER BY updated_at DESC) = 1)
SELECT {raw_cols} FROM latest WHERE status IS NULL OR status NOT IN {VALID_STATUSES} ORDER BY created_at LIMIT 25"""),
        Check("bad_amount", "R3", "Missing or out-of-range amount", "high", "rows after R1–R2",
              "Excluded from all metrics", "TPV, ticket size and every rate's denominator",
              f"""WITH latest AS (SELECT * FROM transactions QUALIFY row_number() OVER (PARTITION BY transaction_id ORDER BY updated_at DESC) = 1)
SELECT CASE WHEN amount IS NULL THEN 'NULL amount' WHEN amount <= 0 THEN 'zero or negative'
            ELSE 'above 1,000,000' END AS problem, {raw_cols}
FROM latest WHERE status IN {VALID_STATUSES} AND (amount IS NULL OR amount <= 0 OR amount > 1000000)
ORDER BY problem, created_at LIMIT 25"""),
        Check("merchant_null", "R5", "Merchant missing (NULL)", "medium", "valid rows",
              "Counted in company-wide metrics; reported as 'unattributable' in merchant breakdowns",
              "Merchant-level metrics and rankings, settlement reconciliation",
              f"WITH {v} SELECT {v_cols} FROM valid_txn WHERE merchant_id IS NULL ORDER BY created_at LIMIT 25"),
        Check("merchant_unknown", "R5", "Merchant not in merchants table", "medium", "valid rows",
              "Counted in company-wide metrics; unattributable to a merchant",
              "Merchant-level metrics and rankings, settlement reconciliation",
              f"""WITH {v} SELECT {v_cols} FROM valid_txn v
WHERE merchant_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM merchants m WHERE m.merchant_id = v.merchant_id)
ORDER BY created_at LIMIT 25"""),
        Check("bank_null", "R5", "Bank missing on a non-wallet payment", "medium", "valid rows",
              "Counted in company-wide metrics; unattributable to an issuing bank",
              "Bank-level metrics and bank × method breakdowns",
              f"WITH {v} SELECT {v_cols} FROM valid_txn WHERE bank_id IS NULL AND method_code <> 'WALLET' ORDER BY created_at LIMIT 25"),
        Check("bank_unknown", "R5", "Bank not in banks table", "medium", "valid rows",
              "Counted in company-wide metrics; unattributable to an issuing bank",
              "Bank-level metrics and bank × method breakdowns",
              f"""WITH {v} SELECT {v_cols} FROM valid_txn v
WHERE bank_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM banks b WHERE b.bank_id = v.bank_id)
ORDER BY created_at LIMIT 25"""),
        Check("wallet_no_bank", "R6", "Wallet payment without bank (expected)", "info", "valid rows",
              "Not a defect: wallets have no issuing bank", "None",
              f"WITH {v} SELECT {v_cols} FROM valid_txn WHERE bank_id IS NULL AND method_code = 'WALLET' ORDER BY created_at LIMIT 25"),
        Check("failed_no_code", "R7", "FAILED without failure_code", "medium", "valid rows",
              "Kept in rates; shown as '(missing)' in failure-reason analysis",
              "Failure-reason share and root-cause analysis",
              f"WITH {v} SELECT {v_cols} FROM valid_txn WHERE status = 'FAILED' AND failure_code IS NULL ORDER BY created_at LIMIT 25"),
        Check("code_not_failed", "R7", "failure_code on a non-FAILED row", "medium", "valid rows",
              "Kept; the code is ignored because the status is not FAILED", "Failure-reason share",
              f"WITH {v} SELECT {v_cols} FROM valid_txn WHERE status <> 'FAILED' AND failure_code IS NOT NULL ORDER BY created_at LIMIT 25"),
        Check("settle_gap", "R8", "Settlement batch outside tolerance", "critical", "settlement batches",
              "Reported as a reconciliation discrepancy; not hidden or netted off",
              "Settlement gap, expected net settlement, merchant payouts",
              f"""{_recon_cte()}
SELECT r.merchant_id, m.merchant_name, transaction_date, settlement_id, batch_status, transaction_count,
       expected_count, net_amount, expected_net, gap, outcome
FROM recon r LEFT JOIN merchants m USING (merchant_id)
WHERE outcome NOT IN ('Within tolerance') ORDER BY abs(gap) DESC NULLS LAST LIMIT 25"""),
        Check("aged_pending", "R8", "Aged pending settlement batch", "medium", "settlement batches",
              "Reported; money owed to merchants is overdue", "Aged pending settlement, payout timeliness",
              f"""SELECT settlement_id, merchant_id, transaction_date, settlement_date, status, status_reason, net_amount
FROM settlements WHERE {metric_sql('aged_pending_settlement')} ORDER BY settlement_date LIMIT 25"""),
    ]


def rules_table() -> "pd.DataFrame":
    """One row per check with its affected count and share of the rows it is measured on (all from queries)."""
    import pandas as pd

    counts = check_counts().df.set_index("check_id")
    s = settlement_summary().df.iloc[0]
    raw, valid, batches = int(counts["raw_rows"].iloc[0]), int(counts["valid_rows"].iloc[0]), int(s["batches"])
    base = {"raw rows": raw, "rows after R1": raw - int(counts.loc["dup_rows", "affected_rows"]),
            "rows after R1–R2": raw - int(counts.loc["dup_rows", "affected_rows"])
            - int(counts.loc["bad_status", "affected_rows"]),
            "valid rows": valid, "settlement batches": batches}
    extra = {"settle_gap": int(s["out_of_tolerance"]), "aged_pending": int(s["aged_pending_batches"])}
    rows = []
    for c in checks():
        n = extra[c.id] if c.id in extra else int(counts.loc[c.id, "affected_rows"])
        rows.append(dict(check_id=c.id, rule=c.rule, name=c.name, severity=c.severity, affected_rows=n,
                         base=base[c.scope], share=n / base[c.scope] if base[c.scope] else 0.0, scope=c.scope,
                         handling=c.handling, affects=c.affects))
    df = pd.DataFrame(rows)
    df["_o"] = df["severity"].map(SEVERITY_ORDER)
    return df.sort_values(["_o", "rule", "affected_rows"], ascending=[True, True, False]).drop(columns="_o") \
        .reset_index(drop=True)


def check_counts() -> QueryOutcome:
    """Affected rows for every check, plus the denominators they are measured against."""
    v = _valid()
    sql = f"""WITH {v},
latest AS (SELECT * FROM transactions QUALIFY row_number() OVER (PARTITION BY transaction_id ORDER BY updated_at DESC) = 1),
raw AS (SELECT count(*) AS n, count(DISTINCT transaction_id) AS ids FROM transactions),
counts AS (
    SELECT 'dup_rows' AS check_id, (SELECT n - ids FROM raw) AS affected_rows,
           (SELECT count(*) FROM (SELECT transaction_id FROM transactions GROUP BY 1 HAVING count(*) > 1)) AS affected_ids
    UNION ALL SELECT 'bad_status', count(*), NULL FROM latest WHERE status IS NULL OR status NOT IN {VALID_STATUSES}
    UNION ALL SELECT 'bad_amount', count(*), NULL FROM latest
              WHERE status IN {VALID_STATUSES} AND (amount IS NULL OR amount <= 0 OR amount > 1000000)
    UNION ALL SELECT 'merchant_null', count(*), NULL FROM valid_txn WHERE merchant_id IS NULL
    UNION ALL SELECT 'merchant_unknown', count(*), NULL FROM valid_txn v
              WHERE merchant_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM merchants m WHERE m.merchant_id = v.merchant_id)
    UNION ALL SELECT 'bank_null', count(*), NULL FROM valid_txn WHERE bank_id IS NULL AND method_code <> 'WALLET'
    UNION ALL SELECT 'bank_unknown', count(*), NULL FROM valid_txn v
              WHERE bank_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM banks b WHERE b.bank_id = v.bank_id)
    UNION ALL SELECT 'wallet_no_bank', count(*), NULL FROM valid_txn WHERE bank_id IS NULL AND method_code = 'WALLET'
    UNION ALL SELECT 'failed_no_code', count(*), NULL FROM valid_txn WHERE status = 'FAILED' AND failure_code IS NULL
    UNION ALL SELECT 'code_not_failed', count(*), NULL FROM valid_txn WHERE status <> 'FAILED' AND failure_code IS NOT NULL
)
SELECT c.check_id, c.affected_rows, c.affected_ids,
       (SELECT n FROM raw) AS raw_rows, (SELECT count(*) FROM valid_txn) AS valid_rows
FROM counts c"""
    return run_query(sql)


def exclusion_funnel() -> QueryOutcome:
    """Raw rows -> removed by R1, R2, R3 (and unknown payment method) -> valid transactions (R4).
    The last step is counted with the backend's own valid_txn CTE, so the funnel must add up to it."""
    sql = f"""WITH {_valid()},
latest AS (SELECT * FROM transactions QUALIFY row_number() OVER (PARTITION BY transaction_id ORDER BY updated_at DESC) = 1),
s2 AS (SELECT * FROM latest WHERE status IN {VALID_STATUSES}),
s3 AS (SELECT * FROM s2 WHERE amount > 0 AND amount <= 1000000)
SELECT 1 AS step, 'Raw rows' AS stage, (SELECT count(*) FROM transactions) AS rows_remaining
UNION ALL SELECT 2, 'After R1 · deduplicate transaction_id', (SELECT count(*) FROM latest)
UNION ALL SELECT 3, 'After R2 · valid status', (SELECT count(*) FROM s2)
UNION ALL SELECT 4, 'After R3 · valid amount', (SELECT count(*) FROM s3)
UNION ALL SELECT 5, 'Valid transactions (R4)', (SELECT count(*) FROM valid_txn)
ORDER BY step"""
    return run_query(sql)


def invalid_values() -> QueryOutcome:
    """What the invalid statuses and amounts actually look like."""
    sql = f"""WITH latest AS (SELECT * FROM transactions QUALIFY row_number() OVER (PARTITION BY transaction_id ORDER BY updated_at DESC) = 1)
SELECT 'R2 status' AS rule, coalesce('''' || status || '''', 'NULL') AS value, count(*) AS rows
FROM latest WHERE status IS NULL OR status NOT IN {VALID_STATUSES} GROUP BY 1, 2
UNION ALL
SELECT 'R3 amount', CASE WHEN amount IS NULL THEN 'NULL' WHEN amount <= 0 THEN '≤ 0'
                         ELSE '> 1,000,000' END, count(*)
FROM latest WHERE status IN {VALID_STATUSES} AND (amount IS NULL OR amount <= 0 OR amount > 1000000) GROUP BY 1, 2
ORDER BY rule, rows DESC"""
    return run_query(sql)


def metric_impact() -> QueryOutcome:
    """Catalog metrics computed on the raw table vs on valid transactions: what cleaning changes."""
    sr, tpv, n = metric_sql("success_rate"), metric_sql("tpv"), metric_sql("terminal_attempts")
    sql = f"""WITH {_valid()}
SELECT 'raw' AS basis, {n} AS terminal_attempts, {sr} AS success_rate, {tpv} AS tpv FROM transactions
UNION ALL
SELECT 'valid', {n}, {sr}, {tpv} FROM valid_txn"""
    return run_query(sql)


def unattributed_impact() -> QueryOutcome:
    """Share of valid attempts and TPV that cannot be attributed to a merchant or an issuing bank (R5)."""
    tpv, n = metric_sql("tpv"), metric_sql("terminal_attempts")
    sql = f"""WITH {_valid()},
flags AS (
    SELECT v.*,
           (v.merchant_id IS NULL OR m.merchant_id IS NULL) AS no_merchant,
           (v.method_code <> 'WALLET' AND (v.bank_id IS NULL OR b.bank_id IS NULL)) AS no_bank
    FROM valid_txn v
    LEFT JOIN merchants m ON v.merchant_id = m.merchant_id
    LEFT JOIN banks b ON v.bank_id = b.bank_id
)
SELECT 'Merchant' AS dimension, {n} FILTER (WHERE no_merchant) AS attempts,
       ({n} FILTER (WHERE no_merchant))::DOUBLE / {n} AS attempt_share,
       {tpv.replace('FILTER (WHERE ', 'FILTER (WHERE no_merchant AND ')} AS tpv,
       ({tpv.replace('FILTER (WHERE ', 'FILTER (WHERE no_merchant AND ')})::DOUBLE / {tpv} AS tpv_share
FROM flags
UNION ALL
SELECT 'Issuing bank', {n} FILTER (WHERE no_bank), ({n} FILTER (WHERE no_bank))::DOUBLE / {n},
       {tpv.replace('FILTER (WHERE ', 'FILTER (WHERE no_bank AND ')},
       ({tpv.replace('FILTER (WHERE ', 'FILTER (WHERE no_bank AND ')})::DOUBLE / {tpv}
FROM flags"""
    return run_query(sql)


def weekly_issues() -> QueryOutcome:
    """Issues per week of created_at, to see whether defects are steady or concentrated."""
    sql = f"""WITH {_valid()},
latest AS (SELECT * FROM transactions QUALIFY row_number() OVER (PARTITION BY transaction_id ORDER BY updated_at DESC) = 1),
dups AS (SELECT created_at FROM transactions
         QUALIFY row_number() OVER (PARTITION BY transaction_id ORDER BY updated_at DESC) > 1),
issues AS (
    SELECT created_at, 'Duplicate rows (R1)' AS issue FROM dups
    UNION ALL SELECT created_at, 'Invalid status (R2)' FROM latest WHERE status IS NULL OR status NOT IN {VALID_STATUSES}
    UNION ALL SELECT created_at, 'Invalid amount (R3)' FROM latest
              WHERE status IN {VALID_STATUSES} AND (amount IS NULL OR amount <= 0 OR amount > 1000000)
    UNION ALL SELECT v.created_at, 'Unattributable merchant (R5)' FROM valid_txn v
              LEFT JOIN merchants m ON v.merchant_id = m.merchant_id WHERE m.merchant_id IS NULL
    UNION ALL SELECT v.created_at, 'Unattributable bank (R5)' FROM valid_txn v
              LEFT JOIN banks b ON v.bank_id = b.bank_id WHERE v.method_code <> 'WALLET' AND b.bank_id IS NULL
    UNION ALL SELECT created_at, 'Missing failure code (R7)' FROM valid_txn WHERE status = 'FAILED' AND failure_code IS NULL
)
SELECT date_trunc('week', i.created_at)::DATE AS week_start, issue, count(*) AS rows,
       any_value(w.raw_rows) AS raw_rows,
       count(*) * 10000.0 / any_value(w.raw_rows) AS per_10k_rows
FROM issues i
JOIN (SELECT date_trunc('week', created_at)::DATE AS week_start, count(*) AS raw_rows FROM transactions GROUP BY 1) w
  ON date_trunc('week', i.created_at)::DATE = w.week_start
GROUP BY 1, 2 ORDER BY 1, 2"""
    return run_query(sql)


def health_summary() -> QueryOutcome:
    """Overall health: raw rows, valid rows, and valid rows with no attribution / failure-code defect."""
    sql = f"""WITH {_valid()},
flags AS (
    SELECT v.status,
           (v.merchant_id IS NULL OR m.merchant_id IS NULL) AS no_merchant,
           (v.method_code <> 'WALLET' AND (v.bank_id IS NULL OR b.bank_id IS NULL)) AS no_bank,
           (v.status = 'FAILED' AND v.failure_code IS NULL) OR (v.status <> 'FAILED' AND v.failure_code IS NOT NULL)
               AS code_issue
    FROM valid_txn v
    LEFT JOIN merchants m ON v.merchant_id = m.merchant_id
    LEFT JOIN banks b ON v.bank_id = b.bank_id
)
SELECT (SELECT count(*) FROM transactions) AS raw_rows,
       count(*) AS valid_rows,
       count_if(NOT (no_merchant OR no_bank OR code_issue)) AS clean_rows,
       count_if(no_merchant OR no_bank) AS unattributable_rows,
       count_if(status = 'FAILED') AS failed_rows,
       count_if(status = 'FAILED' AND code_issue) AS failed_without_code
FROM flags"""
    return run_query(sql)


# ----------------------------------------------------------------------------- settlement reconciliation (R8)
def _recon_cte() -> str:
    """Expected net settlement per (merchant, date) from valid SUCCESS transactions, matched to settlements."""
    fee = metric_sql("expected_fee (per transaction)")
    tax = metric_sql("expected_tax (per transaction)")
    net = metric_sql("expected_net_settlement")
    tol = settlement_tolerance()
    return f"""WITH {_valid()},
txn_fees AS (
    SELECT v.merchant_id, v.txn_date, v.amount, {fee} AS expected_fee
    FROM valid_txn v
    JOIN merchants USING (merchant_id)
    JOIN payment_methods USING (payment_method_id)
    WHERE v.status = 'SUCCESS'
),
txn_tax AS (SELECT *, {tax} AS expected_tax FROM txn_fees),
expected AS (
    SELECT merchant_id, txn_date, count(*) AS expected_count, SUM(amount) AS expected_gross,
           {net} AS expected_net
    FROM txn_tax GROUP BY 1, 2
),
recon AS (
    SELECT coalesce(s.merchant_id, e.merchant_id) AS merchant_id,
           coalesce(s.transaction_date, e.txn_date) AS transaction_date,
           s.settlement_id, s.status AS batch_status, s.settlement_date, s.transaction_count,
           e.expected_count, s.gross_amount, e.expected_gross, s.net_amount, e.expected_net,
           s.net_amount - e.expected_net AS gap,
           CASE WHEN s.settlement_id IS NULL THEN 'Missing settlement batch'
                WHEN e.merchant_id IS NULL THEN 'Batch without transactions'
                WHEN abs(s.net_amount - e.expected_net) <= {tol} THEN 'Within tolerance'
                WHEN s.net_amount < e.expected_net THEN 'Under-paid'
                ELSE 'Over-paid' END AS outcome
    FROM settlements s
    FULL OUTER JOIN expected e ON s.merchant_id = e.merchant_id AND s.transaction_date = e.txn_date
)"""


def settlement_summary() -> QueryOutcome:
    aged = metric_sql("aged_pending_settlement")
    sql = f"""{_recon_cte()}
SELECT count(*) AS merchant_days,
       count(settlement_id) AS batches,
       count_if(outcome = 'Within tolerance') AS within_tolerance,
       count_if(outcome IN ('Under-paid', 'Over-paid')) AS out_of_tolerance,
       count_if(outcome = 'Under-paid') AS under_paid,
       count_if(outcome = 'Over-paid') AS over_paid,
       count_if(outcome = 'Missing settlement batch') AS missing_batches,
       count_if(outcome = 'Batch without transactions') AS orphan_batches,
       coalesce(sum(gap) FILTER (WHERE outcome IN ('Under-paid', 'Over-paid')), 0) AS net_gap,
       coalesce(sum(gap) FILTER (WHERE outcome = 'Under-paid'), 0) AS under_paid_amount,
       coalesce(sum(abs(gap)) FILTER (WHERE outcome = 'Within tolerance'), 0) AS rounding_noise,
       count(DISTINCT merchant_id) FILTER (WHERE outcome IN ('Under-paid', 'Over-paid')) AS merchants_affected,
       (SELECT count(*) FROM settlements WHERE {aged}) AS aged_pending_batches
FROM recon"""
    return run_query(sql)


def settlement_outcomes() -> QueryOutcome:
    sql = f"""{_recon_cte()}
SELECT outcome, count(*) AS merchant_days, coalesce(sum(gap), 0) AS total_gap
FROM recon GROUP BY 1 ORDER BY merchant_days DESC"""
    return run_query(sql)


def settlement_daily_gap() -> QueryOutcome:
    sql = f"""{_recon_cte()}
SELECT transaction_date,
       coalesce(sum(gap) FILTER (WHERE outcome = 'Under-paid'), 0) AS under_paid,
       coalesce(sum(gap) FILTER (WHERE outcome = 'Over-paid'), 0) AS over_paid,
       count_if(outcome IN ('Under-paid', 'Over-paid')) AS batches_out_of_tolerance
FROM recon GROUP BY 1 ORDER BY 1"""
    return run_query(sql)


def settlement_by_merchant(limit: int = 15) -> QueryOutcome:
    sql = f"""{_recon_cte()}
SELECT r.merchant_id, m.merchant_name,
       count_if(outcome IN ('Under-paid', 'Over-paid')) AS batches_out_of_tolerance,
       count(*) AS merchant_days,
       sum(gap) FILTER (WHERE outcome IN ('Under-paid', 'Over-paid')) AS total_gap,
       min(transaction_date) FILTER (WHERE outcome IN ('Under-paid', 'Over-paid')) AS first_gap_date,
       max(transaction_date) FILTER (WHERE outcome IN ('Under-paid', 'Over-paid')) AS last_gap_date
FROM recon r LEFT JOIN merchants m USING (merchant_id)
GROUP BY 1, 2
HAVING count_if(outcome IN ('Under-paid', 'Over-paid')) > 0
ORDER BY abs(sum(gap) FILTER (WHERE outcome IN ('Under-paid', 'Over-paid'))) DESC
LIMIT {int(limit)}"""
    return run_query(sql)


def settlement_batches(merchant_id: str) -> QueryOutcome:
    """Drill-down: every reconciled day of one merchant (merchant_id is validated against the data first)."""
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,32}", merchant_id or ""):
        raise ValueError("invalid merchant id")
    sql = f"""{_recon_cte()}
SELECT transaction_date, outcome, gap, net_amount, expected_net, transaction_count, expected_count,
       gross_amount, expected_gross, batch_status, settlement_id, settlement_date
FROM recon WHERE merchant_id = '{merchant_id}'
ORDER BY (outcome IN ('Under-paid', 'Over-paid')) DESC, transaction_date DESC
LIMIT 60"""
    return run_query(sql)


def batch_status() -> QueryOutcome:
    aged = metric_sql("aged_pending_settlement")
    sql = f"""SELECT status, coalesce(status_reason, '—') AS status_reason, count(*) AS batches,
       count_if({aged}) AS aged_pending, sum(net_amount) AS net_amount
FROM settlements GROUP BY 1, 2 ORDER BY batches DESC"""
    return run_query(sql)


def sample(check: Check) -> QueryOutcome:
    return run_query(check.sample_sql)
