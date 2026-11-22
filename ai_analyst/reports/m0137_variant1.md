# Our success rate in the last two weeks looks worse than the two weeks before. What changed?

## Answer
The drop in success rate over the last two weeks is primarily driven by a sharp performance degradation for merchant M0137 (Zestora Online Mart) on Android. Specifically, M0137 experienced a massive surge in gateway-related failures, notably GATEWAY_ERROR and GATEWAY_TIMEOUT, starting around October 12, 2026.

## Findings (shown by the data)
1. Merchant M0137 (Zestora Online Mart) had its success rate drop from 0.8945 in the baseline period (1,166 attempts) to 0.7310 in the current period (1,104 attempts), explaining 0.556 pp of the overall SR drop.  
   _Evidence: Q1 · ✓ numbers verified against evidence_
2. The M0137 / ANDROID segment saw its success rate drop from 0.8907 to 0.6205, accounting for 174 excess unsuccessful attempts.  
   _Evidence: Q1 · ✓ numbers verified against evidence_
3. GATEWAY_ERROR failures increased from 185 (0.454% of attempts) in the baseline period to 298 (0.918%) in the current period, with M0137 / ANDROID contributing 142 of those in the current period.  
   _Evidence: Q2 · ✓ numbers verified against evidence_
4. GATEWAY_TIMEOUT failures rose from 0 in the baseline period to 33 in the current period, with M0137 / ANDROID accounting for all 33.  
   _Evidence: Q2 · ✓ numbers verified against evidence_
5. For merchant M0137, GATEWAY_ERROR failure count jumped from 5 in the baseline period to 147 in the current period, and GATEWAY_TIMEOUT went from 0 to 33.  
   _Evidence: Q4 · ✓ numbers verified against evidence_

## Hypotheses (possible drivers, not proven)
1. **Merchant M0137 deployed an app update or changed their payment gateway integration on or around October 12, 2026, causing gateway timeouts and errors.**  
   Why plausible: The timing is a sharp step change right on October 12, heavily concentrated on Android and specific gateway error codes.  
   Consistent with: Q3, Q4 · To verify: Check M0137's client release logs or server-side integration changes around October 12.

## Caveats
- Some FAILED transactions lack a failure_code, though the gateway errors and timeouts are well-captured.

## Suggested follow-ups
- Contact merchant M0137 technical support regarding recent Android app releases or gateway configurations.
- Inspect gateway-level logs for M0137 to determine if timeouts are originating from upstream acquirers or internal routing.

## Evidence: queries run
### Q1 · [driver scan] segments ranked by impact on SR change (2026-09-28..2026-10-11 vs 2026-10-12..2026-10-25)
```sql
WITH valid_txn AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT t.*, pm.method_code,
               CAST(t.created_at AS DATE) AS txn_date,
               row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn
        FROM transactions t
        JOIN payment_methods pm USING (payment_method_id)
    )
    WHERE _rn = 1
      AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING')
      AND amount > 0 AND amount <= 1000000
),
base AS (
    SELECT v.*, b.short_name AS bank, m.merchant_name, m.category AS merchant_category,
           CASE WHEN v.txn_date BETWEEN DATE '2026-10-12' AND DATE '2026-10-25' THEN 'cur'
                WHEN v.txn_date BETWEEN DATE '2026-09-28' AND DATE '2026-10-11' THEN 'base' END AS period
    FROM valid_txn v
    LEFT JOIN banks b ON v.bank_id = b.bank_id
    LEFT JOIN merchants m ON v.merchant_id = m.merchant_id
    WHERE v.status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED') 
),
seg AS (
    SELECT 'merchant' AS dimension, coalesce(merchant_id, '(none)') || coalesce(' ' || merchant_name, '') AS segment, period, count(*) AS n, count_if(status = 'SUCCESS') AS s FROM base WHERE period IS NOT NULL GROUP BY ALL
    UNION ALL
    SELECT 'device_platform' AS dimension, device_platform AS segment, period, count(*) AS n, count_if(status = 'SUCCESS') AS s FROM base WHERE period IS NOT NULL GROUP BY ALL
    UNION ALL
    SELECT 'merchant x platform' AS dimension, coalesce(merchant_id, '(none)') || ' / ' || device_platform AS segment, period, count(*) AS n, count_if(status = 'SUCCESS') AS s FROM base WHERE period IS NOT NULL GROUP BY ALL
    UNION ALL
    SELECT 'payment_method' AS dimension, method_code AS segment, period, count(*) AS n, count_if(status = 'SUCCESS') AS s FROM base WHERE period IS NOT NULL GROUP BY ALL
    UNION ALL
    SELECT 'issuing_bank' AS dimension, coalesce(bank, '(none)') AS segment, period, count(*) AS n, count_if(status = 'SUCCESS') AS s FROM base WHERE period IS NOT NULL GROUP BY ALL
    UNION ALL
    SELECT 'bank x method' AS dimension, coalesce(bank, '(none)') || ' / ' || method_code AS segment, period, count(*) AS n, count_if(status = 'SUCCESS') AS s FROM base WHERE period IS NOT NULL GROUP BY ALL
    UNION ALL
    SELECT 'merchant_category' AS dimension, coalesce(merchant_category, '(none)') AS segment, period, count(*) AS n, count_if(status = 'SUCCESS') AS s FROM base WHERE period IS NOT NULL GROUP BY ALL
    UNION ALL
    SELECT 'channel' AS dimension, channel AS segment, period, count(*) AS n, count_if(status = 'SUCCESS') AS s FROM base WHERE period IS NOT NULL GROUP BY ALL
),
piv AS (
    SELECT dimension, segment,
           coalesce(sum(n) FILTER (WHERE period = 'base'), 0) AS n_base,
           coalesce(sum(s) FILTER (WHERE period = 'base'), 0) AS s_base,
           coalesce(sum(n) FILTER (WHERE period = 'cur'), 0) AS n_cur,
           coalesce(sum(s) FILTER (WHERE period = 'cur'), 0) AS s_cur
    FROM seg GROUP BY dimension, segment
),
tot AS (SELECT count_if(period = 'cur') AS total_cur FROM base),
scored AS (
    SELECT dimension, segment, n_base, round(s_base::DOUBLE / n_base, 4) AS sr_baseline,
           n_cur, round(s_cur::DOUBLE / n_cur, 4) AS sr_current,
           round(s_cur::DOUBLE / n_cur - s_base::DOUBLE / n_base, 4) AS sr_change,
           round(n_cur::DOUBLE / total_cur * (s_cur::DOUBLE / n_cur - s_base::DOUBLE / n_base) * 100, 3) AS impact_on_overall_sr_pp,
           round((s_base::DOUBLE / n_base - s_cur::DOUBLE / n_cur) * n_cur) AS excess_unsuccessful_attempts
    FROM piv CROSS JOIN tot
    WHERE n_base >= 100 AND n_cur >= 100
)
SELECT * FROM scored
QUALIFY row_number() OVER (PARTITION BY dimension ORDER BY impact_on_overall_sr_pp ASC) <= 4
ORDER BY impact_on_overall_sr_pp ASC
LIMIT 40
```
| dimension | segment | n_base | sr_baseline | n_cur | sr_current | sr_change | impact_on_overall_sr_pp | excess_unsuccessful_attempts |
|---|---|---|---|---|---|---|---|---|
| merchant | M0137 Zestora Online Mart | 1166 | 0.8945 | 1104 | 0.731 | -0.1635 | -0.556 | 181 |
| merchant x platform | M0137 / ANDROID | 622 | 0.8907 | 643 | 0.6205 | -0.2701 | -0.535 | 174 |
| payment_method | UPI | 25333 | 0.8806 | 14811 | 0.8702 | -0.0104 | -0.476 | 154 |
| merchant_category | ECOMMERCE | 7218 | 0.8904 | 5755 | 0.8636 | -0.0268 | -0.475 | 154 |
| channel | ONLINE | 23137 | 0.8894 | 18490 | 0.8819 | -0.0076 | -0.43 | 140 |
| device_platform | ANDROID | 12711 | 0.8885 | 10198 | 0.8769 | -0.0116 | -0.364 | 118 |
| issuing_bank | SBI | 8587 | 0.8951 | 6507 | 0.8787 | -0.0163 | -0.327 | 106 |
| bank x method | SBI / UPI | 5900 | 0.8815 | 3374 | 0.8577 | -0.0238 | -0.247 | 80 |
| payment_method | NET_BANKING | 1956 | 0.8569 | 2258 | 0.8295 | -0.0274 | -0.19 | 62 |
| payment_method | CREDIT_CARD | 5528 | 0.9474 | 6495 | 0.9404 | -0.0069 | -0.139 | 45 |
| bank x method | SBI / DEBIT_CARD | 1267 | 0.9361 | 1478 | 0.91 | -0.0261 | -0.119 | 39 |
| bank x method | ICICI / UPI | 3492 | 0.8958 | 2145 | 0.8844 | -0.0114 | -0.075 | 24 |
| bank x method | AXIS / UPI | 2225 | 0.8885 | 1346 | 0.8707 | -0.0178 | -0.074 | 24 |
| device_platform | DESKTOP_WEB | 2885 | 0.8984 | 2101 | 0.8886 | -0.0098 | -0.064 | 21 |
| payment_method | DEBIT_CARD | 5707 | 0.931 | 6467 | 0.9286 | -0.0024 | -0.048 | 16 |
_15 of 30 rows shown_

### Q2 · [driver scan] failure-reason shift (2026-09-28..2026-10-11 vs 2026-10-12..2026-10-25)
```sql
WITH valid_txn AS (
    SELECT * EXCLUDE (_rn)
    FROM (
        SELECT t.*, pm.method_code,
               CAST(t.created_at AS DATE) AS txn_date,
               row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn
        FROM transactions t
        JOIN payment_methods pm USING (payment_method_id)
    )
    WHERE _rn = 1
      AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING')
      AND amount > 0 AND amount <= 1000000
),
base AS (
    SELECT v.*, b.short_name AS bank, m.merchant_name, m.category AS merchant_category,
           CASE WHEN v.txn_date BETWEEN DATE '2026-10-12' AND DATE '2026-10-25' THEN 'cur'
                WHEN v.txn_date BETWEEN DATE '2026-09-28' AND DATE '2026-10-11' THEN 'base' END AS period
    FROM valid_txn v
    LEFT JOIN banks b ON v.bank_id = b.bank_id
    LEFT JOIN merchants m ON v.merchant_id = m.merchant_id
    WHERE v.status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED') 
),
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
ORDER BY change_pp DESC
```
| failure_code | failed_base | pct_of_attempts_base | failed_cur | pct_of_attempts_cur | change_pp | top_merchant_platform_cur | top_segment_failed_cur |
|---|---|---|---|---|---|---|---|
| GATEWAY_ERROR | 185 | 0.454 | 298 | 0.918 | 0.463 | M0137 / ANDROID | 142 |
| AUTH_FAILED | 181 | 0.445 | 230 | 0.708 | 0.264 | M0248 / POS_TERMINAL | 6 |
| GATEWAY_TIMEOUT | 0 | 0 | 33 | 0.102 | 0.102 | M0137 / ANDROID | 33 |
| CARD_EXPIRED | 18 | 0.044 | 27 | 0.083 | 0.039 | M0237 / POS_TERMINAL | 3 |
| (missing) | 185 | 0.454 | 148 | 0.456 | 0.001 | M0137 / ANDROID | 20 |
| BANK_TIMEOUT | 342 | 0.84 | 262 | 0.807 | -0.033 | M0137 / ANDROID | 5 |
| ISSUER_UNAVAILABLE | 152 | 0.373 | 110 | 0.339 | -0.035 | M0030 / ANDROID | 3 |
| RISK_DECLINED | 146 | 0.359 | 102 | 0.314 | -0.044 | M0175 / ANDROID | 5 |
| INCORRECT_PIN | 452 | 1.11 | 327 | 1.007 | -0.103 | M0045 / ANDROID | 8 |
| LIMIT_EXCEEDED | 242 | 0.594 | 155 | 0.477 | -0.117 | M0045 / ANDROID | 4 |
| INVALID_VPA | 142 | 0.349 | 75 | 0.231 | -0.118 | M0248 / POS_TERMINAL | 3 |
| BANK_DECLINED | 305 | 0.749 | 200 | 0.616 | -0.133 | M0218 / POS_TERMINAL | 5 |
| INSUFFICIENT_FUNDS | 623 | 1.53 | 453 | 1.395 | -0.135 | M0003 / ANDROID | 10 |
| NETWORK_ERROR | 175 | 0.43 | 88 | 0.271 | -0.159 | M0175 / POS_TERMINAL | 4 |

### Q3 · Daily success rate and failure count for merchant M0137 around the date change
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT txn_date, count_if(status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED')) AS terminal_attempts, count_if(status = 'SUCCESS') AS success_count, round(success_count::DOUBLE / NULLIF(terminal_attempts, 0), 4) AS success_rate FROM valid_txn WHERE merchant_id = 'M0137' AND txn_date >= DATE '2026-09-14' AND txn_date <= DATE '2026-10-25' GROUP BY 1 ORDER BY 1
```
| txn_date | terminal_attempts | success_count | success_rate |
|---|---|---|---|
| 2026-09-14 | 74 | 61 | 0.8243 |
| 2026-09-15 | 75 | 60 | 0.8 |
| 2026-09-16 | 64 | 61 | 0.9531 |
| 2026-09-17 | 70 | 61 | 0.8714 |
| 2026-09-18 | 72 | 63 | 0.875 |
| 2026-09-19 | 66 | 61 | 0.9242 |
| 2026-09-20 | 57 | 52 | 0.9123 |
| 2026-09-21 | 76 | 68 | 0.8947 |
| 2026-09-22 | 73 | 65 | 0.8904 |
| 2026-09-23 | 77 | 69 | 0.8961 |
| 2026-09-24 | 66 | 57 | 0.8636 |
| 2026-09-25 | 83 | 80 | 0.9639 |
| 2026-09-26 | 77 | 72 | 0.9351 |
| 2026-09-27 | 66 | 57 | 0.8636 |
| 2026-09-28 | 71 | 66 | 0.9296 |
_15 of 42 rows shown_

### Q4 · Failure codes for merchant M0137 comparing baseline vs current period
```sql
WITH valid_txn AS (SELECT * EXCLUDE (_rn) FROM (SELECT t.*, pm.method_code, CAST(t.created_at AS DATE) AS txn_date, row_number() OVER (PARTITION BY t.transaction_id ORDER BY t.updated_at DESC) AS _rn FROM transactions t JOIN payment_methods pm USING (payment_method_id)) WHERE _rn = 1 AND status IN ('SUCCESS', 'FAILED', 'USER_CANCELLED', 'PENDING') AND amount > 0 AND amount <= 1000000) SELECT CASE WHEN txn_date BETWEEN DATE '2026-09-28' AND DATE '2026-10-11' THEN 'baseline' WHEN txn_date BETWEEN DATE '2026-10-12' AND DATE '2026-10-25' THEN 'current' ELSE 'other' END AS period, failure_code, count(*) AS failure_count FROM valid_txn WHERE merchant_id = 'M0137' AND status = 'FAILED' AND txn_date >= DATE '2026-09-28' AND txn_date <= DATE '2026-10-25' GROUP BY 1, 2 ORDER BY 1, 3 DESC
```
| period | failure_code | failure_count |
|---|---|---|
| baseline | INSUFFICIENT_FUNDS | 28 |
| baseline | BANK_DECLINED | 15 |
| baseline | INCORRECT_PIN | 13 |
| baseline | BANK_TIMEOUT | 8 |
| baseline | ISSUER_UNAVAILABLE | 6 |
| baseline | NULL | 6 |
| baseline | RISK_DECLINED | 5 |
| baseline | GATEWAY_ERROR | 5 |
| baseline | LIMIT_EXCEEDED | 4 |
| baseline | INVALID_VPA | 3 |
| baseline | NETWORK_ERROR | 3 |
| baseline | AUTH_FAILED | 1 |
| current | GATEWAY_ERROR | 147 |
| current | GATEWAY_TIMEOUT | 33 |
| current | INSUFFICIENT_FUNDS | 21 |
_15 of 25 rows shown_

---
_3 round(s) · 4/4 queries succeeded · 11.1s · gemini-3.5-flash-lite · tokens in/out 24044/1819_